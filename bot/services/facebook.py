import os
import re
import uuid

import aiohttp

from bot.services.cobalt_client import extract_video_url, request_cobalt
from bot.services.stream_download import stream_download_to_file
from bot.services.user_facing_error import UserFacingError

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
        data = await request_cobalt(session, url, provider="facebook")
        download_url = extract_video_url(data, provider="facebook")
        out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp4")
        await stream_download_to_file(
            session,
            download_url,
            out_path,
            chunk_size=DOWNLOAD_CHUNK_BYTES,
            http_error=lambda status: UserFacingError(
                "facebook",
                f"не удалось скачать видео (HTTP {status})",
            ),
            network_error=lambda: UserFacingError(
                "facebook",
                "не удалось скачать видео",
            ),
        )

    return out_path
