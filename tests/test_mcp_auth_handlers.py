"""Тесты bot-стороны pairing-флоу: deep link, pairing-код, кнопки, /mcp."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.handlers.mcp_auth as mcp_auth_module
from bot.config import settings
from bot.handlers.commands import WELCOME_TEXT
from bot.handlers.mcp_auth import (
    PAIRING_CODE_RE,
    handle_deep_link,
    handle_mcp_command,
    handle_pairing_approve,
    handle_pairing_code,
    handle_pairing_decline,
    handle_token_revoke,
)
from bot.services.token_store import TokenStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = TokenStore(db_path=str(tmp_path / "tokens.db"))
    monkeypatch.setattr(mcp_auth_module, "get_token_store", lambda: s)
    return s


def _message(user_id=111, text=""):
    message = MagicMock()
    message.from_user = SimpleNamespace(id=user_id)
    message.text = text
    message.answer = AsyncMock()
    return message


def _callback(user_id=111, data=""):
    callback = MagicMock()
    callback.from_user = SimpleNamespace(id=user_id)
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    return callback


# ---------------------------------------------------------------- deep link


async def test_deep_link_shows_confirm_buttons(store):
    req, _ = await store.create_request(agent_name="claude")
    message = _message(user_id=111)
    command = SimpleNamespace(args=f"mcpauth_{req.id}")

    await handle_deep_link(message, command)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    kwargs = message.answer.await_args.kwargs
    assert "claude" in text
    keyboard = kwargs["reply_markup"].inline_keyboard
    datas = [btn.callback_data for row in keyboard for btn in row]
    assert f"mcpauth:ok:{req.id}" in datas
    assert f"mcpauth:no:{req.id}" in datas
    # запрос привязан к отправителю
    got = await store.get_request(req.id)
    assert got.user_id == 111


async def test_deep_link_unknown_request(store):
    message = _message()
    command = SimpleNamespace(args="mcpauth_nonexistent")

    await handle_deep_link(message, command)

    text = message.answer.await_args.args[0]
    assert "устарел" in text or "не найден" in text


async def test_deep_link_already_bound_to_other_user(store):
    req, _ = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=222)
    message = _message(user_id=111)
    command = SimpleNamespace(args=f"mcpauth_{req.id}")

    await handle_deep_link(message, command)

    text = message.answer.await_args.args[0]
    assert "другому пользователю" in text


# ------------------------------------------------------------- pairing code


def test_pairing_code_filter_matches_only_codes():
    assert PAIRING_CODE_RE.match("A7X9K2")
    assert PAIRING_CODE_RE.match("a7x9k2")  # нормализуется к upper
    assert not PAIRING_CODE_RE.match("A7X9K")  # 5 символов
    assert not PAIRING_CODE_RE.match("A7X9K22")  # 7 символов
    assert not PAIRING_CODE_RE.match("привет")
    assert not PAIRING_CODE_RE.match("A7 9K2")


async def test_pairing_code_binds_and_asks(store):
    req, _ = await store.create_request(agent_name="claude")
    message = _message(user_id=111, text=req.pairing_code.lower())

    await handle_pairing_code(message)

    text = message.answer.await_args.args[0]
    assert "claude" in text
    got = await store.get_request(req.id)
    assert got.user_id == 111


async def test_pairing_code_not_found(store):
    message = _message(text="ZZZZZ9")

    await handle_pairing_code(message)

    text = message.answer.await_args.args[0]
    assert "не найден" in text.lower() or "истёк" in text.lower()


# ------------------------------------------------------------------ buttons


async def test_approve_issues_token_and_edits(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    callback = _callback(user_id=111, data=f"mcpauth:ok:{req.id}")

    await handle_pairing_approve(callback)

    edited = callback.message.edit_text.await_args.args[0]
    assert "✅" in edited and "claude" in edited and "/mcp" in edited
    result = await store.poll(req.id, poll_secret)
    assert result.status == "approved" and result.token


async def test_approve_by_foreign_user_rejected(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    callback = _callback(user_id=222, data=f"mcpauth:ok:{req.id}")

    await handle_pairing_approve(callback)

    callback.message.edit_text.assert_not_awaited()
    result = await store.poll(req.id, poll_secret)
    assert result.status == "pending"


async def test_decline(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    callback = _callback(user_id=111, data=f"mcpauth:no:{req.id}")

    await handle_pairing_decline(callback)

    result = await store.poll(req.id, poll_secret)
    assert result.status == "declined"


async def test_revoke_own_token(store):
    _, record = await store.create_token(user_id=111, label="claude")
    callback = _callback(user_id=111, data=f"mcpauth:revoke:{record.id}")

    await handle_token_revoke(callback)

    assert await store.list_tokens(111) == []


async def test_revoke_foreign_token_rejected(store):
    _, record = await store.create_token(user_id=111, label="claude")
    callback = _callback(user_id=222, data=f"mcpauth:revoke:{record.id}")

    await handle_token_revoke(callback)

    assert len(await store.list_tokens(111)) == 1


# ------------------------------------------------------------- /mcp command


async def test_mcp_command_shows_url_and_tokens(store, monkeypatch):
    monkeypatch.setattr(settings, "WEBAPP_URL", "https://transcriber.example.com/")
    await store.create_token(user_id=111, label="claude")
    message = _message(user_id=111)

    await handle_mcp_command(message)

    text = message.answer.await_args.args[0]
    # rstrip слэша: не https://…//mcp
    assert "https://transcriber.example.com/mcp" in text
    assert "//mcp" not in text.replace("://", "")
    assert "claude mcp add" in text
    assert "claude" in text  # список токенов


async def test_mcp_command_without_webapp_url(store, monkeypatch):
    monkeypatch.setattr(settings, "WEBAPP_URL", "")
    message = _message(user_id=111)

    await handle_mcp_command(message)

    text = message.answer.await_args.args[0]
    assert "WEBAPP_URL" in text


# ------------------------------------------------------------ discoverability


def test_welcome_text_mentions_mcp():
    assert "/mcp" in WELCOME_TEXT
