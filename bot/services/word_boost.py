"""Load `word_boost` and `custom_spelling` overrides from config-pointed files.

Loaded once at import time. Restart the bot to pick up edits.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_word_boost(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        logger.warning("word_boost file %s not found; using empty list", path)
        return []
    seen: set[str] = set()
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if not term or term.startswith("#"):
            continue
        if term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def load_custom_spelling(path: str) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        logger.warning("custom_spelling file %s not found; using empty dict", path)
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("custom_spelling: invalid JSON at %s; using empty dict", path)
        return {}
    if not isinstance(data, dict):
        logger.warning("custom_spelling at %s is not an object; using empty dict", path)
        return {}
    return {str(k): str(v) for k, v in data.items()}


def apply_custom_spelling(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    for src, dst in mapping.items():
        if src and src != dst:
            text = text.replace(src, dst)
    return text
