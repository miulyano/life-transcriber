from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress

import aiofiles
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from bot.config import settings
from bot.services.media import prepare_audio_for_transcription
from bot.services.temp_cleanup import run_periodic_temp_cleanup
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.utils.progress import ProgressReporter
from webapp.auth import validate_init_data
from webapp.delivery import send_transcript_to_chat

logger = logging.getLogger(__name__)

MAX_INIT_DATA_AGE = 24 * 3600  # 24 hours
UPLOAD_ERROR_TEXT = (
    "Не удалось обработать файл. Проверь, что это аудио или видео, и попробуй ещё раз."
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    cleanup_task = asyncio.create_task(
        run_periodic_temp_cleanup(settings.TEMP_DIR, logger=logger)
    )
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="life-transcriber webapp", lifespan=lifespan)


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


async def _process_upload(
    dest: str, user_id: int, filename_hint: str | None = None
) -> None:
    """Transcribe file and deliver result to chat. Runs as a background task."""
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    audio_path: str | None = None
    source_bytes = _file_size(dest)
    started_at = time.monotonic()

    try:
        async with ProgressReporter.for_chat(bot, user_id, "Готовлю аудио…") as reporter:
            try:
                prepare_started_at = time.monotonic()
                audio_path = await prepare_audio_for_transcription(
                    dest, settings.TEMP_DIR
                )
                prepare_seconds = time.monotonic() - prepare_started_at
                audio_bytes = _file_size(audio_path)
                logger.info(
                    "Prepared upload audio for user %s: source=%s bytes, audio=%s bytes, seconds=%.2f",
                    user_id,
                    source_bytes,
                    audio_bytes,
                    prepare_seconds,
                )

                await reporter.set_phase("Транскрибирую…")

                transcribe_started_at = time.monotonic()
                timings: dict[str, float] = {}

                async def on_phase_change(label: str) -> None:
                    now = time.monotonic()
                    if label == "Отправляю результат…":
                        timings["transcribe_seconds"] = now - transcribe_started_at
                        timings["delivery_started_at"] = now

                async def deliver_text(text: str) -> None:
                    await send_transcript_to_chat(bot, user_id, text)

                await run_transcription_pipeline(
                    audio_path,
                    reporter=reporter,
                    deliver_text=deliver_text,
                    filename_hint=filename_hint,
                    on_phase_change=on_phase_change,
                )
                now = time.monotonic()
                transcribe_seconds = timings.get(
                    "transcribe_seconds",
                    now - transcribe_started_at,
                )
                delivery_started_at = timings.get("delivery_started_at", now)
                delivery_seconds = now - delivery_started_at
                await reporter.finish()

                logger.info(
                    "Processed upload for user %s: source=%s bytes, audio=%s bytes, "
                    "prepare=%.2fs, transcribe=%.2fs, delivery=%.2fs, total=%.2fs",
                    user_id,
                    source_bytes,
                    audio_bytes,
                    prepare_seconds,
                    transcribe_seconds,
                    delivery_seconds,
                    time.monotonic() - started_at,
                )
            except Exception:
                logger.exception("Transcription failed for %s (user %s)", dest, user_id)
                await reporter.fail(UPLOAD_ERROR_TEXT)
    finally:
        for path in (audio_path, dest):
            if path and os.path.exists(path):
                os.unlink(path)
        await bot.session.close()


@app.post("/api/upload")
async def upload(
    file: UploadFile,
    init_data: str = Form(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> dict:
    # --- Auth ---
    parsed = validate_init_data(init_data, settings.BOT_TOKEN)
    if not parsed:
        raise HTTPException(403, "Invalid auth")
    if time.time() - parsed["auth_date"] > MAX_INIT_DATA_AGE:
        raise HTTPException(403, "initData expired \u2014 reopen the app")

    user_id = parsed["user_id"]
    if user_id not in settings.allowed_user_ids:
        raise HTTPException(403, "Not whitelisted")

    # --- Save file to disk ---
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    safe_name = (file.filename or "upload").replace("/", "_")
    dest = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}_{safe_name}")

    save_started_at = time.monotonic()
    bytes_written = 0
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            bytes_written += len(chunk)
            await f.write(chunk)

    logger.info(
        "Saved upload %s (user %s, bytes=%s, seconds=%.2f)",
        dest,
        user_id,
        bytes_written,
        time.monotonic() - save_started_at,
    )

    # --- Respond immediately, transcribe in background ---
    background_tasks.add_task(_process_upload, dest, user_id, file.filename)
    return {"ok": True}


# Static files mount last (catchall -- must be after API routes)
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
    name="static",
)
