"""Тесты MCP-тулов: identity, рабочие тулы, интеграционный JSON-RPC на /mcp."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import webapp.mcp_server as server_module
from bot.services.job_store import JobStore
from bot.services.token_store import TokenStore
from bot.services.transcript_store import TranscriptStore
from webapp.mcp_auth import current_user_id
from webapp.mcp_server import (
    UNAUTHORIZED_MESSAGE,
    cancel_job,
    check_access,
    cleanup_text,
    get_job_status,
    get_limit,
    get_transcript,
    list_transcripts,
    make_summary,
    request_access,
    resend_to_chat,
    submit_url,
)


@pytest.fixture()
def job_store(tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(server_module, "get_job_store", lambda: store)
    import webapp.job_runner as runner_module

    monkeypatch.setattr(runner_module, "get_job_store", lambda: store)
    return store


@pytest.fixture()
def token_store(tmp_path, monkeypatch):
    store = TokenStore(db_path=str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(server_module, "get_token_store", lambda: store)
    return store


@pytest.fixture()
def transcript_store(tmp_path, monkeypatch):
    store = TranscriptStore(
        db_path=str(tmp_path / "db.sqlite"), files_dir=str(tmp_path / "files")
    )
    monkeypatch.setattr(server_module, "get_transcript_store", lambda: store)
    return store


@pytest.fixture()
def as_user():
    token = current_user_id.set(111)
    yield 111
    current_user_id.reset(token)


@pytest.fixture()
def bot_username(monkeypatch):
    monkeypatch.setattr(
        server_module, "_get_bot_username", AsyncMock(return_value="test_bot")
    )


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    server_module._rate_buckets.clear()
    yield
    server_module._rate_buckets.clear()


async def _seed_transcript(store, user_id=111, body="Заголовок\n\nТело текста."):
    return await store.save(
        user_id,
        title="Заголовок",
        source_type="youtube",
        duration_sec=10.0,
        body=body,
        segments=[],
    )


# ------------------------------------------------------------- unauthorized


async def test_tools_require_token(job_store, transcript_store):
    with pytest.raises(ToolError, match="request_access"):
        await submit_url("https://x/v")
    with pytest.raises(ToolError):
        await get_job_status("j")
    with pytest.raises(ToolError):
        await list_transcripts()
    with pytest.raises(ToolError):
        await get_limit()


# ---------------------------------------------------------------- auth flow


async def test_request_access_payload(token_store, bot_username):
    result = await request_access("claude")

    assert result["deep_link"] == (
        f"https://telegram.me/test_bot?start=mcpauth_{result['request_id']}"
    )
    assert result["fallback_links"][0].startswith("tg://resolve?domain=test_bot")
    assert result["fallback_links"][1].startswith("https://t.me/test_bot")
    assert "web.telegram.org" in result["fallback_links"][2]
    assert len(result["pairing_code"]) == 6
    assert result["poll_secret"]
    assert "deep_link" in result["instructions"] or "pairing_code" in result["instructions"]


async def test_request_access_rate_limited(token_store, bot_username):
    for _ in range(5):
        await request_access("claude")
    with pytest.raises(ToolError, match="rate limited"):
        await request_access("claude")


async def test_check_access_full_cycle(token_store, bot_username, monkeypatch):
    monkeypatch.setattr(
        server_module.settings, "WEBAPP_URL", "https://tr.example.com"
    )
    result = await request_access("claude")
    request_id, poll_secret = result["request_id"], result["poll_secret"]

    pending = await check_access(request_id, poll_secret)
    assert pending == {"status": "pending", "retry_after_sec": 5}

    await token_store.bind_user(request_id, user_id=111)
    token = await token_store.approve(request_id, user_id=111)
    assert token

    approved = await check_access(request_id, poll_secret)
    assert approved["status"] == "approved"
    assert approved["token"] == token
    assert "https://tr.example.com/mcp" in approved["connect_snippet"]
    assert "--header" in approved["connect_snippet"]

    again = await check_access(request_id, poll_secret)
    assert again["status"] == "delivered"
    assert "token" not in again


async def test_check_access_wrong_secret(token_store, bot_username):
    result = await request_access("claude")
    with pytest.raises(ToolError):
        await check_access(result["request_id"], "wrong")


# ---------------------------------------------------------------- submit_url


async def test_submit_url_spawns_job(job_store, as_user, monkeypatch):
    spawned = []

    def fake_spawn(user_id, coro_factory):
        coro = coro_factory("task-1")
        coro.close()
        spawned.append(user_id)
        return "task-1"

    monkeypatch.setattr(server_module, "spawn_transcription", fake_spawn)
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=AsyncMock()),
    )

    result = await submit_url("https://youtube.com/watch?v=x")

    assert spawned == [111]
    job = await job_store.get(result["job_id"], 111)
    assert job.kind == "url"
    assert job.status == "queued"


async def test_submit_url_invalid(job_store, as_user):
    with pytest.raises(ToolError, match="invalid url"):
        await submit_url("not-a-url")


async def test_submit_url_limit_exhausted(job_store, as_user, monkeypatch):
    from bot.services.usage_store import LimitExceededError

    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(
            assert_within_limit=AsyncMock(side_effect=LimitExceededError(5))
        ),
    )
    with pytest.raises(ToolError, match="limit"):
        await submit_url("https://x/v")


# ------------------------------------------------------------ get_job_status


async def test_get_job_status_isolated(job_store, as_user):
    with pytest.raises(ToolError, match="not found"):
        await get_job_status("nonexistent")


async def test_get_job_status_running(job_store, as_user):
    job = await job_store.create(111, kind="url", source="u")
    await job_store.advance(job.id, phase="Транскрибирую…", progress=0.4)

    result = await get_job_status(job.id)

    assert result["status"] == "running"
    assert result["phase"] == "Транскрибирую…"
    assert result["progress"] == 0.4
    assert "text" not in result


async def test_get_job_status_done_includes_text(
    job_store, transcript_store, as_user
):
    record = await _seed_transcript(transcript_store)
    job = await job_store.create(111, kind="url", source="u")
    await job_store.finalize(job.id, status="done", transcript_id=record.id)

    result = await get_job_status(job.id)

    assert result["text"] == "Заголовок\n\nТело текста."


async def test_get_job_status_done_without_transcript(job_store, as_user, transcript_store):
    job = await job_store.create(111, kind="url", source="u")
    await job_store.finalize(job.id, status="done")

    result = await get_job_status(job.id)

    assert result["text"] is None
    assert "Telegram" in result["note"]


async def test_get_job_status_summary_result(job_store, as_user, tmp_path):
    result_file = tmp_path / "job_x.txt"
    result_file.write_text("конспект", encoding="utf-8")
    job = await job_store.create(111, kind="summary", source="rec")
    await job_store.finalize(job.id, status="done", result_path=str(result_file))

    result = await get_job_status(job.id)

    assert result["result"] == "конспект"


# ---------------------------------------------------------------- cancel_job


async def test_cancel_job_terminal(job_store, as_user):
    job = await job_store.create(111, kind="url", source="u")
    await job_store.finalize(job.id, status="done")

    result = await cancel_job(job.id)

    assert result == {"cancelled": False, "status": "done"}


async def test_cancel_job_running(job_store, as_user, monkeypatch):
    job = await job_store.create(111, kind="url", source="u")
    await job_store.advance(job.id, phase="Транскрибирую…")
    await job_store.set_task_id(job.id, "t-1")
    cancel_mock = MagicMock(return_value=True)
    monkeypatch.setattr(server_module, "cancel_task", cancel_mock)

    result = await cancel_job(job.id)

    cancel_mock.assert_called_once_with("t-1", 111)
    assert result["cancelled"] is True


async def test_submit_url_persists_task_id_before_returning(job_store, as_user, monkeypatch):
    """Fix #3: cancel_job на свежесозданной джобе видит task_id (нет окна)."""
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=AsyncMock()),
    )

    def fake_spawn(user_id, coro_factory):
        coro_factory("task-xyz").close()
        return "task-xyz"

    monkeypatch.setattr(server_module, "spawn_transcription", fake_spawn)

    result = await submit_url("https://youtube.com/watch?v=x")

    job = await job_store.get(result["job_id"], 111)
    assert job.task_id == "task-xyz"  # персистнут ДО возврата job_id


