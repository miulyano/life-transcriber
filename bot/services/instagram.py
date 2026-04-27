import json
import os
import re
import uuid
from typing import Optional

import aiohttp

from bot.config import settings
from bot.services.cobalt_client import extract_video_url, request_cobalt
from bot.services.stream_download import stream_download_to_file
from bot.services.user_facing_error import UserFacingError

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
            data = await request_cobalt(session, url, provider="instagram")
            download_url = extract_video_url(data, provider="instagram")
        except UserFacingError as e:
            if "error.api.fetch.empty" not in str(e):
                raise
            download_url = await _request_instagram_video_url(session, url)
        out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp4")
        await stream_download_to_file(
            session,
            download_url,
            out_path,
            chunk_size=DOWNLOAD_CHUNK_BYTES,
            http_error=lambda status: UserFacingError(
                "instagram",
                f"не удалось скачать видео (HTTP {status})",
            ),
            network_error=lambda: UserFacingError(
                "instagram",
                "не удалось скачать видео",
            ),
        )

    return out_path


async def _request_instagram_video_url(
    session: aiohttp.ClientSession, url: str
) -> str:
    if not settings.INSTAGRAM_COOKIES_PATH:
        raise UserFacingError(
            "instagram",
            "Cobalt не смог обработать ссылку (error.api.fetch.empty)",
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
                raise UserFacingError(
                    "instagram",
                    f"API Instagram вернул HTTP {resp.status}",
                )
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise UserFacingError("instagram", "API Instagram недоступен") from exc

    video_url = _extract_instagram_api_video_url(data)
    if video_url:
        return video_url
    raise UserFacingError("instagram", "API Instagram не вернул видео")


def _extract_shortcode(url: str) -> str:
    match = INSTAGRAM_URL_RE.match(url)
    if not match:
        raise UserFacingError("instagram", "неподдерживаемая ссылка Instagram")
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
        raise UserFacingError("instagram", "не удалось прочитать cookies Instagram")

    cookies = data.get("instagram")
    if isinstance(cookies, list) and cookies and isinstance(cookies[0], str):
        return cookies[0]
    raise UserFacingError("instagram", "не удалось прочитать cookies Instagram")


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
