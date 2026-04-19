import pytest

import bot.services.ffmpeg_runner as ffmpeg_module


class _FakeProcess:
    def __init__(self, returncode: int):
        self.returncode = returncode

    async def communicate(self):
        return None


@pytest.mark.asyncio
async def test_run_ffmpeg_invokes_common_process_settings(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProcess(0)

    monkeypatch.setattr(ffmpeg_module.asyncio, "create_subprocess_exec", fake_exec)

    await ffmpeg_module.run_ffmpeg("-i", "input.mp4", "output.mp3")

    args, kwargs = calls[0]
    assert args == ("ffmpeg", "-y", "-i", "input.mp4", "output.mp3")
    assert kwargs["stdout"] == ffmpeg_module.asyncio.subprocess.DEVNULL
    assert kwargs["stderr"] == ffmpeg_module.asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_run_ffmpeg_raises_on_nonzero_exit(monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return _FakeProcess(7)

    monkeypatch.setattr(ffmpeg_module.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="ffmpeg failed with code 7"):
        await ffmpeg_module.run_ffmpeg("-i", "broken.mp4", "broken.mp3")
