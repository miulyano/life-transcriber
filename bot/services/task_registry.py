"""In-memory реестр запущенных транскрибаций + глобальный лимит одновременности.

Каждая транскрибация спавнится отдельной asyncio-задачей и регистрируется
здесь, чтобы её можно было отменить кнопкой «✖️ Отменить» (callback
``cancel:<task_id>``). Семафор ограничивает число одновременных задач
(``MAX_CONCURRENT_TRANSCRIPTIONS``); ожидающие висят со статусом «В очереди…».
Реестр процессный (как pending_jobs): рестарт бота теряет запущенные задачи.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings

logger = logging.getLogger(__name__)

_semaphore: Optional[asyncio.Semaphore] = None
_running: dict[str, "RunningTask"] = {}


@dataclass
class RunningTask:
    user_id: int
    task: asyncio.Task


def get_semaphore() -> asyncio.Semaphore:
    """Global cap on simultaneous transcriptions; lazy so tests can reset it."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TRANSCRIPTIONS)
    return _semaphore


def new_task_id() -> str:
    """Allocate a cancel token without spawning yet — lets a caller persist
    the id before starting the task (closes the immediate-cancel window)."""
    return uuid.uuid4().hex[:16]


def spawn_transcription(
    user_id: int,
    coro_factory: Callable[[str], Awaitable[None]],
    *,
    task_id: Optional[str] = None,
) -> str:
    """Run a transcription as an independent task; return its cancel token.

    The factory receives the task id so the processor can put a cancel button
    on its progress message before the task itself is created. Pass an
    explicit ``task_id`` (from :func:`new_task_id`) to register the same
    token that was already persisted for the job.
    """
    if task_id is None:
        task_id = new_task_id()

    async def _runner() -> None:
        try:
            await coro_factory(task_id)
        except asyncio.CancelledError:
            pass  # user cancel — the progress reporter already showed the status
        except Exception:
            logger.exception("Transcription task %s failed", task_id)

    task = asyncio.create_task(_runner())
    _running[task_id] = RunningTask(user_id=user_id, task=task)
    task.add_done_callback(lambda _t: _running.pop(task_id, None))
    return task_id


def cancel_task(task_id: str, user_id: int) -> bool:
    """Cancel a running task; False if missing, finished or foreign user."""
    entry = _running.get(task_id)
    if entry is None or entry.user_id != user_id or entry.task.done():
        return False
    entry.task.cancel()
    return True


def cancel_keyboard(task_id: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if task_id is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✖️ Отменить", callback_data=f"cancel:{task_id}"
                )
            ]
        ]
    )
