from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Optional

from bot.config import settings
from bot.services.facebook import download_from_facebook, is_facebook_url
from bot.services.frameio import (
    download_frameio_original,
    is_frameio_url,
    resolve_frameio_media,
)
from bot.utils.fake_progress import FractionCallback, PostprocessCallback
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

__all__ = [
    "SourceMetadata",
    "detect_link_source_type",
    "download_audio",
    "extract_audio",
    "is_youtube_url",
]

_EMPTY = SourceMetadata()

# Unique prefix for --progress-template lines so parsing does not depend on
# which stream (stdout/stderr) a given yt-dlp version writes progress to.
PROGRESS_PREFIX = "LTPROG"
# Prefix for postprocess (--audio-format best extraction) progress-template
# lines, kept distinct from download lines so the UI can switch phase.
POSTPROCESS_PREFIX = "LTPP"
# Audio extraction (stream-copy) after the download has no fraction of its own,
# so the reported download fraction is capped just below completion.
_DOWNLOAD_FRACTION_CEILING = 0.99

YOUTUBE_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)(?:/|$)",
    re.IGNORECASE,
)


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_RE.match(url))


def detect_link_source_type(url: str) -> str:
    """Coarse platform label for a URL; matches the dispatch order of download_audio()."""
    if is_frameio_url(url):
        return "frameio"
    if is_yandex_disk_url(url):
        return "yandex_disk"
    if is_instagram_url(url):
        return "instagram"
    if is_facebook_url(url):
        return "facebook"
    if is_yandex_music_url(url):
        return "yandex_music"
    if is_youtube_url(url):
        return "youtube"
    return "link"


