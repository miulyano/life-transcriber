"""Тесты MCP bearer-auth: ASGI middleware + расширенный резолвер deps."""
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


async def test_middleware_non_http_passthrough(token_store):
    called = []

    async def inner(scope, receive, send):
        called.append(scope["type"])

    app = MCPBearerAuth(inner)
    await app({"type": "lifespan"}, AsyncMock(), AsyncMock())
    assert called == ["lifespan"]
