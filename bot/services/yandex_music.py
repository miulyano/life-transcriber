import re

YANDEX_MUSIC_URL_RE = re.compile(
    r"^https?://music\.yandex\.(?:ru|com|kz|by|ua)/\S+",
    re.IGNORECASE,
)

YANDEX_MUSIC_EPISODE_URL_RE = re.compile(
    r"^https?://music\.yandex\.(?:ru|com|kz|by|ua)/album/\d+/track/\d+(?:[/?#]\S*)?$",
    re.IGNORECASE,
)


def is_yandex_music_url(url: str) -> bool:
    return bool(YANDEX_MUSIC_URL_RE.match(url))


def is_yandex_music_episode_url(url: str) -> bool:
    return bool(YANDEX_MUSIC_EPISODE_URL_RE.match(url))
