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
    """Fake store with the reserve/release/commit contract of UsageStore."""

    def __init__(self, *, limit_hours: Optional[int] = None, used_seconds: float = 0.0):
        self._limit_hours = limit_hours
        self._used = used_seconds
        self._inflight = 0.0
        self.commits: list[tuple[int, float]] = []
        self.reserves: list[tuple[int, float]] = []
        self.releases: list[tuple[int, float]] = []

    async def reserve(self, user_id: int, estimated_seconds: float) -> None:
        self.reserves.append((user_id, estimated_seconds))
        if self._limit_hours is not None and (
            self._used + self._inflight >= self._limit_hours * 3600
        ):
            raise LimitExceededError(self._limit_hours)
        self._inflight += estimated_seconds

    async def release(self, user_id: int, estimated_seconds: float) -> None:
        self.releases.append((user_id, estimated_seconds))
        self._inflight -= estimated_seconds

    async def commit(
        self, user_id: int, estimated_seconds: float, actual_seconds: float
    ) -> None:
        self._inflight -= estimated_seconds
        self._used += actual_seconds
        self.commits.append((user_id, actual_seconds))


ESTIMATED = 120.0


@pytest.fixture(autouse=True)
def _fake_probe_duration(monkeypatch):
    """ffprobe не дергаем в тестах — фиксированная оценка длительности."""
    monkeypatch.setattr(
        pipeline_module, "probe_duration", AsyncMock(return_value=ESTIMATED)
    )


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
    assert store.reserves == [(42, ESTIMATED)]
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
    assert store._inflight == 0.0  # резерв снят вместе с коммитом
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
async def test_transcribe_failure_releases_and_does_not_commit(monkeypatch):
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
    assert store.releases == [(42, ESTIMATED)]
    assert store._inflight == 0.0
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_transcribe_releases_reservation(monkeypatch):
    import asyncio

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(pipeline_module, "transcribe", cancelled)
    store = _Store(limit_hours=10)

    with pytest.raises(asyncio.CancelledError):
        await pipeline_module.run_transcription_pipeline(
            "/tmp/a.mp3",
            reporter=_Reporter(),
            deliver_text=AsyncMock(),
            user_id=42,
            usage_store=store,
        )

    assert store.releases == [(42, ESTIMATED)]
    assert store.commits == []


@pytest.mark.asyncio
async def test_inflight_reservation_blocks_second_concurrent_job(monkeypatch):
    """TOCTOU закрыт: пока первая задача держит резерв у края лимита,
    вторая получает отказ ещё до транскрибации."""
    monkeypatch.setattr(
        pipeline_module, "transcribe", AsyncMock(return_value=_result())
    )
    # Лимит 1 час, израсходовано 3500 c — первая задача резервирует 120 c
    # (перерасход остатка разрешён), вторая уже не проходит.
    store = _Store(limit_hours=1, used_seconds=3500.0)

    await store.reserve(42, ESTIMATED)  # «первая задача» держит резерв
    with pytest.raises(LimitExceededError):
        await pipeline_module.run_transcription_pipeline(
            "/tmp/b.mp3",
            reporter=_Reporter(),
            deliver_text=AsyncMock(),
            user_id=42,
            usage_store=store,
        )
