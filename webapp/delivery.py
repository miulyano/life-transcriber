from aiogram import Bot
from aiogram.types import BufferedInputFile

from bot.utils.text import prepare_transcript


async def send_transcript_to_chat(bot: Bot, chat_id: int, text: str) -> None:
    """Send transcription result to a Telegram chat.

    Uses prepare_transcript() from bot/utils/text.py so inline buttons
    work identically to the message-based reply_text_or_file().
    """
    d = prepare_transcript(text)
    if not d.send_as_file:
        await bot.send_message(chat_id, text, reply_markup=d.keyboard)
    else:
        await bot.send_document(
            chat_id,
            BufferedInputFile(text.encode("utf-8"), filename=d.filename),
            caption=d.title or "Транскрибация готова.",
            reply_markup=d.keyboard,
        )
