import hashlib
import html
import re
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
from bot.utils.source_labels import format_source_line

TELEGRAM_CAPTION_LIMIT = 1024
CHANNEL_LINE_PREFIX = "📺 Канал: "

# [0:15.250] / [1:43:06.021] at the start of a line (timecoded file variant);
# the .mmm part is optional for files produced by older versions.
TIMECODE_RE = re.compile(r"^\[\d{1,2}(?::\d{2}){1,2}(?:\.\d{3})?\] ", re.MULTILINE)


def strip_timecodes(text: str) -> str:
    """Remove leading [m:ss.mmm] / [h:mm:ss.mmm] stamps from a timecoded transcript."""
    return TIMECODE_RE.sub("", text)

# In-memory store: {hash: (text, source_type, timestamp)}
_text_cache: dict[str, tuple[str, Optional[str], float]] = {}
CACHE_TTL = 600  # 10 minutes


def _store_text(text: str, source_type: Optional[str] = None) -> str:
    _evict_expired()
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    _text_cache[h] = (text, source_type, time.monotonic())
    return h


def get_cached_text(h: str) -> Optional[str]:
    entry = _text_cache.get(h)
    if entry is None:
        return None
    text, _source_type, ts = entry
    if time.monotonic() - ts > CACHE_TTL:
        del _text_cache[h]
        return None
    return text


def get_cached_source_type(h: str) -> Optional[str]:
    entry = _text_cache.get(h)
    if entry is None:
        return None
    _text, source_type, ts = entry
    if time.monotonic() - ts > CACHE_TTL:
        del _text_cache[h]
        return None
    return source_type


def _evict_expired() -> None:
    now = time.monotonic()
    expired = [k for k, (_, _, ts) in _text_cache.items() if now - ts > CACHE_TTL]
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


def _truncate_caption(caption: str, reserve: int = 0) -> str:
    limit = TELEGRAM_CAPTION_LIMIT - reserve
    if len(caption) <= limit:
        return caption
    return caption[: limit - 1].rstrip() + "…"


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


def render_html_caption(text: str, footer: str = "") -> str:
    """Caption (header only) as HTML, truncated to Telegram's caption limit.

    Caption is built only from the header — body is dropped. If there's no
    ``\\n\\n`` separator the text is body-only, so caption is empty.
    ``footer`` (plain text) is appended as the last italic line; the header
    is truncated so header + footer stays within the caption limit.
    """
    header_plain = ""
    if "\n\n" in text:
        header_plain, _body = split_header_and_body(text)
    footer_html = f"<i>{html.escape(footer, quote=False)}</i>" if footer else ""
    if not header_plain:
        return footer_html
    truncated = _truncate_caption(header_plain, reserve=len(footer) + 1 if footer else 0)
    header_html = _wrap_header_as_html(truncated)
    if not footer_html:
        return header_html
    return f"{header_html}\n{footer_html}"


def prepare_transcript(
    text: str,
    *,
    source_type: Optional[str] = None,
    has_timecoded_file: bool = False,
) -> _TranscriptPrep:
    """Compute all delivery parameters for a transcript without sending.

    When ``source_type`` is given, a footer line like
    ``Источник: YouTube · 🕒 с таймкодами`` is appended to the caption (file
    path) and to the message body (inline path). ``has_timecoded_file`` says
    whether the document that will be sent is the timecoded variant; inline
    messages are never timecoded.
    """
    h = _store_text(text, source_type)
    send_as_file = len(text) > settings.LONG_TEXT_THRESHOLD
    kb = build_keyboard(text, h, send_as_file=send_as_file)
    title = extract_title(text) or ""
    footer = ""
    if source_type is not None:
        footer = format_source_line(
            source_type, timecoded=send_as_file and has_timecoded_file
        )
    caption_html = render_html_caption(text, footer)
    body_html = render_html_message(text)
    if footer and not send_as_file:
        body_html = f"{body_html}\n\n<i>{html.escape(footer, quote=False)}</i>"
    return _TranscriptPrep(
        send_as_file=send_as_file,
        keyboard=kb,
        title=title,
        caption_html=caption_html,
        body_html=body_html,
        filename=build_filename(title),
    )


async def reply_text_or_file(
    message: Message,
    text: str,
    file_text: Optional[str] = None,
    *,
    source_type: Optional[str] = None,
) -> None:
    """Reply inline or as a document.

    Everything (file/inline threshold, cache, keyboard, caption, filename) is
    derived from the plain ``text``; ``file_text`` only replaces the document
    content — e.g. the timecoded transcript variant. ``source_type`` adds the
    source/timecodes footer to the caption or message body.
    """
    d = prepare_transcript(
        text, source_type=source_type, has_timecoded_file=file_text is not None
    )
    if not d.send_as_file:
        await message.reply(d.body_html, reply_markup=d.keyboard)
    else:
        caption = d.caption_html or "Транскрибация готова."
        await message.reply_document(
            BufferedInputFile((file_text or text).encode("utf-8"), filename=d.filename),
            caption=caption,
            reply_markup=d.keyboard,
        )
