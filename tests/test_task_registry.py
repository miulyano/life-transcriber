import asyncio

import pytest

from bot.config import settings
from bot.services import task_registry
from bot.services.task_registry import (
    cancel_keyboard,
    cancel_task,
    get_semaphore,
    spawn_transcription,
)

# _reset_task_registry autouse-fixture в conftest.py чистит
# _semaphore/_running между тестами.


@pytest.mark.asyncio
async def test_spawn_registers_and_cleans_up():
    done = asyncio.Event()

    async def job(tid: str) -> None:
        assert tid  # фабрика получает свой task_id
        done.set()

    task_id = spawn_transcription(777, job)
    assert task_id in task_registry._running
    assert task_registry._running[task_id].user_id == 777

    await task_registry._running[task_id].task
    await asyncio.sleep(0)  # done_callback
    assert done.is_set()
    assert task_registry._running == {}


@pytest.mark.asyncio
async def test_spawn_swallows_exceptions():
    async def boom(_tid: str) -> None:
        raise RuntimeError("boom")

    task_id = spawn_transcription(777, boom)
    task = task_registry._running[task_id].task
    await task  # исключение не всплывает — залогировано внутри _runner
    assert task.exception() is None


@pytest.mark.asyncio
async def test_cancel_task_foreign_user_returns_false():
    release = asyncio.Event()

    async def hang(_tid: str) -> None:
        await release.wait()

    task_id = spawn_transcription(777, hang)
    assert cancel_task(task_id, 999) is False
    assert not task_registry._running[task_id].task.cancelled()
    release.set()
    await task_registry._running[task_id].task


@pytest.mark.asyncio
async def test_cancel_task_unknown_returns_false():
    assert cancel_task("deadbeef", 777) is False


@pytest.mark.asyncio
async def test_cancel_task_cancels_running():
    release = asyncio.Event()

    async def hang(_tid: str) -> None:
        await release.wait()

    task_id = spawn_transcription(777, hang)
    task = task_registry._running[task_id].task
    await asyncio.sleep(0)  # task стартовала и ждёт event — как в реальном флоу
    assert cancel_task(task_id, 777) is True
    await task  # _runner глотает CancelledError
    await asyncio.sleep(0)
    assert task_registry._running == {}


@pytest.mark.asyncio
async def test_semaphore_reads_setting(monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONCURRENT_TRANSCRIPTIONS", 1)
    sem = get_semaphore()
    assert get_semaphore() is sem  # singleton
    async with sem:
        assert sem.locked()


def test_cancel_keyboard():
    assert cancel_keyboard(None) is None
    kb = cancel_keyboard("abc123")
    button = kb.inline_keyboard[0][0]
    assert button.callback_data == "cancel:abc123"
    assert "Отменить" in button.text
