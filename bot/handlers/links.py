from __future__ import annotations

import os
import re

from aiogram import F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import download_audio
from bot.services.error_messages import format_download_error
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file

router = Router()

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Alias kept so existing tests that reference links._friendly_error continue to work.
_friendly_error = format_download_error


@router.message(F.text.regexp(URL_RE))
async def handle_link(message: Message) -> None:
    urls = URL_RE.findall(message.text)
    url = urls[0]

    audio_path: str | None = None
    source_title: str | None = None
    async with ProgressReporter(message, "Скачиваю аудио по ссылке…") as reporter:
        try:
            try:
                audio_path, source_title = await download_audio(url, settings.TEMP_DIR)
            except RuntimeError as e:
                await reporter.fail(_friendly_error(e))
                return
            await reporter.set_phase("Транскрибирую…")

            async def deliver_text(text: str) -> None:
                await reply_text_or_file(message, text)

            await run_transcription_pipeline(
                audio_path,
                reporter=reporter,
                deliver_text=deliver_text,
                filename_hint=source_title,
            )
        finally:
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
        await reporter.finish()
