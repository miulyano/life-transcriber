import asyncio
import json
import os

import aiohttp
import pytest
from aioresponses import aioresponses

from bot.services.frameio import (
    GRAPHQL_URL,
    FrameioMedia,
    download_frameio_original,
    is_frameio_url,
    parse_frameio_url,
    resolve_frameio_media,
    share_auth_header,
)
from bot.services.user_facing_error import UserFacingError

SHARE = "2d11024f-0139-4d05-b9f0-2fc492934855"
ASSET = "8314ce8c-a0ab-46f5-bbeb-265cd3d18865"
FOLDER = "d591a0cc-a97f-4fd5-a91d-b0de5083f210"
VIEW_URL = f"https://next.frame.io/share/{SHARE}/view/{ASSET}"
ROOT_URL = f"https://next.frame.io/share/{SHARE}/"
FOLDER_URL = f"https://next.frame.io/share/{SHARE}/{FOLDER}"
HLS = f"https://sahls.frame.io/encode-hls/{ASSET}/token/jwt/main.m3u8"
ORIGINAL = f"https://assets.frame.io/uploads/{ASSET}/original.mp4?Signature=x"


# --- URL detection / parsing ---


@pytest.mark.parametrize("url", [
    VIEW_URL,
    VIEW_URL + "?t=12",
    VIEW_URL.replace("https://next.frame.io", "HTTPS://NEXT.FRAME.IO"),
    ROOT_URL,
    ROOT_URL.rstrip("/"),
    FOLDER_URL,
])
def test_frameio_url_detected(url):
    assert is_frameio_url(url)


@pytest.mark.parametrize("url", [
    "https://app.frame.io/reviews/abc/def",
    "https://f.io/abc123",
    f"https://frame.io/share/{SHARE}/view/{ASSET}",
    f"https://example.com/next.frame.io/share/{SHARE}/view/{ASSET}",
    "https://next.frame.io/share/not-a-uuid/view/also-not",
    "https://next.frame.io/",
    "https://youtu.be/abc",
    "",
])
def test_non_frameio_url_rejected(url):
    assert not is_frameio_url(url)


def test_parse_view_url_returns_share_and_asset():
    assert parse_frameio_url(VIEW_URL) == (SHARE, ASSET)
    assert parse_frameio_url(VIEW_URL + "?t=12") == (SHARE, ASSET)


def test_parse_view_url_lowercases_uuids():
    upper = f"https://next.frame.io/share/{SHARE.upper()}/view/{ASSET.upper()}"
    assert parse_frameio_url(upper) == (SHARE, ASSET)


@pytest.mark.parametrize("url", [ROOT_URL, ROOT_URL.rstrip("/"), FOLDER_URL])
def test_parse_folder_urls_have_no_asset(url):
    assert parse_frameio_url(url) == (SHARE, None)


def test_share_auth_header_is_base64_of_share_id():
    assert share_auth_header(SHARE) == "MmQxMTAyNGYtMDEzOS00ZDA1LWI5ZjAtMmZjNDkyOTM0ODU1"


# --- resolve_frameio_media ---


def _asset_payload(typename="VideoAsset", *, media=...):
    asset = {"id": ASSET, "name": "Ruslan_интервью.mp4", "__typename": typename}
    if media is ...:
        media = {
            "duration": 946.19,
            "hlsManifest": HLS,
            "original": {"downloadUrl": ORIGINAL, "filesizeInBytes": 984720780},
        }
    if media is not None:
        asset["media"] = media
    return {"data": {"asset": asset}}


@pytest.mark.asyncio
@pytest.mark.parametrize("typename", ["VideoAsset", "AudioAsset"])
async def test_resolve_happy_path(typename):
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, payload=_asset_payload(typename))

        media = await resolve_frameio_media(VIEW_URL)

        assert media == FrameioMedia(
            name="Ruslan_интервью.mp4",
            hls_manifest=HLS,
            original_url=ORIGINAL,
            original_size=984720780,
        )

        ((_, request),) = m.requests.items()
        call = request[0]
        headers = call.kwargs["headers"]
        assert headers["x-frameio-share-authentication"] == share_auth_header(SHARE)
        assert headers["apollographql-client-name"] == "web-app"
        body = call.kwargs["json"]
        assert body["variables"] == {"assetId": ASSET}
        assert "hlsManifest" in body["query"]


