from __future__ import annotations

import logging
import os
import re

from aiogram import Router
from aiogram.enums import MessageEntityType
from aiogram.types import Message

from bot.config import settings
from bot.handlers._timecode_prompt import ask_timecodes
from bot.services.downloader import detect_link_source_type, download_audio
from bot.services.error_messages import format_download_error, format_exception_chain
from bot.services.pending_jobs import PendingJob
from bot.services.source_meta import SourceMetadata
from bot.services.task_registry import cancel_keyboard, get_semaphore
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.services.usage_store import LimitExceededError, format_limit_exceeded_message
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file

logger = logging.getLogger(__name__)

router = Router()

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Alias kept so existing tests that reference links._friendly_error continue to work.
_friendly_error = format_download_error


def extract_urls(message: Message) -> list[str]:
    """Collect http(s) URLs from text/caption: raw matches, url entities and
    text_link entities (hyperlinks whose URL is not in the visible text)."""
    urls: list[str] = []
    for text, entities in (
        (message.text, message.entities),
        (message.caption, message.caption_entities),
    ):
        if not text:
            continue
        for entity in entities or ():
            if entity.type == MessageEntityType.TEXT_LINK and entity.url:
                urls.append(entity.url)
            elif entity.type == MessageEntityType.URL:
                urls.append(entity.extract_from(text))
        urls.extend(URL_RE.findall(text))
    urls = [u for u in urls if URL_RE.match(u)]
    return list(dict.fromkeys(urls))


def _contains_url(message: Message) -> bool:
    # Media messages (video/voice/video_note) are picked up by their routers,
    # which are registered before this one in bot/main.py. If an audio handler
    # is ever added, its router must also be registered before links.router.
    return bool(extract_urls(message))


@router.message(_contains_url)
async def handle_link(message: Message) -> None:
    url = extract_urls(message)[0]
    await ask_timecodes(
        message,
        PendingJob(
            user_id=message.from_user.id if message.from_user else 0,
            kind="link",
            message=message,
            source_type=detect_link_source_type(url),
            url=url,
        ),
    )


async def process_link(
    message: Message,
    url: str,
    *,
    timecodes: bool = True,
    source_type: str = "link",
    cancel_token: str | None = None,
) -> None:
    """Download audio from the URL and run the transcription pipeline."""
    user_id = message.from_user.id if message.from_user else 0
    audio_path: str | None = None
    source_meta: SourceMetadata = SourceMetadata()
    sem = get_semaphore()
    initial = "В очереди…" if sem.locked() else "Скачиваю аудио по ссылке…"
    async with ProgressReporter(
        message, initial, reply_markup=cancel_keyboard(cancel_token)
    ) as reporter:
        async with sem:
            await reporter.set_phase("Скачиваю аудио по ссылке…")
            limit_exceeded: LimitExceededError | None = None
            try:
                try:
                    audio_path, source_meta = await download_audio(
                        url,
                        settings.TEMP_DIR,
                        on_progress_fraction=reporter.set_progress_fraction,
                        on_postprocess=lambda: reporter.set_phase("Готовлю аудио…"),
                    )
                except RuntimeError as e:
                    # The friendly message hides the real cause; log the whole
                    # `raise ... from` chain so transient provider failures
                    # (e.g. a yt-dlp crash wrapped in a UserFacingError) stay
                    # diagnosable.
                    logger.warning(
                        "Download failed for %s: %s",
                        url,
                        format_exception_chain(e),
                    )
                    await reporter.fail(_friendly_error(e))
                    return
                await reporter.set_phase("Транскрибирую…")

                async def deliver_text(text: str, file_text: str | None = None) -> None:
                    await reply_text_or_file(
                        message,
                        text,
                        file_text if timecodes else None,
                        source_type=source_type,
                    )

                try:
                    await run_transcription_pipeline(
                        audio_path,
                        reporter=reporter,
                        deliver_text=deliver_text,
                        user_id=user_id,
                        source_meta=source_meta,
                        source_type=source_type,
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
