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
