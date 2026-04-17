import logging
from typing import Optional

from openai import AsyncOpenAI

from bot.config import settings
from bot.utils.fake_progress import FractionCallback, run_with_fake_progress

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """\
Ты — помощник, который оформляет сырой текст транскрипции аудио/видео в читаемый вид.

Задачи:
1. Придумай короткий заголовок материала (до ~80 символов), отражающий содержание.
2. Разбей текст на смысловые абзацы.
3. Если это диалог/интервью/подкаст с двумя и более участниками — размечай реплики префиксом с именем или ролью спикера. Если имена явно звучат в тексте (представление, обращения) — используй их. Иначе используй «Спикер 1», «Спикер 2» и т.д. Если это монолог одного человека — не ставь никаких префиксов, просто абзацы.
4. Сохрани исходный язык и смысл. Не придумывай фактов, не сокращай, не перефразируй.

Строгие правила форматирования вывода:
- Только чистый plain text. Никакого Markdown (`*`, `_`, `#`, `` ` ``), никаких HTML-тегов (`<b>`, `<i>` и т.п.).
- Не используй символы `<`, `>`, `&` как разметку. Если они встречаются в самом тексте — оставь как есть, но не добавляй новые.
- Первая строка ровно в формате: `Заголовок: <текст заголовка>`
- После заголовка — пустая строка.
- Абзацы разделяй одной пустой строкой.
- Если есть спикеры — каждая реплика начинается с `<Имя или Спикер N>: ` на той же строке, что и текст реплики.

Если подсказка по имени файла приходит как `Filename: ...` — используй её как контекст для заголовка, но не копируй имя файла как есть, если оно нечитаемое (хэш, uuid и т.п.).
"""


def _build_user_message(raw_text: str, filename_hint: Optional[str]) -> str:
    if filename_hint:
        return f"Filename: {filename_hint}\n\nТранскрипция:\n{raw_text}"
    return f"Транскрипция:\n{raw_text}"


async def _call_openai(raw_text: str, filename_hint: Optional[str]) -> str:
    response = await client.chat.completions.create(
        model=settings.GPT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(raw_text, filename_hint)},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


async def format_transcript(
    raw_text: str,
    filename_hint: Optional[str] = None,
    on_progress_fraction: Optional[FractionCallback] = None,
) -> str:
    """Post-process a raw Whisper transcript: add title, paragraphs, speaker labels.

    On any failure returns raw_text unchanged (bot flow must not break).
    """
    if not raw_text.strip():
        return raw_text

    try:
        if on_progress_fraction is None:
            return await _call_openai(raw_text, filename_hint)
        expected_seconds = max(3.0, len(raw_text) / 2000)
        return await run_with_fake_progress(
            _call_openai(raw_text, filename_hint),
            on_progress_fraction,
            expected_seconds,
        )
    except Exception:
        logger.exception("format_transcript failed; falling back to raw text")
        return raw_text
