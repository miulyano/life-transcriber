import asyncio
import os
from urllib.parse import quote_plus

import aiohttp
import pytest
from aioresponses import aioresponses

from bot.services import yandex_disk
from bot.services.yandex_disk import (
    API_TIMEOUT_SECONDS,
    DOWNLOAD_SOCK_CONNECT_SECONDS,
    DOWNLOAD_SOCK_READ_SECONDS,
    YANDEX_DISK_URL_RE,
    download_from_yandex_disk,
    is_yandex_disk_url,
)


@pytest.mark.parametrize("url", [
    "https://disk.yandex.ru/d/abc123",
    "https://disk.yandex.ru/i/xyz",
    "https://yadi.sk/d/abc",
    "https://yadi.sk/i/abc",
    "https://disk.yandex.com/d/abc",
    "https://disk.yandex.kz/d/abc",
    "https://disk.yandex.by/d/abc",
    "HTTPS://DISK.YANDEX.RU/d/abc",
])
def test_yandex_disk_url_detected(url):
    assert is_yandex_disk_url(url)
    assert YANDEX_DISK_URL_RE.match(url) is not None


@pytest.mark.parametrize("url", [
    "https://youtu.be/abc",
    "https://vk.com/video-1_2",
    "https://example.com",
    "https://example.com/disk.yandex.ru/d/abc",  # not at start
    "https://disk.yandex.ru/client/disk",  # not a public share path
    "https://disk.yandex.ru/",
    "",
])
def test_non_yandex_disk_url_rejected(url):
    assert not is_yandex_disk_url(url)


def _meta_url(public_key: str) -> str:
    return (
        "https://cloud-api.yandex.net/v1/disk/public/resources?public_key="
        + quote_plus(public_key)
    )


def _download_url(public_key: str) -> str:
    return (
        "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key="
        + quote_plus(public_key)
    )


@pytest.mark.asyncio
async def test_download_from_yandex_disk_happy_path(tmp_path):
    public_key = "https://disk.yandex.ru/d/abc123"
    href = "https://downloader.disk.yandex.ru/signed-url"
    payload = b"fake audio bytes"

    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={
                "type": "file",
                "name": "record.mp3",
                "media_type": "audio",
                "size": len(payload),
            },
        )
        m.get(_download_url(public_key), status=200, payload={"href": href})
        m.get(href, status=200, body=payload)

        path, name = await download_from_yandex_disk(public_key, str(tmp_path))

    assert os.path.exists(path)
    assert path.endswith(".mp3")
    assert os.path.dirname(path) == str(tmp_path)
    assert name == "record.mp3"
    with open(path, "rb") as f:
        assert f.read() == payload


@pytest.mark.asyncio
async def test_download_from_yandex_disk_rejects_folder(tmp_path):
    public_key = "https://disk.yandex.ru/d/folder"
    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={"type": "dir", "name": "my-folder"},
        )
        with pytest.raises(RuntimeError, match=r"^yandex-disk:.*папк"):
            await download_from_yandex_disk(public_key, str(tmp_path))


@pytest.mark.asyncio
async def test_download_from_yandex_disk_rejects_non_media(tmp_path):
    public_key = "https://disk.yandex.ru/d/doc"
    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={
                "type": "file",
                "name": "report.pdf",
                "media_type": "document",
            },
        )
        with pytest.raises(RuntimeError, match=r"^yandex-disk:.*аудио"):
            await download_from_yandex_disk(public_key, str(tmp_path))


@pytest.mark.asyncio
async def test_download_from_yandex_disk_private_link(tmp_path):
    public_key = "https://disk.yandex.ru/d/private"
    with aioresponses() as m:
        m.get(_meta_url(public_key), status=404, payload={"error": "not found"})
        with pytest.raises(RuntimeError, match=r"^yandex-disk:.*приватная"):
            await download_from_yandex_disk(public_key, str(tmp_path))


