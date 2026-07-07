from __future__ import annotations

from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.utils.text import prepare_transcript


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
