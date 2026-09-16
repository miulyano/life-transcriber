"""Tests for bot.services.downloader — yt-dlp metadata and progress parsing."""
import pytest
from aioresponses import aioresponses

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
async def test_download_audio_logs_swallowed_yandex_music_error(monkeypatch, caplog):
    """When the custom Yandex Music extractor fails and the code falls back to
    yt-dlp, the extractor's real error must be logged — not swallowed silently —
    so a dead API endpoint (HTTP 404) stays diagnosable."""
    import logging

    from bot.services.user_facing_error import UserFacingError

    async def _boom_custom(url, output_dir):
        raise UserFacingError("yandex-music", "API подкаста вернул HTTP 404")

    async def _fake_ytdlp(url, output_dir, **_kwargs):
        return "/tmp/x.mp3", _parse_ytdlp_meta(b"")

    monkeypatch.setattr(
        downloader_module,
        "download_podcast_episode_from_yandex_music",
        _boom_custom,
    )
    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _fake_ytdlp)

    url = "https://music.yandex.ru/album/31008129/track/128441116"
    with caplog.at_level(logging.WARNING, logger="bot.services.downloader"):
        path, _meta = await download_audio(url, "/tmp")

    # Fallback still runs…
    assert path == "/tmp/x.mp3"
    # …but the swallowed extractor error is now visible in the logs.
    assert any("HTTP 404" in r.getMessage() for r in caplog.records)


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


# --- Frame.io dispatch ---

_FRAMEIO_SHARE = "2d11024f-0139-4d05-b9f0-2fc492934855"
_FRAMEIO_ASSET = "8314ce8c-a0ab-46f5-bbeb-265cd3d18865"
_FRAMEIO_VIEW_URL = f"https://next.frame.io/share/{_FRAMEIO_SHARE}/view/{_FRAMEIO_ASSET}"
_FRAMEIO_ROOT_URL = f"https://next.frame.io/share/{_FRAMEIO_SHARE}/"


@pytest.mark.asyncio
async def test_download_audio_frameio_hls_goes_through_ytdlp(monkeypatch):
    from bot.services.frameio import FrameioMedia

    received = {}

    async def _fake_resolve(url):
        received["resolved"] = url
        return FrameioMedia(
            name="Ruslan_интервью.mp4",
            hls_manifest="https://sahls.frame.io/x/main.m3u8",
            original_url="https://assets.frame.io/uploads/x/original.mp4",
            original_size=1,
        )

    async def _fake_ytdlp(
        url, output_dir, proxy=None, on_progress_fraction=None, on_postprocess=None
    ):
        received["ytdlp_url"] = url
        received["on_progress_fraction"] = on_progress_fraction
        received["on_postprocess"] = on_postprocess
        return "/tmp/x.m4a", _parse_ytdlp_meta(b'{"title": "main"}')

    async def _no_original(*_a, **_k):
        raise AssertionError("original download must not run when HLS is available")

    monkeypatch.setattr(downloader_module, "resolve_frameio_media", _fake_resolve)
    monkeypatch.setattr(downloader_module, "download_frameio_original", _no_original)
    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _fake_ytdlp)

    async def _cb(fraction: float) -> None:
        pass

    async def _pp() -> None:
        pass

    path, meta = await download_audio(
        _FRAMEIO_VIEW_URL, "/tmp", on_progress_fraction=_cb, on_postprocess=_pp
    )

    assert received["resolved"] == _FRAMEIO_VIEW_URL
    assert received["ytdlp_url"] == "https://sahls.frame.io/x/main.m3u8"
    assert received["on_progress_fraction"] is _cb
    assert received["on_postprocess"] is _pp
    assert path == "/tmp/x.m4a"
    # yt-dlp's generic "main" title is ignored in favour of the Frame.io filename
    assert meta.title == "Ruslan_интервью.mp4"
    assert meta.title_is_filename is True


@pytest.mark.asyncio
async def test_download_audio_frameio_falls_back_to_original(monkeypatch, tmp_path):
    from bot.services.frameio import FrameioMedia

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"video")
    received = {}

    async def _fake_resolve(url):
        return FrameioMedia(
            name="clip.mp4",
            hls_manifest=None,
            original_url="https://assets.frame.io/uploads/x/original.mp4",
            original_size=5,
        )

    async def _fake_original(media, output_dir):
        received["original"] = media.original_url
        return str(raw)

    async def _fake_extract(video_path, output_dir):
        received["extract_from"] = video_path
        return str(tmp_path / "audio.mp3")

    async def _no_ytdlp(*_a, **_k):
        raise AssertionError("yt-dlp must not run without an HLS manifest")

    monkeypatch.setattr(downloader_module, "resolve_frameio_media", _fake_resolve)
    monkeypatch.setattr(downloader_module, "download_frameio_original", _fake_original)
    monkeypatch.setattr(downloader_module, "extract_audio", _fake_extract)
    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _no_ytdlp)

    path, meta = await download_audio(_FRAMEIO_VIEW_URL, str(tmp_path))

    assert received["original"] == "https://assets.frame.io/uploads/x/original.mp4"
    assert received["extract_from"] == str(raw)
    assert path == str(tmp_path / "audio.mp3")
    assert meta.title == "clip.mp4"
    assert meta.title_is_filename is True
    # the raw download is removed once audio is extracted
    assert not raw.exists()


