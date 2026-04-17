from pathlib import Path
import re

import pytest
from aioresponses import aioresponses

import bot.services.downloader as downloader_module
from bot.services.yandex_music import (
    ALBUM_API_URL,
    YandexMusicNotPodcastError,
    YANDEX_MUSIC_EPISODE_URL_RE,
    YANDEX_MUSIC_URL_RE,
    download_podcast_episode_from_yandex_music,
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


def _album_url(album_id: str) -> str:
    return f"{ALBUM_API_URL}?album={album_id}"


@pytest.mark.asyncio
async def test_download_podcast_episode_from_yandex_music_uses_rss(tmp_path):
    public_key = "https://music.yandex.ru/album/31008129/track/148046791"
    feed_url = "https://cloud.mave.digital/54896"
    enclosure_url = "https://cdn.example.com/episode.mp3"

    with aioresponses() as m:
        m.get(
            _album_url("31008129"),
            payload={
                "id": 31008129,
                "title": "Куда расти?",
                "type": "podcast",
                "metaType": "podcast",
                "volumes": [[{
                    "id": "148046791",
                    "realId": "148046791",
                    "title": (
                        "Что будет с разработкой в 2029? Артём Арюткин про "
                        "платформу Авито и DevEx"
                    ),
                    "type": "podcast-episode",
                }]],
            },
        )
        m.get(
            re.compile(r"^https://itunes\.apple\.com/search\?.*"),
            payload={
                "resultCount": 1,
                "results": [{
                    "collectionName": "Куда расти?",
                    "feedUrl": feed_url,
                }],
            },
        )
        m.get(
            feed_url,
            body=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<rss><channel><item>"
                "<title><![CDATA[Что будет с разработкой в 2029? "
                "Артём Арюткин про платформу Авито и DevEx]]></title>"
                f'<enclosure url="{enclosure_url}" type="audio/mpeg" />'
                "</item></channel></rss>"
            ),
        )
        m.get(enclosure_url, body=b"audio")

        path = await download_podcast_episode_from_yandex_music(
            public_key,
            str(tmp_path),
        )

    assert Path(path).exists()
    assert Path(path).suffix == ".mp3"
    assert Path(path).read_bytes() == b"audio"


@pytest.mark.asyncio
async def test_download_audio_uses_ytdlp_for_yandex_music_episode(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(downloader_module.settings, "YANDEX_MUSIC_PROXY", None)
    monkeypatch.setattr(downloader_module.settings, "YTDLP_PROXY", None)

    async def fake_download_podcast(*_args, **_kwargs):
        raise YandexMusicNotPodcastError("not a podcast")

    monkeypatch.setattr(
        downloader_module,
        "download_podcast_episode_from_yandex_music",
        fake_download_podcast,
    )

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
async def test_download_audio_uses_rss_for_yandex_music_podcast(tmp_path, monkeypatch):
    audio_path = tmp_path / "episode.mp3"

    async def fake_download_podcast(url, output_dir):
        audio_path.write_bytes(b"audio")
        assert url == "https://music.yandex.ru/album/9091882/track/60513409"
        assert output_dir == str(tmp_path)
        return str(audio_path)

    async def fake_exec(*_args, **_kwargs):
        raise AssertionError("yt-dlp should not be called")

    monkeypatch.setattr(
        downloader_module,
        "download_podcast_episode_from_yandex_music",
        fake_download_podcast,
    )
    monkeypatch.setattr(
        downloader_module.asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    result = await downloader_module.download_audio(
        "https://music.yandex.ru/album/9091882/track/60513409",
        str(tmp_path),
    )

    assert result == str(audio_path)


@pytest.mark.asyncio
async def test_download_audio_uses_yandex_music_proxy(tmp_path, monkeypatch):
    calls = []
    proxy = "socks5://proxy.example:1080"
    monkeypatch.setattr(downloader_module.settings, "YANDEX_MUSIC_PROXY", proxy)
    monkeypatch.setattr(downloader_module.settings, "YTDLP_PROXY", "http://global-proxy")

    async def fake_download_podcast(*_args, **_kwargs):
        raise YandexMusicNotPodcastError("not a podcast")

    monkeypatch.setattr(
        downloader_module,
        "download_podcast_episode_from_yandex_music",
        fake_download_podcast,
    )

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

    await downloader_module.download_audio(
        "https://music.yandex.ru/album/9091882/track/60513409",
        str(tmp_path),
    )

    args, _kwargs = calls[0]
    assert args[args.index("--proxy") + 1] == proxy


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
    monkeypatch.setattr(downloader_module.settings, "YANDEX_MUSIC_PROXY", None)
    monkeypatch.setattr(downloader_module.settings, "YTDLP_PROXY", None)

    async def fake_download_podcast(*_args, **_kwargs):
        raise YandexMusicNotPodcastError("not a podcast")

    monkeypatch.setattr(
        downloader_module,
        "download_podcast_episode_from_yandex_music",
        fake_download_podcast,
    )

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


@pytest.mark.asyncio
async def test_download_audio_reports_yandex_music_region_block(tmp_path, monkeypatch):
    class RegionBlockedProcess:
        returncode = 1

        async def communicate(self):
            return b"", b"HTTP Error 451: Unavailable For Legal Reasons"

    monkeypatch.setattr(downloader_module.settings, "YANDEX_MUSIC_PROXY", None)
    monkeypatch.setattr(downloader_module.settings, "YTDLP_PROXY", None)

    async def fake_download_podcast(*_args, **_kwargs):
        raise RuntimeError("rss failed")

    monkeypatch.setattr(
        downloader_module,
        "download_podcast_episode_from_yandex_music",
        fake_download_podcast,
    )

    async def fake_exec(*_args, **_kwargs):
        return RegionBlockedProcess()

    monkeypatch.setattr(
        downloader_module.asyncio,
        "create_subprocess_exec",
        fake_exec,
    )

    with pytest.raises(RuntimeError, match=r"^yandex-music:.*региона сервера"):
        await downloader_module.download_audio(
            "https://music.yandex.ru/album/9091882/track/60513409",
            str(tmp_path),
        )
