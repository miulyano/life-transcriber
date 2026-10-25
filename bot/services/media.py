from __future__ import annotations

import asyncio
import os
import uuid

from bot.services.ffmpeg_runner import run_ffmpeg

# Битрейт mp3 для транскрибации. Замер 2026-10-25: libmp3lame при 16 kHz mono
# без явного -b:a даёт ровно 24 kbps — закрепляем, чтобы оценка места под
# выход (estimate_transcription_audio_bytes) считалась от той же константы.
TRANSCRIPTION_AUDIO_BITRATE_KBPS = 24
# Запас на заголовки/паддинг кадров: замер 60 с → 180 296 байт при
# «чистых» 180 000 (0.2 %); берём 5 % + 64 КБ.
_ESTIMATE_MARGIN = 1.05
_ESTIMATE_FIXED_BYTES = 64 * 1024


def estimate_transcription_audio_bytes(duration_sec: float) -> int:
    """Верхняя оценка размера mp3, который сделает prepare_audio_for_transcription."""
    raw = max(duration_sec, 0.0) * TRANSCRIPTION_AUDIO_BITRATE_KBPS * 1000 / 8
    return int(raw * _ESTIMATE_MARGIN) + _ESTIMATE_FIXED_BYTES


async def probe_duration(path: str) -> float | None:
    """Media duration in seconds via ffprobe; None if it cannot be determined."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        duration = float(stdout.decode().strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


async def prepare_audio_for_transcription(input_path: str, output_dir: str) -> str:
    """Convert any supported media file to compact audio-only mp3."""
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp3")

    try:
        await run_ffmpeg(
            "-i",
            input_path,
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-acodec",
            "mp3",
            "-b:a",
            f"{TRANSCRIPTION_AUDIO_BITRATE_KBPS}k",
            out_path,
        )
    except RuntimeError:
        if os.path.exists(out_path):
            os.unlink(out_path)
        raise
    return out_path
