"""Исполнение MCP-джоб в webapp-процессе.

Каждый раннер — независимая корутина под ``spawn_transcription`` (общий
семафор процесса + cancel-token). Прогресс дублируется в два канала через
``JobReporter``: привычный TG-прогрессбар (``ProgressReporter.for_chat``)
и персистентная jobs-таблица, которую агент опрашивает через
``get_job_status``. Терминальные статусы (done/error/cancelled) ставит
раннер, зеркально семантике finish/fail/cancelled у ProgressReporter.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from bot.config import settings
from bot.services.derived_texts import build_cleanup_delivery, build_summary_delivery
from bot.services.downloader import detect_link_source_type, download_audio
from bot.services.error_messages import format_download_error, format_exception_chain
from bot.services.job_store import JobStore, get_job_store
from bot.services.media import prepare_audio_for_transcription
from bot.services.source_meta import SourceMetadata
from bot.services.summarizer import cleanup_transcript, summarize
from bot.services.task_registry import get_semaphore
from bot.services.transcript_store import _atomic_write, get_transcript_store
from bot.services.transcription_pipeline import run_transcription_pipeline
from bot.services.usage_store import LimitExceededError, format_limit_exceeded_message
from bot.utils.filename import split_header_and_body
from bot.utils.progress import ProgressReporter
from webapp.delivery import send_transcript_to_chat

logger = logging.getLogger(__name__)

FILE_ERROR_TEXT = (
    "Не удалось обработать файл. Проверь, что это аудио или видео, и попробуй ещё раз."
)
GENERIC_ERROR_TEXT = "Что-то пошло не так. Попробуй ещё раз."

# Троттлинг записи прогресса скачивания в SQLite: yt-dlp шлёт десятки
# апдейтов в секунду, БД молотить нельзя.
_FRACTION_WRITE_INTERVAL_SEC = 1.0


class JobReporter:
    """Tee-репортёр: форвардит в TG-прогрессбар и пишет в jobs-таблицу."""

    def __init__(
        self,
        inner,
        job_store: JobStore,
        job_id: str,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._store = job_store
        self._job_id = job_id
        self._now = now
        self._last_fraction_write: Optional[float] = None

    async def set_phase(self, label: str) -> None:
        await self._inner.set_phase(label)
        await self._store.advance(self._job_id, phase=label, progress=None)

    async def set_progress(self, current: int, total: int) -> None:
        await self._inner.set_progress(current, total)
        if total > 0:
            await self._store.advance(self._job_id, progress=current / total)

    async def set_progress_fraction(self, fraction: float) -> None:
        await self._inner.set_progress_fraction(fraction)
        now = self._now()
        is_final = fraction >= 1.0
        throttled = (
            self._last_fraction_write is not None
            and now - self._last_fraction_write < _FRACTION_WRITE_INTERVAL_SEC
        )
        if throttled and not is_final:
            return
        self._last_fraction_write = now
        await self._store.advance(self._job_id, progress=fraction)


async def _finalize(
    store: JobStore, job_id: str, *, status: str, **fields
) -> None:
    try:
        await store.finalize(job_id, status=status, **fields)
    except Exception:
        logger.exception("Failed to finalize job %s as %s", job_id, status)


def _make_bot() -> Bot:
    return Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run_url_job(
    job_id: str,
    user_id: int,
    url: str,
    *,
    timecodes: bool = True,
    job_store: Optional[JobStore] = None,
) -> None:
    """Зеркало process_link (bot/handlers/links.py), chat-независимое.

    task_id уже персистнут вызывающим (_spawn_job) до запуска задачи, так
    что раннер сразу входит в try: отмена во время старта гарантированно
    финализируется как cancelled.
    """
    store = job_store or get_job_store()
    bot = _make_bot()
    audio_path: Optional[str] = None
    source_type = detect_link_source_type(url)
    try:
        sem = get_semaphore()
        initial = "В очереди…" if sem.locked() else "Скачиваю аудио по ссылке…"
        if sem.locked():
            await store.advance(job_id, phase="В очереди…", promote=False)
        async with ProgressReporter.for_chat(bot, user_id, initial) as tg_reporter:
            reporter = JobReporter(tg_reporter, store, job_id)
            async with sem:
                await reporter.set_phase("Скачиваю аудио по ссылке…")
                try:
                    audio_path, source_meta = await download_audio(
                        url,
                        settings.TEMP_DIR,
                        on_progress_fraction=reporter.set_progress_fraction,
                        on_postprocess=lambda: reporter.set_phase("Готовлю аудио…"),
                    )
                except RuntimeError as e:
                    logger.warning(
                        "MCP download failed for %s: %s", url, format_exception_chain(e)
                    )
                    message = format_download_error(e)
                    await tg_reporter.fail(message)
                    await _finalize(store, job_id, status="error", error=message)
                    return
                await reporter.set_phase("Транскрибирую…")

                async def deliver_text(text: str, file_text: Optional[str] = None) -> None:
                    await send_transcript_to_chat(
                        bot,
                        user_id,
                        text,
                        file_text if timecodes else None,
                        source_type=source_type,
                    )

                try:
                    record = await run_transcription_pipeline(
                        audio_path,
                        reporter=reporter,
                        deliver_text=deliver_text,
                        user_id=user_id,
                        source_meta=source_meta,
                        source_type=source_type,
                    )
                except LimitExceededError as exc:
                    message = format_limit_exceeded_message(exc.limit_hours)
                    await tg_reporter.fail(message)
                    await _finalize(store, job_id, status="error", error=message)
                    return
                await tg_reporter.finish()
                await _finalize(
                    store,
                    job_id,
                    status="done",
                    transcript_id=record.id if record else None,
                )
    except asyncio.CancelledError:
        await _finalize(store, job_id, status="cancelled")
        raise
    except Exception:
        logger.exception("MCP url job %s failed (user %s)", job_id, user_id)
        await _finalize(store, job_id, status="error", error=GENERIC_ERROR_TEXT)
    finally:
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)
        await bot.session.close()


async def run_file_job(
    job_id: str,
    user_id: int,
    source_path: str,
    *,
    filename_hint: Optional[str] = None,
    timecodes: bool = True,
    job_store: Optional[JobStore] = None,
) -> None:
    """Зеркало _process_upload (webapp/main.py), со статусами в jobs."""
    store = job_store or get_job_store()
    bot = _make_bot()
    audio_path: Optional[str] = None
    try:
        sem = get_semaphore()
        initial = "В очереди…" if sem.locked() else "Готовлю аудио…"
        if sem.locked():
            await store.advance(job_id, phase="В очереди…", promote=False)
        async with ProgressReporter.for_chat(bot, user_id, initial) as tg_reporter:
            reporter = JobReporter(tg_reporter, store, job_id)
            async with sem:
                await reporter.set_phase("Готовлю аудио…")
                try:
                    audio_path = await prepare_audio_for_transcription(
                        source_path, settings.TEMP_DIR
                    )
                except Exception:
                    logger.exception(
                        "MCP file job %s: prepare failed (user %s)", job_id, user_id
                    )
                    await tg_reporter.fail(FILE_ERROR_TEXT)
                    await _finalize(store, job_id, status="error", error=FILE_ERROR_TEXT)
                    return
                await reporter.set_phase("Транскрибирую…")

                async def deliver_text(text: str, file_text: Optional[str] = None) -> None:
                    await send_transcript_to_chat(
                        bot,
                        user_id,
                        text,
                        file_text if timecodes else None,
                        source_type="webapp",
                    )

                try:
                    record = await run_transcription_pipeline(
                        audio_path,
                        reporter=reporter,
                        deliver_text=deliver_text,
                        user_id=user_id,
                        source_meta=SourceMetadata(
                            title=filename_hint or None, title_is_filename=True
                        ),
                        source_type="webapp",
                    )
                except LimitExceededError as exc:
                    message = format_limit_exceeded_message(exc.limit_hours)
                    await tg_reporter.fail(message)
                    await _finalize(store, job_id, status="error", error=message)
                    return
                await tg_reporter.finish()
                await _finalize(
                    store,
                    job_id,
                    status="done",
                    transcript_id=record.id if record else None,
                )
    except asyncio.CancelledError:
        await _finalize(store, job_id, status="cancelled")
        raise
    except Exception:
        logger.exception("MCP file job %s failed (user %s)", job_id, user_id)
        await _finalize(store, job_id, status="error", error=GENERIC_ERROR_TEXT)
    finally:
        for path in (audio_path, source_path):
            if path and os.path.exists(path):
                os.unlink(path)
        await bot.session.close()


async def _write_job_result(job_id: str, content: str) -> str:
    """Результат summary/cleanup-джобы — файлом рядом с транскриптами."""

    def _run() -> str:
        os.makedirs(settings.TRANSCRIPTS_DIR, exist_ok=True)
        path = os.path.join(settings.TRANSCRIPTS_DIR, f"job_{job_id}.txt")
        _atomic_write(path, content)
        return path

    return await asyncio.to_thread(_run)


async def run_summary_job(
    job_id: str,
    user_id: int,
    transcript_id: str,
    *,
    job_store: Optional[JobStore] = None,
) -> None:
    """Краткий конспект готового транскрипта: GPT + доставка как в боте."""
    store = job_store or get_job_store()
    bot = _make_bot()
    try:
        transcripts = get_transcript_store()
        record = await transcripts.get(transcript_id, user_id)
        if record is None:
            await _finalize(
                store, job_id, status="error", error="Транскрипция не найдена"
            )
            return
        text = await transcripts.read_text(record)

        async with ProgressReporter.for_chat(
            bot, user_id, "Делаю краткий конспект…"
        ) as tg_reporter:
            reporter = JobReporter(tg_reporter, store, job_id)
            await reporter.set_phase("Делаю краткий конспект…")
            _header, body = split_header_and_body(text)
            summary = await summarize(body or text, on_progress=reporter.set_progress)
            await reporter.set_phase("Отправляю результат…")
            delivery = build_summary_delivery(text, summary)
            if not delivery.as_file:
                await bot.send_message(user_id, delivery.message_html, parse_mode="HTML")
            else:
                await bot.send_document(
                    user_id,
                    BufferedInputFile(
                        delivery.file_text.encode("utf-8"), filename=delivery.filename
                    ),
                    caption=delivery.caption,
                )
            await tg_reporter.finish()
        result_path = await _write_job_result(job_id, summary)
        await _finalize(store, job_id, status="done", result_path=result_path)
    except asyncio.CancelledError:
        await _finalize(store, job_id, status="cancelled")
        raise
    except Exception:
        logger.exception("MCP summary job %s failed (user %s)", job_id, user_id)
        await _finalize(store, job_id, status="error", error=GENERIC_ERROR_TEXT)
    finally:
        await bot.session.close()


async def run_cleanup_job(
    job_id: str,
    user_id: int,
    transcript_id: str,
    *,
    job_store: Optional[JobStore] = None,
) -> None:
    """Очистка текста готового транскрипта: GPT + доставка как в боте."""
    store = job_store or get_job_store()
    bot = _make_bot()
    try:
        transcripts = get_transcript_store()
        record = await transcripts.get(transcript_id, user_id)
        if record is None:
            await _finalize(
                store, job_id, status="error", error="Транскрипция не найдена"
            )
            return
        text = await transcripts.read_text(record)

        async with ProgressReporter.for_chat(
            bot, user_id, "Очищаю текст…"
        ) as tg_reporter:
            reporter = JobReporter(tg_reporter, store, job_id)
            await reporter.set_phase("Очищаю текст…")
            _header, body = split_header_and_body(text)
            cleaned_body = await cleanup_transcript(
                body or text, on_progress=reporter.set_progress
            )
            await reporter.set_phase("Отправляю результат…")
            delivery = build_cleanup_delivery(
                text, cleaned_body, source_type=record.source_type
            )
            await bot.send_document(
                user_id,
                BufferedInputFile(
                    delivery.file_text.encode("utf-8"), filename=delivery.filename
                ),
                caption=delivery.caption,
            )
            await tg_reporter.finish()
        result_path = await _write_job_result(job_id, delivery.file_text)
        await _finalize(store, job_id, status="done", result_path=result_path)
    except asyncio.CancelledError:
        await _finalize(store, job_id, status="cancelled")
        raise
    except Exception:
        logger.exception("MCP cleanup job %s failed (user %s)", job_id, user_id)
        await _finalize(store, job_id, status="error", error=GENERIC_ERROR_TEXT)
    finally:
        await bot.session.close()
