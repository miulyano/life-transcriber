"""Audio → transcript via AssemblyAI Universal model.

Returns a :class:`FormattedTranscript` with:
- ``body`` — final text ready to deliver (title + speaker-labelled paragraphs);
- ``raw_text`` — the AssemblyAI text without speaker prefixes (used by
  ``cleanup_transcript`` and ``summarize`` callbacks so they get full context).

AssemblyAI handles diarization (real, acoustic), punctuation, casing and
paragraph segmentation server-side, so we no longer chunk audio nor ask GPT
to "guess" speakers from prose.
"""
import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import assemblyai as aai

from bot.config import settings
from bot.services.formatter import generate_title, render_with_speakers
from bot.services.word_boost import (
    apply_custom_spelling,
    load_custom_spelling,
    load_word_boost,
)
from bot.utils.fake_progress import FractionCallback, run_with_fake_progress

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]


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
        speech_model=aai.SpeechModel(settings.ASSEMBLYAI_SPEECH_MODEL),
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


def _utterances_from_response(transcript: "aai.Transcript") -> list[Utterance]:
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


async def _run_assemblyai(audio_path: str) -> "aai.Transcript":
    """Submit audio to AssemblyAI and wait for completion (off the event loop)."""
    config = _build_config()
    transcriber = aai.Transcriber(config=config)

    def _do() -> "aai.Transcript":
        # Synchronous SDK call wraps upload + polling internally.
        return transcriber.transcribe(audio_path)

    transcript = await asyncio.to_thread(_do)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI error: {transcript.error}")
    return transcript


async def transcribe(
    audio_path: str,
    *,
    filename_hint: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_progress_fraction: Optional[FractionCallback] = None,
) -> FormattedTranscript:
    """Transcribe audio and return a fully formatted transcript ready to deliver.

    A fake-progress bar is shown if ``on_progress_fraction`` is provided —
    AssemblyAI doesn't expose real progress, so we fake it as before.
    """
    if on_progress_fraction is not None:
        # Rough estimate: AssemblyAI Universal processes ~10× real-time,
        # plus ~5s upload. Use a conservative 30s default — the loop caps
        # at FAKE_CEILING anyway.
        return await run_with_fake_progress(
            _transcribe_inner(audio_path, filename_hint),
            on_progress_fraction,
            expected_seconds=30.0,
        )
    return await _transcribe_inner(audio_path, filename_hint)


async def _transcribe_inner(
    audio_path: str, filename_hint: Optional[str]
) -> FormattedTranscript:
    transcript = await _run_assemblyai(audio_path)
    raw_text = (transcript.text or "").strip()
    raw_text = apply_custom_spelling(raw_text, _CUSTOM_SPELLING)

    utterances = _utterances_from_response(transcript)
    # Apply custom_spelling to each utterance too, otherwise the body and
    # raw_text would diverge.
    for u in utterances:
        u.text = apply_custom_spelling(u.text, _CUSTOM_SPELLING)

    speaker_count = len({u.speaker for u in utterances}) if utterances else (1 if raw_text else 0)
    body_text = render_with_speakers(utterances) if utterances else raw_text

    title = ""
    if raw_text:
        with suppress(Exception):
            title = await generate_title(raw_text, filename_hint)
        if not title:
            title = (filename_hint or "").strip()

    body = f"{title}\n\n{body_text}".strip() if title else body_text
    language = getattr(transcript, "language_code", None) or settings.FORCE_LANGUAGE_CODE
    return FormattedTranscript(
        title=title,
        body=body,
        raw_text=raw_text,
        language=language,
        speaker_count=speaker_count,
    )
