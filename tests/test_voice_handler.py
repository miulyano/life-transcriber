from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bot.handlers import voice


async def test_voice_file_keeps_progress_until_result_is_sent(tmp_path, monkeypatch):
    events = []

    class Reporter:
        def __init__(self, _message, label):
            events.append(("init", label))

        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            events.append("exit")

        async def set_phase(self, label):
            events.append(("phase", label))

        async def set_progress(self, _current, _total):
            events.append("progress")

        async def set_progress_fraction(self, _fraction):
            events.append("fraction")

        async def finish(self):
            events.append("finish")

    async def fake_download(_file_id, destination):
        events.append(("download", destination))
        Path(destination).write_bytes(b"voice")

    async def fake_pipeline(audio_path, *, reporter, deliver_text, filename_hint=None, on_phase_change=None):
        events.append(("pipeline", audio_path, filename_hint))
        await deliver_text("transcript")

    async def fake_reply_text_or_file(_message, text):
        events.append(("reply", text))

    monkeypatch.setattr(voice.settings, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "ProgressReporter", Reporter)
    monkeypatch.setattr(voice, "run_transcription_pipeline", fake_pipeline)
    monkeypatch.setattr(voice, "reply_text_or_file", fake_reply_text_or_file)

    bot = MagicMock()
    bot.download = AsyncMock(side_effect=fake_download)
    message = MagicMock()

    await voice._handle(message, bot, "file-id", ".ogg", "Транскрибирую…")

    pipeline_event = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "pipeline"
    )
    assert pipeline_event[1].endswith(".ogg")
    assert events.index(("reply", "transcript")) < events.index("finish")
