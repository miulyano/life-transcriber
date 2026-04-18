import json
import os
import re
import uuid
from typing import Optional

import aiohttp

from bot.config import settings

INSTAGRAM_URL_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/(?P<shortcode>[\w-]+)",
    re.IGNORECASE,
)

HTTP_TIMEOUT_SECONDS = 120
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB
INSTAGRAM_API_URL = "https://www.instagram.com/api/v1/media/{media_id}/info/"
INSTAGRAM_SHORTCODE_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def is_instagram_url(url: str) -> bool:
    return bool(INSTAGRAM_URL_RE.match(url))


async def download_from_instagram(url: str, output_dir: str) -> str:
    """Download video from a public Instagram URL via Cobalt API.

    Returns the path to the downloaded .mp4 file. Raises RuntimeError with
    an ``instagram:`` prefix on user-facing failures so the handler can
    render a friendly message.
    """
    os.makedirs(output_dir, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            data = await _request_cobalt(session, url)
            download_url = _extract_video_url(data)
        except RuntimeError as e:
            if "error.api.fetch.empty" not in str(e):
                raise
            download_url = await _request_instagram_video_url(session, url)
        out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp4")
        await _download_to_file(session, download_url, out_path)

    return out_path


async def _request_cobalt(session: aiohttp.ClientSession, url: str) -> dict:
    cobalt_url = f"{settings.COBALT_API_URL}/"
    body = {"url": url, "videoQuality": "720"}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    try:
        async with session.post(cobalt_url, json=body, headers=headers) as resp:
            if resp.status != 200:
                error_data = await _read_cobalt_error(resp)
                if error_data:
                    return error_data
                raise RuntimeError(
                    f"instagram: Cobalt вернул HTTP {resp.status}"
                )
            return await resp.json()
    except (aiohttp.ClientError, ConnectionError, OSError):
        raise RuntimeError(
            "instagram: Cobalt недоступен, попробуйте позже"
        )


async def _read_cobalt_error(resp: aiohttp.ClientResponse) -> Optional[dict]:
    try:
        data = await resp.json()
    except (aiohttp.ContentTypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("status") == "error":
        return data
    return None


def _extract_video_url(data: dict) -> str:
    status = data.get("status")

    if status in ("tunnel", "redirect"):
        video_url = data.get("url")
        if video_url:
            return video_url
        raise RuntimeError("instagram: неожиданный ответ от Cobalt")

    if status == "picker":
        picker = data.get("picker", [])
        for item in picker:
            if item.get("type") == "video":
                video_url = item.get("url")
                if video_url:
                    return video_url
        raise RuntimeError("instagram: в публикации нет видео")

    if status == "error":
        code = ""
        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code", "")
        raise RuntimeError(
            f"instagram: Cobalt не смог обработать ссылку ({code})"
            if code
            else "instagram: Cobalt не смог обработать ссылку"
        )

    raise RuntimeError("instagram: неожиданный ответ от Cobalt")


async def _request_instagram_video_url(
    session: aiohttp.ClientSession, url: str
) -> str:
    if not settings.INSTAGRAM_COOKIES_PATH:
        raise RuntimeError(
            "instagram: Cobalt не смог обработать ссылку (error.api.fetch.empty)"
        )

    shortcode = _extract_shortcode(url)
    cookie = _load_instagram_cookie(settings.INSTAGRAM_COOKIES_PATH)
    media_id = _decode_shortcode(shortcode)
    api_url = INSTAGRAM_API_URL.format(media_id=media_id)
    headers = {
        "Accept": "application/json",
        "Cookie": cookie,
        "Referer": f"https://www.instagram.com/reels/{shortcode}/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        ),
        "X-IG-App-ID": "936619743392459",
    }

    try:
        async with session.get(api_url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"instagram: API Instagram вернул HTTP {resp.status}"
                )
            data = await resp.json(content_type=None)
    except aiohttp.ClientError:
        raise RuntimeError("instagram: API Instagram недоступен")

    video_url = _extract_instagram_api_video_url(data)
    if video_url:
        return video_url
    raise RuntimeError("instagram: API Instagram не вернул видео")


def _extract_shortcode(url: str) -> str:
    match = INSTAGRAM_URL_RE.match(url)
    if not match:
        raise RuntimeError("instagram: неподдерживаемая ссылка Instagram")
    return match.group("shortcode")


def _decode_shortcode(shortcode: str) -> int:
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + INSTAGRAM_SHORTCODE_ALPHABET.index(char)
    return media_id


def _load_instagram_cookie(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("instagram: не удалось прочитать cookies Instagram")

    cookies = data.get("instagram")
    if isinstance(cookies, list) and cookies and isinstance(cookies[0], str):
        return cookies[0]
    raise RuntimeError("instagram: не удалось прочитать cookies Instagram")


def _extract_instagram_api_video_url(data: dict) -> Optional[str]:
    items = data.get("items")
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        video_url = _pick_video_version_url(item.get("video_versions"))
        if video_url:
            return video_url

        carousel = item.get("carousel_media")
        if not isinstance(carousel, list):
            continue
        for carousel_item in carousel:
            if not isinstance(carousel_item, dict):
                continue
            video_url = _pick_video_version_url(
                carousel_item.get("video_versions")
            )
            if video_url:
                return video_url

    return None


def _pick_video_version_url(video_versions: object) -> Optional[str]:
    if not isinstance(video_versions, list):
        return None
    for version in video_versions:
        if isinstance(version, dict) and version.get("url"):
            return version["url"]
    return None


async def _download_to_file(
    session: aiohttp.ClientSession, href: str, out_path: str
) -> None:
    try:
        async with session.get(href) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"instagram: не удалось скачать видео (HTTP {resp.status})"
                )
            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
                    f.write(chunk)
    except aiohttp.ClientError:
        raise RuntimeError("instagram: не удалось скачать видео")
