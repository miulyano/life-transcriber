from pathlib import Path

import pytest

import bot.services.downloader as downloader_module
import bot.services.media as media_module
from bot.services.media import prepare_audio_for_transcription


@pytest.mark.asyncio
async def test_prepare_audio_for_transcription_runs_ffmpeg(tmp_path, monkeypatch):
    source = tmp_path / "upload.mp4"
    source.write_bytes(b"video")
    calls = []

    async def fake_run_ffmpeg(*args):
        calls.append(args)
        (tmp_path / "prepared.mp3").write_bytes(b"audio")
        (tmp_path / "prepared.mp3").replace(args[-1])

    monkeypatch.setattr(media_module, "run_ffmpeg", fake_run_ffmpeg)

    result = await prepare_audio_for_transcription(str(source), str(tmp_path))

    result_path = Path(result)
    assert result.endswith(".mp3")
    assert result_path.read_bytes() == b"audio"
    args = calls[0]
    assert args[:2] == ("-i", str(source))
    assert "-vn" in args
    assert args[args.index("-ar") + 1] == "16000"
    assert args[args.index("-ac") + 1] == "1"
    assert args[args.index("-acodec") + 1] == "mp3"
    assert args[-1] == result


@pytest.mark.asyncio
async def test_prepare_audio_for_transcription_removes_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "broken.mov"
    source.write_bytes(b"not media")
    output_paths = []

    async def fake_run_ffmpeg(*args):
        out_path = Path(args[-1])
        out_path.write_bytes(b"partial")
        output_paths.append(out_path)
        raise RuntimeError("ffmpeg failed with code 1")

    monkeypatch.setattr(media_module, "run_ffmpeg", fake_run_ffmpeg)

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
