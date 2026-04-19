import html
from typing import Optional

from aiogram.types import BufferedInputFile
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.services.summarizer import cleanup_transcript, summarize
from bot.utils.filename import build_filename, extract_title
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


def _looks_like_title_line(line: str) -> bool:
    """A short single-line heading without speaker-style ``Label:`` prefix."""
    if not line or len(line) > 100:
        return False
    if "\n" in line:
        return False
    # Speaker labels look like ``Name:`` or ``Спикер 1:`` — those are reply
    # prefixes, not titles, so we should NOT treat the first line as a title
    # to drop in that case.
    if ":" in line:
        before_colon = line.split(":", 1)[0]
        if 1 <= len(before_colon) <= 40 and "\n" not in before_colon:
            return False
    return True


def _ensure_title_in_cleaned(cleaned: str, original_title: Optional[str]) -> str:
    """Re-prepend ``original_title`` if cleanup model dropped or rewrote it."""
    if not original_title:
        return cleaned
    cleaned_first_line = extract_title(cleaned) or ""
    if cleaned_first_line == original_title:
        return cleaned
    body = cleaned.lstrip()
    # Treat the first line as a paraphrased title only if cleaned has a clear
    # title/body split (a blank line after the first line) AND the first line
    # looks like a heading. Otherwise the whole cleaned text is body — keep it
    # and just prepend the original title.
    has_title_block = "\n\n" in body
    if (
        has_title_block
        and cleaned_first_line
        and _looks_like_title_line(cleaned_first_line)
    ):
        body = body.split("\n\n", 1)[1].lstrip()
    return f"{original_title}\n\n{body}"


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
        summary = await summarize(text, on_progress=reporter.set_progress)
        await reporter.set_phase("Отправляю результат…")
        body = markdown_to_telegram_html(summary)
        await callback.message.reply(
            f"📝 Краткий конспект:\n\n{body}",
            parse_mode="HTML",
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
        cleaned = await cleanup_transcript(text, on_progress=reporter.set_progress)
        await reporter.set_phase("Отправляю результат…")

        original_title = extract_title(text)
        cleaned_with_title = _ensure_title_in_cleaned(cleaned, original_title)
        filename = build_filename(
            f"{original_title} clean" if original_title else "clean transcript",
        )
        caption = (
            f"Очищенный текст: {original_title}"
            if original_title
            else "Очищенный текст"
        )
        await callback.message.reply_document(
            BufferedInputFile(cleaned_with_title.encode("utf-8"), filename=filename),
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
