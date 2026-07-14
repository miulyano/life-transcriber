from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress
from glob import glob

import aiofiles
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from bot.config import settings
from bot.services.job_store import get_job_store
from bot.services.media import prepare_audio_for_transcription
from bot.services.source_meta import SourceMetadata
from bot.services.task_registry import get_semaphore
from bot.services.temp_cleanup import run_periodic_temp_cleanup
from bot.services.token_store import get_token_store
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.services.usage_store import LimitExceededError, format_limit_exceeded_message
from bot.utils.progress import ProgressReporter
from fastapi import Depends

from webapp.auth import validate_init_data
from webapp.delivery import send_transcript_to_chat
from webapp.deps import resolve_user_id
from webapp.mcp_auth import MCPBearerAuth
from webapp.mcp_server import mcp
from webapp.transcripts_api import router as transcripts_router

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
    # Only this process executes MCP jobs, so anything queued/running at
    # startup is guaranteed dead after a restart.
    stale = await get_job_store().mark_stale_interrupted()
    if stale:
        logger.warning("Marked %d stale MCP jobs as interrupted", stale)
    removed_jobs = await get_job_store().cleanup_old(days=30)
    removed_requests = await get_token_store().cleanup_old(days=30)
    if removed_jobs or removed_requests:
        logger.info(
            "Housekeeping: removed %d old jobs, %d old auth requests",
            removed_jobs,
            removed_requests,
        )
    try:
        # Streamable HTTP requires a running session manager for /mcp.
        async with mcp.session_manager.run():
            yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(title="life-transcriber webapp", lifespan=lifespan)

# Upload endpoints stream to the shared temp volume; anything larger than the
# per-file cap plus a little multipart overhead is rejected before the body is
# parsed (Starlette spools file parts to disk otherwise). This is defence in
# depth — the primary ingress limit belongs in Caddy (request_body max_size,
# which should be >= MAX_UPLOAD_MB).
_MULTIPART_OVERHEAD = 16 * 1024 * 1024  # 16 MB for multipart headers/boundaries
_MAX_REQUEST_BODY_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024 + _MULTIPART_OVERHEAD
_UPLOAD_PATHS = ("/api/files", "/api/upload")


@app.middleware("http")
async def limit_upload_body(request, call_next):
    if request.url.path in _UPLOAD_PATHS:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
            if declared > _MAX_REQUEST_BODY_BYTES:
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "Request too large"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Force revalidation of static files.

    Telegram WebView caches subresources aggressively when no Cache-Control is
    set: after a deploy it may serve fresh index.html with a stale app.js
    (dead UI handlers). The files are tiny — always revalidate.
    """
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


async def _process_upload(
    dest: str, user_id: int, filename_hint: str | None = None, timecodes: bool = True
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
        # get_semaphore() caps simultaneous transcriptions within this process
        # (the webapp runs separately from the bot, so each has its own cap).
        async with get_semaphore(), ProgressReporter.for_chat(
            bot, user_id, "Готовлю аудио…"
        ) as reporter:
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
                    if label == "Форматирую…":
                        timings["format_started_at"] = now
                    elif label == "Отправляю результат…":
                        timings["transcribe_seconds"] = timings.get(
                            "format_started_at", now
                        ) - transcribe_started_at
                        timings["delivery_started_at"] = now

                async def deliver_text(text: str, file_text: str | None = None) -> None:
                    await send_transcript_to_chat(
                        bot,
                        user_id,
                        text,
                        file_text if timecodes else None,
                        source_type="webapp",
                    )

                try:
                    await run_transcription_pipeline(
                        audio_path,
                        reporter=reporter,
                        deliver_text=deliver_text,
                        user_id=user_id,
                        source_meta=SourceMetadata(
                            title=filename_hint or None, title_is_filename=True
                        ),
                        on_phase_change=on_phase_change,
                        source_type="webapp",
                    )
                except LimitExceededError as exc:
                    await reporter.fail(format_limit_exceeded_message(exc.limit_hours))
                    return
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
    timecodes: bool = Form(True),
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
    background_tasks.add_task(_process_upload, dest, user_id, file.filename, timecodes)
    return {"ok": True}


MAX_AGENT_UPLOAD_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024  # один файл
MAX_AGENT_PENDING_BYTES = settings.MAX_PENDING_UPLOAD_MB * 1024 * 1024  # на юзера

# Сериализует загрузки одного пользователя, чтобы per-user quota не
# обходилась конкурентными запросами (все увидели бы объём до записи).
# Single-process uvicorn — in-process Lock достаточно.
_upload_locks: dict[int, asyncio.Lock] = {}


def _upload_lock(user_id: int) -> asyncio.Lock:
    lock = _upload_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _upload_locks[user_id] = lock
    return lock


def _pending_upload_bytes(user_id: int) -> int:
    """Суммарный размер незабранных загрузок этого пользователя."""
    total = 0
    for prefix in (f"mcpfile_{user_id}_", f"mcpclaim_{user_id}_"):
        for path in glob(os.path.join(settings.TEMP_DIR, f"{prefix}*")):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    return total


@app.post("/api/files")
async def upload_file_for_agent(
    file: UploadFile, user_id: int = Depends(resolve_user_id)
) -> dict:
    """Upload a media file for a later MCP submit_file(file_id) call.

    The user_id embedded in the filename is the ownership check: submit_file
    globs only mcpfile_<its own user_id>_… paths. Unclaimed uploads are
    swept by the periodic temp cleanup (6h TTL). A per-file size cap and a
    per-user pending-storage quota bound disk use on the shared temp volume;
    a per-user lock makes the quota hold under concurrent uploads.
    """
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    file_id = uuid.uuid4().hex
    safe_name = os.path.basename(file.filename or "upload").replace("/", "_") or "upload"
    dest = os.path.join(settings.TEMP_DIR, f"mcpfile_{user_id}_{file_id}_{safe_name}")

    async with _upload_lock(user_id):
        existing = _pending_upload_bytes(user_id)
        if existing >= MAX_AGENT_PENDING_BYTES:
            raise HTTPException(
                413, "Too many pending uploads — submit or wait for them to expire"
            )
        bytes_written = 0
        try:
            async with aiofiles.open(dest, "wb") as f:
                while chunk := await file.read(1 << 20):
                    bytes_written += len(chunk)
                    if bytes_written > MAX_AGENT_UPLOAD_BYTES:
                        raise HTTPException(413, "File too large")
                    if existing + bytes_written > MAX_AGENT_PENDING_BYTES:
                        raise HTTPException(413, "Pending upload quota exceeded")
                    await f.write(chunk)
        except BaseException:
            # Do not leave a partial/oversized upload on the shared volume.
            if os.path.exists(dest):
                os.unlink(dest)
            raise

    logger.info(
        "Saved agent file %s (user %s, bytes=%s)", dest, user_id, bytes_written
    )
    return {"file_id": file_id}


app.include_router(transcripts_router)

# MCP endpoint (streamable-http, stateless). Mounted before the static
# catchall; bearer identity is injected per-request by MCPBearerAuth.
app.mount("/mcp", MCPBearerAuth(mcp.streamable_http_app()))

# Static files mount last (catchall -- must be after API routes)
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
    name="static",
)
