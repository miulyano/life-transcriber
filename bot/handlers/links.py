from __future__ import annotations

import os
import re

from aiogram import F, Router
from aiogram.types import Message

from bot.config import settings
from bot.handlers._timecode_prompt import ask_timecodes
from bot.services.downloader import download_audio
from bot.services.error_messages import format_download_error
from bot.services.pending_jobs import PendingJob
from bot.services.source_meta import SourceMetadata
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.services.usage_store import LimitExceededError, format_limit_exceeded_message
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
    await ask_timecodes(
        message,
        PendingJob(
            user_id=message.from_user.id if message.from_user else 0,
            kind="link",
            message=message,
            source_type="link",
            url=url,
        ),
    )


async def process_link(message: Message, url: str, *, timecodes: bool = True) -> None:
    """Download audio from the URL and run the transcription pipeline."""
    user_id = message.from_user.id if message.from_user else 0
    audio_path: str | None = None
    source_meta: SourceMetadata = SourceMetadata()
    async with ProgressReporter(message, "Скачиваю аудио по ссылке…") as reporter:
        limit_exceeded: LimitExceededError | None = None
        try:
            try:
                audio_path, source_meta = await download_audio(url, settings.TEMP_DIR)
            except RuntimeError as e:
                await reporter.fail(_friendly_error(e))
                return
            await reporter.set_phase("Транскрибирую…")

            async def deliver_text(text: str, file_text: str | None = None) -> None:
                await reply_text_or_file(message, text, file_text if timecodes else None)

            try:
                await run_transcription_pipeline(
                    audio_path,
                    reporter=reporter,
                    deliver_text=deliver_text,
                    user_id=user_id,
                    source_meta=source_meta,
                    source_type="link",
                )
            except LimitExceededError as exc:
                limit_exceeded = exc
        finally:
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
        if limit_exceeded is not None:
            await reporter.fail(format_limit_exceeded_message(limit_exceeded.limit_hours))
        else:
            await reporter.finish()
