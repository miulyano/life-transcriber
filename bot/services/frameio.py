from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from bot.services.stream_download import stream_download_to_file
from bot.services.user_facing_error import UserFacingError

logger = logging.getLogger(__name__)

_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"

# Any public share link on the new Frame.io app: root folder, sub-folder or
# a single asset. Matching the whole family lets folder links fail with a
# Frame.io-specific hint instead of falling through to yt-dlp.
FRAMEIO_URL_RE = re.compile(
    rf"^https?://next\.frame\.io/share/(?P<share>{_UUID})(?:/|$)",
    re.IGNORECASE,
)
# Link to one asset inside the share — the only form that can be transcribed.
FRAMEIO_VIEW_URL_RE = re.compile(
    rf"^https?://next\.frame\.io/share/(?P<share>{_UUID})/view/(?P<asset>{_UUID})(?:[/?#]|$)",
    re.IGNORECASE,
)

GRAPHQL_URL = "https://api.frame.io/graphql"

# Public share links are authorised by the share id alone (base64 in a header);
# the client-name header is mandatory — without it the API answers 403.
_CLIENT_NAME = "web-app"

# One small JSON round-trip — strict total budget.
API_TIMEOUT_SECONDS = 30

# Original-file fallback streams a possibly multi-GB upload, so only
# per-socket timeouts apply (same policy as Yandex Disk).
DOWNLOAD_SOCK_CONNECT_SECONDS = 30
DOWNLOAD_SOCK_READ_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB

_MEDIA_FIELDS = "media { duration hlsManifest original { downloadUrl filesizeInBytes } }"
_QUERY = (
    "query GetAssetMedia($assetId: ID!) { asset(assetId: $assetId) { id name __typename "
    f"... on VideoAsset {{ {_MEDIA_FIELDS} }} "
    f"... on AudioAsset {{ {_MEDIA_FIELDS} }} }} }}"
)


@dataclass
class FrameioMedia:
    """What the share API knows about one asset's downloadable media."""

    name: str | None
    hls_manifest: str | None
    original_url: str | None
    original_size: int | None


def is_frameio_url(url: str) -> bool:
    return bool(FRAMEIO_URL_RE.match(url))


def parse_frameio_url(url: str) -> tuple[str, str | None]:
    """Return (share_id, asset_id) — asset_id is None for folder links."""
    view = FRAMEIO_VIEW_URL_RE.match(url)
    if view:
        return view.group("share").lower(), view.group("asset").lower()
    root = FRAMEIO_URL_RE.match(url)
    if not root:
        raise ValueError(f"not a Frame.io share link: {url}")
    return root.group("share").lower(), None


def share_auth_header(share_id: str) -> str:
    return base64.b64encode(share_id.encode("ascii")).decode("ascii")


async def resolve_frameio_media(url: str) -> FrameioMedia:
    """Ask the Frame.io share API where the asset's media lives.

    No login or cookie is needed for a public share: the share id from the
    URL is the credential. Raises ``UserFacingError("frameio", …)`` for
    folder links, missing/private assets, non-media assets and API failures.
    """
    share_id, asset_id = parse_frameio_url(url)
    if asset_id is None:
        raise UserFacingError(
            "frameio",
            "ссылка ведёт на папку share — открой нужное видео "
            "и скопируй ссылку с /view/",
        )

    headers = {
        "x-frameio-share-authentication": share_auth_header(share_id),
        "apollographql-client-name": _CLIENT_NAME,
    }
    body = {"operationName": "GetAssetMedia", "variables": {"assetId": asset_id}, "query": _QUERY}
    timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(GRAPHQL_URL, json=body, headers=headers) as resp:
                if resp.status != 200:
                    raise UserFacingError(
                        "frameio", f"Frame.io отклонил запрос (HTTP {resp.status})"
                    )
                data = await resp.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
        raise UserFacingError("frameio", "Frame.io не отвечает, попробуй позже") from exc

    return _parse_media_response(data)


def _parse_media_response(data: object) -> FrameioMedia:
    if not isinstance(data, dict):
        raise UserFacingError("frameio", "Frame.io не отвечает, попробуй позже")
    asset = (data.get("data") or {}).get("asset")
    if not asset:
        # The API reports both "not found" and "invalid share" as NOT_FOUND;
        # only the message tells whether the link or the share is at fault.
        errors = data.get("errors") or []
        message = str((errors[0] if errors else {}).get("message", "")).lower()
        if "share" in message:
            raise UserFacingError(
                "frameio",
                "ссылка приватная, защищена паролем или больше не действует",
            )
        raise UserFacingError("frameio", "ссылка недействительна или файл удалён")

    media = asset.get("media")
    if not isinstance(media, dict):
        raise UserFacingError(
            "frameio",
            f"по ссылке не видео и не аудио ({asset.get('__typename')})",
        )

    original = media.get("original") or {}
    size = original.get("filesizeInBytes")
    result = FrameioMedia(
        name=asset.get("name") or None,
        hls_manifest=media.get("hlsManifest") or None,
        original_url=original.get("downloadUrl") or None,
        original_size=size if isinstance(size, int) else None,
    )
    if result.hls_manifest is None and result.original_url is None:
        raise UserFacingError(
            "frameio", "файл ещё обрабатывается на Frame.io, попробуй позже"
        )
    return result


async def download_frameio_original(media: FrameioMedia, output_dir: str) -> str:
    """Fallback for assets without an HLS transcode: fetch the original upload."""
    if not media.original_url:
        raise UserFacingError(
            "frameio", "файл ещё обрабатывается на Frame.io, попробуй позже"
        )
    os.makedirs(output_dir, exist_ok=True)
    if media.original_size:
        logger.info(
            "frameio: downloading original %s (%.1f MB)",
            media.name or "<unnamed>",
            media.original_size / (1024 * 1024),
        )
    ext = Path(media.name).suffix if media.name else ""
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}{ext}")
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=DOWNLOAD_SOCK_CONNECT_SECONDS,
        sock_read=DOWNLOAD_SOCK_READ_SECONDS,
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await stream_download_to_file(
            session,
            media.original_url,
            out_path,
            chunk_size=DOWNLOAD_CHUNK_BYTES,
            http_error=lambda status: UserFacingError(
                "frameio", f"скачивание файла вернуло HTTP {status}"
            ),
            network_error=lambda: UserFacingError(
                "frameio",
                "скачивание прервано — соединение нестабильно, попробуй ещё раз",
            ),
        )
    return out_path
