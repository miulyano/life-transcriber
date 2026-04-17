import asyncio
import os
import uuid
from pathlib import Path

from bot.services.facebook import download_from_facebook, is_facebook_url
from bot.services.instagram import download_from_instagram, is_instagram_url
from bot.services.media import prepare_audio_for_transcription
from bot.services.yandex_disk import download_from_yandex_disk, is_yandex_disk_url
from bot.services.yandex_music import (
    is_yandex_music_episode_url,
    is_yandex_music_url,
)


async def download_audio(url: str, output_dir: str) -> str:
    """Download audio from a URL (YouTube, RuTube, VK, Yandex Disk, etc.)."""
    if is_yandex_disk_url(url):
        raw_path = await download_from_yandex_disk(url, output_dir)
        try:
            return await extract_audio(raw_path, output_dir)
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_instagram_url(url):
        raw_path = await download_from_instagram(url, output_dir)
        try:
            return await extract_audio(raw_path, output_dir)
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_facebook_url(url):
        raw_path = await download_from_facebook(url, output_dir)
        try:
            return await extract_audio(raw_path, output_dir)
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_yandex_music_url(url):
        if not is_yandex_music_episode_url(url):
            raise RuntimeError(
                "yandex-music: пришлите ссылку на конкретный выпуск подкаста, "
                "а не на весь подкаст"
            )
        try:
            return await _download_with_ytdlp(url, output_dir)
        except RuntimeError as e:
            raise RuntimeError(
                "yandex-music: не удалось скачать выпуск. Возможно, ссылка "
                "недоступна или Яндекс Музыка запросила проверку"
            ) from e

    return await _download_with_ytdlp(url, output_dir)


async def _download_with_ytdlp(url: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.%(ext)s")

    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--output", out_path,
        "--no-progress",
        "--quiet",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed (code {proc.returncode}): {stderr.decode().strip()}"
        )

    # yt-dlp replaces %(ext)s — find the resulting file
    base = Path(out_path).with_suffix("")
    parent = Path(out_path).parent
    candidates = list(parent.glob(f"{base.name}.*"))
    if not candidates:
        raise RuntimeError("yt-dlp did not produce an output file")
    return str(candidates[0])


async def extract_audio(video_path: str, output_dir: str) -> str:
    """Extract audio track from a video file using FFmpeg."""
    return await prepare_audio_for_transcription(video_path, output_dir)
