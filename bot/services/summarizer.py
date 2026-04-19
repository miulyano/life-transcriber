import logging
from typing import Awaitable, Callable, Optional

from openai import AsyncOpenAI

from bot.config import settings
from bot.utils.text_chunking import SENTENCE_BOUNDARIES, split_long_text

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

ProgressCallback = Callable[[int, int], Awaitable[None]]

SUMMARY_CHUNK_MAX_CHARS = 24_000
SUMMARY_CHUNK_OVERLAP_CHARS = 1_200
SUMMARY_CHUNK_MAX_TOKENS = 700
FINAL_SUMMARY_MAX_TOKENS = 1_200
# 6_000 input chars (~2_400 RU tokens) + 4_096 output tokens leaves headroom so
# the cleaned response (typically 80–95% the size of the input) doesn't get
# truncated mid-sentence at the model's max_tokens cap.
CLEANUP_CHUNK_MAX_CHARS = 6_000
CLEANUP_MAX_TOKENS = 4_096

_HEADER_HINT = (
    "Заголовок категории — обычным текстом с двоеточием. После заголовка категории — "
    "одна пустая строка перед содержимым категории. Между двумя соседними категориями — "
    "также пустая строка. Если хочешь разделить категории — отдельная строка из трёх "
    "звёздочек `***`."
)

SYSTEM_PROMPT = (
    "Ты — помощник по созданию конспектов. "
    "Сделай краткий конспект основных мыслей из предоставленного текста. "
    "Выдели ключевые идеи в виде коротких тезисов. "
    "Отвечай на том же языке, что и исходный текст. "
    "Не используй Markdown-разметку: никаких `**жирный**`, `*курсив*`, `#заголовок`, "
    "обратных кавычек. " + _HEADER_HINT
)

CHUNK_SYSTEM_PROMPT = (
    "Ты — помощник по созданию конспектов длинных транскрибаций. "
    "Извлеки из фрагмента подробные структурированные заметки для будущего конспекта. "
    "Не делай финальный краткий конспект. Сохрани ключевые идеи, факты, решения, "
    "задачи, вопросы, имена, термины и важные связи между ними. "
    "Если фрагмент обрывается, не додумывай отсутствующий контекст. "
    "Отвечай на том же языке, что и исходный текст."
)

FINAL_SYSTEM_PROMPT = (
    "Ты — помощник по созданию конспектов. "
    "Собери единый краткий конспект из заметок по фрагментам транскрибации. "
    "Убери повторы, которые могли появиться из-за overlap между фрагментами. "
    "Сохрани сквозные темы, важные выводы, решения, задачи и открытые вопросы. "
    "Отвечай на том же языке, что и исходный материал. "
    "Не используй Markdown-разметку: никаких `**жирный**`, `*курсив*`, `#заголовок`, "
    "обратных кавычек. " + _HEADER_HINT
)

CLEANUP_SYSTEM_PROMPT = (
    "Ты — редактор транскрибаций. "
    "Очисти текст от слов-паразитов, повторов, пауз, мусорных вставок и грязных формулировок, "
    "но не меняй смысл. "
    "Сохрани исходную структуру: порядок блоков, абзацы, заголовки, списки и обозначения спикеров. "
    "Не превращай текст в конспект, не добавляй новые разделы и не объединяй абзацы. "
    "Не пропускай и не сокращай начало текста (вступление, приветствие, представление "
    "темы) — его тоже надо очистить и сохранить. "
    "Если фрагмент начинается или заканчивается на полуслове, не додумывай недостающий контекст. "
    "Отвечай на том же языке, что и исходный текст. "
    "Не используй Markdown-разметку."
)


def _split_long_text(text: str) -> list[str]:
    """Thin wrapper around :func:`split_long_text` with summarizer defaults.

    Kept as a named module-level function so tests can monkeypatch it.
    """
    return split_long_text(
        text,
        max_chars=SUMMARY_CHUNK_MAX_CHARS,
        overlap_chars=SUMMARY_CHUNK_OVERLAP_CHARS,
    )


def _chunk_user_message(chunk: str, index: int, total: int) -> str:
    return f"Фрагмент транскрибации {index}/{total}:\n\n{chunk}"


