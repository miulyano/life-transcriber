"""Render AssemblyAI utterances, generate a title, and detect speaker names.

One GPT-4o call (analyze_transcript) returns both the title and a speaker
name map.  For multi-speaker recordings the labeled transcript (A: ... B: ...)
is sent so GPT can identify names across the full text.  For mono recordings
raw_text is used (no speaker detection needed).
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from openai import AsyncOpenAI

from bot.config import settings
from bot.services.prompts import ANALYSIS_SYSTEM_PROMPT, PARAGRAPH_SPLIT_SYSTEM_PROMPT
from bot.utils.text_chunking import SENTENCE_BOUNDARIES, split_long_text

if TYPE_CHECKING:
    from bot.services.transcriber import Utterance

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

ANALYSIS_MAX_INPUT_CHARS = 20_000  # ~8K tokens for Russian; stays under 30K TPM limit
ANALYSIS_MAX_TOKENS = 200

PARA_SPLIT_THRESHOLD = 300  # chars; shorter single-speaker texts are fine as-is
PARA_SPLIT_MAX_INPUT = 20_000

_SENT_END_RE = re.compile(r'[.!?…]["»)\']?$')

_LABELED_BLOCK_RE = re.compile(r"^([^\n:]{1,40}?):\s(.*)", re.DOTALL | re.UNICODE)


def _speaker_label_resolver(
    name_map: Optional[dict[str, str]],
) -> Callable[[str], str]:
    """Return a closure mapping AssemblyAI labels to display names.

    Real names come from name_map; unknown speakers get "Спикер N" numbered
    in order of first appearance.  Shared by both renderers so labels match.
    """
    mapping: dict[str, str] = {}

    def _label_for(speaker: str) -> str:
        if speaker not in mapping:
            real = (name_map or {}).get(speaker, "")
            mapping[speaker] = real.strip() or f"Спикер {len(mapping) + 1}"
        return mapping[speaker]

    return _label_for


def _merge_adjacent_same_speaker(text: str) -> str:
    blocks = re.split(r"\n{2,}", text)
    merged: list[str] = []
    prev_label: Optional[str] = None
    for block in blocks:
        match = _LABELED_BLOCK_RE.match(block)
        if not match:
            merged.append(block)
            prev_label = None
            continue
        label = match.group(1).strip()
        body = match.group(2).strip()
        if label == prev_label and merged:
            merged[-1] = f"{merged[-1]} {body}".rstrip()
            continue
        merged.append(block)
        prev_label = label
    return "\n\n".join(merged)


def render_with_speakers(
    utterances: list["Utterance"],
    name_map: Optional[dict[str, str]] = None,
) -> str:
    """Convert AssemblyAI utterances into Telegram-friendly plain text.

    - name_map: optional {speaker_label → real name}, e.g. {"A": "Иван"}.
      Falls back to "Спикер N" for unknown speakers.
    - Single-speaker recording: no label prefix, just paragraphs.
    """
    if not utterances:
        return ""
    speakers = {u.speaker for u in utterances}
    if len(speakers) == 1:
        return "\n\n".join(u.text.strip() for u in utterances if u.text.strip())

    _label_for = _speaker_label_resolver(name_map)

    parts: list[str] = []
    for u in utterances:
        text = u.text.strip()
        if not text:
            continue
        parts.append(f"{_label_for(u.speaker)}: {text}")
    body = "\n\n".join(parts)
    return _merge_adjacent_same_speaker(body)


def _fmt_ts(ms: int) -> str:
    total = max(ms, 0)
    h, rem = divmod(total // 1000, 3600)
    m, s = divmod(rem, 60)
    frac = total % 1000
    if h:
        return f"[{h}:{m:02d}:{s:02d}.{frac:03d}]"
    return f"[{m}:{s:02d}.{frac:03d}]"


@dataclass
class TimecodeSegment:
    """One timecoded sentence: stamp source for the ``[m:ss.mmm] text`` line.

    ``speaker`` is the resolved display label («Иван» / «Спикер 1»);
    ``None`` for single-speaker recordings.
    """

    start_ms: int
    text: str
    speaker: Optional[str] = None


def build_timecode_segments(
    utterances: list["Utterance"],
    name_map: Optional[dict[str, str]] = None,
) -> list[TimecodeSegment]:
    """Split utterances into per-sentence segments with start timestamps.

    Every sentence becomes one segment (an utterance boundary counts as a
    sentence boundary); the stamp is the start of the sentence's first word.
    Sentences are never broken mid-way, so unpunctuated stretches keep growing.
    """
    if not utterances:
        return []
    multi = len({u.speaker for u in utterances}) > 1
    _label_for = _speaker_label_resolver(name_map)

    segments: list[TimecodeSegment] = []
    cur_words: list[str] = []
    cur_start = 0
    cur_label: Optional[str] = None
    prev_word = ""

    def start_segment(stamp_ms: int) -> None:
        nonlocal cur_words, cur_start
        if cur_words:
            segments.append(TimecodeSegment(cur_start, " ".join(cur_words), cur_label))
        cur_words = []
        cur_start = stamp_ms

    for u in utterances:
        text = u.text.strip()
        if not text:
            continue
        words = [(w.text.strip(), w.start_ms) for w in u.words if w.text.strip()]
        if not words:
            words = [(text, u.start_ms)]

        # Utterance boundary counts as a sentence boundary.
        start_segment(words[0][1])
        cur_label = _label_for(u.speaker) if multi else None

        for w_text, w_start in words:
            if cur_words and _SENT_END_RE.search(prev_word):
                start_segment(w_start)
            cur_words.append(w_text)
            prev_word = w_text

    if cur_words:
        segments.append(TimecodeSegment(cur_start, " ".join(cur_words), cur_label))
    return segments


def render_timecode_segments(segments: list[TimecodeSegment]) -> str:
    """Render segments as ``[m:ss.mmm] text`` lines.

    Multi-speaker: each speaker turn is a block — label on its own line,
    stamped lines below, blank line between blocks.  Mono (``speaker`` is
    None): no label lines, just stamped lines.
    """
    out_lines: list[str] = []
    prev_speaker: Optional[str] = None
    for seg in segments:
        if seg.speaker is not None and seg.speaker != prev_speaker:
            if out_lines:
                out_lines.append("")
            out_lines.append(seg.speaker)
        prev_speaker = seg.speaker
        out_lines.append(f"{_fmt_ts(seg.start_ms)} {seg.text}")
    return "\n".join(out_lines)


def render_with_timecodes(
    utterances: list["Utterance"],
    name_map: Optional[dict[str, str]] = None,
) -> str:
    """Per-sentence timecoded rendering for the .txt file variant."""
    return render_timecode_segments(build_timecode_segments(utterances, name_map))


async def analyze_transcript(
    raw_text: str,
    utterances: list["Utterance"],
    filename_hint: Optional[str],
) -> tuple[str, dict[str, str]]:
    """One GPT-4o call → (title, speaker_name_map).

    For multi-speaker recordings sends the labeled transcript so GPT can find
    speaker names throughout the full text.  Returns ("", {}) on any failure.
    """
    if not raw_text.strip():
        return "", {}

    speakers = {u.speaker for u in utterances}
    if len(speakers) >= 2:
        labeled_lines = [f"{u.speaker}: {u.text}" for u in utterances]
        transcript_text = "\n".join(labeled_lines)
    else:
        transcript_text = raw_text

    if len(transcript_text) > ANALYSIS_MAX_INPUT_CHARS:
        logger.warning(
            "analyze_transcript input too long (%d chars), truncating to %d",
            len(transcript_text),
            ANALYSIS_MAX_INPUT_CHARS,
        )
        transcript_text = transcript_text[:ANALYSIS_MAX_INPUT_CHARS]

    user_parts = []
    if filename_hint:
        user_parts.append(f"Source: {filename_hint}")
    user_parts.append(f"Транскрипция:\n{transcript_text}")
    user_message = "\n\n".join(user_parts)

    try:
        response = await client.chat.completions.create(
            model=settings.GPT_MODEL,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=ANALYSIS_MAX_TOKENS,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")

        raw_title = str(data.get("title", "")).strip()
        title = raw_title.strip('"').strip("'").strip().rstrip(".")
        title = " ".join(title.split())

        raw_speakers = data.get("speakers", {})
        name_map = {
            k: str(v).strip()
            for k, v in raw_speakers.items()
            if isinstance(v, str) and str(v).strip()
        }
        return title, name_map

    except Exception:
        logger.warning("analyze_transcript failed", exc_info=True)
        return "", {}


def split_sentences(text: str) -> list[str]:
    """Cut ``text`` into sentences on whitespace after a terminator word.

    Whitespace-only tokenisation — the words themselves are never altered, so
    ``"".join(result).split()`` always equals the input without whitespace.
    Uses the same terminator rule as ``build_timecode_segments``.
    """
    sentences: list[str] = []
    cur: list[str] = []
    for word in text.split():
        cur.append(word)
        if _SENT_END_RE.search(word):
            sentences.append(" ".join(cur))
            cur = []
    if cur:
        sentences.append(" ".join(cur))
    return sentences


def _squash(text: str) -> str:
    return "".join(text.split())


def _sanitize_breaks(raw: object, sentence_count: int) -> list[int]:
    """Keep only valid 1-based sentence indices in (1, sentence_count]."""
    if not isinstance(raw, list):
        return []
    out: set[int] = set()
    for value in raw:
        if isinstance(value, bool):
            continue
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, int) and 1 < value <= sentence_count:
            out.add(value)
    return sorted(out)


def _assemble_paragraphs(sentences: list[str], breaks: list[int]) -> str:
    paragraphs: list[str] = []
    start = 0
    for b in breaks:
        paragraphs.append(" ".join(sentences[start : b - 1]))
        start = b - 1
    paragraphs.append(" ".join(sentences[start:]))
    return "\n\n".join(paragraphs)


async def _split_chunk(chunk: str) -> str:
    """Paragraph split for a single chunk. Returns chunk unchanged on failure.

    GPT never rewrites the text: it receives the numbered sentences and
    returns only the indices where a new paragraph starts. Paragraphs are
    then assembled from the original sentences, so words cannot be lost or
    invented (a rewrite-based prompt once shipped a GPT summary instead of
    half a lecture).
    """
    sentences = split_sentences(chunk)
    if len(sentences) < 2:
        return chunk
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, 1))
    try:
        response = await client.chat.completions.create(
            model=settings.GPT_MODEL,
            messages=[
                {"role": "system", "content": PARAGRAPH_SPLIT_SYSTEM_PROMPT},
                {"role": "user", "content": numbered},
            ],
            temperature=0.0,
            max_tokens=min(4000, 50 + 6 * len(sentences)),
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        raw_breaks = data.get("breaks", []) if isinstance(data, dict) else []
    except Exception:
        logger.warning("_split_chunk failed", exc_info=True)
        return chunk
    breaks = _sanitize_breaks(raw_breaks, len(sentences))
    if not breaks:
        logger.warning(
            "_split_chunk: no usable paragraph breaks for %d sentences (got %r)",
            len(sentences),
            raw_breaks,
        )
        return chunk
    return _assemble_paragraphs(sentences, breaks)


async def split_into_paragraphs(
    text: str,
    on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> str:
    """Split single-speaker solid text into semantic paragraphs via GPT.

    GPT only picks break positions between sentences (see ``_split_chunk``);
    the words are always the original ones.  Long texts are processed
    chunk-by-chunk so the entire text is formatted, not just the first
    PARA_SPLIT_MAX_INPUT characters.
    ``on_progress(done, total)`` is reported per chunk, but only when there
    is more than one chunk — a 1/1 counter carries no information.
    Returns the original text unchanged on any failure.
    """
    if not text.strip():
        return text
    chunks = split_long_text(text, PARA_SPLIT_MAX_INPUT, prefer_boundaries=SENTENCE_BOUNDARIES)
    report = on_progress if len(chunks) > 1 else None

    async def _emit(done: int) -> None:
        # A failing progress callback must not abort formatting — the docstring
        # promises the text is returned unchanged on any failure.
        if report is None:
            return
        try:
            await report(done, len(chunks))
        except Exception:
            logger.warning("split_into_paragraphs progress callback failed", exc_info=True)

    results = []
    for i, chunk in enumerate(chunks):
        await _emit(i)
        results.append(await _split_chunk(chunk))
    await _emit(len(chunks))
    joined = "\n\n".join(results)
    # Invariant: paragraph splitting may only move whitespace. Anything else
    # means text was lost or invented — deliver the original instead.
    if _squash(joined) != _squash(text):
        logger.error(
            "split_into_paragraphs altered the text (%d vs %d non-space chars); "
            "returning original",
            len(_squash(joined)),
            len(_squash(text)),
        )
        return text
    return joined


def render_segments_plain(segments: list[TimecodeSegment]) -> str:
    """Body text without stamps, rebuilt from persisted segments.

    Mono (``speaker`` is None): one solid block of sentences — the caller
    decides whether to run ``split_into_paragraphs``.  Multi-speaker: one
    ``«Имя: …»`` block per speaker turn, blank line between turns — the same
    shape ``render_with_speakers`` produces from utterances.
    """
    if not segments:
        return ""
    multi = any(s.speaker is not None for s in segments)
    if not multi:
        return " ".join(s.text.strip() for s in segments if s.text.strip())
    blocks: list[str] = []
    cur_speaker: Optional[str] = None
    cur: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if cur and seg.speaker != cur_speaker:
            blocks.append(f"{cur_speaker}: {' '.join(cur)}")
            cur = []
        cur_speaker = seg.speaker
        cur.append(text)
    if cur:
        blocks.append(f"{cur_speaker}: {' '.join(cur)}")
    return "\n\n".join(blocks)
