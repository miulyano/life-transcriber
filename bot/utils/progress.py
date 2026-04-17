import asyncio
from contextlib import suppress
from typing import Awaitable, Callable, Optional

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import Message

BAR_WIDTH = 10
TICK_SECONDS = 2.0
FILLED = "🟩"
EMPTY = "⬜"


def render_indeterminate(position: int, width: int = BAR_WIDTH) -> str:
    cells = [EMPTY] * width
    if 0 <= position < width:
        cells[position] = FILLED
    return "".join(cells)


def render_determinate(current: int, total: int, width: int = BAR_WIDTH) -> str:
    if total <= 0:
        return render_indeterminate(0, width)
    ratio = max(0, min(current, total)) / total
    filled = round(ratio * width)
    return FILLED * filled + EMPTY * (width - filled)


def position_at_tick(tick: int, width: int = BAR_WIDTH) -> int:
    """Ping-pong: 0,1,...,width-1,width-2,...,1,0,1,... Cycle = 2*(width-1)."""
    if width <= 1:
        return 0
    period = 2 * (width - 1)
    p = tick % period
    return p if p < width else period - p


SleepFn = Callable[[float], Awaitable[None]]


class ProgressReporter:
    """Owns one status message and keeps it updated with an animated progress bar.

    Usage:
        async with ProgressReporter(message, "Скачиваю…") as r:
            ...work...
            await r.set_phase("Транскрибирую…")
            ...more work...
            await r.finish()      # deletes the status message
            # on any exception without finish/fail, __aexit__ calls fail(str(exc))
    """

    def __init__(
        self,
        message: Message,
        initial_label: str,
        *,
        tick_seconds: float = TICK_SECONDS,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._message = message
        self._bot = message.bot
        self._label = initial_label
        self._tick_seconds = tick_seconds
        self._sleep = sleep

        self._chat_id: Optional[int] = None
        self._message_id: Optional[int] = None
        self._status_message: Optional[Message] = None

        self._tick: int = 0
        self._progress: Optional[tuple[int, int]] = None
        self._fraction: Optional[float] = None
        self._last_rendered: Optional[str] = None
        self._stopped: bool = False
        self._resolved: bool = False  # finish() or fail() was called

        self._edit_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    async def __aenter__(self) -> "ProgressReporter":
        self._status_message = await self._message.reply(self._compose())
        self._chat_id = self._status_message.chat.id
        self._message_id = self._status_message.message_id
        self._last_rendered = self._status_message.text
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._resolved:
            if exc is not None:
                await self.fail(f"Ошибка: {exc}")
            else:
                await self._stop_task()
        else:
            await self._stop_task()
        return None

    async def set_phase(self, label: str) -> None:
        self._label = label
        self._progress = None
        self._fraction = None
        await self._do_edit(self._compose())

    async def set_progress(self, current: int, total: int) -> None:
        self._progress = (current, total)
        self._fraction = None
        await self._do_edit(self._compose())

    async def set_progress_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self._progress = None
        await self._do_edit(self._compose())

    async def finish(self) -> None:
        self._resolved = True
        self._stopped = True
        await self._stop_task()
        try:
            await self._bot.delete_message(
                chat_id=self._chat_id, message_id=self._message_id
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            with suppress(TelegramBadRequest, TelegramForbiddenError):
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                    text="✅ Готово",
                )

    async def fail(self, text: str) -> None:
        self._resolved = True
        self._stopped = True
        await self._stop_task()
        with suppress(TelegramBadRequest, TelegramForbiddenError):
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=f"❌ {text}",
            )

    def _compose(self) -> str:
        if self._fraction is not None:
            cells = round(self._fraction * BAR_WIDTH)
            if self._fraction < 1.0:
                cells = min(cells, BAR_WIDTH - 1)
            bar = render_determinate(cells, BAR_WIDTH)
            return f"{self._label}\n{bar}"
        if self._progress is not None:
            current, total = self._progress
            bar = render_determinate(current, total)
            return f"{self._label}\n{bar} {current}/{total}"
        pos = position_at_tick(self._tick)
        bar = render_indeterminate(pos)
        return f"{self._label}\n{bar}"

    async def _do_edit(self, text: str) -> None:
        if self._stopped or self._message_id is None:
            return
        if text == self._last_rendered:
            return
        async with self._edit_lock:
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                    text=text,
                )
                self._last_rendered = text
            except TelegramBadRequest:
                self._last_rendered = text
            except TelegramRetryAfter as e:
                await self._sleep(e.retry_after)
            except TelegramForbiddenError:
                self._stopped = True

    async def _run(self) -> None:
        try:
            while not self._stopped:
                await self._sleep(self._tick_seconds)
                if self._stopped:
                    break
                self._tick += 1
                await self._do_edit(self._compose())
        except asyncio.CancelledError:
            raise

    async def _stop_task(self) -> None:
        self._stopped = True
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
