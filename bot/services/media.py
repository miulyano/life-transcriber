from __future__ import annotations

import asyncio
import os
import uuid

from bot.services.ffmpeg_runner import run_ffmpeg


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
            out_path,
        )
    except RuntimeError:
        if os.path.exists(out_path):
            os.unlink(out_path)
        raise
    return out_path
