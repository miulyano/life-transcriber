import hashlib
import time
from typing import NamedTuple, Optional

from aiogram.types import (
    BufferedInputFile,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.utils.filename import build_filename, extract_title

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
        if send_as_file:
            rows.append(
                [InlineKeyboardButton(text="🧹 Очистить текст", callback_data=f"cleanup:{text_hash}")]
            )

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


class _TranscriptPrep(NamedTuple):
    send_as_file: bool
    keyboard: Optional[InlineKeyboardMarkup]
    title: str
    filename: str


def prepare_transcript(text: str) -> _TranscriptPrep:
    """Compute all delivery parameters for a transcript without sending."""
    h = _store_text(text)
    send_as_file = len(text) > settings.LONG_TEXT_THRESHOLD
    kb = build_keyboard(text, h, send_as_file=send_as_file)
    title = extract_title(text)
    return _TranscriptPrep(
        send_as_file=send_as_file,
        keyboard=kb,
        title=title,
        filename=build_filename(title),
    )


async def reply_text_or_file(message: Message, text: str) -> None:
    d = prepare_transcript(text)
    if not d.send_as_file:
        await message.reply(text, reply_markup=d.keyboard)
    else:
        await message.reply_document(
            BufferedInputFile(text.encode("utf-8"), filename=d.filename),
            caption=d.title or "Транскрибация готова.",
            reply_markup=d.keyboard,
        )
