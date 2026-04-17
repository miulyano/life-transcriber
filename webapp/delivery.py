from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.config import settings
from bot.utils.text import _store_text, build_keyboard


async def send_transcript_to_chat(bot: Bot, chat_id: int, text: str) -> None:
    """Send transcription result to a Telegram chat.

    Mirrors reply_text_or_file() from bot/utils/text.py but takes
    (bot, chat_id) instead of Message, since the webapp has no originating message.
    Reuses _store_text and build_keyboard so inline buttons work identically.
    """
    h = _store_text(text)
    if len(text) <= settings.LONG_TEXT_THRESHOLD:
        kb = build_keyboard(text, h, send_as_file=False)
        await bot.send_message(chat_id, text, reply_markup=kb)
    else:
        kb = build_keyboard(text, h, send_as_file=True)
        await bot.send_document(
            chat_id,
            BufferedInputFile(text.encode("utf-8"), filename="transcript.txt"),
            caption="Транскрипция готова.",
            reply_markup=kb,
        )
