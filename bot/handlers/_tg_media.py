from __future__ import annotations

import os
import uuid

from aiogram import Bot
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import extract_audio
from bot.services.source_meta import SourceMetadata
from bot.services.task_registry import cancel_keyboard, get_semaphore
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.services.usage_store import LimitExceededError, format_limit_exceeded_message
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file


async def download_tg_file(bot: Bot, file_id: str, suffix: str) -> str:
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    dest = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}{suffix}")
    await bot.download(file_id, destination=dest)
    return dest


async def process_tg_media(
    message: Message,
    bot: Bot,
    file_id: str,
    suffix: str,
    *,
    label: str,
    extract_audio_first: bool = False,
    filename_hint: str | None = None,
    source_type: str = "unknown",
    timecodes: bool = True,
    cancel_token: str | None = None,
) -> None:
    """Download a Telegram file, optionally extract audio, then transcribe."""
    user_id = message.from_user.id if message.from_user else 0
    source_meta = SourceMetadata(title=filename_hint or None, title_is_filename=True)
    sem = get_semaphore()
    initial = "В очереди…" if sem.locked() else label
    async with ProgressReporter(
        message, initial, reply_markup=cancel_keyboard(cancel_token)
    ) as reporter:
        async with sem:
            await reporter.set_phase(label)
            media_path: str | None = None
            audio_path: str | None = None
            limit_exceeded: LimitExceededError | None = None
            try:
                media_path = await download_tg_file(bot, file_id, suffix)
                if extract_audio_first:
                    await reporter.set_phase("Извлекаю аудио…")
                    audio_path = await extract_audio(media_path, settings.TEMP_DIR)
                    await reporter.set_phase("Транскрибирую…")
                    transcribe_path = audio_path
                else:
                    transcribe_path = media_path

                async def deliver_text(text: str, file_text: str | None = None) -> None:
                    await reply_text_or_file(message, text, file_text if timecodes else None)

                try:
                    await run_transcription_pipeline(
                        transcribe_path,
                        reporter=reporter,
                        deliver_text=deliver_text,
                        user_id=user_id,
                        source_meta=source_meta,
                        source_type=source_type,
                    )
                except LimitExceededError as exc:
                    limit_exceeded = exc
            finally:
                if media_path and os.path.exists(media_path):
                    os.unlink(media_path)
                if audio_path and os.path.exists(audio_path):
                    os.unlink(audio_path)
            if limit_exceeded is not None:
                await reporter.fail(format_limit_exceeded_message(limit_exceeded.limit_hours))
            else:
                await reporter.finish()
