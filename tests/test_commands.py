from unittest.mock import AsyncMock, MagicMock

from bot.handlers.commands import WELCOME_TEXT, handle_start


def test_welcome_text_mentions_key_features():
    assert "Голосовые" in WELCOME_TEXT
    assert "20 MB" in WELCOME_TEXT
    assert "Mini App" in WELCOME_TEXT
    assert "YouTube" in WELCOME_TEXT
    assert "Яндекс Диск" in WELCOME_TEXT


def test_welcome_text_uses_html_bold():
    assert "<b>" in WELCOME_TEXT and "</b>" in WELCOME_TEXT


async def test_handle_start_sends_welcome():
    message = MagicMock()
    message.answer = AsyncMock()

    await handle_start(message)

    message.answer.assert_awaited_once_with(WELCOME_TEXT)
