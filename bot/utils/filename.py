"""Build safe, readable filenames for sent transcripts.

The first line of a formatted transcript is the material's title
(see formatter.py SYSTEM_PROMPT). We derive a 3-4 word latin slug from it
so the sent .txt has a name the user can recognize later.
"""

from __future__ import annotations

import re

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_DEFAULT_FILENAME = "transcript.txt"
_MAX_WORDS = 4
_MAX_LEN = 60


def _transliterate(text: str) -> str:
    result = []
    for ch in text:
        lower = ch.lower()
        if lower in _CYRILLIC_TO_LATIN:
            mapped = _CYRILLIC_TO_LATIN[lower]
            result.append(mapped.upper() if ch.isupper() else mapped)
        else:
            result.append(ch)
    return "".join(result)


def build_filename(title: str | None, suffix: str = ".txt") -> str:
    """Return a safe latin filename derived from a material title.

    Takes up to 4 words, transliterates Cyrillic, strips non-alphanumerics,
    lowercases, joins with `-`. Falls back to `transcript.txt` for empty input.
    """
    if not title or not title.strip():
        return _DEFAULT_FILENAME

    translit = _transliterate(title.strip())
    # Keep only word chars and spaces, then split into words.
    cleaned = re.sub(r"[^0-9A-Za-z\s]+", " ", translit)
    words = [w for w in cleaned.split() if w]
    if not words:
        return _DEFAULT_FILENAME

    slug = "-".join(words[:_MAX_WORDS]).lower()
    if len(slug) > _MAX_LEN:
        slug = slug[:_MAX_LEN].rstrip("-")
    if not slug:
        return _DEFAULT_FILENAME
    return f"{slug}{suffix}"


def extract_title(text: str) -> str | None:
    """Return the first non-empty line of a formatted transcript as title."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
