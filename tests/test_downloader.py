"""Tests for bot.services.downloader — yt-dlp metadata and progress parsing."""
import pytest

import bot.services.downloader as downloader_module
from bot.services.downloader import _parse_ytdlp_meta, download_audio, parse_progress_line


def test_parse_ytdlp_meta_extracts_title_and_uploader():
    stdout = b'{"title":"Lex Fridman Podcast","uploader":"Lex Fridman","channel":"LexClips"}\n'
    meta = _parse_ytdlp_meta(stdout)
    assert meta.title == "Lex Fridman Podcast"
    # uploader wins over channel when both present
    assert meta.uploader == "Lex Fridman"


def test_parse_ytdlp_meta_falls_back_to_channel_when_no_uploader():
    stdout = b'{"title":"Some Stream","uploader":null,"channel":"Stream Channel"}\n'
    meta = _parse_ytdlp_meta(stdout)
    assert meta.title == "Some Stream"
    assert meta.uploader == "Stream Channel"


def test_parse_ytdlp_meta_handles_empty_stdout():
    meta = _parse_ytdlp_meta(b"")
    assert meta.title is None
    assert meta.uploader is None


def test_parse_ytdlp_meta_handles_na_sentinels():
    stdout = b'{"title":"NA","uploader":"NA","channel":"NA"}\n'
    meta = _parse_ytdlp_meta(stdout)
    assert meta.title is None
    assert meta.uploader is None


def test_parse_ytdlp_meta_handles_non_json_falls_back_to_title():
    # Older format or unexpected output — treat the line as a plain title.
    stdout = b"Just a plain title\n"
    meta = _parse_ytdlp_meta(stdout)
    assert meta.title == "Just a plain title"
    assert meta.uploader is None


def test_parse_ytdlp_meta_takes_last_non_empty_line():
    stdout = b"\n\n{\"title\":\"Final\",\"uploader\":\"Au\",\"channel\":null}\n"
    meta = _parse_ytdlp_meta(stdout)
    assert meta.title == "Final"
    assert meta.uploader == "Au"


# ---------- parse_progress_line ----------


def test_parse_progress_line_uses_fragment_progress():
    # Fragmented downloads report whole-file progress via fragment index/count.
    # Bytes are per-fragment noise here and must be ignored; the old
    # estimate-based path would freeze the bar near completion.
    assert parse_progress_line("LTPROG 500 NA 3 12") == pytest.approx(0.25)


def test_parse_progress_line_fragment_complete():
    assert parse_progress_line("LTPROG 100 NA 12 12") == 1.0


def test_parse_progress_line_with_total_bytes():
    # Non-fragmented HTTP file: real total_bytes, no fragments.
    assert parse_progress_line("LTPROG 100 200 NA NA") == pytest.approx(0.5)


def test_parse_progress_line_no_total_returns_none():
    # No fragments and no real total_bytes -> no number (indeterminate bar).
    # The undersized total_bytes_estimate is never used as a denominator.
    assert parse_progress_line("LTPROG 100 NA NA NA") is None
    assert parse_progress_line("LTPROG 100 0 NA NA") is None


def test_parse_progress_line_clamps_overshoot():
    assert parse_progress_line("LTPROG 500 400 NA NA") == 1.0


def test_parse_progress_line_ignores_non_progress_lines():
    assert parse_progress_line('{"title":"Video","uploader":"Au"}') is None
    assert parse_progress_line("") is None
    assert parse_progress_line("WARNING: something") is None
    assert parse_progress_line("LTPROG garbage 100 NA NA") is None
    assert parse_progress_line("LTPROG 100 200 NA") is None  # wrong field count


# ---------- download_audio progress plumbing ----------


@pytest.mark.asyncio
async def test_download_audio_passes_progress_callback_to_ytdlp(monkeypatch):
    received = {}

    async def _fake_ytdlp(url, output_dir, proxy=None, on_progress_fraction=None):
        received["on_progress_fraction"] = on_progress_fraction
        return "/tmp/x.mp3", _parse_ytdlp_meta(b"")

    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _fake_ytdlp)

    async def _cb(fraction: float) -> None:
        pass

    await download_audio("https://example.com/video", "/tmp", on_progress_fraction=_cb)
    assert received["on_progress_fraction"] is _cb


@pytest.mark.asyncio
async def test_download_with_ytdlp_kills_process_on_reader_error(monkeypatch, tmp_path):
    """A readline failure (e.g. line over the StreamReader limit) must not leak
    the yt-dlp child — proc.kill() is called before the error propagates."""
    killed = {"count": 0}

    class BoomStream:
        async def readline(self):
            raise ValueError("Separator is not found, and chunk exceed the limit")

    class EmptyStream:
        async def readline(self):
            return b""

    class FakeProc:
        returncode = None

        def __init__(self):
            self.stdout = BoomStream()
            self.stderr = EmptyStream()

        def kill(self):
            killed["count"] += 1

        async def wait(self):
            self.returncode = -9
            return self.returncode

    async def fake_exec(*_args, **_kwargs):
        return FakeProc()

    monkeypatch.setattr(downloader_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ValueError):
        await downloader_module._download_with_ytdlp("https://x/v", str(tmp_path))
    assert killed["count"] == 1
