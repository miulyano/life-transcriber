"""Rebuild a stored transcript body from its persisted timecode segments.

The segments (``<id>.json``) hold every sentence AssemblyAI returned, so a
body damaged by a formatting step (e.g. GPT once replaced half a lecture
with a summary) can be regenerated without re-transcribing.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from bot.services.formatter import (
    PARA_SPLIT_THRESHOLD,
    render_segments_plain,
    split_into_paragraphs,
)
from bot.services.transcriber import _build_header
from bot.services.transcript_store import TranscriptRecord, TranscriptStore

# Russian speech yields ~11–13 body chars per second of audio; well below that
# the body is missing text (the broken lecture sat at 6.5).
LOW_DENSITY_CHARS_PER_SEC = 8.0


def is_low_density(record: TranscriptRecord, min_cps: float = LOW_DENSITY_CHARS_PER_SEC) -> bool:
    """True when the stored body is suspiciously short for its duration."""
    if record.duration_sec <= 0:
        return False
    return record.char_count / record.duration_sec < min_cps


async def rebuild_transcript_body(
    store: TranscriptStore,
    record: TranscriptRecord,
    on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> str:
    """Regenerate ``record``'s body from segments and persist it.

    Mirrors the shape ``transcribe()`` produces: header (title + channel),
    blank line, then speaker blocks or GPT-paragraphed mono text.
    Raises ``ValueError`` when the record has no stored segments.
    """
    segments = await store.read_segments(record)
    if not segments:
        raise ValueError(f"transcript {record.id} has no stored segments to rebuild from")

    text = render_segments_plain(segments)
    mono = all(s.speaker is None for s in segments)
    if mono and len(text) > PARA_SPLIT_THRESHOLD:
        text = await split_into_paragraphs(text, on_progress=on_progress)

    header = _build_header(record.title, record.channel)
    body = f"{header}\n\n{text}".strip() if header else text
    await store.update_body(record, body)
    return body
