from __future__ import annotations

import os
import uuid

from aiogram import Bot
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import extract_audio
from bot.services.transcription_pipeline import run_transcription_pipeline
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
) -> None:
    """Download a Telegram file, optionally extract audio, then transcribe."""
    async with ProgressReporter(message, label) as reporter:
        media_path = await download_tg_file(bot, file_id, suffix)
        audio_path: str | None = None
        try:
            if extract_audio_first:
                await reporter.set_phase("Извлекаю аудио…")
                audio_path = await extract_audio(media_path, settings.TEMP_DIR)
                await reporter.set_phase("Транскрибирую…")
                transcribe_path = audio_path
            else:
                transcribe_path = media_path

            async def deliver_text(text: str) -> None:
                await reply_text_or_file(message, text)

            await run_transcription_pipeline(
                transcribe_path,
                reporter=reporter,
                deliver_text=deliver_text,
                filename_hint=filename_hint,
            )
        finally:
            if os.path.exists(media_path):
                os.unlink(media_path)
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
        await reporter.finish()
