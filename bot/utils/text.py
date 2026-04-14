import hashlib
import time
from typing import Optional

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings

# In-memory store: {hash: (text, timestamp)}
_text_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 600  # 10 minutes


def _store_text(text: str) -> str:
    _evict_expired()
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    _text_cache[h] = (text, time.monotonic())
    return h


def get_cached_text(h: str) -> Optional[str]:
    entry = _text_cache.get(h)
    if entry is None:
        return None
    text, ts = entry
    if time.monotonic() - ts > CACHE_TTL:
        del _text_cache[h]
        return None
    return text


def _evict_expired() -> None:
    now = time.monotonic()
    expired = [k for k, (_, ts) in _text_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _text_cache[k]


def summary_keyboard(text_hash: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Краткий конспект", callback_data=f"summary:{text_hash}")]
        ]
    )


async def reply_text_or_file(message: Message, text: str) -> None:
    text_hash = _store_text(text)
    keyboard = summary_keyboard(text_hash)

    if len(text) <= settings.LONG_TEXT_THRESHOLD:
        await message.reply(text, reply_markup=keyboard)
    else:
        file_bytes = text.encode("utf-8")
        await message.reply_document(
            BufferedInputFile(file_bytes, filename="transcript.txt"),
            caption="Транскрипция готова.",
            reply_markup=keyboard,
        )