# ------------------------------------------------------- transcripts / limit


async def test_list_transcripts_own_only(transcript_store, as_user):
    await _seed_transcript(transcript_store, user_id=111)
    await _seed_transcript(transcript_store, user_id=222)

    result = await list_transcripts()

    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Заголовок"


async def test_get_transcript_text(transcript_store, as_user):
    record = await _seed_transcript(transcript_store)

    result = await get_transcript(record.id)

    assert result["text"] == "Заголовок\n\nТело текста."
    assert result["timecoded"] is False


async def test_get_transcript_timecoded_unavailable(transcript_store, as_user):
    record = await _seed_transcript(transcript_store)

    result = await get_transcript(record.id, timecoded=True)

    assert result["timecoded"] is False
    assert result["timecoded_available"] is False


async def test_get_transcript_foreign_not_found(transcript_store, as_user):
    record = await _seed_transcript(transcript_store, user_id=222)
    with pytest.raises(ToolError, match="not found"):
        await get_transcript(record.id)


async def test_make_summary_and_cleanup_spawn_jobs(
    transcript_store, job_store, as_user, monkeypatch
):
    record = await _seed_transcript(transcript_store)

    def fake_spawn(user_id, coro_factory):
        coro_factory("t").close()
        return "t"

    monkeypatch.setattr(server_module, "spawn_transcription", fake_spawn)

    r1 = await make_summary(record.id)
    r2 = await cleanup_text(record.id)

    assert (await job_store.get(r1["job_id"], 111)).kind == "summary"
    assert (await job_store.get(r2["job_id"], 111)).kind == "cleanup"


