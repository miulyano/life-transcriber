from io import BytesIO
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from aiogram.types import InaccessibleMessage, Message

from bot.handlers.callbacks import (
    _extract_text_from_message,
    handle_cleanup,
    handle_summary,
)
from bot.utils import text as text_mod


@pytest.fixture(autouse=True)
def clear_cache():
    text_mod._text_cache.clear()
    yield
    text_mod._text_cache.clear()


@pytest.fixture(autouse=True)
def mock_progress_reporter():
    """Replace ProgressReporter with a no-op so tests don't try to send/edit
    Telegram messages or spawn the heartbeat asyncio task."""

    class _NoopReporter:
        def __init__(self, *_args, **_kwargs):
            self.set_progress = AsyncMock()
            self.set_phase = AsyncMock()
            self.finish = AsyncMock()
            self.fail = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    with patch("bot.handlers.callbacks.ProgressReporter", _NoopReporter):
        yield


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


async def test_extract_document_strips_timecodes():
    """A downloaded .txt may be the timecoded variant — stamps must not reach GPT."""
    doc = MagicMock()
    doc.file_id = "file_tc"
    cb = _make_callback(document=doc)
    cb.bot.download.return_value = BytesIO(
        "Заголовок\n\nСпикер 1\n[0:00] Привет.\n[0:20] Ещё.".encode("utf-8")
    )

    result = await _extract_text_from_message(cb)

    assert result == "Заголовок\n\nСпикер 1\nПривет.\nЕщё."


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

    mock_sum.assert_awaited_once_with("cached text", on_progress=ANY)
    cb.answer.assert_awaited_once_with()


async def test_handle_summary_fallback_inline():
    cb = _make_callback(text="fallback text")
    cb.data = "summary:nonexistent_hash"
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary"
        await handle_summary(cb)

    mock_sum.assert_awaited_once_with("fallback text", on_progress=ANY)


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

    mock_sum.assert_awaited_once_with("doc text", on_progress=ANY)


async def test_handle_summary_fallback_document_with_timecodes():
    doc = MagicMock()
    doc.file_id = "file_tc2"
    cb = _make_callback(document=doc)
    cb.data = "summary:nonexistent_hash"
    cb.bot.download.return_value = BytesIO(
        "Заголовок\n\n[0:00] Привет.\n[0:20] Пока.".encode("utf-8")
    )
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary"
        await handle_summary(cb)

    mock_sum.assert_awaited_once_with("Привет.\nПока.", on_progress=ANY)


async def test_handle_cleanup_fallback_document_with_timecodes():
    doc = MagicMock()
    doc.file_id = "file_tc3"
    cb = _make_callback(document=doc)
    cb.data = "cleanup:nonexistent_hash"
    cb.bot.download.return_value = BytesIO(
        "Заголовок\n\nСпикер 1\n[0:00] Ну это текст.".encode("utf-8")
    )
    cb.message.reply_document = AsyncMock()

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        mock_cleanup.return_value = "Спикер 1\nТекст."
        await handle_cleanup(cb)

    mock_cleanup.assert_awaited_once_with("Спикер 1\nНу это текст.", on_progress=ANY)


async def test_handle_summary_fallback_recaches():
    cb = _make_callback(text="recache me")
    cb.data = "summary:nonexistent_hash"
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "summary"
        await handle_summary(cb)

    h = text_mod._store_text("recache me")
    assert text_mod.get_cached_text(h) == "recache me"


async def test_handle_summary_long_sends_as_file():
    source_text = "Оригинальный заголовок\n\nДлинный исходный текст."
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"summary:{h}"
    cb.message.reply = AsyncMock()
    cb.message.reply_document = AsyncMock()

    # Summary that exceeds TELEGRAM_TEXT_LIMIT after HTML prefix/wrapping.
    long_summary = "очень длинный конспект " * 500

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = long_summary
        await handle_summary(cb)

    cb.message.reply.assert_not_awaited()
    cb.message.reply_document.assert_awaited_once()
    sent_file = cb.message.reply_document.await_args.args[0]
    body = sent_file.data.decode("utf-8")
    # File contains the raw plain-text summary, not HTML.
    assert body == long_summary
    caption = cb.message.reply_document.await_args.kwargs["caption"]
    assert "Оригинальный заголовок" in caption


async def test_handle_summary_short_still_sends_as_text():
    source_text = "Заголовок\n\nКороткий текст."
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"summary:{h}"
    cb.message.reply = AsyncMock()
    cb.message.reply_document = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "Короткий конспект."
        await handle_summary(cb)

    cb.message.reply.assert_awaited_once()
    cb.message.reply_document.assert_not_awaited()


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

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        mock_cleanup.return_value = "Заголовок\n\nТестовый текст."
        await handle_cleanup(cb)

    # Cleanup receives only the body (no header) so the channel/title line
    # can't be mistaken for a speaker reply.
    mock_cleanup.assert_awaited_once_with(
        "Ну это, в общем, тестовый текст.", on_progress=ANY
    )
    cb.answer.assert_awaited_once_with()
    cb.message.reply_document.assert_awaited_once()
    caption = cb.message.reply_document.await_args.kwargs["caption"]
    assert caption == "Очищенный текст: Заголовок"


