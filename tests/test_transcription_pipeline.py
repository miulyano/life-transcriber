from unittest.mock import AsyncMock

import pytest

import bot.services.transcription_pipeline as pipeline_module
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


def _result(body="formatted body", title="T", raw="raw"):
    return FormattedTranscript(
        title=title, body=body, raw_text=raw, language="ru", speaker_count=2
    )


@pytest.mark.asyncio
async def test_pipeline_calls_transcribe_then_delivers_body(monkeypatch):
    reporter = _Reporter()
    events = []

    async def fake_transcribe(path, *, filename_hint=None, on_phase=None, on_progress=None, on_progress_fraction=None):
        events.append(("transcribe", path, filename_hint))
        return _result(body="T\n\nСпикер 1: hi")

    async def fake_deliver(text):
        events.append(("deliver", text))

    async def fake_phase_change(label):
        events.append(("phase-change", label))

    monkeypatch.setattr(pipeline_module, "transcribe", fake_transcribe)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=reporter,
        deliver_text=fake_deliver,
        filename_hint="title hint",
        on_phase_change=fake_phase_change,
    )

    assert events == [
        ("transcribe", "/tmp/audio.mp3", "title hint"),
        ("phase-change", "Отправляю результат…"),
        ("deliver", "T\n\nСпикер 1: hi"),
    ]
    assert reporter.events == [
        ("phase", "Отправляю результат…"),
    ]


@pytest.mark.asyncio
async def test_pipeline_passes_none_filename_hint(monkeypatch):
    reporter = _Reporter()
    transcribe_mock = AsyncMock(return_value=_result())

    monkeypatch.setattr(pipeline_module, "transcribe", transcribe_mock)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=reporter,
        deliver_text=AsyncMock(),
    )

    assert transcribe_mock.await_args.kwargs["filename_hint"] is None
    assert "on_phase" in transcribe_mock.await_args.kwargs