@pytest.mark.asyncio
async def test_resolve_asset_not_found():
    payload = {
        "data": {"asset": None},
        "errors": [{"message": "not found", "extensions": {"code": "NOT_FOUND"}}],
    }
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, payload=payload)
        with pytest.raises(UserFacingError, match=r"^frameio:.*недействительна"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_invalid_share_is_private():
    payload = {
        "data": {"asset": None},
        "errors": [{"message": "invalid share", "extensions": {"code": "NOT_FOUND"}}],
    }
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, payload=payload)
        with pytest.raises(UserFacingError, match=r"^frameio:.*приватная"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_rejects_non_media_asset():
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, payload=_asset_payload("FolderAsset", media=None))
        with pytest.raises(UserFacingError, match=r"^frameio:.*не видео и не аудио"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_not_transcoded_yet():
    media = {"duration": None, "hlsManifest": None, "original": None}
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, payload=_asset_payload(media=media))
        with pytest.raises(UserFacingError, match=r"^frameio:.*обрабатывается"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_hls_missing_keeps_original():
    media = {
        "duration": 10.0,
        "hlsManifest": None,
        "original": {"downloadUrl": ORIGINAL, "filesizeInBytes": 123},
    }
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, payload=_asset_payload(media=media))
        result = await resolve_frameio_media(VIEW_URL)
    assert result.hls_manifest is None
    assert result.original_url == ORIGINAL
    assert result.original_size == 123


@pytest.mark.asyncio
async def test_resolve_http_error():
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=403, payload={"errors": [{"message": "permission denied"}]})
        with pytest.raises(UserFacingError, match=r"^frameio:.*HTTP 403"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_network_error():
    with aioresponses() as m:
        m.post(GRAPHQL_URL, exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(UserFacingError, match=r"^frameio:.*не отвечает"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_timeout():
    with aioresponses() as m:
        m.post(GRAPHQL_URL, exception=asyncio.TimeoutError())
        with pytest.raises(UserFacingError, match=r"^frameio:.*не отвечает"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_non_json_body():
    with aioresponses() as m:
        m.post(GRAPHQL_URL, status=200, body="<html>oops</html>", content_type="text/html")
        with pytest.raises(UserFacingError, match=r"^frameio:.*не отвечает"):
            await resolve_frameio_media(VIEW_URL)


@pytest.mark.asyncio
async def test_resolve_folder_url_fails_without_network():
    with aioresponses() as m:
        with pytest.raises(UserFacingError, match=r"^frameio:.*папк"):
            await resolve_frameio_media(ROOT_URL)
        assert not m.requests


# --- download_frameio_original ---


@pytest.mark.asyncio
async def test_download_original_happy_path(tmp_path):
    payload = b"fake video bytes"
    media = FrameioMedia(
        name="clip.mov", hls_manifest=None, original_url=ORIGINAL, original_size=len(payload)
    )
    with aioresponses() as m:
        m.get(ORIGINAL, status=200, body=payload)
        path = await download_frameio_original(media, str(tmp_path))

    assert os.path.dirname(path) == str(tmp_path)
    assert path.endswith(".mov")
    with open(path, "rb") as f:
        assert f.read() == payload


@pytest.mark.asyncio
async def test_download_original_http_error_cleans_up(tmp_path):
    media = FrameioMedia(name="clip.mp4", hls_manifest=None, original_url=ORIGINAL, original_size=1)
    with aioresponses() as m:
        m.get(ORIGINAL, status=500)
        with pytest.raises(UserFacingError, match=r"^frameio:.*HTTP 500"):
            await download_frameio_original(media, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_original_without_url_is_rejected(tmp_path):
    media = FrameioMedia(name="clip.mp4", hls_manifest=None, original_url=None, original_size=None)
    with pytest.raises(UserFacingError, match=r"^frameio:.*обрабатывается"):
        await download_frameio_original(media, str(tmp_path))
