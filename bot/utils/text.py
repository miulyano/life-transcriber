import hashlib
import time
from typing import Optional

from aiogram.types import (
    BufferedInputFile,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

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


def build_keyboard(text: str, text_hash: str, send_as_file: bool) -> Optional[InlineKeyboardMarkup]:
    rows = []

    if not send_as_file:
        if len(text) <= 256:
            copy_btn = InlineKeyboardButton(
                text="📋 Скопировать текст",
                copy_text=CopyTextButton(text=text),
            )
        else:
            copy_btn = InlineKeyboardButton(
                text="📋 Скопировать текст",
                callback_data=f"copy:{text_hash}",
            )
        rows.append([copy_btn])

    if len(text) >= settings.MIN_SUMMARY_LEN:
        rows.append(
            [InlineKeyboardButton(text="📝 Краткий конспект", callback_data=f"summary:{text_hash}")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def reply_text_or_file(message: Message, text: str) -> None:
    text_hash = _store_text(text)

    if len(text) <= settings.LONG_TEXT_THRESHOLD:
        keyboard = build_keyboard(text, text_hash, send_as_file=False)
        await message.reply(text, reply_markup=keyboard)
    else:
        keyboard = build_keyboard(text, text_hash, send_as_file=True)
        file_bytes = text.encode("utf-8")
        await message.reply_document(
            BufferedInputFile(file_bytes, filename="transcript.txt"),
            caption="Транскрипция готова.",
            reply_markup=keyboard,
        )