async def test_handle_cleanup_fallback_document():
    doc = MagicMock()
    doc.file_id = "file_789"
    cb = _make_callback(document=doc)
    cb.data = "cleanup:nonexistent_hash"
    cb.bot.download.return_value = BytesIO(
        "Заголовок\n\nНу это текст.".encode("utf-8")
    )
    cb.message.reply_document = AsyncMock()

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        mock_cleanup.return_value = "Заголовок\n\nТекст."
        await handle_cleanup(cb)

    mock_cleanup.assert_awaited_once_with("Ну это текст.", on_progress=ANY)
    cb.message.reply_document.assert_awaited_once()


async def test_handle_cleanup_no_text_anywhere():
    cb = _make_callback(is_inaccessible=True)
    cb.data = "cleanup:nonexistent_hash"

    await handle_cleanup(cb)

    cb.answer.assert_awaited_once()
    call_args = cb.answer.await_args
    assert "Не удалось получить текст" in call_args.args[0]
    assert call_args.kwargs.get("show_alert") is True


async def test_handle_cleanup_caption_uses_original_title_not_cleaned_first_line():
    source_text = "Реальный заголовок\n\nДлинный сырой текст."
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"cleanup:{h}"
    cb.message.reply_document = AsyncMock()

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        # Cleanup model rewrote the first line as a paraphrase of the title.
        mock_cleanup.return_value = "Перефразированный заголовок\n\nЧистый текст."
        await handle_cleanup(cb)

    caption = cb.message.reply_document.await_args.kwargs["caption"]
    assert caption == "Очищенный текст: Реальный заголовок"


async def test_handle_cleanup_file_starts_with_original_title_when_dropped():
    source_text = "Настоящий заголовок\n\nТекст с мусором."
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"cleanup:{h}"
    cb.message.reply_document = AsyncMock()

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        # Model dropped the title entirely.
        mock_cleanup.return_value = "Текст без мусора."
        await handle_cleanup(cb)

    sent_file = cb.message.reply_document.await_args.args[0]
    body = sent_file.data.decode("utf-8")
    assert body.startswith("Настоящий заголовок\n\nТекст без мусора.")


# ---------- cleanup/summary: header is split off before LLM ----------


async def test_cleanup_strips_header_before_sending_to_gpt():
    """Header (title + ``📺 Канал: …``) must NOT reach cleanup_transcript —
    otherwise GPT mistakes the channel line for a speaker prefix and rewrites
    the first speaker reply."""
    source_text = (
        "Реальный заголовок\n"
        "📺 Канал: Куда расти?\n"
        "\n"
        "Спикер 1: реальная реплика."
    )
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"cleanup:{h}"
    cb.message.reply_document = AsyncMock()

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        mock_cleanup.return_value = "Спикер 1: реальная реплика."
        await handle_cleanup(cb)

    # GPT got body only — no title, no channel line.
    mock_cleanup.assert_awaited_once_with(
        "Спикер 1: реальная реплика.", on_progress=ANY
    )
    sent_file = cb.message.reply_document.await_args.args[0]
    body = sent_file.data.decode("utf-8")
    # Final file: header restored verbatim, then cleaned body.
    assert body == (
        "Реальный заголовок\n📺 Канал: Куда расти?\n\nСпикер 1: реальная реплика."
    )


async def test_cleanup_without_header_sends_full_text():
    """If the cached text has no blank-line separator, treat all of it as body."""
    source_text = "Просто длинный текст без шапки."
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"cleanup:{h}"
    cb.message.reply_document = AsyncMock()

    with patch(
        "bot.handlers.callbacks.cleanup_transcript", new_callable=AsyncMock
    ) as mock_cleanup:
        mock_cleanup.return_value = "Очищенный текст."
        await handle_cleanup(cb)

    mock_cleanup.assert_awaited_once_with(source_text, on_progress=ANY)
    sent_file = cb.message.reply_document.await_args.args[0]
    assert sent_file.data.decode("utf-8") == "Очищенный текст."


async def test_summary_strips_header_before_sending_to_gpt():
    source_text = (
        "Заголовок\n"
        "📺 Канал: Куда расти?\n"
        "\n"
        "Длинный исходный текст для конспекта."
    )
    h = text_mod._store_text(source_text)
    cb = _make_callback(text=None)
    cb.data = f"summary:{h}"
    cb.message.reply = AsyncMock()

    with patch("bot.handlers.callbacks.summarize", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "Короткий конспект."
        await handle_summary(cb)

    mock_sum.assert_awaited_once_with(
        "Длинный исходный текст для конспекта.", on_progress=ANY
    )
