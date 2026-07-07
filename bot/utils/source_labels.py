"""Human-readable labels for transcript source types.

Single source of truth for both bot captions and the Mini App list
(the API serves the precomputed label to the frontend).
"""
from __future__ import annotations

from typing import Optional

SOURCE_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "yandex_disk": "Яндекс Диск",
    "yandex_music": "Яндекс Музыка",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "link": "Ссылка",
    "voice": "Голосовое",
    "video_note": "Кружок",
    "video": "Видео",
    "document": "Файл",
    "webapp": "Файл",
}


def source_label(source_type: Optional[str]) -> Optional[str]:
    """Label for a source type, or None when unknown/unmapped."""
    if not source_type:
        return None
    return SOURCE_LABELS.get(source_type)


def format_source_line(source_type: Optional[str], *, timecoded: bool) -> str:
    """Footer line like ``Источник: YouTube · 🕒 с таймкодами``.

    Without a known source only the timecode part remains, so the line is
    never empty.
    """
    tc_part = "🕒 с таймкодами" if timecoded else "без таймкодов"
    label = source_label(source_type)
    if label is None:
        return tc_part
    return f"Источник: {label} · {tc_part}"