@pytest.mark.asyncio
async def test_download_from_yandex_disk_missing_href(tmp_path):
    public_key = "https://disk.yandex.ru/d/weird"
    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={"type": "file", "name": "a.mp3", "media_type": "audio"},
        )
        m.get(_download_url(public_key), status=200, payload={})
        with pytest.raises(RuntimeError, match=r"^yandex-disk:"):
            await download_from_yandex_disk(public_key, str(tmp_path))


# ---------- size precheck ----------


@pytest.mark.asyncio
async def test_oversize_file_rejected_before_download(tmp_path, monkeypatch):
    """A file over MAX_DOWNLOAD_MB is rejected from metadata alone — the
    download href is never requested (only the meta URL is mocked)."""
    from bot.config import settings

    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 4096)
    public_key = "https://disk.yandex.ru/d/huge"
    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={
                "type": "file",
                "name": "movie.mp4",
                "media_type": "video",
                "size": 28 * 1024**3,
            },
        )
        with pytest.raises(RuntimeError, match=r"^yandex-disk:.*слишком большой"):
            await download_from_yandex_disk(public_key, str(tmp_path))
    assert os.listdir(tmp_path) == []


@pytest.mark.asyncio
async def test_insufficient_disk_rejected_before_download(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bot.config import settings
    from bot.services import download_precheck

    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 0)
    monkeypatch.setattr(
        download_precheck.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=40 * 1024**3, used=0, free=2 * 1024**3),
    )
    public_key = "https://disk.yandex.ru/d/big"
    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={
                "type": "file",
                "name": "long.mp4",
                "media_type": "video",
                "size": 5 * 1024**3,
            },
        )
        with pytest.raises(RuntimeError, match=r"^yandex-disk:.*не хватит места"):
            await download_from_yandex_disk(public_key, str(tmp_path))
    assert os.listdir(tmp_path) == []


# ---------- partial file cleanup ----------


def _patch_failing_writes(monkeypatch, tmp_path, exc: BaseException) -> None:
    """Make writes into tmp_path fail with ``exc`` after some bytes land on
    disk — simulates the disk filling up mid-download."""
    import builtins

    real_open = builtins.open

    def failing_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        if "wb" not in mode or not str(path).startswith(str(tmp_path)):
            return f

        class _Exploder:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                f.close()
                return False

            def write(self, data):
                f.write(data[:1])  # a real partial byte hits the disk
                raise exc

        return _Exploder()

    monkeypatch.setattr(builtins, "open", failing_open)


def _mock_small_download(m, public_key: str, href: str) -> None:
    m.get(
        _meta_url(public_key),
        status=200,
        payload={
            "type": "file",
            "name": "rec.mp3",
            "media_type": "audio",
            "size": 4,
        },
    )
    m.get(_download_url(public_key), status=200, payload={"href": href})
    m.get(href, status=200, body=b"data")


@pytest.mark.asyncio
async def test_enospc_cleans_partial_file_and_raises_friendly(tmp_path, monkeypatch):
    """OSError ENOSPC mid-write (the 28.5 GB incident): the partial file must
    be removed and the user gets a friendly message, not a raw traceback."""
    import errno

    public_key = "https://disk.yandex.ru/d/enospc"
    href = "https://downloader.disk.yandex.ru/signed-enospc"
    _patch_failing_writes(
        monkeypatch, tmp_path, OSError(errno.ENOSPC, "No space left on device")
    )
    with aioresponses() as m:
        _mock_small_download(m, public_key, href)
        with pytest.raises(RuntimeError, match=r"^yandex-disk:.*мест"):
            await download_from_yandex_disk(public_key, str(tmp_path))
    assert os.listdir(tmp_path) == []


