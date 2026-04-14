from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.utils import text as text_mod
from bot.utils.text import (
    CACHE_TTL,
    _store_text,
    get_cached_text,
    reply_text_or_file,
    summary_keyboard,
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


def test_summary_keyboard_callback_data():
    kb = summary_keyboard("abc123")
    button = kb.inline_keyboard[0][0]
    assert button.callback_data == "summary:abc123"
    assert "конспект" in button.text.lower()


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


async def test_reply_caches_text_with_summary_keyboard():
    """After reply_text_or_file, the text is retrievable from cache via keyboard hash."""
    message = MagicMock()
    message.reply = AsyncMock()

    text = "cached text to check"
    await reply_text_or_file(message, text)

    # Keyboard was passed with callback_data "summary:<hash>"
    kwargs = message.reply.await_args.kwargs
    keyboard = kwargs["reply_markup"]
    callback_data = keyboard.inline_keyboard[0][0].callback_data
    hash_from_cb = callback_data.split(":", 1)[1]

    assert get_cached_text(hash_from_cb) == text
