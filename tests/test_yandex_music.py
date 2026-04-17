from pathlib import Path

import pytest

import bot.services.downloader as downloader_module
from bot.services.yandex_music import (
    YANDEX_MUSIC_EPISODE_URL_RE,
    YANDEX_MUSIC_URL_RE,
    is_yandex_music_episode_url,
    is_yandex_music_url,
)


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


class _FailedProcess:
    returncode = 1

    async def communicate(self):
        return b"", b"captcha"


@pytest.mark.parametrize("url", [
    "https://music.yandex.ru/album/9091882/track/60513409",
    "https://music.yandex.ru/album/9091882/track/60513409/",
    "https://music.yandex.ru/album/9091882/track/60513409?utm_source=share",
    "http://music.yandex.com/album/9091882/track/60513409",
    "https://music.yandex.kz/album/9091882/track/60513409",
    "HTTPS://MUSIC.YANDEX.RU/album/9091882/track/60513409",
])
def test_yandex_music_episode_url_detected(url):
    assert is_yandex_music_url(url)
    assert is_yandex_music_episode_url(url)
    assert YANDEX_MUSIC_URL_RE.match(url) is not None
    assert YANDEX_MUSIC_EPISODE_URL_RE.match(url) is not None


@pytest.mark.parametrize("url", [
    "https://music.yandex.ru/album/9091882",
    "https://music.yandex.ru/users/user/playlists/123",
    "https://music.yandex.ru/artist/123/tracks",
    "https://disk.yandex.ru/d/abc",
    "https://example.com/music.yandex.ru/album/1/track/2",
    "",
])
def test_non_yandex_music_episode_url_rejected(url):
    assert not is_yandex_music_episode_url(url)


@pytest.mark.asyncio
async def test_download_audio_uses_ytdlp_for_yandex_music_episode(
    tmp_path, monkeypatch
):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        out_pattern = args[args.index("--output") + 1]
        Path(out_pattern.replace("%(ext)s", "mp3")).write_bytes(b"audio")
        return _FakeProcess()

    monkeypatch.setattr(
        downloader_module.asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    url = "https://music.yandex.ru/album/9091882/track/60513409"
    path = await downloader_module.download_audio(url, str(tmp_path))

    assert Path(path).exists()
    assert Path(path).suffix == ".mp3"
    args, kwargs = calls[0]
    assert args[0] == "yt-dlp"
    assert "--extract-audio" in args
    assert url in args
    assert kwargs["stdout"] == downloader_module.asyncio.subprocess.PIPE
    assert kwargs["stderr"] == downloader_module.asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_download_audio_rejects_yandex_music_album_url(tmp_path, monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        raise AssertionError("yt-dlp should not be called")

    monkeypatch.setattr(
        downloader_module.asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    with pytest.raises(RuntimeError, match=r"^yandex-music:.*конкретный выпуск"):
        await downloader_module.download_audio(
            "https://music.yandex.ru/album/9091882",
            str(tmp_path),
        )


@pytest.mark.asyncio
async def test_download_audio_wraps_yandex_music_ytdlp_error(tmp_path, monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return _FailedProcess()

    monkeypatch.setattr(
        downloader_module.asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    with pytest.raises(RuntimeError, match=r"^yandex-music:.*не удалось скачать"):
        await downloader_module.download_audio(
            "https://music.yandex.ru/album/9091882/track/60513409",
            str(tmp_path),
        )