def _final_user_message(notes: list[str]) -> str:
    numbered = [f"Заметки фрагмента {index}:\n{note}" for index, note in enumerate(notes, 1)]
    return "\n\n".join(numbered)


def _split_cleanup_text(text: str, max_chars: int = CLEANUP_CHUNK_MAX_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            # Sentence-aware split prevents cutting mid-word at chunk boundary,
            # which would otherwise force the model to start the next chunk
            # with a partial sentence and risk dropping content.
            chunks.extend(
                split_long_text(
                    paragraph,
                    max_chars=max_chars,
                    overlap_chars=0,
                    prefer_boundaries=SENTENCE_BOUNDARIES,
                )
            )
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        chunks.append(current)
        current = paragraph

    if current:
        chunks.append(current)

    return chunks


async def _complete(system_prompt: str, user_message: str, max_tokens: int) -> str:
    response = await client.chat.completions.create(
        model=settings.GPT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


async def _complete_with_retry(
    system_prompt: str, user_message: str, max_tokens: int
) -> str:
    """Like ``_complete`` but raises on truncation and retries once.

    Used by cleanup where ``finish_reason="length"`` means we lost text from
    the END of a chunk — silently accepting that would produce an incomplete
    cleaned transcript.
    """
    last_error: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            response = await client.chat.completions.create(
                model=settings.GPT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            choice = response.choices[0]
            content = (choice.message.content or "").strip()
            finish = getattr(choice, "finish_reason", None) or "stop"
            if finish != "stop":
                raise RuntimeError(
                    f"cleanup chunk truncated (finish_reason={finish!r}, "
                    f"input_chars={len(user_message)})"
                )
            return content
        except Exception as e:  # noqa: BLE001 — retry any transient failure
            last_error = e
            logger.warning("cleanup chunk attempt %d failed: %s", attempt, e)
    assert last_error is not None
    raise RuntimeError("cleanup chunk: retry exhausted") from last_error


async def _report(
    on_progress: Optional[ProgressCallback], done: int, total: int
) -> None:
    if on_progress is None:
        return
    try:
        await on_progress(done, total)
    except Exception:
        logger.exception("on_progress callback failed")


async def _summarize_chunk(chunk: str, index: int, total: int) -> str:
    return await _complete(
        CHUNK_SYSTEM_PROMPT,
        _chunk_user_message(chunk, index, total),
        SUMMARY_CHUNK_MAX_TOKENS,
    )


async def _finalize_notes(notes: list[str]) -> str:
    user_message = _final_user_message(notes)
    if len(user_message) <= SUMMARY_CHUNK_MAX_CHARS:
        return await _complete(FINAL_SYSTEM_PROMPT, user_message, FINAL_SUMMARY_MAX_TOKENS)

    condensed_notes = []
    chunks = _split_long_text(user_message)
    for index, chunk in enumerate(chunks, 1):
        condensed_notes.append(await _summarize_chunk(chunk, index, len(chunks)))
    return await _finalize_notes(condensed_notes)


async def summarize(
    text: str, on_progress: Optional[ProgressCallback] = None
) -> str:
    text = text.strip()
    chunks = _split_long_text(text)
    if len(chunks) <= 1:
        await _report(on_progress, 0, 1)
        result = await _complete(SYSTEM_PROMPT, text, 1024)
        await _report(on_progress, 1, 1)
        return result

    total = len(chunks) + 1  # +1 for the final summary step
    await _report(on_progress, 0, total)

    notes = []
    for index, chunk in enumerate(chunks, 1):
        notes.append(await _summarize_chunk(chunk, index, len(chunks)))
        await _report(on_progress, index, total)
    final = await _finalize_notes(notes)
    await _report(on_progress, total, total)
    return final


async def cleanup_transcript(
    text: str, on_progress: Optional[ProgressCallback] = None
) -> str:
    chunks = _split_cleanup_text(text)
    if not chunks:
        return ""

    total = len(chunks)
    await _report(on_progress, 0, total)
    cleaned_chunks = []
    for index, chunk in enumerate(chunks, 1):
        cleaned_chunks.append(
            await _complete_with_retry(CLEANUP_SYSTEM_PROMPT, chunk, CLEANUP_MAX_TOKENS)
        )
        await _report(on_progress, index, total)
    return "\n\n".join(cleaned_chunks)
