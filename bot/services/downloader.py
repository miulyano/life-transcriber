from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from bot.config import settings
from bot.services.facebook import download_from_facebook, is_facebook_url
from bot.services.instagram import download_from_instagram, is_instagram_url
from bot.services.media import prepare_audio_for_transcription
from bot.services.source_meta import SourceMetadata
from bot.services.user_facing_error import UserFacingError
from bot.services.yandex_disk import download_from_yandex_disk, is_yandex_disk_url
from bot.services.yandex_music import (
    YandexMusicNotPodcastError,
    download_podcast_episode_from_yandex_music,
    is_yandex_music_episode_url,
    is_yandex_music_url,
)

logger = logging.getLogger(__name__)

__all__ = ["SourceMetadata", "download_audio", "extract_audio"]

_EMPTY = SourceMetadata()


def _clean(value: object) -> Optional[str]:
    """Normalise yt-dlp/string values: drop None, empty, 'NA' sentinels."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "NA":
        return None
    return text


async def download_audio(url: str, output_dir: str) -> tuple[str, SourceMetadata]:
    """Download audio from a URL and return (local_path, source_metadata)."""
    if is_yandex_disk_url(url):
        raw_path, title = await download_from_yandex_disk(url, output_dir)
        try:
            audio = await extract_audio(raw_path, output_dir)
            return audio, SourceMetadata(title=_clean(title))
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_instagram_url(url):
        raw_path = await download_from_instagram(url, output_dir)
        try:
            return await extract_audio(raw_path, output_dir), _EMPTY
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_facebook_url(url):
        raw_path = await download_from_facebook(url, output_dir)
        try:
            return await extract_audio(raw_path, output_dir), _EMPTY
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_yandex_music_url(url):
        if not is_yandex_music_episode_url(url):
            raise UserFacingError(
                "yandex-music",
                "пришлите ссылку на конкретный выпуск подкаста, "
                "а не на весь подкаст"
            )
        try:
            return await download_podcast_episode_from_yandex_music(url, output_dir)
        except YandexMusicNotPodcastError:
            pass
        except RuntimeError:
            pass

        try:
            return await _download_with_ytdlp(
                url,
                output_dir,
                proxy=settings.YANDEX_MUSIC_PROXY or settings.YTDLP_PROXY,
            )
        except RuntimeError as e:
            if "HTTP Error 451" in str(e) or "Unavailable For Legal Reasons" in str(e):
                raise UserFacingError(
                    "yandex-music",
                    "Яндекс Музыка недоступна из региона сервера. "
                    "Нужен прокси или сервер в регионе, где она открывается"
                ) from e
            raise UserFacingError(
                "yandex-music",
                "не удалось скачать выпуск. Возможно, ссылка "
                "недоступна или Яндекс Музыка запросила проверку"
            ) from e

    return await _download_with_ytdlp(
        url,
        output_dir,
        proxy=settings.YTDLP_PROXY,
    )


def _parse_ytdlp_meta(stdout: bytes) -> SourceMetadata:
    """Parse the JSON line printed by yt-dlp via --print after_move:%(.{...})j."""
    text = stdout.decode().strip()
    if not text:
        return _EMPTY
    # The flag prints one JSON object per finished item. Take the last non-empty line.
    line = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "")
    if not line:
        return _EMPTY
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("yt-dlp metadata line is not valid JSON: %r", line)
        return SourceMetadata(title=_clean(line))
    if not isinstance(data, dict):
        return _EMPTY
    return SourceMetadata(
        title=_clean(data.get("title")),
        uploader=_clean(data.get("uploader")) or _clean(data.get("channel")),
    )


async def _download_with_ytdlp(
    url: str,
    output_dir: str,
    proxy: Optional[str] = None,
) -> tuple[str, SourceMetadata]:
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--output", out_path,
        "--print", "after_move:%(.{title,uploader,channel})j",
        "--no-progress",
        "--quiet",
    ]
    if proxy:
        cmd.extend(["--proxy", proxy])
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed (code {proc.returncode}): {stderr.decode().strip()}"
        )

    meta = _parse_ytdlp_meta(stdout)

    # yt-dlp replaces %(ext)s — find the resulting file
    base = Path(out_path).with_suffix("")
    parent = Path(out_path).parent
    candidates = list(parent.glob(f"{base.name}.*"))
    if not candidates:
        raise RuntimeError("yt-dlp did not produce an output file")
    return str(candidates[0]), meta


async def extract_audio(video_path: str, output_dir: str) -> str:
    """Extract and normalise audio from a video file (16 kHz mono MP3).

    Thin alias for media.prepare_audio_for_transcription(), exposed here so
    handlers and pipeline code can import a single downloader entry-point.
    """
    return await prepare_audio_for_transcription(video_path, output_dir)
