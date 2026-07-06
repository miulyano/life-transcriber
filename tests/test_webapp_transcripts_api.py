"""Tests for the transcripts REST API (list / file / delete / resend, dual auth)."""
import hashlib
import hmac
import json
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import webapp.transcripts_api as api_module
from bot.config import settings
from bot.services.formatter import TimecodeSegment
from bot.services.transcript_store import TranscriptStore
from webapp.main import app

BOT_TOKEN = "test_token"  # matches conftest.py


def _make_init_data(user_id: int = 111, auth_date: Optional[int] = None) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    user = json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":"))
    fields = {"auth_date": str(auth_date), "user": user}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _init_headers(user_id: int = 111, auth_date: Optional[int] = None) -> dict:
    return {"X-Telegram-Init-Data": _make_init_data(user_id, auth_date)}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = TranscriptStore(
        db_path=str(tmp_path / "transcripts.db"),
        files_dir=str(tmp_path / "transcripts"),
    )
    monkeypatch.setattr(api_module, "get_transcript_store", lambda: store)
    return store


@pytest.fixture
def tokens(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKENS", "agent-token:111")
    settings.__dict__.pop("api_tokens", None)  # reset cached_property
    yield
    settings.__dict__.pop("api_tokens", None)


async def _seed(store, user_id=111, title="Тест", body="Тест\n\nПривет.", segments=None):
    return await store.save(
        user_id,
        title=title,
        source_type="voice",
        duration_sec=10.0,
        body=body,
        segments=segments or [],
    )


# --- Auth ---


def test_no_auth_401(client, store):
    assert client.get("/api/transcripts").status_code == 401


def test_bearer_invalid_401(client, store, tokens):
    res = client.get("/api/transcripts", headers=_bearer("wrong"))
    assert res.status_code == 401


def test_bearer_valid_200(client, store, tokens):
    res = client.get("/api/transcripts", headers=_bearer("agent-token"))
    assert res.status_code == 200
    assert res.json() == {"items": []}


def test_init_data_valid_200(client, store):
    res = client.get("/api/transcripts", headers=_init_headers(111))
    assert res.status_code == 200


def test_init_data_tampered_403(client, store):
    headers = _init_headers(111)
    headers["X-Telegram-Init-Data"] += "x"
    assert client.get("/api/transcripts", headers=headers).status_code == 403


def test_init_data_expired_403(client, store):
    old = int(time.time()) - 25 * 3600
    res = client.get("/api/transcripts", headers=_init_headers(111, auth_date=old))
    assert res.status_code == 403


def test_init_data_not_whitelisted_403(client, store):
    res = client.get("/api/transcripts", headers=_init_headers(999))
    assert res.status_code == 403


# --- List ---


@pytest.mark.asyncio
async def test_list_only_own_rows(client, store):
    await _seed(store, user_id=111, title="mine")
    await _seed(store, user_id=222, title="foreign")

    res = client.get("/api/transcripts", headers=_init_headers(111))
    items = res.json()["items"]
    assert [i["title"] for i in items] == ["mine"]
    assert set(items[0]) == {
        "id", "title", "created_at", "source_type", "duration_sec", "char_count"
    }


# --- File download ---


@pytest.mark.asyncio
async def test_download_file(client, store):
    record = await _seed(store, body="Тест\n\nПривет.")
    res = client.get(f"/api/transcripts/{record.id}/file", headers=_init_headers(111))
    assert res.status_code == 200
    assert res.text == "Тест\n\nПривет."
    assert "attachment" in res.headers["content-disposition"]


@pytest.mark.asyncio
async def test_download_foreign_404(client, store):
    record = await _seed(store, user_id=222)
    res = client.get(f"/api/transcripts/{record.id}/file", headers=_init_headers(111))
    assert res.status_code == 404


# --- Delete ---


@pytest.mark.asyncio
async def test_delete_then_404(client, store, tmp_path):
    record = await _seed(store)
    res = client.delete(f"/api/transcripts/{record.id}", headers=_init_headers(111))
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    res = client.delete(f"/api/transcripts/{record.id}", headers=_init_headers(111))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_foreign_404(client, store):
    record = await _seed(store, user_id=222)
    res = client.delete(f"/api/transcripts/{record.id}", headers=_init_headers(111))
    assert res.status_code == 404


# --- Resend ---


@pytest.fixture
def sent(monkeypatch):
    send_mock = AsyncMock()
    bot = MagicMock()
    bot.session.close = AsyncMock()
    # ProgressReporter.for_chat status message lifecycle
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.delete_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    monkeypatch.setattr(api_module, "Bot", MagicMock(return_value=bot))
    monkeypatch.setattr(api_module, "send_transcript_to_chat", send_mock)
    return send_mock, bot


@pytest.mark.asyncio
async def test_resend_plain(client, store, sent):
    send_mock, bot = sent
    record = await _seed(store, body="Тест\n\nПривет.")
    res = client.post(
        f"/api/transcripts/{record.id}/resend",
        headers=_init_headers(111),
        json={"timecoded": False},
    )
    assert res.status_code == 200
    send_mock.assert_awaited_once_with(bot, 111, "Тест\n\nПривет.", None)
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_timecoded_renders_segments(client, store, sent):
    send_mock, _bot = sent
    segments = [
        TimecodeSegment(start_ms=0, text="Привет.", speaker=None),
        TimecodeSegment(start_ms=65_000, text="Пока.", speaker=None),
    ]
    record = await _seed(store, body="Тест\n\nПривет. Пока.", segments=segments)
    res = client.post(
        f"/api/transcripts/{record.id}/resend",
        headers=_init_headers(111),
        json={"timecoded": True},
    )
    assert res.status_code == 200
    file_text = send_mock.await_args.args[3]
    assert file_text == "Тест\n\n[0:00.000] Привет.\n[1:05.000] Пока."


@pytest.mark.asyncio
async def test_resend_timecoded_without_segments_falls_back_to_plain(client, store, sent):
    send_mock, _bot = sent
    record = await _seed(store, body="Тест\n\nПривет.", segments=[])
    res = client.post(
        f"/api/transcripts/{record.id}/resend",
        headers=_init_headers(111),
        json={"timecoded": True},
    )
    assert res.status_code == 200
    assert send_mock.await_args.args[3] is None


@pytest.mark.asyncio
async def test_resend_failure_reports_in_chat(client, store, sent):
    """Delivery error → status message becomes an in-chat ❌, HTTP is still 200."""
    send_mock, bot = sent
    send_mock.side_effect = RuntimeError("telegram down")
    record = await _seed(store)
    res = client.post(
        f"/api/transcripts/{record.id}/resend",
        headers=_init_headers(111),
        json={"timecoded": False},
    )
    assert res.status_code == 200
    edited = bot.edit_message_text.await_args.kwargs["text"]
    assert edited.startswith("❌")
    bot.delete_message.assert_not_awaited()
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_foreign_404(client, store, sent):
    record = await _seed(store, user_id=222)
    res = client.post(
        f"/api/transcripts/{record.id}/resend",
        headers=_init_headers(111),
        json={"timecoded": False},
    )
    assert res.status_code == 404
