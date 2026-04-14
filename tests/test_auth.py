from unittest.mock import AsyncMock, MagicMock

from bot.middlewares.auth import AuthMiddleware


async def test_allows_whitelisted_user():
    mw = AuthMiddleware()
    msg = MagicMock()
    msg.from_user.id = 111  # in ALLOWED_USER_IDS (set by conftest)
    handler = AsyncMock(return_value="handled")

    result = await mw(handler, msg, {})

    handler.assert_awaited_once_with(msg, {})
    assert result == "handled"


async def test_blocks_unknown_user():
    mw = AuthMiddleware()
    msg = MagicMock()
    msg.from_user.id = 9999  # not in whitelist
    handler = AsyncMock()

    result = await mw(handler, msg, {})

    handler.assert_not_called()
    assert result is None


async def test_blocks_when_from_user_is_none():
    mw = AuthMiddleware()
    msg = MagicMock()
    msg.from_user = None
    handler = AsyncMock()

    result = await mw(handler, msg, {})

    handler.assert_not_called()
    assert result is None


async def test_allows_second_whitelisted_user():
    mw = AuthMiddleware()
    msg = MagicMock()
    msg.from_user.id = 222  # also in ALLOWED_USER_IDS
    handler = AsyncMock(return_value="ok")

    result = await mw(handler, msg, {})

    handler.assert_awaited_once()
    assert result == "ok"
