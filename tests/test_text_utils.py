from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.utils import text as text_mod
from bot.utils.text import (
    CACHE_TTL,
    _store_text,
    build_keyboard,
    get_cached_text,
    reply_text_or_file,
)


@pytest.fixture(autouse=True)
def clear_cache():
    text_mod._text_cache.clear()
    yield
    text_mod._text_cache.clear()


def test_store_returns_16_char_hash():
    h = _store_text("hello")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_store_and_retrieve():
    h = _store_text("some transcription text")
    assert get_cached_text(h) == "some transcription text"


def test_same_text_same_hash():
    h1 = _store_text("hello")
    h2 = _store_text("hello")
    assert h1 == h2


def test_different_text_different_hash():
    h1 = _store_text("hello")
    h2 = _store_text("world")
    assert h1 != h2


def test_get_nonexistent_returns_none():
    assert get_cached_text("nonexistent_hash") is None


def test_ttl_expiration(monkeypatch):
    times = [100.0]
    monkeypatch.setattr(text_mod.time, "monotonic", lambda: times[0])

    h = _store_text("will expire")
    assert get_cached_text(h) == "will expire"

    # Jump past TTL
    times[0] = 100.0 + CACHE_TTL + 1
    assert get_cached_text(h) is None
    # Entry is evicted from cache
    assert h not in text_mod._text_cache


def test_ttl_not_expired_yet(monkeypatch):
    times = [100.0]
    monkeypatch.setattr(text_mod.time, "monotonic", lambda: times[0])

    h = _store_text("still fresh")
    times[0] = 100.0 + CACHE_TTL - 1
    assert get_cached_text(h) == "still fresh"


def test_build_keyboard_summary_button():
    # Long enough text to trigger summary button, sent inline
    text = "x" * 600
    h = _store_text(text)
    kb = build_keyboard(text, h, send_as_file=False)
    # First row: copy, second row: summary
    summary_btn = kb.inline_keyboard[1][0]
    assert summary_btn.callback_data == f"summary:{h}"
    assert "конспект" in summary_btn.text.lower()


def test_build_keyboard_cleanup_button_on_file():
    text = "x" * 2500
    h = _store_text(text)
    kb = build_keyboard(text, h, send_as_file=True)

    cleanup_btn = kb.inline_keyboard[1][0]
    assert cleanup_btn.callback_data == f"cleanup:{h}"
    assert "очист" in cleanup_btn.text.lower()


def test_build_keyboard_no_summary_on_short_text():
    # Short text should not have a summary button
    text = "x" * 100
    h = _store_text(text)
    kb = build_keyboard(text, h, send_as_file=False)
    all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert not any(cb.startswith("summary:") for cb in all_callbacks)


def test_build_keyboard_no_copy_on_file():
    # File mode should have no copy button
    text = "x" * 2500
    h = _store_text(text)
    kb = build_keyboard(text, h, send_as_file=True)
    all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert not any(cb.startswith("copy:") for cb in all_callbacks)
    assert any(cb.startswith("summary:") for cb in all_callbacks)
    assert any(cb.startswith("cleanup:") for cb in all_callbacks)


async def test_short_text_sent_inline():
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    short_text = "x" * 100  # well under threshold
    await reply_text_or_file(message, short_text)

    message.reply.assert_awaited_once()
    message.reply_document.assert_not_called()
    # Header-less text (no \n\n) — sent as plain (HTML-escaped), no <b> wrap.
    assert message.reply.await_args.args[0] == short_text


async def test_long_text_sent_as_file():
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    long_text = "x" * 3000  # > 2000 threshold
    await reply_text_or_file(message, long_text)

    message.reply_document.assert_awaited_once()
    message.reply.assert_not_called()


async def test_threshold_boundary_inline():
    """Exactly threshold length should still go inline (≤)."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    from bot.config import settings
    boundary_text = "x" * settings.LONG_TEXT_THRESHOLD
    await reply_text_or_file(message, boundary_text)

    message.reply.assert_awaited_once()
    message.reply_document.assert_not_called()


async def test_threshold_boundary_file():
    """One char over threshold should go as file."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    from bot.config import settings
    over_text = "x" * (settings.LONG_TEXT_THRESHOLD + 1)
    await reply_text_or_file(message, over_text)

    message.reply_document.assert_awaited_once()
    message.reply.assert_not_called()


async def test_long_text_caption_html_formats_title_and_channel():
    """Caption uses HTML: <b>title</b> + <i>📺 Канал: …</i>."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    title = "Подкаст про AI"
    uploader = "ШАД"
    body_text = "x" * 3000  # > LONG_TEXT_THRESHOLD → file
    full = f"{title}\n📺 Канал: {uploader}\n\n{body_text}"
    await reply_text_or_file(message, full)

    caption = message.reply_document.await_args.kwargs["caption"]
    assert caption == f"<b>{title}</b>\n<i>📺 Канал: {uploader}</i>"


async def test_long_text_caption_without_channel():
    """When there is no 'Канал:' line, caption is just the bold title."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    title = "Просто заголовок"
    body_text = "x" * 3000
    full = f"{title}\n\n{body_text}"
    await reply_text_or_file(message, full)

    caption = message.reply_document.await_args.kwargs["caption"]
    assert caption == f"<b>{title}</b>"


