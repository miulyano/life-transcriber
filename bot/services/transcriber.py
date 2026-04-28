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
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import assemblyai as aai
from assemblyai import api as _aai_api

from bot.config import settings
from bot.services.formatter import (
    PARA_SPLIT_THRESHOLD,
    analyze_transcript,
    render_with_speakers,
    split_into_paragraphs,
)
from bot.services.word_boost import (
    apply_custom_spelling,
    load_custom_spelling,
    load_word_boost,
)
from bot.utils.fake_progress import FractionCallback

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]
PhaseCallback = Callable[[str], Awaitable[None]]


@dataclass
class Utterance:
    speaker: str  # AssemblyAI label: "A", "B", "C", ...
    text: str
    start_ms: int
    end_ms: int


@dataclass
class FormattedTranscript:
    title: str
    body: str  # title + blank line + speaker-labelled body
    raw_text: str  # AssemblyAI text after custom_spelling, no speaker prefixes
    language: Optional[str]
    speaker_count: int


# Loaded once at import — restart the bot to pick up edits to the data files.
_WORD_BOOST = load_word_boost(settings.WORD_BOOST_FILE)
_CUSTOM_SPELLING = load_custom_spelling(settings.CUSTOM_SPELLING_FILE)

aai.settings.api_key = settings.ASSEMBLYAI_API_KEY


def _build_config() -> aai.TranscriptionConfig:
    cfg = aai.TranscriptionConfig(
        speech_models=[settings.ASSEMBLYAI_SPEECH_MODEL],
        speaker_labels=True,
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
    filename_hint: Optional[str] = None,
    on_phase: Optional[PhaseCallback] = None,
    on_progress: Optional[ProgressCallback] = None,  # unused, kept for API compat
    on_progress_fraction: Optional[FractionCallback] = None,
) -> FormattedTranscript:
    """Transcribe audio and return a fully formatted transcript ready to deliver.

    Phases emitted via ``on_phase``:
    - "Форматирую…" — when the GPT analysis step begins (title + speaker names).
    The caller is responsible for setting the initial "Транскрибирую…" phase.
    """
    return await _transcribe_inner(audio_path, filename_hint, on_phase, on_progress_fraction)


async def _transcribe_inner(
    audio_path: str,
    filename_hint: Optional[str],
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

    title = ""
    name_map: dict[str, str] = {}
    if raw_text:
        try:
            title, name_map = await analyze_transcript(raw_text, utterances, filename_hint)
        except Exception:
            logger.warning("analyze_transcript raised, falling back to filename", exc_info=True)
        if not title:
            title = (filename_hint or "").strip()

    body_text = render_with_speakers(utterances, name_map) if utterances else raw_text
    if speaker_count == 1 and "\n\n" not in body_text and len(body_text) > PARA_SPLIT_THRESHOLD:
        body_text = await split_into_paragraphs(body_text)
    body = f"{title}\n\n{body_text}".strip() if title else body_text
    language = getattr(transcript, "language_code", None) or settings.FORCE_LANGUAGE_CODE
    return FormattedTranscript(
        title=title,
        body=body,
        raw_text=raw_text,
        language=language,
        speaker_count=speaker_count,
    )
