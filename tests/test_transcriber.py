"""Tests for bot.services.transcriber — fake-progress loop and chunked path."""
import asyncio

import pytest

import bot.services.transcriber as transcriber_module
import bot.utils.fake_progress as fake_progress_module
from bot.services.transcriber import MAX_WHISPER_BYTES, transcribe


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

async def _fast_transcribe(path: str) -> str:
    """Simulates a quick Whisper API call."""
    await asyncio.sleep(0.05)
    return "ok"


def _make_audio_file(tmp_path, size: int) -> str:
    p = tmp_path / "audio.mp3"
    p.write_bytes(b"\0" * size)
    return str(p)


# ---------------------------------------------------------------------------
# Non-chunked path: fake-progress loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fake_progress_called_multiple_times(tmp_path, monkeypatch):
    """on_progress_fraction must be called ≥2 times: at least one tick + final 1.0."""
    monkeypatch.setattr(transcriber_module, "_transcribe_file", _fast_transcribe)
    # Speed up: very small expected time so ticks fire quickly
    monkeypatch.setattr(transcriber_module, "FAKE_RATE_BYTES_PER_SEC", 1)  # 1 byte/s → ~0s expected
    monkeypatch.setattr(transcriber_module, "FAKE_TICK_SECONDS", 0.01)

    calls: list[float] = []
    async def collect(f: float) -> None:
        calls.append(f)

    audio = _make_audio_file(tmp_path, 1024)
    result = await transcribe(audio, on_progress_fraction=collect)

    assert result == "ok"
    assert len(calls) >= 2
    assert calls[-1] == 1.0


@pytest.mark.asyncio
async def test_fake_progress_values_monotonic_and_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(transcriber_module, "_transcribe_file", _fast_transcribe)
    monkeypatch.setattr(transcriber_module, "FAKE_RATE_BYTES_PER_SEC", 1)
    monkeypatch.setattr(transcriber_module, "FAKE_TICK_SECONDS", 0.01)
    monkeypatch.setattr(transcriber_module, "FAKE_CEILING", 0.95)

    calls: list[float] = []
    async def collect(f: float) -> None:
        calls.append(f)

    audio = _make_audio_file(tmp_path, 1024)
    await transcribe(audio, on_progress_fraction=collect)

    intermediate = calls[:-1]  # exclude final 1.0
    assert all(v <= 0.95 for v in intermediate), f"intermediate exceeded ceiling: {intermediate}"
    # Monotonically non-decreasing
    for a, b in zip(intermediate, intermediate[1:]):
        assert a <= b, f"not monotonic: {a} > {b}"


@pytest.mark.asyncio
async def test_fake_progress_not_called_without_callback(tmp_path, monkeypatch):
    """Without on_progress_fraction nothing should be started."""
    called = []
    original = transcriber_module._fake_progress_loop

    async def spy(*args, **kwargs):
        called.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(transcriber_module, "_transcribe_file", _fast_transcribe)
    monkeypatch.setattr(transcriber_module, "_fake_progress_loop", spy)

    audio = _make_audio_file(tmp_path, 1024)
    await transcribe(audio)  # no callback

    assert called == [], "_fake_progress_loop should not be called without callback"


@pytest.mark.asyncio
async def test_transcribe_returns_correct_text(tmp_path, monkeypatch):
    monkeypatch.setattr(transcriber_module, "_transcribe_file",
                        lambda _: asyncio.coroutine(lambda: "hello world")())

    async def _ret(_path):
        return "hello world"

    monkeypatch.setattr(transcriber_module, "_transcribe_file", _ret)

    audio = _make_audio_file(tmp_path, 512)
    result = await transcribe(audio)
    assert result == "hello world"


# ---------------------------------------------------------------------------
# Chunked path: on_progress smoke test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chunked_path_calls_on_progress(tmp_path, monkeypatch):
    """For files >MAX_WHISPER_BYTES, on_progress must be called N+1 times (0/N then i+1/N)."""

    async def _fake_split(_path: str) -> list[str]:
        # Return 3 fake chunk files
        chunks = []
        for i in range(3):
            p = tmp_path / f"chunk_{i:03d}.mp3"
            p.write_bytes(b"\0" * 10)
            chunks.append(str(p))
        return chunks

    monkeypatch.setattr(transcriber_module, "_split_audio", _fake_split)
    monkeypatch.setattr(transcriber_module, "_transcribe_file",
                        lambda _: asyncio.coroutine(lambda: "part")())

    async def _ret(_path):
        return "part"

    monkeypatch.setattr(transcriber_module, "_transcribe_file", _ret)

    calls: list[tuple[int, int]] = []
    async def collect(current: int, total: int) -> None:
        calls.append((current, total))

    # File must be > MAX_WHISPER_BYTES to trigger chunked path
    audio = _make_audio_file(tmp_path, MAX_WHISPER_BYTES + 1)
    result = await transcribe(audio, on_progress=collect)

    assert result == "part part part"
    assert calls[0] == (0, 3)      # initial (0, N)
    assert calls[-1] == (3, 3)     # final (N, N)
    assert len(calls) == 4         # (0,3), (1,3), (2,3), (3,3)


