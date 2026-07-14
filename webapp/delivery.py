from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from bot.config import settings
from bot.services.formatter import render_timecode_segments
from bot.services.transcript_store import TranscriptRecord, TranscriptStore
from bot.utils.filename import split_header_and_body
from bot.utils.progress import ProgressReporter
from bot.utils.text import prepare_transcript

logger = logging.getLogger(__name__)


async def send_transcript_to_chat(
    bot: Bot,
    chat_id: int,
    text: str,
    file_text: str | None = None,
    *,
    source_type: str | None = None,
) -> None:
    """Send transcription result to a Telegram chat.

    Uses prepare_transcript() from bot/utils/text.py so inline buttons
    work identically to the message-based reply_text_or_file().
    ``file_text`` only replaces the document content (timecoded variant);
    ``source_type`` adds the source/timecodes footer.
    """
    d = prepare_transcript(
        text, source_type=source_type, has_timecoded_file=file_text is not None
    )
    if not d.send_as_file:
        await bot.send_message(chat_id, d.body_html, reply_markup=d.keyboard)
    else:
        await bot.send_document(
            chat_id,
            BufferedInputFile((file_text or text).encode("utf-8"), filename=d.filename),
            caption=d.caption_html or "Транскрибация готова.",
            reply_markup=d.keyboard,
        )


async def build_resend_payload(
    record: TranscriptRecord, store: TranscriptStore, timecoded: bool
) -> tuple[str, str | None]:
    """Text + optional timecoded file body for re-sending a stored transcript.

    Timecodes are only deliverable on the file path (len > threshold);
    for short transcripts the flag is silently ignored, mirroring
    prepare_transcript's send_as_file logic.
    """
    text = await store.read_text(record)
    file_text: str | None = None
    if timecoded and len(text) > settings.LONG_TEXT_THRESHOLD:
        segments = await store.read_segments(record)
        if segments:
            rendered = render_timecode_segments(segments)
            header, _ = split_header_and_body(text)
            file_text = f"{header}\n\n{rendered}".strip() if header else rendered
    return text, file_text


async def deliver_transcript_with_status(
    user_id: int, text: str, file_text: str | None, source_type: str | None
) -> None:
    """Send a stored transcript to chat with an in-chat status message.

    Same status model as _process_upload: a ProgressReporter status message
    in chat shows the phase; on failure it turns into an in-chat error.
    """
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with ProgressReporter.for_chat(
            bot, user_id, "Отправляю результат…"
        ) as reporter:
            try:
                await send_transcript_to_chat(
                    bot, user_id, text, file_text, source_type=source_type
                )
                await reporter.finish()
            except Exception:
                logger.exception("Resend to chat failed for user %s", user_id)
                await reporter.fail(
                    "Не удалось отправить транскрибацию. Попробуй ещё раз."
                )
    finally:
        await bot.session.close()