async def test_resend_to_chat(transcript_store, as_user, monkeypatch):
    record = await _seed_transcript(transcript_store)
    deliver = AsyncMock()
    monkeypatch.setattr(server_module, "deliver_transcript_with_status", deliver)

    result = await resend_to_chat(record.id)
    await asyncio.sleep(0)  # даём create_task стартовать

    assert result == {"ok": True}
    deliver.assert_awaited_once()


async def test_get_limit_no_limit(as_user, monkeypatch):
    status = SimpleNamespace(
        has_limit=False,
        limit_hours=None,
        used_seconds=0.0,
        remaining_seconds=float("inf"),
        is_exhausted=False,
    )
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(get_status=AsyncMock(return_value=status)),
    )
    monkeypatch.setattr(
        server_module, "_build_limit_text", AsyncMock(return_value="нет лимита")
    )

    result = await get_limit()

    assert result["limit_hours"] is None
    assert result["exhausted"] is False
    assert result["text"] == "нет лимита"


# ------------------------------------------------------- integration (/mcp)


MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _rpc(method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return body


def test_mcp_endpoint_end_to_end(monkeypatch, tmp_path):
    """JSON-RPC tools/call через реальный ASGI-стек: mount + auth middleware.

    Один TestClient на все кейсы: session_manager.run() у streamable-http
    одноразовый на процесс — второй lifespan упал бы с RuntimeError.
    """
    from fastapi.testclient import TestClient

    from webapp.main import app

    status = SimpleNamespace(
        has_limit=False,
        limit_hours=None,
        used_seconds=0.0,
        remaining_seconds=float("inf"),
        is_exhausted=False,
    )
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(get_status=AsyncMock(return_value=status)),
    )
    monkeypatch.setattr(
        server_module, "_build_limit_text", AsyncMock(return_value="нет лимита")
    )

    store = TokenStore(db_path=str(tmp_path / "tok.db"))
    import bot.services.token_store as ts_module

    monkeypatch.setattr(ts_module, "_default_store", store)
    token, _ = asyncio.run(store.create_token(user_id=111, label="it"))

    with TestClient(app) as client:
        # без токена: get_limit → isError с инструкцией про request_access
        res = client.post(
            "/mcp/",
            headers=MCP_HEADERS,
            json=_rpc("tools/call", {"name": "get_limit", "arguments": {}}, id_=2),
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["result"]["isError"] is True
        assert "request_access" in payload["result"]["content"][0]["text"]

        # с токеном: результат приходит
        res = client.post(
            "/mcp/",
            headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json=_rpc("tools/call", {"name": "get_limit", "arguments": {}}, id_=3),
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["result"]["isError"] is False
        text = payload["result"]["content"][0]["text"]
        assert json.loads(text)["text"] == "нет лимита"

        # tools/list отдаёт все тулы
        res = client.post(
            "/mcp/", headers=MCP_HEADERS, json=_rpc("tools/list", id_=4)
        )
        names = {t["name"] for t in res.json()["result"]["tools"]}
        assert {
            "request_access",
            "check_access",
            "submit_url",
            "submit_file",
            "get_job_status",
            "cancel_job",
            "list_transcripts",
            "get_transcript",
            "make_summary",
            "cleanup_text",
            "resend_to_chat",
            "get_limit",
        } <= names
