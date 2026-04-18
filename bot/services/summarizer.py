from openai import AsyncOpenAI

from bot.config import settings

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SUMMARY_CHUNK_MAX_CHARS = 24_000
SUMMARY_CHUNK_OVERLAP_CHARS = 1_200
SUMMARY_CHUNK_MAX_TOKENS = 700
FINAL_SUMMARY_MAX_TOKENS = 1_200
CLEANUP_CHUNK_MAX_CHARS = 12_000
CLEANUP_MAX_TOKENS = 4_096

SYSTEM_PROMPT = (
    "Ты — помощник по созданию конспектов. "
    "Сделай краткий конспект основных мыслей из предоставленного текста. "
    "Выдели ключевые идеи в виде коротких тезисов. "
    "Отвечай на том же языке, что и исходный текст. "
    "Не используй Markdown-разметку: никаких `**жирный**`, `*курсив*`, `#заголовок`, "
    "обратных кавычек. Заголовок категории — обычным текстом с двоеточием. "
    "Если хочешь разделить категории — отдельная строка из трёх звёздочек `***`."
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
    "обратных кавычек. Заголовок категории — обычным текстом с двоеточием. "
    "Если хочешь разделить категории — отдельная строка из трёх звёздочек `***`."
)

CLEANUP_SYSTEM_PROMPT = (
    "Ты — редактор транскрибаций. "
    "Очисти текст от слов-паразитов, повторов, пауз, мусорных вставок и грязных формулировок, "
    "но не меняй смысл. "
    "Сохрани исходную структуру: порядок блоков, абзацы, заголовки, списки и обозначения спикеров. "
    "Не превращай текст в конспект, не добавляй новые разделы и не объединяй абзацы. "
    "Если фрагмент начинается или заканчивается на полуслове, не додумывай недостающий контекст. "
    "Отвечай на том же языке, что и исходный текст. "
    "Не используй Markdown-разметку."
)


def _split_long_text(
    text: str,
    max_chars: int = SUMMARY_CHUNK_MAX_CHARS,
    overlap_chars: int = SUMMARY_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    content_limit = max(1, max_chars - overlap_chars - 2)
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    def append_piece(piece: str) -> None:
        nonlocal current
        if not current:
            current = piece
            return
        candidate = f"{current}\n\n{piece}"
        if len(candidate) <= content_limit:
            current = candidate
            return
        chunks.append(current)
        current = piece

    for paragraph in paragraphs:
        if len(paragraph) <= content_limit:
            append_piece(paragraph)
            continue

        words = paragraph.split()
        piece = ""
        for word in words:
            if len(word) > content_limit:
                if piece:
                    append_piece(piece)
                    piece = ""
                for start in range(0, len(word), content_limit):
                    append_piece(word[start : start + content_limit])
                continue
            candidate = f"{piece} {word}".strip()
            if len(candidate) <= content_limit:
                piece = candidate
                continue
            if piece:
                append_piece(piece)
            piece = word
        if piece:
            append_piece(piece)

    if current:
        chunks.append(current)

    chunks_with_overlap = [chunks[0]]
    for previous, chunk in zip(chunks, chunks[1:]):
        overlap = previous[-overlap_chars:].strip()
        if overlap:
            chunks_with_overlap.append(f"{overlap}\n\n{chunk}")
        else:
            chunks_with_overlap.append(chunk)
    return chunks_with_overlap


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
            chunks.extend(_split_long_text(paragraph, max_chars=max_chars, overlap_chars=0))
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


async def summarize(text: str) -> str:
    text = text.strip()
    chunks = _split_long_text(text)
    if len(chunks) <= 1:
        return await _complete(SYSTEM_PROMPT, text, 1024)

    notes = []
    for index, chunk in enumerate(chunks, 1):
        notes.append(await _summarize_chunk(chunk, index, len(chunks)))
    return await _finalize_notes(notes)


async def cleanup_transcript(text: str) -> str:
    chunks = _split_cleanup_text(text)
    if not chunks:
        return ""

    cleaned_chunks = []
    for chunk in chunks:
        cleaned_chunks.append(await _complete(CLEANUP_SYSTEM_PROMPT, chunk, CLEANUP_MAX_TOKENS))
    return "\n\n".join(cleaned_chunks)
