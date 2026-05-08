from typing import Optional
from unittest.mock import AsyncMock

import pytest

import bot.services.transcription_pipeline as pipeline_module
from bot.services.transcriber import FormattedTranscript
from bot.services.usage_store import LimitExceededError


class _Reporter:
    def __init__(self):
        self.events = []

    async def set_phase(self, label: str) -> None:
        self.events.append(("phase", label))

    async def set_progress(self, current: int, total: int) -> None:
        self.events.append(("progress", current, total))

    async def set_progress_fraction(self, fraction: float) -> None:
        self.events.append(("fraction", fraction))


class _Store:
    def __init__(self, *, limit_hours: Optional[int] = None, used_seconds: float = 0.0):
        self._limit_hours = limit_hours
        self._used = used_seconds
        self.commits: list[tuple[int, float]] = []
        self.checks: list[int] = []

    async def assert_within_limit(self, user_id: int) -> None:
        self.checks.append(user_id)
        if self._limit_hours is not None and self._used >= self._limit_hours * 3600:
            raise LimitExceededError(self._limit_hours)

    async def add_seconds(self, user_id: int, seconds: float) -> None:
        self.commits.append((user_id, seconds))
        self._used += seconds


def _result(duration=600.0):
    return FormattedTranscript(
        title="t",
        body="b",
        raw_text="r",
        language=None,
        speaker_count=1,
        audio_duration_sec=duration,
    )


@pytest.mark.asyncio
async def test_pre_check_blocks_before_transcribe(monkeypatch):
    transcribe_mock = AsyncMock(return_value=_result())
    monkeypatch.setattr(pipeline_module, "transcribe", transcribe_mock)
    deliver = AsyncMock()
    store = _Store(limit_hours=1, used_seconds=3600.0)

    with pytest.raises(LimitExceededError) as exc:
        await pipeline_module.run_transcription_pipeline(
            "/tmp/a.mp3",
            reporter=_Reporter(),
            deliver_text=deliver,
            user_id=42,
            usage_store=store,
        )

    assert exc.value.limit_hours == 1
    transcribe_mock.assert_not_called()
    deliver.assert_not_awaited()
    assert store.checks == [42]
    assert store.commits == []


@pytest.mark.asyncio
async def test_post_commit_records_actual_duration(monkeypatch):
    transcribe_mock = AsyncMock(return_value=_result(duration=1234.5))
    monkeypatch.setattr(pipeline_module, "transcribe", transcribe_mock)
    deliver = AsyncMock()
    store = _Store(limit_hours=10)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/a.mp3",
        reporter=_Reporter(),
        deliver_text=deliver,
        user_id=42,
        usage_store=store,
    )

    transcribe_mock.assert_awaited_once()
    assert store.commits == [(42, 1234.5)]
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_limit_user_still_records_usage(monkeypatch):
    transcribe_mock = AsyncMock(return_value=_result(duration=300.0))
    monkeypatch.setattr(pipeline_module, "transcribe", transcribe_mock)
    deliver = AsyncMock()
    store = _Store(limit_hours=None)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/a.mp3",
        reporter=_Reporter(),
        deliver_text=deliver,
        user_id=999,
        usage_store=store,
    )

    assert store.commits == [(999, 300.0)]


@pytest.mark.asyncio
async def test_transcribe_failure_does_not_commit(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("AssemblyAI down")

    monkeypatch.setattr(pipeline_module, "transcribe", boom)
    deliver = AsyncMock()
    store = _Store(limit_hours=10)

    with pytest.raises(RuntimeError, match="AssemblyAI down"):
        await pipeline_module.run_transcription_pipeline(
            "/tmp/a.mp3",
            reporter=_Reporter(),
            deliver_text=deliver,
            user_id=42,
            usage_store=store,
        )

    assert store.commits == []
    deliver.assert_not_awaited()
