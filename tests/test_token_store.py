"""Тесты TokenStore: api_tokens + auth_requests (pairing-флоу MCP)."""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from bot.services.token_store import (
    AUTH_REQUEST_TTL_SEC,
    MAX_PENDING_PER_SOURCE,
    TokenStore,
)


@pytest.fixture()
def store(tmp_path):
    return TokenStore(db_path=str(tmp_path / "tokens.db"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- api_tokens


async def test_create_token_returns_plaintext_once(store):
    token, record = await store.create_token(user_id=111, label="claude")
    assert len(token) >= 32
    assert record.label == "claude"
    assert record.user_id == 111
    # plaintext нигде не хранится — только хэш
    assert record.token_hash == _sha256(token)


async def test_resolve_token(store):
    token, _ = await store.create_token(user_id=111, label="a")
    assert await store.resolve_token(token) == 111


async def test_resolve_unknown_token(store):
    assert await store.resolve_token("nonexistent") is None


async def test_resolve_revoked_token(store):
    token, record = await store.create_token(user_id=111, label="a")
    assert await store.revoke_token(record.id, user_id=111) is True
    assert await store.resolve_token(token) is None


async def test_revoke_foreign_token_fails(store):
    _, record = await store.create_token(user_id=111, label="a")
    assert await store.revoke_token(record.id, user_id=222) is False
    # токен всё ещё жив
    tokens = await store.list_tokens(111)
    assert len(tokens) == 1 and not tokens[0].revoked


async def test_list_tokens_only_own(store):
    await store.create_token(user_id=111, label="a")
    await store.create_token(user_id=222, label="b")
    tokens = await store.list_tokens(111)
    assert [t.label for t in tokens] == ["a"]


async def test_label_dedup_suffix(store):
    _, r1 = await store.create_token(user_id=111, label="agent")
    _, r2 = await store.create_token(user_id=111, label="agent")
    assert r1.label == "agent"
    assert r2.label == "agent-2"


async def test_touch_last_used(store):
    token, record = await store.create_token(user_id=111, label="a")
    assert record.last_used_at is None
    await store.resolve_token(token, touch=True)
    tokens = await store.list_tokens(111)
    assert tokens[0].last_used_at is not None


# ------------------------------------------------------------- auth_requests


async def test_create_request_fields(store):
    req, poll_secret = await store.create_request(agent_name="claude", source="1.2.3.4")
    assert req.status == "pending"
    assert req.user_id is None
    assert req.source == "1.2.3.4"
    assert len(req.pairing_code) == 6
    assert req.pairing_code.isalnum() and req.pairing_code == req.pairing_code.upper()
    # секрет не хранится в открытом виде
    assert req.poll_secret_hash == _sha256(poll_secret)


async def test_bind_user_once(store):
    req, _ = await store.create_request(agent_name="claude")
    assert await store.bind_user(req.id, user_id=111) is True
    # повторный bind тем же юзером — ок (идемпотентно)
    assert await store.bind_user(req.id, user_id=111) is True
    # чужим — нет
    assert await store.bind_user(req.id, user_id=222) is False


async def test_find_by_pairing_code(store):
    req, _ = await store.create_request(agent_name="claude")
    found = await store.find_by_pairing_code(req.pairing_code)
    assert found is not None and found.id == req.id
    assert await store.find_by_pairing_code("ZZZZZZ") is None


async def test_approve_generates_token(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    result = await store.approve(req.id, user_id=111)
    assert result is not None  # plaintext-токен
    # токен появился в api_tokens и резолвится
    assert await store.resolve_token(result) == 111


async def test_approve_wrong_user(store):
    req, _ = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    assert await store.approve(req.id, user_id=222) is None


async def test_approve_twice_returns_none(store):
    req, _ = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    assert await store.approve(req.id, user_id=111) is not None
    assert await store.approve(req.id, user_id=111) is None


async def test_decline(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    assert await store.decline(req.id, user_id=111) is True
    status = await store.poll(req.id, poll_secret)
    assert status.status == "declined"


async def test_poll_pending(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    status = await store.poll(req.id, poll_secret)
    assert status.status == "pending"
    assert status.token is None


async def test_poll_wrong_secret(store):
    req, _ = await store.create_request(agent_name="claude")
    assert await store.poll(req.id, "wrong-secret") is None


async def test_poll_unknown_request(store):
    assert await store.poll("nonexistent", "whatever") is None


async def test_poll_delivers_token_exactly_once(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    await store.approve(req.id, user_id=111)

    first = await store.poll(req.id, poll_secret)
    assert first.status == "approved"
    assert first.token is not None

    second = await store.poll(req.id, poll_secret)
    assert second.status == "delivered"
    assert second.token is None


async def test_expired_request_not_bindable(store):
    req, poll_secret = await store.create_request(agent_name="claude")
    # состариваем запрос напрямую в БД
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await store._set_expires_at(req.id, past)

    assert await store.bind_user(req.id, user_id=111) is False
    status = await store.poll(req.id, poll_secret)
    assert status.status == "expired"


async def test_expired_request_not_approvable(store):
    req, _ = await store.create_request(agent_name="claude")
    await store.bind_user(req.id, user_id=111)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await store._set_expires_at(req.id, past)
    assert await store.approve(req.id, user_id=111) is None


async def test_per_source_cap_rejects_flood(store):
    # один источник (IP) может держать максимум MAX_PENDING_PER_SOURCE слотов
    reqs = []
    for _ in range(MAX_PENDING_PER_SOURCE):
        req, _ = await store.create_request(agent_name="flood", source="1.2.3.4")
        reqs.append(req)
    with pytest.raises(RuntimeError, match="this source"):
        await store.create_request(agent_name="one-more", source="1.2.3.4")
    # существующие живы
    assert await store.bind_user(reqs[0].id, user_id=111) is True


async def test_flood_does_not_block_other_source(store):
    # Fix #4: флуд одного IP не блокирует вход другому пользователю
    for _ in range(MAX_PENDING_PER_SOURCE):
        await store.create_request(agent_name="flood", source="1.2.3.4")
    legit, _ = await store.create_request(agent_name="legit", source="9.9.9.9")
    assert legit.status == "pending"


async def test_pending_cap_expired_frees_slot(store):
    reqs = []
    for _ in range(MAX_PENDING_PER_SOURCE):
        req, _ = await store.create_request(agent_name="flood", source="1.2.3.4")
        reqs.append(req)
    # истёкший по TTL запрос освобождает слот (lazy-expire), живые — нет
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await store._set_expires_at(reqs[0].id, past)

    req_new, _ = await store.create_request(agent_name="legit", source="1.2.3.4")
    assert req_new.status == "pending"


async def test_cleanup_old_requests(store):
    req, _ = await store.create_request(agent_name="a")
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    await store._set_created_at(req.id, old)
    removed = await store.cleanup_old(days=30)
    assert removed == 1
    assert await store.find_by_pairing_code(req.pairing_code) is None
