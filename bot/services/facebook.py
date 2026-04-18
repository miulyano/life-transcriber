import os
import re
import uuid
from typing import Optional

import aiohttp

from bot.config import settings

FACEBOOK_URL_RE = re.compile(
    r"^https?://(?:(?:www\.|m\.)?facebook\.com|fb\.watch)/",
    re.IGNORECASE,
)

HTTP_TIMEOUT_SECONDS = 120
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB


def is_facebook_url(url: str) -> bool:
    return bool(FACEBOOK_URL_RE.match(url))


async def download_from_facebook(url: str, output_dir: str) -> str:
    """Download video from a public Facebook URL via Cobalt API.

    Returns the path to the downloaded .mp4 file. Raises RuntimeError with
    a ``facebook:`` prefix on user-facing failures so the handler can
    render a friendly message.
    """
    os.makedirs(output_dir, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _request_cobalt(session, url)
        download_url = _extract_video_url(data)
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
                    f"facebook: Cobalt вернул HTTP {resp.status}"
                )
            return await resp.json()
    except (aiohttp.ClientError, ConnectionError, OSError):
        raise RuntimeError(
            "facebook: Cobalt недоступен, попробуйте позже"
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
        raise RuntimeError("facebook: неожиданный ответ от Cobalt")

    if status == "picker":
        picker = data.get("picker", [])
        for item in picker:
            if item.get("type") == "video":
                video_url = item.get("url")
                if video_url:
                    return video_url
        raise RuntimeError("facebook: в публикации нет видео")

    if status == "error":
        code = ""
        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code", "")
        raise RuntimeError(
            f"facebook: Cobalt не смог обработать ссылку ({code})"
            if code
            else "facebook: Cobalt не смог обработать ссылку"
        )

    raise RuntimeError("facebook: неожиданный ответ от Cobalt")


async def _download_to_file(
    session: aiohttp.ClientSession, href: str, out_path: str
) -> None:
    try:
        async with session.get(href) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"facebook: не удалось скачать видео (HTTP {resp.status})"
                )
            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
                    f.write(chunk)
    except aiohttp.ClientError:
        raise RuntimeError("facebook: не удалось скачать видео")