async def test_long_text_caption_truncated_when_too_long():
    """Caption header longer than Telegram limit (1024) is truncated before HTML wrap."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    title = "T" * 1100  # already over the cap
    body_text = "x" * 3000
    full = f"{title}\n\n{body_text}"
    await reply_text_or_file(message, full)

    caption = message.reply_document.await_args.kwargs["caption"]
    # Visible text (without HTML tags) must stay within Telegram's caption limit.
    visible = caption.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    assert len(visible) <= 1024
    assert visible.endswith("…")


async def test_caption_and_body_escape_html_special_chars():
    """Title/uploader/body with '<>&' must be HTML-escaped to avoid breaking entity parsing."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    body_text = "spkr: <hello> & <world>" + "x" * 3000
    full = f"<title & more>\n📺 Канал: A&B <x>\n\n{body_text}"
    await reply_text_or_file(message, full)

    caption = message.reply_document.await_args.kwargs["caption"]
    assert "&lt;title &amp; more&gt;" in caption
    assert "&lt;x&gt;" in caption
    # Tags themselves are not escaped — they remain valid HTML markup.
    assert caption.startswith("<b>")
    assert "</b>" in caption and "<i>" in caption


async def test_inline_short_with_header_formats_html():
    """Short text with header → inline reply with <b>title</b> + <i>📺 Канал: …</i> + body."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    full = "Заголовок\n📺 Канал: ШАД\n\nКороткий текст реплики."
    await reply_text_or_file(message, full)

    sent = message.reply.await_args.args[0]
    assert sent == (
        "<b>Заголовок</b>\n<i>📺 Канал: ШАД</i>\n\nКороткий текст реплики."
    )


async def test_file_content_uses_file_text_variant():
    """Document body comes from file_text; caption/keyboard/cache from plain text."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    plain = "Заголовок\n\n" + "x" * 3000
    timecoded = "Заголовок\n\n[0:00.000] " + "x" * 3000
    await reply_text_or_file(message, plain, timecoded)

    sent_file = message.reply_document.await_args.args[0]
    assert sent_file.data.decode("utf-8") == timecoded
    caption = message.reply_document.await_args.kwargs["caption"]
    assert caption == "<b>Заголовок</b>"
    # Cache (summary/cleanup source) keeps the plain variant.
    keyboard = message.reply_document.await_args.kwargs["reply_markup"]
    summary_cb = keyboard.inline_keyboard[0][0].callback_data
    h = summary_cb.split(":", 1)[1]
    assert get_cached_text(h) == plain


async def test_inline_ignores_file_text():
    """Threshold is decided by plain text; short plain goes inline without stamps."""
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    plain = "x" * 100
    await reply_text_or_file(message, plain, "[0:00.000] " + "x" * 3000)

    message.reply.assert_awaited_once()
    message.reply_document.assert_not_called()
    assert message.reply.await_args.args[0] == plain


def test_strip_timecodes_removes_leading_stamps():
    text = (
        "Заголовок\n\nСпикер 1\n[0:00.000] Привет.\n[12:40.250] Середина.\n"
        "[1:43:06.021] Поздняя реплика."
    )
    assert text_mod.strip_timecodes(text) == (
        "Заголовок\n\nСпикер 1\nПривет.\nСередина.\nПоздняя реплика."
    )


def test_strip_timecodes_handles_legacy_format_without_millis():
    text = "[0:00] Привет.\n[1:43:06] Поздняя реплика."
    assert text_mod.strip_timecodes(text) == "Привет.\nПоздняя реплика."


def test_strip_timecodes_keeps_mid_line_brackets():
    text = "Смотри [12:34] в записи."
    assert text_mod.strip_timecodes(text) == text


async def test_reply_caches_text_via_copy_button():
    """After reply_text_or_file, text is retrievable via copy button hash (inline text > 256 chars)."""
    message = MagicMock()
    message.reply = AsyncMock()

    text = "x" * 300  # > 256 so copy uses callback_data, short enough for inline
    await reply_text_or_file(message, text)

    kwargs = message.reply.await_args.kwargs
    keyboard = kwargs["reply_markup"]
    # First row is copy button with callback_data "copy:<hash>"
    copy_cb = keyboard.inline_keyboard[0][0].callback_data
    assert copy_cb.startswith("copy:")
    hash_from_cb = copy_cb.split(":", 1)[1]
    assert get_cached_text(hash_from_cb) == text
