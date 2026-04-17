from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bot.handlers import video


async def test_video_file_keeps_progress_until_result_is_sent(tmp_path, monkeypatch):
    events = []
    audio_path = tmp_path / "audio.mp3"

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
        Path(destination).write_bytes(b"video")

    async def fake_extract_audio(path, _output_dir):
        events.append(("extract", path))
        audio_path.write_bytes(b"audio")
        return str(audio_path)

    async def fake_transcribe(path, **_kwargs):
        events.append(("transcribe", path))
        return "transcript"

    async def fake_reply_text_or_file(_message, text):
        events.append(("reply", text))

    monkeypatch.setattr(video.settings, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(video, "ProgressReporter", Reporter)
    monkeypatch.setattr(video, "extract_audio", fake_extract_audio)
    monkeypatch.setattr(video, "transcribe", fake_transcribe)
    monkeypatch.setattr(video, "reply_text_or_file", fake_reply_text_or_file)

    bot = MagicMock()
    bot.download = AsyncMock(side_effect=fake_download)
    message = MagicMock()

    await video._process(message, bot, "file-id", ".mp4")

    assert events.index(("phase", "Отправляю результат…")) < events.index(("reply", "transcript"))
    assert events.index(("reply", "transcript")) < events.index("finish")
