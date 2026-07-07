"""Audio → transcript via AssemblyAI Universal model.

Returns a :class:`FormattedTranscript` with:
- ``body`` — final text ready to deliver (title + speaker-labelled paragraphs);
- ``raw_text`` — the AssemblyAI text without speaker prefixes (used by
  ``cleanup_transcript`` and ``summarize`` callbacks so they get full context).

Progress is reported via real AssemblyAI status polling (queued → processing →
completed) — no fake timer.  The GPT formatting step (title + speaker names)
triggers the "Форматирую…" phase via ``on_phase`` callback.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import assemblyai as aai
from assemblyai import api as _aai_api

from bot.config import settings
from bot.services.formatter import (
    PARA_SPLIT_THRESHOLD,
    TimecodeSegment,
    analyze_transcript,
    build_timecode_segments,
    render_timecode_segments,
    render_with_speakers,
    split_into_paragraphs,
)
from bot.services.source_meta import SourceMetadata
from bot.services.word_boost import (
    apply_custom_spelling,
    load_custom_spelling,
    load_word_boost,
)
from bot.utils.fake_progress import FractionCallback

# A title that looks like an ID/hash (e.g. `dQw4w9WgXcQ`, `a1b2c3d4-...`) carries no
# meaning; we fall back to GPT in that case.  Matches ASCII alphanumerics plus
# `-`/`_`, length ≥ 8, no spaces, no non-ASCII letters.
_HASH_TITLE_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


def _is_hash_like_title(title: str) -> bool:
    stripped = title.strip()
    if not stripped or " " in stripped:
        return False
    if not _HASH_TITLE_RE.fullmatch(stripped):
        return False
    has_letter = any(c.isalpha() for c in stripped)
    has_digit = any(c.isdigit() for c in stripped)
    return has_letter and has_digit

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]
PhaseCallback = Callable[[str], Awaitable[None]]


@dataclass
class Word:
    text: str
    start_ms: int
    end_ms: int


@dataclass
class Utterance:
    speaker: str  # AssemblyAI label: "A", "B", "C", ...
    text: str
    start_ms: int
    end_ms: int
    words: list[Word] = field(default_factory=list)


@dataclass
class FormattedTranscript:
    title: str
    body: str  # title (+ "Канал: ...") + blank line + speaker-labelled body
    raw_text: str  # AssemblyAI text after custom_spelling, no speaker prefixes
    language: Optional[str]
    speaker_count: int
    audio_duration_sec: float = 0.0
    uploader: Optional[str] = None
    body_timecoded: str = ""  # same as body but with [m:ss] stamps (file variant)
    timecode_segments: list[TimecodeSegment] = field(default_factory=list)


# Loaded once at import — restart the bot to pick up edits to the data files.
_WORD_BOOST = load_word_boost(settings.WORD_BOOST_FILE)
_CUSTOM_SPELLING = load_custom_spelling(settings.CUSTOM_SPELLING_FILE)

aai.settings.api_key = settings.ASSEMBLYAI_API_KEY


def _build_config() -> aai.TranscriptionConfig:
    # Hard caps on the speaker count (min/max are boundaries, not hints) —
    # without them AssemblyAI allows up to 30 speakers on 10+ min audio and
    # tends to split short interjections into phantom extra speakers.
    speaker_options = None
    if settings.DIARIZATION_MIN_SPEAKERS or settings.DIARIZATION_MAX_SPEAKERS:
        speaker_options = aai.types.SpeakerOptions(
            min_speakers_expected=settings.DIARIZATION_MIN_SPEAKERS,
            max_speakers_expected=settings.DIARIZATION_MAX_SPEAKERS,
        )
    cfg = aai.TranscriptionConfig(
        speech_models=[settings.ASSEMBLYAI_SPEECH_MODEL],
        speaker_labels=True,
        speaker_options=speaker_options,
        punctuate=True,
        format_text=True,
        disfluencies=False,
    )
    if settings.FORCE_LANGUAGE_CODE:
        cfg.language_code = settings.FORCE_LANGUAGE_CODE
    else:
        cfg.language_detection = True
    if _WORD_BOOST:
        cfg.set_word_boost(_WORD_BOOST, boost=aai.WordBoost(settings.WORD_BOOST_LEVEL))
    return cfg


def _utterances_from_response(transcript) -> list[Utterance]:
    out: list[Utterance] = []
    for u in transcript.utterances or []:
        out.append(
            Utterance(
                speaker=str(u.speaker),
                text=u.text or "",
                start_ms=int(getattr(u, "start", 0) or 0),
                end_ms=int(getattr(u, "end", 0) or 0),
                words=[
                    Word(
                        text=w.text or "",
                        start_ms=int(getattr(w, "start", 0) or 0),
                        end_ms=int(getattr(w, "end", 0) or 0),
                    )
                    for w in (getattr(u, "words", None) or [])
                ],
            )
        )
    return out


async def _run_assemblyai(
    audio_path: str,
    on_fraction: Optional[FractionCallback] = None,
) -> object:
    """Submit audio to AssemblyAI, poll for real status, return completed transcript.

    Progress fraction: ~5% while queued, grows 0.10→0.90 during processing.
    Uses transcript._impl.transcript_id (internal SDK field) to enable manual
    polling via assemblyai.api.get_transcript without a second blocking call.
    """
    config = _build_config()
    transcriber = aai.Transcriber(config=config)

    # Non-blocking submit: uploads file + queues job
    transcript = await asyncio.to_thread(transcriber.submit, audio_path)
    # _impl is internal but stable — only way to get ID without blocking
    transcript_id = transcript._impl.transcript_id
    http_client = transcript._client.http_client

    processing_start: Optional[float] = None

    while True:
        raw = await asyncio.to_thread(_aai_api.get_transcript, http_client, transcript_id)

        if raw.status == aai.TranscriptStatus.queued:
            frac = 0.05
        elif raw.status == aai.TranscriptStatus.processing:
            if processing_start is None:
                processing_start = asyncio.get_event_loop().time()
            elapsed = asyncio.get_event_loop().time() - processing_start
            frac = min(0.90, 0.10 + elapsed / 120.0)
        elif raw.status in (aai.TranscriptStatus.completed, aai.TranscriptStatus.error):
            break
        else:
            frac = 0.5

        if on_fraction:
            await on_fraction(frac)
        await asyncio.sleep(3.0)

    if raw.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI error: {raw.error}")
    if on_fraction:
        await on_fraction(1.0)
    return raw


async def transcribe(
    audio_path: str,
    *,
    source_meta: Optional[SourceMetadata] = None,
    on_phase: Optional[PhaseCallback] = None,
    on_progress: Optional[ProgressCallback] = None,  # unused, kept for API compat
    on_progress_fraction: Optional[FractionCallback] = None,
) -> FormattedTranscript:
    """Transcribe audio and return a fully formatted transcript ready to deliver.

    If ``source_meta.title`` is a meaningful string (not an opaque id/hash and
    not a raw filename — ``source_meta.title_is_filename``), it is used as the
    result title verbatim — GPT (``analyze_transcript``) is still called to
    detect speaker names, but its title is discarded.  Otherwise the
    GPT-generated title is used and ``source_meta.title`` serves only as a
    hint for GPT (and a last-resort fallback).

    Phases emitted via ``on_phase``:
    - "Форматирую…" — when the GPT analysis step begins (title + speaker names).
    The caller is responsible for setting the initial "Транскрибирую…" phase.
    """
    return await _transcribe_inner(audio_path, source_meta, on_phase, on_progress_fraction)


def _build_header(title: str, uploader: Optional[str]) -> str:
    if title and uploader:
        return f"{title}\n📺 Канал: {uploader}"
    return title


async def _transcribe_inner(
    audio_path: str,
    source_meta: Optional[SourceMetadata],
    on_phase: Optional[PhaseCallback],
    on_fraction: Optional[FractionCallback],
) -> FormattedTranscript:
    transcript = await _run_assemblyai(audio_path, on_fraction)
    raw_text = (transcript.text or "").strip()
    raw_text = apply_custom_spelling(raw_text, _CUSTOM_SPELLING)

    utterances = _utterances_from_response(transcript)
    for u in utterances:
        u.text = apply_custom_spelling(u.text, _CUSTOM_SPELLING)

    speaker_count = len({u.speaker for u in utterances}) if utterances else (1 if raw_text else 0)

    if on_phase:
        await on_phase("Форматирую…")

    source_title = (source_meta.title or "").strip() if source_meta else ""
    uploader = (source_meta.uploader or "").strip() if source_meta else ""
    title_is_filename = bool(source_meta and source_meta.title_is_filename)
    prefer_original = (
        bool(source_title)
        and not title_is_filename
        and not _is_hash_like_title(source_title)
    )

    title = ""
    name_map: dict[str, str] = {}
    if raw_text:
        try:
            gpt_title, name_map = await analyze_transcript(
                raw_text, utterances, source_title or None
            )
        except Exception:
            logger.warning("analyze_transcript raised, falling back to source title", exc_info=True)
            gpt_title = ""

        if prefer_original:
            title = source_title
        elif gpt_title:
            title = gpt_title
        else:
            title = source_title  # last-resort fallback even if hash-like

    body_text = render_with_speakers(utterances, name_map) if utterances else raw_text
    if speaker_count == 1 and "\n\n" not in body_text and len(body_text) > PARA_SPLIT_THRESHOLD:
        body_text = await split_into_paragraphs(body_text)

    # Word texts skipped custom_spelling above, so apply it per segment; the
    # rendered file variant and the persisted segments stay identical.
    segments = build_timecode_segments(utterances, name_map) if utterances else []
    for seg in segments:
        seg.text = apply_custom_spelling(seg.text, _CUSTOM_SPELLING)
    timecoded_text = render_timecode_segments(segments)

    header = _build_header(title, uploader or None)
    if header and body_text:
        body = f"{header}\n\n{body_text}"
    else:
        body = header or body_text
    body = body.strip()

    if timecoded_text:
        body_timecoded = f"{header}\n\n{timecoded_text}".strip() if header else timecoded_text
    else:
        body_timecoded = body

    language = getattr(transcript, "language_code", None) or settings.FORCE_LANGUAGE_CODE
    audio_duration_sec = float(getattr(transcript, "audio_duration", 0) or 0)
    return FormattedTranscript(
        title=title,
        body=body,
        raw_text=raw_text,
        language=language,
        speaker_count=speaker_count,
        audio_duration_sec=audio_duration_sec,
        uploader=(uploader or None),
        body_timecoded=body_timecoded,
        timecode_segments=segments,
    )
