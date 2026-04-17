"""Tests for send_transcript_to_chat (webapp delivery layer)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webapp.delivery import send_transcript_to_chat


@pytest.fixture()
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_document = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_short_text_sends_message(mock_bot):
    text = "короткая транскрипция"
    await send_transcript_to_chat(mock_bot, chat_id=111, text=text)

    mock_bot.send_message.assert_called_once()
    call_args = mock_bot.send_message.call_args
    assert call_args.args[0] == 111
    assert call_args.args[1] == text
    mock_bot.send_document.assert_not_called()


@pytest.mark.asyncio
async def test_long_text_sends_document(mock_bot):
    # 2001 chars > LONG_TEXT_THRESHOLD (2000)
    text = "а" * 2001
    await send_transcript_to_chat(mock_bot, chat_id=222, text=text)

    mock_bot.send_document.assert_called_once()
    call_kwargs = mock_bot.send_document.call_args
    assert call_kwargs.args[0] == 222
    # BufferedInputFile should be the second positional arg
    from aiogram.types import BufferedInputFile
    assert isinstance(call_kwargs.args[1], BufferedInputFile)
    mock_bot.send_message.assert_not_called()