@pytest.mark.asyncio
async def test_unexpected_error_cleans_partial_file(tmp_path, monkeypatch):
    """Any other exception during the download (here: task cancellation) must
    also remove the partial file before propagating."""
    public_key = "https://disk.yandex.ru/d/cancelled"
    href = "https://downloader.disk.yandex.ru/signed-cancelled"
    _patch_failing_writes(monkeypatch, tmp_path, asyncio.CancelledError())
    with aioresponses() as m:
        _mock_small_download(m, public_key, href)
        with pytest.raises(asyncio.CancelledError):
            await download_from_yandex_disk(public_key, str(tmp_path))
    assert os.listdir(tmp_path) == []


# ---------- timeout behavior ----------


@pytest.mark.asyncio
async def test_session_uses_download_friendly_timeout_and_api_override(
    tmp_path, monkeypatch
):
    """Session-level timeout has no ``total`` cap (download-friendly), while
    metadata/href calls override it with a strict ``total=API_TIMEOUT_SECONDS``.
    """
    public_key = "https://disk.yandex.ru/d/abc123"
    href = "https://downloader.disk.yandex.ru/signed-url"
    payload = b"x" * 1024

    captured_session_timeouts: list[aiohttp.ClientTimeout] = []
    captured_get_timeouts: list[aiohttp.ClientTimeout | object] = []

    real_session_cls = aiohttp.ClientSession

    class SpyingSession(real_session_cls):
        def __init__(self, *args, **kwargs):
            captured_session_timeouts.append(kwargs.get("timeout"))
            super().__init__(*args, **kwargs)

        def get(self, url, **kwargs):
            captured_get_timeouts.append(kwargs.get("timeout"))
            return super().get(url, **kwargs)

    monkeypatch.setattr(yandex_disk.aiohttp, "ClientSession", SpyingSession)

    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={
                "type": "file",
                "name": "rec.mp3",
                "media_type": "audio",
                "size": len(payload),
            },
        )
        m.get(_download_url(public_key), status=200, payload={"href": href})
        m.get(href, status=200, body=payload)

        await download_from_yandex_disk(public_key, str(tmp_path))

    # Session timeout: no total cap, but per-socket timeouts set.
    assert len(captured_session_timeouts) == 1
    session_to = captured_session_timeouts[0]
    assert isinstance(session_to, aiohttp.ClientTimeout)
    assert session_to.total is None
    assert session_to.sock_connect == DOWNLOAD_SOCK_CONNECT_SECONDS
    assert session_to.sock_read == DOWNLOAD_SOCK_READ_SECONDS

    # First two session.get(...) calls (meta + href) pass total=API_TIMEOUT_SECONDS.
    meta_to = captured_get_timeouts[0]
    href_to = captured_get_timeouts[1]
    assert isinstance(meta_to, aiohttp.ClientTimeout)
    assert meta_to.total == API_TIMEOUT_SECONDS
    assert isinstance(href_to, aiohttp.ClientTimeout)
    assert href_to.total == API_TIMEOUT_SECONDS

    # Third get (the actual file download) has no per-call override — it
    # inherits the session-wide download-friendly timeout.
    assert captured_get_timeouts[2] is None


@pytest.mark.asyncio
async def test_download_timeout_raises_friendly_runtimeerror(tmp_path):
    public_key = "https://disk.yandex.ru/d/big"
    href = "https://downloader.disk.yandex.ru/signed-url-big"

    with aioresponses() as m:
        m.get(
            _meta_url(public_key),
            status=200,
            payload={
                "type": "file",
                "name": "big.mp4",
                "media_type": "video",
                "size": 500 * 1024 * 1024,
            },
        )
        m.get(_download_url(public_key), status=200, payload={"href": href})
        # aioresponses raises the configured exception at the body-streaming
        # step, which is exactly the real failure mode we want to cover.
        m.get(href, exception=asyncio.TimeoutError())

        with pytest.raises(RuntimeError, match=r"^yandex-disk: скачивание прервано"):
            await download_from_yandex_disk(public_key, str(tmp_path))

    # No partial file should be left behind in the output dir.
    leftover = [p for p in os.listdir(tmp_path)]
    assert leftover == []
