from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

from bot.services.user_facing_error import UserFacingError

logger = logging.getLogger(__name__)

YANDEX_DISK_URL_RE = re.compile(
    r"^https?://(?:disk\.yandex\.(?:ru|com|kz|by|ua)|yadi\.sk)/(?:d|i)/\S+",
    re.IGNORECASE,
)

API_BASE = "https://cloud-api.yandex.net/v1/disk/public/resources"

# Metadata/href API calls are tiny and must be fast — strict total budget.
API_TIMEOUT_SECONDS = 30

# Download uses per-socket timeouts instead of a total-elapsed cap, so the
# session tolerates long-running streaming as long as bytes keep arriving.
DOWNLOAD_SOCK_CONNECT_SECONDS = 30
DOWNLOAD_SOCK_READ_SECONDS = 60

DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB


def is_yandex_disk_url(url: str) -> bool:
    return bool(YANDEX_DISK_URL_RE.match(url))


async def download_from_yandex_disk(
    url: str, output_dir: str
) -> tuple[str, str | None]:
    """Download a public Yandex Disk file and return (path, original_name).

    Uses the public Cloud API — no auth token required. Rejects folders and
    non-audio/video resources. Raises RuntimeError with a ``yandex-disk:``
    prefix on user-facing failures so the handler can render a friendly
    message.

    Timeout policy: metadata/href calls use a strict 30s total timeout;
    the actual file download uses per-socket timeouts so big files can
    stream for as long as bytes keep arriving.
    """
    os.makedirs(output_dir, exist_ok=True)
    # Session-wide timeout = download-friendly (no total cap). API calls
    # below override this with their own short ``total`` budget.
    download_timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=DOWNLOAD_SOCK_CONNECT_SECONDS,
        sock_read=DOWNLOAD_SOCK_READ_SECONDS,
    )

    async with aiohttp.ClientSession(timeout=download_timeout) as session:
        meta = await _fetch_meta(session, url)
        _validate_meta(meta)
        _log_expected_size(meta)
        href = await _fetch_download_href(session, url)
        name = meta.get("name")
        ext = _pick_extension(name)
        out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}{ext}")
        await _download_to_file(session, href, out_path)

    return out_path, name


def _api_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS)


async def _fetch_meta(session: aiohttp.ClientSession, public_key: str) -> dict:
    url = f"{API_BASE}?{urlencode({'public_key': public_key})}"
    async with session.get(url, timeout=_api_timeout()) as resp:
        if resp.status == 404:
            raise UserFacingError(
                "yandex-disk",
                "ссылка приватная, удалена или недействительна",
            )
        if resp.status != 200:
            raise UserFacingError(
                "yandex-disk",
                f"API метаданных вернул {resp.status}",
            )
        return await resp.json()


def _validate_meta(meta: dict) -> None:
    if meta.get("type") == "dir":
        raise UserFacingError(
            "yandex-disk",
            "ссылка ведёт на папку, нужен файл с аудио или видео",
        )
    media_type = meta.get("media_type")
    if media_type not in {"audio", "video"}:
        raise UserFacingError(
            "yandex-disk",
            f"файл не аудио и не видео (media_type={media_type!r})",
        )


def _log_expected_size(meta: dict) -> None:
    size = meta.get("size")
    if isinstance(size, int) and size > 0:
        logger.info(
            "yandex-disk: downloading %s (%.1f MB)",
            meta.get("name") or "<unnamed>",
            size / (1024 * 1024),
        )


async def _fetch_download_href(
    session: aiohttp.ClientSession, public_key: str
) -> str:
    url = f"{API_BASE}/download?{urlencode({'public_key': public_key})}"
    async with session.get(url, timeout=_api_timeout()) as resp:
        if resp.status != 200:
            raise UserFacingError(
                "yandex-disk",
                f"API ссылки на скачивание вернул {resp.status}",
            )
        data = await resp.json()
    href = data.get("href")
    if not href:
        raise UserFacingError("yandex-disk", "API не вернул ссылку на скачивание")
    return href


async def _download_to_file(
    session: aiohttp.ClientSession, href: str, out_path: str
) -> None:
    bytes_written = 0
    expected_bytes: int | None = None
    try:
        async with session.get(href) as resp:
            if resp.status != 200:
                raise UserFacingError(
                    "yandex-disk",
                    f"скачивание файла вернуло {resp.status}",
                )
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                expected_bytes = int(cl)
            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
                    f.write(chunk)
                    bytes_written += len(chunk)
    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
        _cleanup_partial(out_path)
        mb_done = bytes_written / (1024 * 1024)
        if expected_bytes:
            mb_total = expected_bytes / (1024 * 1024)
            logger.warning(
                "yandex-disk: download stalled after %.1f/%.1f MB: %r",
                mb_done,
                mb_total,
                exc,
            )
        else:
            logger.warning(
                "yandex-disk: download stalled after %.1f MB: %r", mb_done, exc
            )
        raise UserFacingError(
            "yandex-disk",
            "скачивание прервано — возможно, файл слишком большой "
            "или соединение нестабильно, попробуй ещё раз",
        ) from exc


def _cleanup_partial(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        logger.exception("yandex-disk: failed to clean up partial download at %s", path)


def _pick_extension(name) -> str:
    if not name:
        return ""
    suffix = Path(name).suffix
    return suffix if suffix else ""
