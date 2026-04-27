"""Render AssemblyAI utterances + generate a title.

AssemblyAI already returns punctuated, cased, paragraph-segmented text with
real (acoustic) speaker labels. We map labels A/B/C → "Спикер 1/2/3", merge
adjacent same-speaker turns, and ask GPT for a short title with full-transcript
context.
"""
import logging
import re
from typing import TYPE_CHECKING, Optional

from openai import AsyncOpenAI

from bot.config import settings

if TYPE_CHECKING:
    from bot.services.transcriber import Utterance

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# GPT-4o has a 128K context window — ~10h of audio fits whole. We cap at
# ~100K chars (~40K tokens for Russian) just to leave room for the title
# response and avoid pathological inputs.
TITLE_MAX_INPUT_CHARS = 100_000
TITLE_MAX_TOKENS = 80

TITLE_SYSTEM_PROMPT = """\
Ты — помощник, который придумывает короткий заголовок для расшифровки аудио/видео.

По полному тексту транскрипции (опционально — подсказка по источнику) верни ТОЛЬКО строку заголовка — до ~80 символов, без кавычек, без префиксов («Заголовок:», «Title:»), без точки в конце. Заголовок должен отражать суть разговора/материала, а не пересказывать отдельные фразы.

Если подсказка источника нечитаема (хэш, uuid, случайный идентификатор) — игнорируй её и опирайся только на содержание.
"""


_LABELED_BLOCK_RE = re.compile(r"^([^\n:]{1,40}?):\s(.*)", re.DOTALL | re.UNICODE)


def _label_for(speaker: str, mapping: dict[str, str]) -> str:
    if speaker not in mapping:
        mapping[speaker] = f"Спикер {len(mapping) + 1}"
    return mapping[speaker]


def _merge_adjacent_same_speaker(text: str) -> str:
    """Merge two consecutive paragraphs labelled by the same speaker."""
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


def render_with_speakers(utterances: list["Utterance"]) -> str:
    """Convert AssemblyAI utterances into Telegram-friendly plain text.

    - Maps A/B/C → "Спикер 1/2/3" by first appearance.
    - One blank line between utterances; merges adjacent same-speaker turns.
    - For a single-speaker recording: no "Спикер 1:" prefix at all — just
      paragraphs.
    """
    if not utterances:
        return ""
    speakers = {u.speaker for u in utterances}
    single = len(speakers) == 1
    if single:
        # AssemblyAI already paragraph-segments; one utterance per paragraph.
        return "\n\n".join(u.text.strip() for u in utterances if u.text.strip())

    mapping: dict[str, str] = {}
    parts: list[str] = []
    for u in utterances:
        text = u.text.strip()
        if not text:
            continue
        label = _label_for(u.speaker, mapping)
        parts.append(f"{label}: {text}")
    body = "\n\n".join(parts)
    return _merge_adjacent_same_speaker(body)


async def generate_title(raw_text: str, filename_hint: Optional[str]) -> str:
    """Ask GPT-4o for a short title given the full transcript.

    Returns "" on any failure — caller falls back to filename hint.
    """
    if not raw_text.strip():
        return ""
    sample = raw_text
    if len(sample) > TITLE_MAX_INPUT_CHARS:
        logger.warning(
            "title input too long (%d chars), truncating to %d",
            len(sample),
            TITLE_MAX_INPUT_CHARS,
        )
        sample = sample[:TITLE_MAX_INPUT_CHARS]

    user_parts = []
    if filename_hint:
        user_parts.append(f"Source: {filename_hint}")
    user_parts.append(f"Транскрипция:\n{sample}")
    user_message = "\n\n".join(user_parts)

    response = await client.chat.completions.create(
        model=settings.GPT_MODEL,
        messages=[
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=TITLE_MAX_TOKENS,
        temperature=0.0,
    )
    content = (response.choices[0].message.content or "").strip()
    title = content.strip('"').strip("'").strip()
    title = title.rstrip(".")
    title = " ".join(title.split())
    return title
