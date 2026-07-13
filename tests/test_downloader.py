"""Tests for bot.services.downloader — yt-dlp metadata and progress parsing."""
import pytest

import bot.services.downloader as downloader_module
from bot.services.downloader import (
    _build_ytdlp_cmd,
    _dispatch_progress_line,
    _parse_ytdlp_meta,
    download_audio,
    parse_progress_line,
)


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


# ---------- _build_ytdlp_cmd ----------


def _flag_value(cmd, flag):
    """Return the argument following ``flag`` in ``cmd`` (None if absent)."""
    for i, arg in enumerate(cmd):
        if arg == flag and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


def test_ytdlp_cmd_extracts_audio_by_copy():
    # Extract the audio track without re-encoding: -f prefers an audio-only
    # stream, --audio-format best does a stream copy. The old mp3/q0 flags
    # forced a slow re-encode (the frozen-99% hang) and must be gone.
    cmd = _build_ytdlp_cmd("https://x/v", "/tmp/out.%(ext)s", None)
    assert _flag_value(cmd, "-f") == "bestaudio/best"
    assert "--extract-audio" in cmd
    assert _flag_value(cmd, "--audio-format") == "best"
    assert "--audio-quality" not in cmd
    assert "mp3" not in cmd


def test_ytdlp_cmd_has_postprocess_template():
    # A second progress-template surfaces the postprocess (audio-extract) stage
    # so the UI can switch off the frozen download bar.
    cmd = _build_ytdlp_cmd("https://x/v", "/tmp/out.%(ext)s", None)
    templates = [
        cmd[i + 1]
        for i, arg in enumerate(cmd)
        if arg == "--progress-template" and i + 1 < len(cmd)
    ]
    assert any(t.startswith("postprocess:") and "LTPP" in t for t in templates)
    assert any(t.startswith("download:") and "LTPROG" in t for t in templates)


def test_ytdlp_cmd_proxy_and_url():
    without = _build_ytdlp_cmd("https://x/v", "/tmp/out.%(ext)s", None)
    assert "--proxy" not in without
    assert without[-1] == "https://x/v"

    withp = _build_ytdlp_cmd("https://x/v", "/tmp/out.%(ext)s", "http://p:1")
    assert _flag_value(withp, "--proxy") == "http://p:1"
    assert withp[-1] == "https://x/v"


# ---------- _dispatch_progress_line ----------


def _new_state():
    return {"last_fraction": 0.0, "postprocess_fired": False}


@pytest.mark.asyncio
async def test_dispatch_fires_postprocess_once_and_drops_ltpp():
    calls = {"n": 0}

    async def on_pp() -> None:
        calls["n"] += 1

    state = _new_state()
    keep: list[str] = []
    for line in ("LTPROG 500 NA 3 12", "LTPP started", "LTPP processing"):
        await _dispatch_progress_line(line, keep, state, None, on_pp)

    assert calls["n"] == 1  # only the first postprocess status fires the callback
    assert keep == []  # LTPP lines are neither progress nor error/meta text


@pytest.mark.asyncio
async def test_dispatch_reports_only_increasing_fraction():
    got: list[float] = []

    async def on_frac(f: float) -> None:
        got.append(f)

    state = _new_state()
    await _dispatch_progress_line("LTPROG 500 NA 3 12", [], state, on_frac, None)  # 0.25
    await _dispatch_progress_line("LTPROG 500 NA 6 12", [], state, on_frac, None)  # 0.50
    await _dispatch_progress_line("LTPROG 500 NA 3 12", [], state, on_frac, None)  # 0.25 < 0.50
    assert got == [pytest.approx(0.25), pytest.approx(0.50)]


@pytest.mark.asyncio
async def test_dispatch_clamps_fraction_to_download_ceiling():
    got: list[float] = []

    async def on_frac(f: float) -> None:
        got.append(f)

    await _dispatch_progress_line(
        "LTPROG 100 NA 12 12", [], _new_state(), on_frac, None
    )  # 12/12 = 1.0 -> capped below completion
    assert got == [pytest.approx(downloader_module._DOWNLOAD_FRACTION_CEILING)]


@pytest.mark.asyncio
async def test_dispatch_ignores_download_fraction_after_postprocess():
    # stdout (LTPROG) and stderr (LTPP) are read by independent coroutines, so a
    # buffered download line can arrive after postprocessing already started.
    # Once postprocess fired, late fractions must not re-determinate the bar
    # (which would flip the animated "Готовлю аудио…" phase back to a frozen %).
    got: list[float] = []

    async def on_frac(f: float) -> None:
        got.append(f)

    async def on_pp() -> None:
        pass

    state = _new_state()
    await _dispatch_progress_line("LTPROG 500 NA 3 12", [], state, on_frac, on_pp)  # 0.25
    await _dispatch_progress_line("LTPP started", [], state, on_frac, on_pp)
    await _dispatch_progress_line("LTPROG 500 NA 9 12", [], state, on_frac, on_pp)  # late
    assert got == [pytest.approx(0.25)]  # the post-postprocess 0.75 is dropped


@pytest.mark.asyncio
async def test_dispatch_keeps_non_progress_lines():
    keep: list[str] = []
    await _dispatch_progress_line("WARNING: boom", keep, _new_state(), None, None)
    assert keep == ["WARNING: boom"]


# ---------- download_audio progress plumbing ----------


@pytest.mark.asyncio
async def test_download_audio_passes_callbacks_to_ytdlp(monkeypatch):
    received = {}

    async def _fake_ytdlp(
        url, output_dir, proxy=None, on_progress_fraction=None, on_postprocess=None
    ):
        received["on_progress_fraction"] = on_progress_fraction
        received["on_postprocess"] = on_postprocess
        return "/tmp/x.mp3", _parse_ytdlp_meta(b"")

    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _fake_ytdlp)

    async def _cb(fraction: float) -> None:
        pass

    async def _pp() -> None:
        pass

    await download_audio(
        "https://example.com/video",
        "/tmp",
        on_progress_fraction=_cb,
        on_postprocess=_pp,
    )
    assert received["on_progress_fraction"] is _cb
    assert received["on_postprocess"] is _pp


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
