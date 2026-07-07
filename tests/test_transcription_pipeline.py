from unittest.mock import AsyncMock

import pytest

import bot.services.transcription_pipeline as pipeline_module
from bot.services.source_meta import SourceMetadata
from bot.services.transcriber import FormattedTranscript


class _Reporter:
    def __init__(self):
        self.events = []

    async def set_phase(self, label: str) -> None:
        self.events.append(("phase", label))

    async def set_progress(self, current: int, total: int) -> None:
        self.events.append(("progress", current, total))

    async def set_progress_fraction(self, fraction: float) -> None:
        self.events.append(("fraction", fraction))


class _NoLimitStore:
    def __init__(self):
        self.commits: list[tuple[int, float]] = []
        self.reserves: list[tuple[int, float]] = []
        self.releases: list[tuple[int, float]] = []

    async def reserve(self, user_id: int, estimated_seconds: float) -> None:
        self.reserves.append((user_id, estimated_seconds))

    async def release(self, user_id: int, estimated_seconds: float) -> None:
        self.releases.append((user_id, estimated_seconds))

    async def commit(
        self, user_id: int, estimated_seconds: float, actual_seconds: float
    ) -> None:
        self.commits.append((user_id, actual_seconds))


@pytest.fixture(autouse=True)
def _fake_probe_duration(monkeypatch):
    """ffprobe не дергаем в тестах — фиксированная оценка длительности."""
    monkeypatch.setattr(
        pipeline_module, "probe_duration", AsyncMock(return_value=120.0)
    )


class _SpyTranscriptStore:
    def __init__(self, fail: bool = False):
        self.saved: list[dict] = []
        self._fail = fail

    async def save(
        self, user_id, *, title, source_type, duration_sec, body, segments, channel=None
    ):
        if self._fail:
            raise RuntimeError("disk full")
        self.saved.append(
            {
                "user_id": user_id,
                "title": title,
                "source_type": source_type,
                "duration_sec": duration_sec,
                "body": body,
                "segments": segments,
                "channel": channel,
            }
        )


def _result(body="formatted body", title="T", raw="raw", duration=42.0):
    return FormattedTranscript(
        title=title,
        body=body,
        raw_text=raw,
        language="ru",
        speaker_count=2,
        audio_duration_sec=duration,
    )


@pytest.mark.asyncio
async def test_pipeline_calls_transcribe_then_delivers_body(monkeypatch):
    reporter = _Reporter()
    events = []

    async def fake_transcribe(path, *, source_meta=None, on_phase=None, on_progress=None, on_progress_fraction=None):
        events.append(("transcribe", path, source_meta))
        return _result(body="T\n\nСпикер 1: hi")

    async def fake_deliver(text, file_text=None):
        events.append(("deliver", text, file_text))

    async def fake_phase_change(label):
        events.append(("phase-change", label))

    monkeypatch.setattr(pipeline_module, "transcribe", fake_transcribe)

    store = _NoLimitStore()
    meta = SourceMetadata(title="title hint", uploader="Channel")
    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=reporter,
        deliver_text=fake_deliver,
        user_id=111,
        source_meta=meta,
        on_phase_change=fake_phase_change,
        usage_store=store,
    )

    assert events == [
        ("transcribe", "/tmp/audio.mp3", meta),
        ("phase-change", "Отправляю результат…"),
        ("deliver", "T\n\nСпикер 1: hi", None),
    ]
    assert reporter.events == [
        ("phase", "Отправляю результат…"),
    ]
    assert store.commits == [(111, 42.0)]


@pytest.mark.asyncio
async def test_pipeline_passes_none_source_meta(monkeypatch):
    reporter = _Reporter()
    transcribe_mock = AsyncMock(return_value=_result())

    monkeypatch.setattr(pipeline_module, "transcribe", transcribe_mock)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=reporter,
        deliver_text=AsyncMock(),
        user_id=111,
        usage_store=_NoLimitStore(),
    )

    assert transcribe_mock.await_args.kwargs["source_meta"] is None
    assert "on_phase" in transcribe_mock.await_args.kwargs


@pytest.mark.asyncio
async def test_pipeline_persists_before_delivery(monkeypatch):
    order = []
    monkeypatch.setattr(
        pipeline_module, "transcribe", AsyncMock(return_value=_result(body="B", title="T"))
    )

    transcript_store = _SpyTranscriptStore()
    orig_save = transcript_store.save

    async def tracking_save(*args, **kwargs):
        order.append("save")
        return await orig_save(*args, **kwargs)

    transcript_store.save = tracking_save

    async def deliver(text, file_text=None):
        order.append("deliver")

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=_Reporter(),
        deliver_text=deliver,
        user_id=111,
        usage_store=_NoLimitStore(),
        source_type="voice",
        transcript_store=transcript_store,
    )

    assert order == ["save", "deliver"]
    assert transcript_store.saved == [
        {
            "user_id": 111,
            "title": "T",
            "source_type": "voice",
            "duration_sec": 42.0,
            "body": "B",
            "segments": [],
            "channel": None,
        }
    ]


@pytest.mark.asyncio
async def test_pipeline_store_failure_does_not_block_delivery(monkeypatch):
    monkeypatch.setattr(pipeline_module, "transcribe", AsyncMock(return_value=_result()))
    deliver = AsyncMock()

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=_Reporter(),
        deliver_text=deliver,
        user_id=111,
        usage_store=_NoLimitStore(),
        transcript_store=_SpyTranscriptStore(fail=True),
    )

    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_limit_exceeded_no_save(monkeypatch):
    from bot.services.usage_store import LimitExceededError

    class _BlockedStore(_NoLimitStore):
        async def reserve(self, user_id: int, estimated_seconds: float) -> None:
            raise LimitExceededError(5)

    monkeypatch.setattr(pipeline_module, "transcribe", AsyncMock(return_value=_result()))
    transcript_store = _SpyTranscriptStore()

    with pytest.raises(LimitExceededError):
        await pipeline_module.run_transcription_pipeline(
            "/tmp/audio.mp3",
            reporter=_Reporter(),
            deliver_text=AsyncMock(),
            user_id=111,
            usage_store=_BlockedStore(),
            transcript_store=transcript_store,
        )

    assert transcript_store.saved == []
