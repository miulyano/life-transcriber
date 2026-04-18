from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

YANDEX_DISK_URL_RE = re.compile(
    r"^https?://(?:disk\.yandex\.(?:ru|com|kz|by|ua)|yadi\.sk)/(?:d|i)/\S+",
    re.IGNORECASE,
)

API_BASE = "https://cloud-api.yandex.net/v1/disk/public/resources"
HTTP_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB


def is_yandex_disk_url(url: str) -> bool:
    return bool(YANDEX_DISK_URL_RE.match(url))


async def download_from_yandex_disk(
    url: str, output_dir: str
) -> tuple[str, str | None]:
    """Download a public Yandex Disk file and return (path, original_name).

    Uses the public Cloud API — no auth token required. Rejects folders and
    non-audio/video resources. Raises RuntimeError with a `yandex-disk:` prefix
    on user-facing failures so the handler can render a friendly message.
    """
    os.makedirs(output_dir, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        meta = await _fetch_meta(session, url)
        _validate_meta(meta)
        href = await _fetch_download_href(session, url)
        name = meta.get("name")
        ext = _pick_extension(name)
        out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}{ext}")
        await _download_to_file(session, href, out_path)

    return out_path, name


async def _fetch_meta(session: aiohttp.ClientSession, public_key: str) -> dict:
    url = f"{API_BASE}?{urlencode({'public_key': public_key})}"
    async with session.get(url) as resp:
        if resp.status == 404:
            raise RuntimeError(
                "yandex-disk: ссылка приватная, удалена или недействительна"
            )
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-disk: API метаданных вернул {resp.status}"
            )
        return await resp.json()


def _validate_meta(meta: dict) -> None:
    if meta.get("type") == "dir":
        raise RuntimeError(
            "yandex-disk: ссылка ведёт на папку, нужен файл с аудио или видео"
        )
    media_type = meta.get("media_type")
    if media_type not in {"audio", "video"}:
        raise RuntimeError(
            f"yandex-disk: файл не аудио и не видео (media_type={media_type!r})"
        )


async def _fetch_download_href(
    session: aiohttp.ClientSession, public_key: str
) -> str:
    url = f"{API_BASE}/download?{urlencode({'public_key': public_key})}"
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-disk: API ссылки на скачивание вернул {resp.status}"
            )
        data = await resp.json()
    href = data.get("href")
    if not href:
        raise RuntimeError("yandex-disk: API не вернул ссылку на скачивание")
    return href


async def _download_to_file(
    session: aiohttp.ClientSession, href: str, out_path: str
) -> None:
    async with session.get(href) as resp:
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-disk: скачивание файла вернуло {resp.status}"
            )
        with open(out_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
                f.write(chunk)


def _pick_extension(name) -> str:
    if not name:
        return ""
    suffix = Path(name).suffix
    return suffix if suffix else ""
