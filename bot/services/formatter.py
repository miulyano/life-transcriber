"""Render AssemblyAI utterances, generate a title, and detect speaker names.

One GPT-4o call (analyze_transcript) returns both the title and a speaker
name map.  For multi-speaker recordings the labeled transcript (A: ... B: ...)
is sent so GPT can identify names across the full text.  For mono recordings
raw_text is used (no speaker detection needed).
"""
import json
import logging
import re
from typing import TYPE_CHECKING, Optional

from openai import AsyncOpenAI

from bot.config import settings

if TYPE_CHECKING:
    from bot.services.transcriber import Utterance

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

ANALYSIS_MAX_INPUT_CHARS = 100_000
ANALYSIS_MAX_TOKENS = 200

ANALYSIS_SYSTEM_PROMPT = """\
Ты — ассистент. По транскрипции аудио/видео верни JSON-объект с двумя полями:

"title" — короткий заголовок, до ~80 символов, отражает суть диалога/материала. \
Без кавычек, без точки в конце. Если подсказка источника нечитаема (хэш, uuid, \
случайный идентификатор) — игнорируй её и опирайся только на содержание.

"speakers" — объект, где ключи — метки спикеров (A, B, C...), значения — реальные \
имена, если их можно однозначно определить из текста (самоназвание, прямое обращение). \
Если имя спикера установить нельзя — не включай ключ. Если имён нет вообще — пустой объект.

Пример (имена известны):
{"title": "Встреча команды: планирование спринта", "speakers": {"A": "Иван", "B": "Маша"}}

Пример (имена неизвестны):
{"title": "Обзор продукта: демо новых фич", "speakers": {}}
"""

_LABELED_BLOCK_RE = re.compile(r"^([^\n:]{1,40}?):\s(.*)", re.DOTALL | re.UNICODE)


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

    mapping: dict[str, str] = {}

    def _label_for(speaker: str) -> str:
        if speaker not in mapping:
            real = (name_map or {}).get(speaker, "")
            mapping[speaker] = real.strip() or f"Спикер {len(mapping) + 1}"
        return mapping[speaker]

    parts: list[str] = []
    for u in utterances:
        text = u.text.strip()
        if not text:
            continue
        parts.append(f"{_label_for(u.speaker)}: {text}")
    body = "\n\n".join(parts)
    return _merge_adjacent_same_speaker(body)


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
