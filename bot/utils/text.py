import hashlib
import html
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
from bot.utils.filename import (
    build_filename,
    extract_title,
    split_header_and_body,
)

TELEGRAM_CAPTION_LIMIT = 1024
CHANNEL_LINE_PREFIX = "📺 Канал: "

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
    caption_html: str
    body_html: str
    filename: str


def _truncate_caption(caption: str) -> str:
    if len(caption) <= TELEGRAM_CAPTION_LIMIT:
        return caption
    return caption[: TELEGRAM_CAPTION_LIMIT - 1].rstrip() + "…"


def _wrap_header_as_html(header_plain: str) -> str:
    """Render a plain header (title + optional ``📺 Канал: …`` line) as HTML.

    Title goes inside ``<b>`` and the channel line — inside ``<i>``.  All
    user-supplied substrings are HTML-escaped so a title containing ``<`` /
    ``>`` / ``&`` cannot break Telegram entity parsing.
    """
    if not header_plain:
        return ""
    lines = header_plain.splitlines()
    title = lines[0] if lines else ""
    parts = [f"<b>{html.escape(title, quote=False)}</b>"] if title else []
    for line in lines[1:]:
        if line.startswith(CHANNEL_LINE_PREFIX):
            parts.append(f"<i>{html.escape(line, quote=False)}</i>")
        elif line.strip():
            parts.append(html.escape(line, quote=False))
    return "\n".join(parts)


def render_html_message(text: str) -> str:
    """Full Telegram-HTML rendering: formatted header + escaped body.

    A "header" exists only when ``text`` has a ``\\n\\n`` separator — i.e. the
    transcript pipeline put title (and optional channel line) before the body.
    Without a separator the whole text is body and is sent as plain (escaped).
    """
    if "\n\n" not in text:
        return html.escape(text, quote=False)
    header_plain, body = split_header_and_body(text)
    header_html = _wrap_header_as_html(header_plain)
    body_html = html.escape(body, quote=False)
    if not header_html:
        return body_html
    if not body_html:
        return header_html
    return f"{header_html}\n\n{body_html}"


def render_html_caption(text: str) -> str:
    """Caption (header only) as HTML, truncated to Telegram's caption limit.

    Caption is built only from the header — body is dropped. If there's no
    ``\\n\\n`` separator the text is body-only, so caption is empty.
    """
    if "\n\n" not in text:
        return ""
    header_plain, _body = split_header_and_body(text)
    if not header_plain:
        return ""
    truncated = _truncate_caption(header_plain)
    return _wrap_header_as_html(truncated)


def prepare_transcript(text: str) -> _TranscriptPrep:
    """Compute all delivery parameters for a transcript without sending."""
    h = _store_text(text)
    send_as_file = len(text) > settings.LONG_TEXT_THRESHOLD
    kb = build_keyboard(text, h, send_as_file=send_as_file)
    title = extract_title(text) or ""
    caption_html = render_html_caption(text)
    body_html = render_html_message(text)
    return _TranscriptPrep(
        send_as_file=send_as_file,
        keyboard=kb,
        title=title,
        caption_html=caption_html,
        body_html=body_html,
        filename=build_filename(title),
    )


async def reply_text_or_file(message: Message, text: str) -> None:
    d = prepare_transcript(text)
    if not d.send_as_file:
        await message.reply(d.body_html, reply_markup=d.keyboard)
    else:
        caption = d.caption_html or "Транскрибация готова."
        await message.reply_document(
            BufferedInputFile(text.encode("utf-8"), filename=d.filename),
            caption=caption,
            reply_markup=d.keyboard,
        )
