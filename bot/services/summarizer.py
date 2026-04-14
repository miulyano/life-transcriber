from openai import AsyncOpenAI

from bot.config import settings

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = (
    "Ты — помощник по созданию конспектов. "
    "Сделай краткий конспект основных мыслей из предоставленного текста. "
    "Выдели ключевые идеи в виде коротких тезисов. "
    "Отвечай на том же языке, что и исходный текст."
)


async def summarize(text: str) -> str:
    response = await client.chat.completions.create(
        model=settings.GPT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        max_tokens=1024,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()
