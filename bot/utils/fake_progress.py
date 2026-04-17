import asyncio
import time
from contextlib import suppress
from typing import Awaitable, Callable

FAKE_TICK_SECONDS = 0.5
FAKE_CEILING = 0.95

FractionCallback = Callable[[float], Awaitable[None]]


async def _fake_progress_loop(
    done: asyncio.Event,
    on_progress_fraction: FractionCallback,
    expected_seconds: float,
) -> None:
    start = time.monotonic()
    while not done.is_set():
        elapsed = time.monotonic() - start
        fraction = min(FAKE_CEILING, elapsed / expected_seconds)
        with suppress(Exception):
            await on_progress_fraction(fraction)
        try:
            await asyncio.wait_for(done.wait(), timeout=FAKE_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def run_with_fake_progress(
    coro: Awaitable,
    on_progress_fraction: FractionCallback,
    expected_seconds: float,
):
    """Run an awaitable while driving a fake progress bar.

    Emits fraction updates up to FAKE_CEILING until the awaitable finishes,
    then pushes 1.0. Returns the awaitable's result.
    """
    expected_seconds = max(3.0, expected_seconds)
    done = asyncio.Event()
    task = asyncio.create_task(
        _fake_progress_loop(done, on_progress_fraction, expected_seconds)
    )
    try:
        result = await coro
    finally:
        done.set()
        with suppress(Exception):
            await task
    with suppress(Exception):
        await on_progress_fraction(1.0)
    return result
