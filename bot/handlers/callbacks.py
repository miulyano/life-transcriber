import html
from typing import Optional

from aiogram.types import BufferedInputFile
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.constants import TELEGRAM_TEXT_LIMIT
from bot.services.summarizer import cleanup_transcript, summarize
from bot.utils.filename import build_filename, extract_title, split_header_and_body
from bot.utils.markdown import markdown_to_telegram_html
from bot.utils.progress import ProgressReporter
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

    await callback.answer()
    async with ProgressReporter(callback.message, "Делаю краткий конспект…") as reporter:
        _header, body = split_header_and_body(text)
        summary_input = body or text
        summary = await summarize(summary_input, on_progress=reporter.set_progress)
        await reporter.set_phase("Отправляю результат…")
        body = markdown_to_telegram_html(summary)
        message = f"📝 Краткий конспект:\n\n{body}"
        if len(message) <= TELEGRAM_TEXT_LIMIT:
            await callback.message.reply(message, parse_mode="HTML")
        else:
            # Telegram text-message limit is ~4096; long summaries go as a
            # plain-text file instead so the user still gets the full thing.
            original_title = extract_title(text)
            filename = build_filename(
                f"{original_title} summary" if original_title else "summary",
            )
            caption = (
                f"📝 Краткий конспект: {original_title}"
                if original_title
                else "📝 Краткий конспект (длинный — прислал файлом)"
            )
            await callback.message.reply_document(
                BufferedInputFile(summary.encode("utf-8"), filename=filename),
                caption=caption,
            )
        await reporter.finish()


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

    await callback.answer()
    async with ProgressReporter(callback.message, "Очищаю текст…") as reporter:
        header, body = split_header_and_body(text)
        cleanup_input = body or text
        cleaned_body = await cleanup_transcript(
            cleanup_input, on_progress=reporter.set_progress
        )
        await reporter.set_phase("Отправляю результат…")

        if header and body:
            final_text = f"{header}\n\n{cleaned_body.lstrip()}"
        else:
            final_text = cleaned_body

        original_title = extract_title(text)
        filename = build_filename(
            f"{original_title} clean" if original_title else "clean transcript",
        )
        caption = (
            f"Очищенный текст: {original_title}"
            if original_title
            else "Очищенный текст"
        )
        await callback.message.reply_document(
            BufferedInputFile(final_text.encode("utf-8"), filename=filename),
            caption=caption,
        )
        await reporter.finish()


@router.callback_query(F.data.startswith("copy:"))
async def handle_copy(callback: CallbackQuery) -> None:
    text_hash = callback.data.split(":", 1)[1]
    text = await _resolve_text(callback, text_hash)

    if text is None:
        await callback.answer("Текст недоступен. Отправь аудио заново.", show_alert=True)
        return

    await callback.answer()
    await callback.message.reply(f"<code>{html.escape(text)}</code>", parse_mode="HTML")
