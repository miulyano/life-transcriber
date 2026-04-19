from unittest.mock import AsyncMock

import pytest

import bot.services.transcription_pipeline as pipeline_module


class _Reporter:
    def __init__(self):
        self.events = []

    async def set_phase(self, label: str) -> None:
        self.events.append(("phase", label))

    async def set_progress(self, current: int, total: int) -> None:
        self.events.append(("progress", current, total))

    async def set_progress_fraction(self, fraction: float) -> None:
        self.events.append(("fraction", fraction))


@pytest.mark.asyncio
async def test_run_transcription_pipeline_orders_transcribe_format_and_deliver(monkeypatch):
    reporter = _Reporter()
    events = []

    async def fake_transcribe(path: str, on_progress=None, on_progress_fraction=None) -> str:
        events.append(("transcribe", path))
        await on_progress(1, 3)
        await on_progress_fraction(0.5)
        return "raw text"

    async def fake_format(
        text: str,
        filename_hint=None,
        on_progress=None,
        on_progress_fraction=None,
    ) -> str:
        events.append(("format", text, filename_hint))
        await on_progress(2, 3)
        await on_progress_fraction(1.0)
        return "formatted text"

    async def fake_deliver(text: str) -> None:
        events.append(("deliver", text))

    async def fake_phase_change(label: str) -> None:
        events.append(("phase-change", label))

    monkeypatch.setattr(pipeline_module, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline_module, "format_transcript", fake_format)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=reporter,
        deliver_text=fake_deliver,
        filename_hint="title hint",
        on_phase_change=fake_phase_change,
    )

    assert events == [
        ("transcribe", "/tmp/audio.mp3"),
        ("phase-change", "Форматирую…"),
        ("format", "raw text", "title hint"),
        ("phase-change", "Отправляю результат…"),
        ("deliver", "formatted text"),
    ]
    assert reporter.events == [
        ("progress", 1, 3),
        ("fraction", 0.5),
        ("phase", "Форматирую…"),
        ("progress", 2, 3),
        ("fraction", 1.0),
        ("phase", "Отправляю результат…"),
    ]


@pytest.mark.asyncio
async def test_run_transcription_pipeline_passes_none_filename_hint(monkeypatch):
    reporter = _Reporter()
    format_mock = AsyncMock(return_value="formatted")

    monkeypatch.setattr(pipeline_module, "transcribe", AsyncMock(return_value="raw"))
    monkeypatch.setattr(pipeline_module, "format_transcript", format_mock)

    await pipeline_module.run_transcription_pipeline(
        "/tmp/audio.mp3",
        reporter=reporter,
        deliver_text=AsyncMock(),
    )

    assert format_mock.await_args.kwargs["filename_hint"] is None
