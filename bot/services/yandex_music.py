from __future__ import annotations

import os
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode, urlparse

import aiohttp

ALBUM_API_URL = "https://music.yandex.ru/handlers/album.jsx"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
HTTP_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MB

YANDEX_MUSIC_URL_RE = re.compile(
    r"^https?://music\.yandex\.(?:ru|com|kz|by|ua)/\S+",
    re.IGNORECASE,
)

YANDEX_MUSIC_EPISODE_URL_RE = re.compile(
    r"^https?://music\.yandex\.(?:ru|com|kz|by|ua)/album/(?P<album_id>\d+)/track/(?P<track_id>\d+)(?:[/?#]\S*)?$",
    re.IGNORECASE,
)


class YandexMusicNotPodcastError(RuntimeError):
    pass


def is_yandex_music_url(url: str) -> bool:
    return bool(YANDEX_MUSIC_URL_RE.match(url))


def is_yandex_music_episode_url(url: str) -> bool:
    return bool(YANDEX_MUSIC_EPISODE_URL_RE.match(url))


async def download_podcast_episode_from_yandex_music(
    url: str,
    output_dir: str,
) -> tuple[str, str | None]:
    match = YANDEX_MUSIC_EPISODE_URL_RE.match(url)
    if not match:
        raise RuntimeError("yandex-music: некорректная ссылка на выпуск")

    album_id = match.group("album_id")
    track_id = match.group("track_id")
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        album = await _fetch_album(session, album_id, url)
        if album.get("type") != "podcast" and album.get("metaType") != "podcast":
            raise YandexMusicNotPodcastError(
                "yandex-music: ссылка ведёт не на подкаст"
            )

        track = _find_track(album, track_id)
        if not track:
            raise RuntimeError("yandex-music: выпуск не найден в подкасте")
        if track.get("type") != "podcast-episode":
            raise YandexMusicNotPodcastError(
                "yandex-music: ссылка ведёт не на выпуск подкаста"
            )

        podcast_title = album.get("title")
        episode_title = track.get("title")
        feed_url = await _find_podcast_feed(session, podcast_title)
        enclosure_url = await _find_episode_enclosure(
            session,
            feed_url,
            episode_title,
        )
        path = await _download_to_file(session, enclosure_url, output_dir)
        source_title = _compose_source_title(podcast_title, episode_title)
        return path, source_title


def _compose_source_title(
    podcast_title: str | None, episode_title: str | None
) -> str | None:
    parts = [p for p in (podcast_title, episode_title) if p]
    if not parts:
        return None
    base = " — ".join(parts)
    return f"подкаст «{base}»"


async def _fetch_album(
    session: aiohttp.ClientSession,
    album_id: str,
    referer: str,
) -> dict:
    headers = {
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "X-Retpath-Y": referer,
    }
    url = f"{ALBUM_API_URL}?{urlencode({'album': album_id})}"
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-music: API подкаста вернул HTTP {resp.status}"
            )
        data = await resp.json(content_type=None)
    if data.get("type") == "captcha" or "captcha" in data:
        raise RuntimeError("yandex-music: Яндекс Музыка запросила капчу")
    return data


def _find_track(album: dict, track_id: str) -> dict | None:
    for volume in album.get("volumes") or []:
        for track in volume or []:
            if str(track.get("id")) == track_id or str(track.get("realId")) == track_id:
                return track
    return None


async def _find_podcast_feed(
    session: aiohttp.ClientSession,
    podcast_title: str | None,
) -> str:
    if not podcast_title:
        raise RuntimeError("yandex-music: API не вернул название подкаста")

    query = urlencode({
        "term": podcast_title,
        "media": "podcast",
        "entity": "podcast",
        "limit": 5,
        "country": "ru",
    })
    async with session.get(f"{ITUNES_SEARCH_URL}?{query}") as resp:
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-music: поиск RSS подкаста вернул HTTP {resp.status}"
            )
        data = await resp.json(content_type=None)

    results = [item for item in data.get("results", []) if item.get("feedUrl")]
    normalized_title = _normalize_title(podcast_title)
    for item in results:
        if _normalize_title(item.get("collectionName")) == normalized_title:
            return item["feedUrl"]
    if len(results) == 1:
        return results[0]["feedUrl"]
    raise RuntimeError("yandex-music: не удалось найти открытый RSS подкаста")


async def _find_episode_enclosure(
    session: aiohttp.ClientSession,
    feed_url: str,
    episode_title: str | None,
) -> str:
    if not episode_title:
        raise RuntimeError("yandex-music: API не вернул название выпуска")

    async with session.get(feed_url) as resp:
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-music: RSS подкаста вернул HTTP {resp.status}"
            )
        body = await resp.read()

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise RuntimeError("yandex-music: RSS подкаста не разобрался") from e

    normalized_title = _normalize_title(episode_title)
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        title = _find_child_text(item, "title")
        if _normalize_title(title) != normalized_title:
            continue
        enclosure = _find_child(item, "enclosure")
        if enclosure is not None and enclosure.get("url"):
            return enclosure.get("url")

    raise RuntimeError("yandex-music: выпуск не найден в RSS подкаста")


async def _download_to_file(
    session: aiohttp.ClientSession,
    href: str,
    output_dir: str,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}{_pick_extension(href)}")

    async with session.get(href) as resp:
        if resp.status != 200:
            raise RuntimeError(
                f"yandex-music: скачивание выпуска вернуло HTTP {resp.status}"
            )
        with open(out_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
                f.write(chunk)

    return out_path


def _find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _find_child_text(parent: ET.Element, name: str) -> str | None:
    child = _find_child(parent, name)
    if child is None:
        return None
    return child.text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    title = title.replace("\xa0", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", title).strip().casefold()


def _pick_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    if suffix:
        return suffix
    return ".mp3"
