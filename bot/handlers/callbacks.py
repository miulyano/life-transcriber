import html
from typing import Optional

from aiogram.types import BufferedInputFile
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.services.summarizer import cleanup_transcript, summarize
from bot.utils.filename import build_filename, extract_title
from bot.utils.markdown import markdown_to_telegram_html
from bot.utils.text import _store_text, get_cached_text

router = Router()


async def _extract_text_from_message(callback: CallbackQuery) -> Optional[str]:
    msg = callback.message
    if not isinstance(msg, Message):
        return None
    if msg.text:
        return msg.text
    if msg.document:
        bio = await callback.bot.download(msg.document.file_id)
        return bio.read().decode("utf-8")
    return None


async def _resolve_text(callback: CallbackQuery, text_hash: str) -> Optional[str]:
    text = get_cached_text(text_hash)

    if text is None:
        text = await _extract_text_from_message(callback)
        if text is not None:
            _store_text(text)

    return text


@router.callback_query(F.data.startswith("summary:"))
async def handle_summary(callback: CallbackQuery) -> None:
    text_hash = callback.data.split(":", 1)[1]
    text = await _resolve_text(callback, text_hash)

    if text is None:
        await callback.answer(
            "Не удалось получить текст. Отправь аудио заново.",
            show_alert=True,
        )
        return

    await callback.answer("Генерирую конспект...")
    try:
        summary = await summarize(text)
        body = markdown_to_telegram_html(summary)
        await callback.message.reply(
            f"📝 Краткий конспект:\n\n{body}",
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.message.reply(f"Ошибка при генерации конспекта: {e}")


@router.callback_query(F.data.startswith("cleanup:"))
async def handle_cleanup(callback: CallbackQuery) -> None:
    text_hash = callback.data.split(":", 1)[1]
    text = await _resolve_text(callback, text_hash)

    if text is None:
        await callback.answer(
            "Не удалось получить текст. Отправь аудио заново.",
            show_alert=True,
        )
        return

    await callback.answer("Очищаю текст...")
    try:
        cleaned = await cleanup_transcript(text)
        title = extract_title(cleaned)
        filename = build_filename(
            f"{title} clean" if title else "clean transcript",
        )
        await callback.message.reply_document(
            BufferedInputFile(cleaned.encode("utf-8"), filename=filename),
            caption=title or "Очищенная транскрибация готова.",
        )
    except Exception as e:
        await callback.message.reply(f"Ошибка при очистке текста: {e}")


@router.callback_query(F.data.startswith("copy:"))
async def handle_copy(callback: CallbackQuery) -> None:
    text_hash = callback.data.split(":", 1)[1]
    text = await _resolve_text(callback, text_hash)

    if text is None:
        await callback.answer("Текст недоступен. Отправь аудио заново.", show_alert=True)
        return

    await callback.answer()
    await callback.message.reply(f"<code>{html.escape(text)}</code>", parse_mode="HTML")
