from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import InaccessibleMessage, Message

from bot.handlers.callbacks import _extract_text_from_message, handle_cleanup, handle_summary
from bot.utils import text as text_mod


@pytest.fixture(autouse=True)
def clear_cache():
    text_mod._text_cache.clear()
    yield
    text_mod._text_cache.clear()


def _make_callback(*, text=None, document=None, is_inaccessible=False):
    callback = MagicMock()
    callback.bot = MagicMock()
    callback.bot.download = AsyncMock()
    callback.answer = AsyncMock()

    if is_inaccessible:
        msg = MagicMock(spec=InaccessibleMessage)
    else:
        msg = MagicMock(spec=Message)
        msg.text = text
        msg.document = document

    callback.message = msg
    return callback


async def test_extract_inline_text():
    cb = _make_callback(text="hello world")
    result = await _extract_text_from_message(cb)
    assert result == "hello world"


async def test_extract_document_text():
    doc = MagicMock()
    doc.file_id = "file_123"
    cb = _make_callback(document=doc)
    cb.bot.download.return_value = BytesIO("document content".encode("utf-8"))

    result = await _extract_text_from_message(cb)

    assert result == "document content"
    cb.bot.download.assert_awaited_once_with("file_123")


async def test_extract_inaccessible_message():
    cb = _make_callback(is_inaccessible=True)
    result = await _extract_text_from_message(cb)
    assert result is None


async def test_extract_no_text_no_document():
    cb = _make_callback(text=None, document=None)
    result = await _extract_text_from_message(cb)
    assert result is None


async def test_handle_summary_from_cache():
    h = text_mod._store_text("cached text")
    cb = _make_callback(text=None)
    cb.data = f"summary:{h}"
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary result"
        await handle_summary(cb)

    mock_sum.assert_awaited_once_with("cached text")
    cb.answer.assert_any_await("Генерирую конспект...")


async def test_handle_summary_fallback_inline():
    cb = _make_callback(text="fallback text")
    cb.data = "summary:nonexistent_hash"
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary"
        await handle_summary(cb)

    mock_sum.assert_awaited_once_with("fallback text")


async def test_handle_summary_fallback_document():
    doc = MagicMock()
    doc.file_id = "file_456"
    cb = _make_callback(document=doc)
    cb.data = "summary:nonexistent_hash"
    cb.bot.download.return_value = BytesIO("doc text".encode("utf-8"))
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary"
        await handle_summary(cb)

    mock_sum.assert_awaited_once_with("doc text")


async def test_handle_summary_fallback_recaches():
    cb = _make_callback(text="recache me")
    cb.data = "summary:nonexistent_hash"
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary"
        await handle_summary(cb)

    h = text_mod._store_text("recache me")
    assert text_mod.get_cached_text(h) == "recache me"


async def test_handle_summary_no_text_anywhere():
    cb = _make_callback(is_inaccessible=True)
    cb.data = "summary:nonexistent_hash"

    await handle_summary(cb)

    cb.answer.assert_awaited_once()
    call_args = cb.answer.await_args
    assert "Не удалось получить текст" in call_args.args[0]
    assert call_args.kwargs.get("show_alert") is True


async def test_handle_cleanup_from_cache():
    source_text = "Заголовок\n\nНу это, в общем, тестовый текст."
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"cleanup:{h}"
    cb.message.reply_document = AsyncMock()

    with patch("bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock) as mock_cleanup:
        mock_cleanup.return_value = "Заголовок\n\nТестовый текст."
        await handle_cleanup(cb)

    mock_cleanup.assert_awaited_once_with(source_text)
    cb.answer.assert_any_await("Очищаю текст...")
    cb.message.reply_document.assert_awaited_once()


async def test_handle_cleanup_fallback_document():
    doc = MagicMock()
    doc.file_id = "file_789"
    cb = _make_callback(document=doc)
    cb.data = "cleanup:nonexistent_hash"
    cb.bot.download.return_value = BytesIO("Заголовок\n\nНу это текст.".encode("utf-8"))
    cb.message.reply_document = AsyncMock()

    with patch("bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock) as mock_cleanup:
        mock_cleanup.return_value = "Заголовок\n\nТекст."
        await handle_cleanup(cb)

    mock_cleanup.assert_awaited_once_with("Заголовок\n\nНу это текст.")
    cb.message.reply_document.assert_awaited_once()


async def test_handle_cleanup_no_text_anywhere():
    cb = _make_callback(is_inaccessible=True)
    cb.data = "cleanup:nonexistent_hash"

    await handle_cleanup(cb)

    cb.answer.assert_awaited_once()
    call_args = cb.answer.await_args
    assert "Не удалось получить текст" in call_args.args[0]
    assert call_args.kwargs.get("show_alert") is True
