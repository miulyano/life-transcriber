import asyncio
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

from bot.config import settings

MAX_WHISPER_BYTES = 24 * 1024 * 1024  # 24 MB (Whisper limit is 25 MB)
CHUNK_DURATION_SECONDS = 600  # 10 minutes per chunk

ProgressCallback = Callable[[int, int], Awaitable[None]]

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def transcribe(
    audio_path: str,
    on_progress: Optional[ProgressCallback] = None,
) -> str:
    file_size = os.path.getsize(audio_path)
    if file_size > MAX_WHISPER_BYTES:
        chunks = await _split_audio(audio_path)
        total = len(chunks)
        if on_progress is not None:
            await on_progress(0, total)
        parts = []
        for i, chunk in enumerate(chunks):
            try:
                parts.append(await _transcribe_file(chunk))
            finally:
                os.unlink(chunk)
            if on_progress is not None:
                await on_progress(i + 1, total)
        return " ".join(parts)
    return await _transcribe_file(audio_path)


async def _transcribe_file(path: str) -> str:
    with open(path, "rb") as f:
        response = await client.audio.transcriptions.create(
            model=settings.WHISPER_MODEL,
            file=f,
            response_format="text",
        )
    return response.strip()


async def _split_audio(audio_path: str) -> list[str]:
    base = Path(audio_path).stem
    out_dir = Path(audio_path).parent
    pattern = str(out_dir / f"{base}_chunk_%03d.mp3")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", audio_path,
        "-f", "segment",
        "-segment_time", str(CHUNK_DURATION_SECONDS),
        "-ar", "16000",
        "-ac", "1",
        "-acodec", "mp3",
        pattern,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()

    chunks = sorted(out_dir.glob(f"{base}_chunk_*.mp3"))
    return [str(c) for c in chunks]
