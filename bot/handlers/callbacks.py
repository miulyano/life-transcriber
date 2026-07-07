import html
from contextlib import suppress
from typing import Optional

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.constants import TELEGRAM_TEXT_LIMIT
from bot.services.pending_jobs import pop_job
from bot.services.summarizer import cleanup_transcript, summarize
from bot.services.task_registry import cancel_task, spawn_transcription
from bot.utils.filename import build_filename, extract_title, split_header_and_body
from bot.utils.markdown import markdown_to_telegram_html
from bot.utils.progress import ProgressReporter
from bot.utils.source_labels import format_source_line
from bot.utils.text import (
    _store_text,
    get_cached_source_type,
    get_cached_text,
    strip_timecodes,
)

router = Router()


@router.callback_query(F.data.startswith("tc:"))
async def handle_timecode_choice(callback: CallbackQuery) -> None:
    """Start a pending transcription with the chosen delivery format."""
    # Local imports to avoid a handlers-package import cycle.
    from bot.handlers._tg_media import process_tg_media
    from bot.handlers.links import process_link

    try:
        _, flag, pending_id = callback.data.split(":", 2)
    except ValueError:
        await callback.answer("Некорректный запрос.", show_alert=True)
        return

    user_id = callback.from_user.id if callback.from_user else 0
    job = pop_job(pending_id, user_id)

    if flag == "x":
        # Cancel: drop the job (already popped) and the question message.
        # An expired/missing job still deletes the message — nothing to run anyway.
        await callback.answer("Отменено.")
        if isinstance(callback.message, Message):
            with suppress(TelegramBadRequest, TelegramForbiddenError):
                await callback.message.delete()
        return

    timecodes = flag == "1"
    if job is None:
        await callback.answer("Запрос устарел, отправь заново.", show_alert=True)
        return

    await callback.answer()
    # Drop the question message — progress replies to the original one.
    if isinstance(callback.message, Message):
        with suppress(TelegramBadRequest, TelegramForbiddenError):
            await callback.message.delete()

    # Spawn instead of await: the handler returns immediately, so several
    # transcriptions can run side by side and each one is cancellable.
    if job.kind == "link":
        spawn_transcription(
            user_id,
            lambda tid: process_link(
                job.message,
                job.url,
                timecodes=timecodes,
                source_type=job.source_type,
                cancel_token=tid,
            ),
        )
    else:
        spawn_transcription(
            user_id,
            lambda tid: process_tg_media(
                job.message,
                callback.bot,
                job.file_id,
                job.suffix,
                label=job.label,
                extract_audio_first=job.extract_audio_first,
                filename_hint=job.filename_hint,
                source_type=job.source_type,
                timecodes=timecodes,
                cancel_token=tid,
            ),
        )


@router.callback_query(F.data.startswith("cancel:"))
async def handle_cancel_running(callback: CallbackQuery) -> None:
    """Cancel a running transcription from its progress-message button."""
    task_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id if callback.from_user else 0
    if cancel_task(task_id, user_id):
        await callback.answer("Отменяю…")
    else:
        await callback.answer("Задача уже завершена.")


async def _extract_text_from_message(callback: CallbackQuery) -> Optional[str]:
    msg = callback.message
    if not isinstance(msg, Message):
        return None
    if msg.text:
        return msg.text
    if msg.document:
        bio = await callback.bot.download(msg.document.file_id)
        # The .txt may be the timecoded variant — GPT input must stay clean.
        return strip_timecodes(bio.read().decode("utf-8"))
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
        # Cleanup always works on the plain (non-timecoded) cached variant.
        source_line = format_source_line(
            get_cached_source_type(text_hash), timecoded=False
        )
        caption = f"{caption}\n{source_line}"
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