def _clean(value: object) -> Optional[str]:
    """Normalise yt-dlp/string values: drop None, empty, 'NA' sentinels."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "NA":
        return None
    return text


async def download_audio(
    url: str,
    output_dir: str,
    on_progress_fraction: Optional[FractionCallback] = None,
    on_postprocess: Optional[PostprocessCallback] = None,
) -> tuple[str, SourceMetadata]:
    """Download audio from a URL and return (local_path, source_metadata).

    ``on_progress_fraction`` receives download progress 0..1 for yt-dlp
    sources; ``on_postprocess`` fires once when yt-dlp starts extracting the
    audio track. Other branches (Yandex Disk, Instagram, Facebook) do not
    report progress.
    """
    if is_frameio_url(url):
        media = await resolve_frameio_media(url)
        source_meta = SourceMetadata(title=_clean(media.name), title_is_filename=True)
        if media.hls_manifest:
            # The share API hands out an HLS master with an audio-only rendition;
            # yt-dlp's ``-f bestaudio/best`` picks it, so only audio is fetched
            # and the fragment-based progress bar works as for any HLS source.
            # yt-dlp's generic title for a manifest is "main" — ignored.
            path, _ = await _download_with_ytdlp(
                media.hls_manifest,
                output_dir,
                on_progress_fraction=on_progress_fraction,
                on_postprocess=on_postprocess,
            )
            return path, source_meta
        raw_path = await download_frameio_original(media, output_dir)
        try:
            return await extract_audio(raw_path, output_dir), source_meta
        finally:
            if os.path.exists(raw_path):
                os.unlink(raw_path)

    if is_yandex_disk_url(url):
        raw_path, title = await download_from_yandex_disk(url, output_dir)
        try:
            audio = await extract_audio(raw_path, output_dir)
            return audio, SourceMetadata(title=_clean(title), title_is_filename=True)
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
        except YandexMusicNotPodcastError as e:
            # Expected control flow: the link is Yandex Music but not a podcast
            # episode, so the yt-dlp fallback below is the right next step.
            logger.debug("Yandex Music episode extractor skipped: %s", e)
        except RuntimeError as e:
            # A real extractor failure (dead API endpoint, captcha, HTTP error).
            # Don't swallow it silently — log before falling back to yt-dlp so
            # the actual cause stays diagnosable.
            logger.warning("Yandex Music episode extractor failed, "
                           "falling back to yt-dlp: %s", e)

        try:
            return await _download_with_ytdlp(
                url,
                output_dir,
                proxy=settings.YANDEX_MUSIC_PROXY or settings.YTDLP_PROXY,
                on_progress_fraction=on_progress_fraction,
                on_postprocess=on_postprocess,
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

    try:
        return await _download_with_ytdlp(
            url,
            output_dir,
            proxy=settings.YTDLP_PROXY,
            on_progress_fraction=on_progress_fraction,
            on_postprocess=on_postprocess,
        )
    except RuntimeError as e:
        # Cookies expose a real Google account (YouTube flags accounts used by
        # yt-dlp), so they are sent only when anonymous access is refused for
        # age verification — never for videos that work without sign-in.
        cookies_file = _youtube_cookies_file(url, e)
        if not cookies_file:
            raise
        logger.info("YouTube asked for age confirmation, retrying with cookies")

    return await _download_with_ytdlp(
        url,
        output_dir,
        proxy=settings.YTDLP_PROXY,
        cookies_file=cookies_file,
        on_progress_fraction=on_progress_fraction,
        on_postprocess=on_postprocess,
    )


_AGE_GATE_MARKER = "Sign in to confirm your age"


def _youtube_cookies_file(url: str, error: Exception) -> Optional[str]:
    """Cookies path if the failure is a YouTube age gate and a file is configured."""
    path = settings.YTDLP_COOKIES_FILE
    if not path or not is_youtube_url(url) or _AGE_GATE_MARKER not in str(error):
        return None
    if not os.path.isfile(path):
        logger.warning("YTDLP_COOKIES_FILE is set but not found: %s", path)
        return None
    return path


def parse_progress_line(line: str) -> Optional[float]:
    """Parse a '--progress-template' line into a 0..1 fraction.

    Expected form: 'LTPROG <downloaded_bytes> <total_bytes> <fragment_index>
    <fragment_count>' where any field may be 'NA'. Returns None for
    non-progress lines and whenever no trustworthy number is available — the
    caller then shows an indeterminate bar rather than a misleading percent.

    Fragmented downloads (YouTube DASH/HLS) restart the byte counters on every
    fragment and only expose an undersized total_bytes_estimate, so whole-file
    progress is taken from fragment_index / fragment_count when present. Plain
    HTTP files fall back to downloaded_bytes / total_bytes, but only when
    total_bytes is a real value — the estimate is never used as a denominator.
    """
    parts = line.strip().split()
    if len(parts) != 5 or parts[0] != PROGRESS_PREFIX:
        return None
    downloaded, total, frag_index, frag_count = parts[1:]
    try:
        if frag_index != "NA" and frag_count != "NA":
            index = float(frag_index)
            count = float(frag_count)
            if count > 0 and index >= 0:
                return min(index / count, 1.0)
        if total != "NA":
            done = float(downloaded)
            size = float(total)
            if size > 0 and done >= 0:
                return min(done / size, 1.0)
    except ValueError:
        return None
    return None


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


def _build_ytdlp_cmd(
    url: str,
    out_path: str,
    proxy: Optional[str],
    cookies_file: Optional[str] = None,
) -> list[str]:
    """Assemble the yt-dlp argument list (kept pure so it can be unit-tested).

    ``-f bestaudio/best`` prefers an audio-only stream; ``--extract-audio
    --audio-format best`` extracts the track by stream copy (no re-encode), so
    only audio — never the full video — is uploaded downstream. A second
    progress-template exposes the postprocess (extraction) stage.
    """
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "best",
        "--output", out_path,
        "--print", "after_move:%(.{title,uploader,channel})j",
        "--quiet",
        "--progress",
        "--newline",
        "--progress-template",
        f"download:{PROGRESS_PREFIX} %(progress.downloaded_bytes)s"
        " %(progress.total_bytes)s"
        " %(progress.fragment_index)s %(progress.fragment_count)s",
        "--progress-template",
        f"postprocess:{POSTPROCESS_PREFIX} %(progress.status)s",
    ]
    if proxy:
        cmd.extend(["--proxy", proxy])
    if cookies_file:
        cmd.extend(["--cookies", cookies_file])
    cmd.append(url)
    return cmd


async def _dispatch_progress_line(
    line: str,
    keep: list[str],
    state: dict,
    on_progress_fraction: Optional[FractionCallback],
    on_postprocess: Optional[PostprocessCallback],
) -> None:
    """Route one yt-dlp output line: postprocess signal, progress, or keep.

    ``state`` carries cross-stream, cross-line values: ``last_fraction`` (the
    reported fraction is kept monotonic — fragmented downloads restart byte
    counters) and ``postprocess_fired`` (fire ``on_postprocess`` only once).
    """
    if line.startswith(POSTPROCESS_PREFIX):
        if on_postprocess is not None and not state["postprocess_fired"]:
            state["postprocess_fired"] = True
            with suppress(Exception):
                await on_postprocess()
        return
    fraction = parse_progress_line(line)
    if fraction is None:
        # Non-progress line (yt-dlp error text, or the after_move meta JSON that
        # prints *after* postprocessing) — always keep it.
        keep.append(line)
    elif (
        not state["postprocess_fired"]
        and on_progress_fraction is not None
        and fraction > state["last_fraction"]
    ):
        # stdout (download) and stderr (postprocess) are read by independent
        # coroutines, so a buffered download line can arrive after
        # postprocessing began. Reporting a fraction then re-determinates the
        # bar and undoes the animated "extracting audio" phase — so drop it.
        state["last_fraction"] = fraction
        with suppress(Exception):
            await on_progress_fraction(min(fraction, _DOWNLOAD_FRACTION_CEILING))


async def _download_with_ytdlp(
    url: str,
    output_dir: str,
    proxy: Optional[str] = None,
    cookies_file: Optional[str] = None,
    on_progress_fraction: Optional[FractionCallback] = None,
    on_postprocess: Optional[PostprocessCallback] = None,
) -> tuple[str, SourceMetadata]:
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.%(ext)s")

    cmd = _build_ytdlp_cmd(url, out_path, proxy, cookies_file)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Default StreamReader line limit is 64 KiB; a verbose yt-dlp error
        # dump on one line would raise ValueError from readline(). 1 MiB is
        # far beyond any real yt-dlp line yet still bounded.
        limit=1024 * 1024,
    )

    # Shared across both stream readers: monotonic fraction + one-shot
    # postprocess flag (yt-dlp may write progress to stdout or stderr).
    state = {"last_fraction": 0.0, "postprocess_fired": False}

    async def _read_stream(stream: asyncio.StreamReader, keep: list[str]) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\n")
            await _dispatch_progress_line(
                line, keep, state, on_progress_fraction, on_postprocess
            )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    try:
        await asyncio.gather(
            _read_stream(proc.stdout, stdout_lines),
            _read_stream(proc.stderr, stderr_lines),
        )
        await proc.wait()
    except BaseException:
        # Any failure — cancellation, a readline limit error, etc. — must not
        # leak the yt-dlp child: kill it and reap so it can't block on a full
        # pipe and hang as a zombie.
        proc.kill()
        with suppress(ProcessLookupError):
            await proc.wait()
        raise

    if proc.returncode != 0:
        stderr_text = "\n".join(stderr_lines).strip()
        raise RuntimeError(
            f"yt-dlp failed (code {proc.returncode}): {stderr_text}"
        )

    meta = _parse_ytdlp_meta("\n".join(stdout_lines).encode())

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
