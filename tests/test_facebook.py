import os

import pytest
from aioresponses import aioresponses

from bot.services.facebook import (
    FACEBOOK_URL_RE,
    download_from_facebook,
    is_facebook_url,
)

COBALT_URL = "http://cobalt:9000/"


# ── URL detection ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.facebook.com/watch?v=123456",
    "https://www.facebook.com/user/videos/456",
    "https://www.facebook.com/reel/789",
    "https://www.facebook.com/share/r/HASH123/",
    "https://www.facebook.com/share/v/HASH456/",
    "https://fb.watch/HASH789",
    "https://m.facebook.com/watch?v=123",
    "HTTPS://WWW.FACEBOOK.COM/reel/1",
])
def test_facebook_url_detected(url):
    assert is_facebook_url(url)
    assert FACEBOOK_URL_RE.match(url) is not None


@pytest.mark.parametrize("url", [
    "https://instagram.com/reel/abc",
    "https://fakefacebook.com/watch",
    "https://youtube.com/watch?v=abc",
    "https://example.com/facebook.com/reel/abc",
    "https://notfb.watch/abc",
    "",
])
def test_non_facebook_url_rejected(url):
    assert not is_facebook_url(url)


# ── Happy path downloads ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_tunnel_response(tmp_path):
    video_bytes = b"fake mp4 video"

    with aioresponses() as m:
        m.post(
            COBALT_URL,
            payload={"status": "tunnel", "url": "https://cdn.example.com/v.mp4"},
        )
        m.get("https://cdn.example.com/v.mp4", body=video_bytes)

        path = await download_from_facebook(
            "https://www.facebook.com/reel/123456/", str(tmp_path)
        )

    assert os.path.exists(path)
    assert path.endswith(".mp4")
    assert os.path.dirname(path) == str(tmp_path)
    with open(path, "rb") as f:
        assert f.read() == video_bytes


@pytest.mark.asyncio
async def test_download_redirect_response(tmp_path):
    video_bytes = b"redirect video"

    with aioresponses() as m:
        m.post(
            COBALT_URL,
            payload={"status": "redirect", "url": "https://cdn.example.com/r.mp4"},
        )
        m.get("https://cdn.example.com/r.mp4", body=video_bytes)

        path = await download_from_facebook(
            "https://www.facebook.com/watch?v=999", str(tmp_path)
        )

    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == video_bytes


@pytest.mark.asyncio
async def test_download_picker_response(tmp_path):
    video_bytes = b"picker video"

    with aioresponses() as m:
        m.post(
            COBALT_URL,
            payload={
                "status": "picker",
                "picker": [
                    {"type": "photo", "url": "https://cdn.example.com/img.jpg"},
                    {"type": "video", "url": "https://cdn.example.com/vid.mp4"},
                ],
            },
        )
        m.get("https://cdn.example.com/vid.mp4", body=video_bytes)

        path = await download_from_facebook(
            "https://www.facebook.com/share/v/HASH/", str(tmp_path)
        )

    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == video_bytes


# ── Error cases ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cobalt_error_response(tmp_path):
    with aioresponses() as m:
        m.post(
            COBALT_URL,
            payload={
                "status": "error",
                "error": {"code": "content.video.unavailable"},
            },
        )
        with pytest.raises(RuntimeError, match=r"^facebook:.*обработать"):
            await download_from_facebook(
                "https://www.facebook.com/reel/PRIV/", str(tmp_path)
            )


@pytest.mark.asyncio
async def test_cobalt_http_400_error_payload(tmp_path):
    with aioresponses() as m:
        m.post(
            COBALT_URL,
            status=400,
            payload={
                "status": "error",
                "error": {"code": "error.api.fetch.fail"},
            },
        )
        with pytest.raises(RuntimeError) as exc:
            await download_from_facebook(
                "https://www.facebook.com/reel/FAIL/", str(tmp_path)
            )

    message = str(exc.value)
    assert message == (
        "facebook: Cobalt не смог обработать ссылку (error.api.fetch.fail)"
    )
    assert "HTTP 400" not in message


@pytest.mark.asyncio
async def test_cobalt_http_500(tmp_path):
    with aioresponses() as m:
        m.post(COBALT_URL, status=500)
        with pytest.raises(RuntimeError, match=r"^facebook:.*500"):
            await download_from_facebook(
                "https://www.facebook.com/reel/ERR/", str(tmp_path)
            )


@pytest.mark.asyncio
async def test_cobalt_unreachable(tmp_path):
    with aioresponses() as m:
        m.post(COBALT_URL, exception=ConnectionError("refused"))
        with pytest.raises(RuntimeError, match=r"facebook:.*недоступен"):
            await download_from_facebook(
                "https://www.facebook.com/reel/DOWN/", str(tmp_path)
            )


@pytest.mark.asyncio
async def test_download_fails(tmp_path):
    with aioresponses() as m:
        m.post(
            COBALT_URL,
            payload={"status": "tunnel", "url": "https://cdn.example.com/gone.mp4"},
        )
        m.get("https://cdn.example.com/gone.mp4", status=404)
        with pytest.raises(RuntimeError, match=r"facebook:.*скачать"):
            await download_from_facebook(
                "https://www.facebook.com/reel/GONE/", str(tmp_path)
            )


@pytest.mark.asyncio
async def test_picker_no_video(tmp_path):
    with aioresponses() as m:
        m.post(
            COBALT_URL,
            payload={
                "status": "picker",
                "picker": [
                    {"type": "photo", "url": "https://cdn.example.com/a.jpg"},
                    {"type": "photo", "url": "https://cdn.example.com/b.jpg"},
                ],
            },
        )
        with pytest.raises(RuntimeError, match=r"facebook:.*нет видео"):
            await download_from_facebook(
                "https://www.facebook.com/share/r/PHOTOS/", str(tmp_path)
            )
