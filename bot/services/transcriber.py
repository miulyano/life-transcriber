import asyncio
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

from bot.config import settings
from bot.services.ffmpeg_runner import run_ffmpeg
from bot.utils.fake_progress import FractionCallback

MAX_WHISPER_BYTES = 24 * 1024 * 1024  # 24 MB (Whisper limit is 25 MB)
CHUNK_DURATION_SECONDS = 600  # 10 minutes per chunk

# How many Whisper chunk requests run concurrently. 3 keeps us well under
# OpenAI's default Whisper rate limits and avoids opening too many sockets /
# holding too many file handles at once, while eliminating the wall-clock
# penalty of strictly-sequential per-chunk awaits on long inputs.
WHISPER_PARALLELISM = 3

# Fake-progress rate estimate for non-chunked Whisper calls (no real progress signal).
FAKE_RATE_BYTES_PER_SEC = 200_000
FAKE_TICK_SECONDS = 0.5
FAKE_CEILING = 0.95

ProgressCallback = Callable[[int, int], Awaitable[None]]

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def transcribe(
    audio_path: str,
    on_progress: Optional[ProgressCallback] = None,
    on_progress_fraction: Optional[FractionCallback] = None,
) -> str:
    file_size = os.path.getsize(audio_path)
    if file_size > MAX_WHISPER_BYTES:
        chunks = await _split_audio(audio_path)
        parts = await _transcribe_chunks_parallel(chunks, on_progress)
        return " ".join(parts)

    if on_progress_fraction is None:
        return await _transcribe_file(audio_path)

    expected_seconds = file_size / FAKE_RATE_BYTES_PER_SEC
    return await _run_with_fake_progress(
        _transcribe_file(audio_path),
        on_progress_fraction,
        expected_seconds,
    )


async def _fake_progress_loop(
    done: asyncio.Event,
    on_progress_fraction: FractionCallback,
    expected_seconds: float,
) -> None:
    start = time.monotonic()
    while not done.is_set():
        elapsed = time.monotonic() - start
        fraction = min(FAKE_CEILING, elapsed / expected_seconds)
        with suppress(Exception):
            await on_progress_fraction(fraction)
        try:
            await asyncio.wait_for(done.wait(), timeout=FAKE_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _run_with_fake_progress(
    coro: Awaitable,
    on_progress_fraction: FractionCallback,
    expected_seconds: float,
):
    expected_seconds = max(3.0, expected_seconds)
    done = asyncio.Event()
    task = asyncio.create_task(
        _fake_progress_loop(done, on_progress_fraction, expected_seconds)
    )
    try:
        result = await coro
    finally:
        done.set()
        with suppress(Exception):
            await task
    with suppress(Exception):
        await on_progress_fraction(1.0)
    return result


async def _transcribe_chunks_parallel(
    chunks: list[str], on_progress: Optional[ProgressCallback]
) -> list[str]:
    """Transcribe chunks concurrently (bounded by ``WHISPER_PARALLELISM``).

    Chunk files are unlinked as soon as their transcription finishes (or
    fails), so a crash in one task does not leave the others' temp files
    behind. ``on_progress`` is invoked by completion order, not chunk index;
    the final ``(total, total)`` report is still guaranteed because every
    task either completes or cancels before this function returns.

    If any chunk fails, the remaining in-flight tasks are cancelled and we
    wait for them to run their cleanup (``finally: os.unlink``) before
    re-raising — otherwise Python's default ``asyncio.gather`` would leave
    siblings orphaned and their chunk files would leak.
    """
    total = len(chunks)
    if on_progress is not None:
        await on_progress(0, total)

    semaphore = asyncio.Semaphore(WHISPER_PARALLELISM)
    done_count = 0
    lock = asyncio.Lock()

    async def run(chunk: str) -> str:
        nonlocal done_count
        try:
            async with semaphore:
                text = await _transcribe_file(chunk)
        finally:
            with suppress(OSError):
                os.unlink(chunk)
        if on_progress is not None:
            async with lock:
                done_count += 1
                current = done_count
            await on_progress(current, total)
        return text

    tasks = [asyncio.create_task(run(c)) for c in chunks]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            if not t.done():
                t.cancel()
        # Let every task finish its `finally` before we re-raise — otherwise
        # cancelled tasks are still running in the background when the caller
        # checks for leftover files.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


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
    chunk_glob = f"{base}_chunk_*.mp3"

    try:
        await run_ffmpeg(
            "-i",
            audio_path,
            "-f",
            "segment",
            "-segment_time",
            str(CHUNK_DURATION_SECONDS),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-acodec",
            "mp3",
            pattern,
        )
    except RuntimeError:
        for chunk in out_dir.glob(chunk_glob):
            with suppress(OSError):
                chunk.unlink()
        raise

    chunks = sorted(out_dir.glob(chunk_glob))
    if not chunks:
        raise RuntimeError("ffmpeg did not produce audio chunks")
    return [str(c) for c in chunks]
