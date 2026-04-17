from pathlib import Path

import pytest

import bot.services.downloader as downloader_module
import bot.services.media as media_module
from bot.services.media import prepare_audio_for_transcription


class _FakeProcess:
    def __init__(self, returncode: int):
        self.returncode = returncode

    async def communicate(self):
        return None


@pytest.mark.asyncio
async def test_prepare_audio_for_transcription_runs_ffmpeg(tmp_path, monkeypatch):
    source = tmp_path / "upload.mp4"
    source.write_bytes(b"video")
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-1]).write_bytes(b"audio")
        return _FakeProcess(0)

    monkeypatch.setattr(media_module.asyncio, "create_subprocess_exec", fake_exec)

    result = await prepare_audio_for_transcription(str(source), str(tmp_path))

    assert Path(result).suffix == ".mp3"
    assert Path(result).read_bytes() == b"audio"
    args, kwargs = calls[0]
    assert args[:4] == ("ffmpeg", "-y", "-i", str(source))
    assert "-vn" in args
    assert args[args.index("-ar") + 1] == "16000"
    assert args[args.index("-ac") + 1] == "1"
    assert args[args.index("-acodec") + 1] == "mp3"
    assert kwargs["stdout"] == media_module.asyncio.subprocess.DEVNULL
    assert kwargs["stderr"] == media_module.asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_prepare_audio_for_transcription_removes_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "broken.mov"
    source.write_bytes(b"not media")
    output_paths = []

    async def fake_exec(*args, **_kwargs):
        out_path = Path(args[-1])
        out_path.write_bytes(b"partial")
        output_paths.append(out_path)
        return _FakeProcess(1)

    monkeypatch.setattr(media_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="ffmpeg failed with code 1"):
        await prepare_audio_for_transcription(str(source), str(tmp_path))

    assert output_paths
    assert not output_paths[0].exists()


@pytest.mark.asyncio
async def test_extract_audio_reuses_media_preparation(monkeypatch):
    calls = []

    async def fake_prepare(input_path: str, output_dir: str) -> str:
        calls.append((input_path, output_dir))
        return "/tmp/audio.mp3"

    monkeypatch.setattr(
        downloader_module,
        "prepare_audio_for_transcription",
        fake_prepare,
    )

    result = await downloader_module.extract_audio("/tmp/video.mp4", "/tmp/out")

    assert result == "/tmp/audio.mp3"
    assert calls == [("/tmp/video.mp4", "/tmp/out")]