@pytest.mark.asyncio
async def test_download_audio_frameio_folder_link_fails_fast(monkeypatch):
    from bot.services.user_facing_error import UserFacingError

    async def _boom(*_a, **_k):
        raise AssertionError("no network / yt-dlp call expected for a folder link")

    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _boom)

    with aioresponses() as m:
        with pytest.raises(UserFacingError, match=r"^frameio:.*папк"):
            await download_audio(_FRAMEIO_ROOT_URL, "/tmp")
        assert not m.requests


# ---------- YouTube cookies ----------

_AGE_ERROR = (
    "yt-dlp failed (code 1): ERROR: [youtube] abc: Sign in to confirm your age. "
    "Use --cookies-from-browser or --cookies for the authentication."
)


def test_ytdlp_cmd_adds_cookies_when_given():
    without = _build_ytdlp_cmd("https://x/v", "/tmp/out.%(ext)s", None)
    assert "--cookies" not in without

    withc = _build_ytdlp_cmd(
        "https://x/v", "/tmp/out.%(ext)s", None, cookies_file="/secrets/yt.txt"
    )
    assert withc[withc.index("--cookies") + 1] == "/secrets/yt.txt"
    assert withc[-1] == "https://x/v"


def _fake_ytdlp_factory(calls, fail_without_cookies=None):
    """Record cookies_file per call; optionally fail the cookie-less attempt."""
    async def _fake_ytdlp(url, output_dir, proxy=None, cookies_file=None, **_kw):
        calls.append(cookies_file)
        if cookies_file is None and fail_without_cookies:
            raise RuntimeError(fail_without_cookies)
        return "/tmp/x.mp3", _parse_ytdlp_meta(b"")
    return _fake_ytdlp


@pytest.fixture
def cookies_file(monkeypatch, tmp_path):
    path = tmp_path / "yt.txt"
    path.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(downloader_module.settings, "YTDLP_COOKIES_FILE", str(path))
    return str(path)


@pytest.mark.asyncio
async def test_download_audio_youtube_public_never_sends_cookies(monkeypatch, cookies_file):
    """Cookies expose the account, so a video that works anonymously must not use them."""
    calls = []
    monkeypatch.setattr(downloader_module, "_download_with_ytdlp", _fake_ytdlp_factory(calls))

    await download_audio("https://www.youtube.com/watch?v=abc", "/tmp")
    assert calls == [None]


@pytest.mark.asyncio
async def test_download_audio_youtube_age_restricted_retries_with_cookies(
    monkeypatch, cookies_file, caplog
):
    import logging

    calls = []
    monkeypatch.setattr(
        downloader_module, "_download_with_ytdlp", _fake_ytdlp_factory(calls, _AGE_ERROR)
    )

    with caplog.at_level(logging.INFO, logger="bot.services.downloader"):
        path, _ = await download_audio("https://youtu.be/abc", "/tmp")

    assert path == "/tmp/x.mp3"
    assert calls == [None, cookies_file]
    assert any("cookies" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_download_audio_youtube_other_error_does_not_use_cookies(monkeypatch, cookies_file):
    calls = []
    monkeypatch.setattr(
        downloader_module,
        "_download_with_ytdlp",
        _fake_ytdlp_factory(calls, "yt-dlp failed (code 1): ERROR: SABR streaming"),
    )

    with pytest.raises(RuntimeError, match="SABR"):
        await download_audio("https://www.youtube.com/watch?v=abc", "/tmp")
    assert calls == [None]


@pytest.mark.asyncio
async def test_download_audio_age_restricted_without_cookies_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        downloader_module.settings, "YTDLP_COOKIES_FILE", str(tmp_path / "absent.txt")
    )
    calls = []
    monkeypatch.setattr(
        downloader_module, "_download_with_ytdlp", _fake_ytdlp_factory(calls, _AGE_ERROR)
    )

    with pytest.raises(RuntimeError, match="confirm your age"):
        await download_audio("https://www.youtube.com/watch?v=abc", "/tmp")
    assert calls == [None]


@pytest.mark.asyncio
async def test_download_audio_non_youtube_age_error_does_not_use_cookies(monkeypatch, cookies_file):
    calls = []
    monkeypatch.setattr(
        downloader_module, "_download_with_ytdlp", _fake_ytdlp_factory(calls, _AGE_ERROR)
    )

    with pytest.raises(RuntimeError):
        await download_audio("https://rutube.ru/video/xyz/", "/tmp")
    assert calls == [None]
