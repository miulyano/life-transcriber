"""Tests for bot.services.downloader — yt-dlp metadata parsing."""
from bot.services.downloader import _parse_ytdlp_meta


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
