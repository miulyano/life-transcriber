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


def test_parse_progress_line_with_total_bytes():
    assert parse_progress_line("LTPROG 100 200 NA") == pytest.approx(0.5)


def test_parse_progress_line_falls_back_to_estimate():
    assert parse_progress_line("LTPROG 100 NA 400") == pytest.approx(0.25)


def test_parse_progress_line_no_total_returns_none():
    assert parse_progress_line("LTPROG 100 NA NA") is None
    assert parse_progress_line("LTPROG 100 0 NA") is None


def test_parse_progress_line_clamps_overshoot():
    # Fragmented downloads can report downloaded > estimate.
    assert parse_progress_line("LTPROG 500 400 NA") == 1.0


def test_parse_progress_line_ignores_non_progress_lines():
    assert parse_progress_line('{"title":"Video","uploader":"Au"}') is None
    assert parse_progress_line("") is None
    assert parse_progress_line("WARNING: something") is None
    assert parse_progress_line("LTPROG garbage 100 NA") is None
    assert parse_progress_line("LTPROG 100 200") is None  # wrong field count


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
