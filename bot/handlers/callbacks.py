from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.services.summarizer import summarize
from bot.utils.text import get_cached_text, reply_text_or_file

router = Router()


@router.callback_query(F.data.startswith("summary:"))
async def handle_summary(callback: CallbackQuery) -> None:
    text_hash = callback.data.split(":", 1)[1]
    text = get_cached_text(text_hash)

    if text is None:
        await callback.answer("Текст устарел (>10 мин). Отправь аудио заново.", show_alert=True)
        return

    await callback.answer("Генерирую конспект...")
    try:
        summary = await summarize(text)
        await callback.message.reply(f"📝 Краткий конспект:\n\n{summary}")
    except Exception as e:
        await callback.message.reply(f"Ошибка при генерации конспекта: {e}")