@pytest.mark.asyncio
async def test_chunked_path_runs_in_parallel_and_preserves_order(
    tmp_path, monkeypatch
):
    """Parallelism: total time ≈ longest chunk, not sum of chunks.

    Also: concatenated result must match chunk order (0,1,2), even though the
    first-finished task can be any of them.
    """
    chunk_count = 3

    async def _fake_split(_path: str) -> list[str]:
        chunks = []
        for i in range(chunk_count):
            p = tmp_path / f"chunk_{i:03d}.mp3"
            p.write_bytes(b"\0" * 10)
            chunks.append(str(p))
        return chunks

    # Each chunk takes 0.2s. Serial: ~0.6s. Parallel (WHISPER_PARALLELISM=3): ~0.2s.
    per_chunk_sleep = 0.2

    async def _slow_transcribe(path: str) -> str:
        await asyncio.sleep(per_chunk_sleep)
        # Encode chunk index in the result so we can verify ordering.
        idx = int(path.rsplit("_", 1)[1].split(".")[0])
        return f"part{idx}"

    monkeypatch.setattr(transcriber_module, "_split_audio", _fake_split)
    monkeypatch.setattr(transcriber_module, "_transcribe_file", _slow_transcribe)

    audio = _make_audio_file(tmp_path, MAX_WHISPER_BYTES + 1)

    started = asyncio.get_event_loop().time()
    result = await transcribe(audio)
    elapsed = asyncio.get_event_loop().time() - started

    # Order is preserved.
    assert result == "part0 part1 part2"
    # Wall-clock should be much closer to one chunk than to the sum.
    assert elapsed < per_chunk_sleep * chunk_count * 0.8, (
        f"expected parallel execution (~{per_chunk_sleep}s), "
        f"got {elapsed:.3f}s (serial would be ~{per_chunk_sleep * chunk_count}s)"
    )


@pytest.mark.asyncio
async def test_chunked_path_unlinks_chunks_even_on_failure(tmp_path, monkeypatch):
    """A failing chunk must not leak the other chunks' temp files."""
    chunks = []
    for i in range(3):
        p = tmp_path / f"chunk_{i:03d}.mp3"
        p.write_bytes(b"\0" * 10)
        chunks.append(str(p))

    async def _fake_split(_path: str) -> list[str]:
        return list(chunks)

    async def _transcribe(path: str) -> str:
        if path.endswith("001.mp3"):
            raise RuntimeError("whisper exploded")
        await asyncio.sleep(0.05)
        return "ok"

    monkeypatch.setattr(transcriber_module, "_split_audio", _fake_split)
    monkeypatch.setattr(transcriber_module, "_transcribe_file", _transcribe)

    audio = _make_audio_file(tmp_path, MAX_WHISPER_BYTES + 1)
    with pytest.raises(RuntimeError, match="whisper exploded"):
        await transcribe(audio)

    # All three chunk files must be gone regardless of which task failed.
    for chunk in chunks:
        assert not __import__("os").path.exists(chunk), f"{chunk} leaked"


@pytest.mark.asyncio
async def test_split_audio_raises_when_ffmpeg_produces_no_chunks(tmp_path, monkeypatch):
    audio = _make_audio_file(tmp_path, 1024)
    calls = []

    async def fake_run_ffmpeg(*args):
        calls.append(args)

    monkeypatch.setattr(transcriber_module, "run_ffmpeg", fake_run_ffmpeg)

    with pytest.raises(RuntimeError, match="ffmpeg did not produce audio chunks"):
        await transcriber_module._split_audio(audio)

    args = calls[0]
    assert args[:2] == ("-i", audio)
    assert args[args.index("-f") + 1] == "segment"
    assert args[args.index("-segment_time") + 1] == str(transcriber_module.CHUNK_DURATION_SECONDS)
    assert args[-1].endswith("_chunk_%03d.mp3")


@pytest.mark.asyncio
async def test_split_audio_cleans_partial_chunks_after_ffmpeg_failure(tmp_path, monkeypatch):
    audio = _make_audio_file(tmp_path, 1024)
    partial_a = tmp_path / "audio_chunk_000.mp3"
    partial_b = tmp_path / "audio_chunk_001.mp3"

    async def fake_run_ffmpeg(*_args):
        partial_a.write_bytes(b"a")
        partial_b.write_bytes(b"b")
        raise RuntimeError("ffmpeg failed with code 1")

    monkeypatch.setattr(transcriber_module, "run_ffmpeg", fake_run_ffmpeg)

    with pytest.raises(RuntimeError, match="ffmpeg failed with code 1"):
        await transcriber_module._split_audio(audio)

    assert not partial_a.exists()
    assert not partial_b.exists()
