"""Тесты MCP bearer-auth: ASGI middleware + расширенный резолвер deps."""
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from bot.config import settings
from bot.services.token_store import TokenStore
from webapp.deps import resolve_bearer_token
from webapp.mcp_auth import MCPBearerAuth, current_user_id


@pytest.fixture()
def token_store(tmp_path, monkeypatch):
    store = TokenStore(db_path=str(tmp_path / "tokens.db"))
    import bot.services.token_store as ts_module

    monkeypatch.setattr(ts_module, "_default_store", store)
    return store


@pytest.fixture()
def legacy_tokens(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKENS", "legacy-token:111")
    settings.__dict__.pop("api_tokens", None)
    yield
    settings.__dict__.pop("api_tokens", None)


# --------------------------------------------------------- resolve_bearer_token


async def test_resolver_db_token(token_store):
    token, _ = await token_store.create_token(user_id=333, label="agent")
    assert await resolve_bearer_token(token) == 333


async def test_resolver_revoked_db_token(token_store):
    token, record = await token_store.create_token(user_id=333, label="agent")
    await token_store.revoke_token(record.id, user_id=333)
    assert await resolve_bearer_token(token) is None


async def test_resolver_legacy_env_fallback(token_store, legacy_tokens):
    assert await resolve_bearer_token("legacy-token") == 111


async def test_resolver_unknown_token(token_store, legacy_tokens):
    assert await resolve_bearer_token("nope") is None


# ------------------------------------------------------------ MCPBearerAuth


class _App:
    """Захватывает ContextVar в момент обработки запроса."""

    def __init__(self):
        self.seen_user_ids = []

    async def __call__(self, scope, receive, send):
        self.seen_user_ids.append(current_user_id.get())
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(headers: list[tuple[bytes, bytes]]):
    return {"type": "http", "method": "POST", "path": "/", "headers": headers}


async def _call(app, scope):
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send)
    return sent


async def test_middleware_valid_token_sets_contextvar(token_store):
    token, _ = await token_store.create_token(user_id=333, label="agent")
    inner = _App()
    app = MCPBearerAuth(inner)

    await _call(app, _scope([(b"authorization", f"Bearer {token}".encode())]))

    assert inner.seen_user_ids == [333]


async def test_middleware_no_token_sets_none(token_store):
    inner = _App()
    app = MCPBearerAuth(inner)

    await _call(app, _scope([]))

    assert inner.seen_user_ids == [None]  # запрос проходит: auth-тулы без токена


async def test_middleware_invalid_token_sets_none(token_store):
    inner = _App()
    app = MCPBearerAuth(inner)

    await _call(app, _scope([(b"authorization", b"Bearer garbage")]))

    assert inner.seen_user_ids == [None]


async def test_middleware_resets_contextvar(token_store):
    token, _ = await token_store.create_token(user_id=333, label="agent")
    app = MCPBearerAuth(_App())
    await _call(app, _scope([(b"authorization", f"Bearer {token}".encode())]))
    # вне запроса контекст чист
    assert current_user_id.get() is None


# ----------------------------------------------- error guard (не 500 наружу)


class _BoomApp:
    """Инner-приложение, падающее до отправки ответа."""

    async def __call__(self, scope, receive, send):
        raise RuntimeError("boom")


class _BoomAfterStartApp:
    """Падает после http.response.start — ответ уже начат."""

    async def __call__(self, scope, receive, send):
        await send(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        raise RuntimeError("late boom")


def _sent_json_body(sent):
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return json.loads(body)


async def test_middleware_inner_exception_returns_jsonrpc_error(token_store):
    """Исключение внутри MCP-стека → JSON-RPC error, не сырой 500 uvicorn'а."""
    app = MCPBearerAuth(_BoomApp())

    sent = await _call(app, _scope([]))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 500
    payload = _sent_json_body(sent)
    assert payload["jsonrpc"] == "2.0"
    assert payload["error"]["code"] == -32603


async def test_middleware_auth_resolve_error_returns_jsonrpc_error(monkeypatch):
    """Сбой SQLite при резолве токена → JSON-RPC error, а не необработанное
    исключение (наблюдалось как 500 c ExceptionGroup в проде)."""

    async def _fail(token, touch=False):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("webapp.mcp_auth.resolve_bearer_token", _fail)
    inner = _App()
    app = MCPBearerAuth(inner)

    sent = await _call(app, _scope([(b"authorization", b"Bearer tok")]))

    assert inner.seen_user_ids == []  # до inner-приложения не дошли
    assert sent[0]["status"] == 500
    payload = _sent_json_body(sent)
    assert payload["error"]["code"] == -32603


async def test_middleware_exception_after_response_started_reraises(token_store):
    """Ответ уже начат — второй http.response.start недопустим, исключение
    отдаётся наверх (uvicorn закроет соединение)."""
    app = MCPBearerAuth(_BoomAfterStartApp())

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    with pytest.raises(RuntimeError, match="late boom"):
        await app(_scope([]), receive, send)

    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1


async def test_middleware_contextvar_reset_after_exception(token_store):
    app = MCPBearerAuth(_BoomApp())
    await _call(app, _scope([]))
    assert current_user_id.get() is None


async def test_middleware_non_http_passthrough(token_store):
    called = []

    async def inner(scope, receive, send):
        called.append(scope["type"])

    app = MCPBearerAuth(inner)
    await app({"type": "lifespan"}, AsyncMock(), AsyncMock())
    assert called == ["lifespan"]
