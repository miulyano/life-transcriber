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


async def test_short_text_sent_inline():
    message = MagicMock()
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    short_text = "x" * 100  # well under threshold
    await reply_text_or_file(message, short_text)

    message.reply.assert_awaited_once()
    message.reply_document.assert_not_called()
    # Check text passed as first positional arg
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
