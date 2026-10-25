"""Тесты одноразовых upload-тикетов: TokenStore, тул prepare_upload, PUT /api/uploads."""
import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

import webapp.main as main_module
import webapp.mcp_server as server_module
from bot.config import settings
from bot.services.job_store import JobStore
from bot.services.token_store import (
    MAX_ACTIVE_UPLOAD_TICKETS,
    UPLOAD_TICKET_TTL_SEC,
    TokenStore,
    UploadTicketCapError,
)
from webapp.main import app
from webapp.mcp_auth import current_user_id
from webapp.mcp_server import prepare_upload, submit_file


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def store(tmp_path, monkeypatch):
    store = TokenStore(db_path=str(tmp_path / "tokens.db"))
    monkeypatch.setattr(server_module, "get_token_store", lambda: store)
    monkeypatch.setattr(main_module, "get_token_store", lambda: store)
    return store


@pytest.fixture()
def job_store(tmp_path, monkeypatch):
    js = JobStore(db_path=str(tmp_path / "jobs.db"))
    monkeypatch.setattr(server_module, "get_job_store", lambda: js)
    return js


@pytest.fixture()
def temp_dir(tmp_path, monkeypatch):
    d = tmp_path / "tmp"
    d.mkdir()
    monkeypatch.setattr(settings, "TEMP_DIR", str(d))
    return d


@pytest.fixture()
def as_user():
    token = current_user_id.set(111)
    yield 111
    current_user_id.reset(token)


@pytest.fixture()
def no_limit(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=AsyncMock()),
    )


@pytest.fixture()
def fake_spawn(monkeypatch):
    spawned = []

    def _spawn(user_id, coro_factory, *, task_id=None):
        coro_factory("t").close()
        spawned.append(user_id)
        return "t"

    monkeypatch.setattr(server_module, "spawn_transcription", _spawn)
    return spawned


@pytest.fixture()
def client():
    # Без контекст-менеджера: lifespan (session_manager) одноразовый на процесс
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def webapp_url(monkeypatch):
    monkeypatch.setattr(settings, "WEBAPP_URL", "https://example.test:2083")


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


# --------------------------------------------------------------- TokenStore


async def test_create_ticket_stores_hash_only(store):
    ticket, record = await store.create_upload_ticket(111, "rec.mp3", timecodes=False)
    assert len(ticket) >= 32
    assert record.user_id == 111
    assert record.filename == "rec.mp3"
    assert record.timecodes is False
    assert record.claimed_at is None and record.used_at is None
    expires = datetime.fromisoformat(record.expires_at)
    created = datetime.fromisoformat(record.created_at)
    assert expires - created == timedelta(seconds=UPLOAD_TICKET_TTL_SEC)
    # plaintext в БД отсутствует — только sha256
    claimed = await store.claim_upload_ticket(ticket)
    assert claimed is not None and claimed.id == record.id
    assert await store.claim_upload_ticket(_sha256(ticket)) is None


async def test_claim_is_single_winner(store):
    ticket, _ = await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    results = await asyncio.gather(
        store.claim_upload_ticket(ticket), store.claim_upload_ticket(ticket)
    )
    assert sorted(r is not None for r in results) == [False, True]


async def test_release_allows_reclaim(store):
    ticket, record = await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    assert await store.claim_upload_ticket(ticket) is not None
    assert await store.claim_upload_ticket(ticket) is None
    await store.release_upload_ticket(record.id)
    assert await store.claim_upload_ticket(ticket) is not None


async def test_used_ticket_not_claimable(store):
    ticket, record = await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    assert await store.claim_upload_ticket(ticket) is not None
    await store.mark_upload_ticket_used(record.id)
    await store.release_upload_ticket(record.id)  # release после used — no-op
    assert await store.claim_upload_ticket(ticket) is None


async def test_expired_ticket_not_claimable(store):
    ticket, record = await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    await store._set_upload_ticket_expires_at(record.id, _past())
    assert await store.claim_upload_ticket(ticket) is None


async def test_unknown_ticket_not_claimable(store):
    assert await store.claim_upload_ticket("nope") is None
    assert await store.claim_upload_ticket("") is None


async def test_active_ticket_cap_per_user(store):
    for _ in range(MAX_ACTIVE_UPLOAD_TICKETS):
        await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    with pytest.raises(UploadTicketCapError):
        await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    # другой пользователь не заблокирован
    await store.create_upload_ticket(222, "a.mp3", timecodes=True)


async def test_cap_frees_on_used_and_expired(store):
    tickets = []
    for _ in range(MAX_ACTIVE_UPLOAD_TICKETS):
        tickets.append(await store.create_upload_ticket(111, "a.mp3", timecodes=True))
    await store.mark_upload_ticket_used(tickets[0][1].id)
    await store.create_upload_ticket(111, "b.mp3", timecodes=True)
    with pytest.raises(UploadTicketCapError):
        await store.create_upload_ticket(111, "c.mp3", timecodes=True)
    await store._set_upload_ticket_expires_at(tickets[1][1].id, _past())
    await store.create_upload_ticket(111, "c.mp3", timecodes=True)


async def test_cleanup_old_removes_tickets(store):
    _, record = await store.create_upload_ticket(111, "a.mp3", timecodes=True)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    await store._set_upload_ticket_created_at(record.id, old)
    _, fresh = await store.create_upload_ticket(111, "b.mp3", timecodes=True)
    removed = await store.cleanup_old(days=30)
    assert removed >= 1
    assert await store.count_active_upload_tickets(111) == 1


# ----------------------------------------------------------- prepare_upload


async def test_prepare_upload_requires_auth(store):
    with pytest.raises(ToolError, match="unauthorized"):
        await prepare_upload("rec.mp3")


async def test_prepare_upload_returns_ticket_and_command(store, as_user, no_limit, webapp_url):
    result = await prepare_upload("../../rec with space.m4a", timecodes=False)
    assert result["upload_url"] == "https://example.test:2083/api/uploads"
    assert result["expires_in"] == UPLOAD_TICKET_TTL_SEC
    assert result["max_mb"] == settings.MAX_UPLOAD_MB
    assert result["filename"] == "rec with space.m4a"
    ticket = result["ticket"]
    assert f'-H "X-Upload-Ticket: {ticket}"' in result["command"]
    assert "-T" in result["command"] and result["upload_url"] in result["command"]
    assert "Bearer" not in result["command"]
    record = await store.claim_upload_ticket(ticket)
    assert record.user_id == 111
    assert record.filename == "rec with space.m4a"
    assert record.timecodes is False


async def test_prepare_upload_limit_exhausted(store, as_user, monkeypatch):
    from bot.services.usage_store import LimitExceededError

    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(
            assert_within_limit=AsyncMock(side_effect=LimitExceededError(5))
        ),
    )
    with pytest.raises(ToolError, match="limit"):
        await prepare_upload("rec.mp3")
    assert await store.count_active_upload_tickets(111) == 0


async def test_prepare_upload_cap(store, as_user, no_limit):
    for _ in range(MAX_ACTIVE_UPLOAD_TICKETS):
        await prepare_upload("rec.mp3")
    with pytest.raises(ToolError, match="too many"):
        await prepare_upload("rec.mp3")


# --------------------------------------------------------- PUT /api/uploads


async def _ticket(store, **kw) -> str:
    ticket, _ = await store.create_upload_ticket(
        kw.get("user_id", 111), kw.get("filename", "rec.mp3"), kw.get("timecodes", True)
    )
    return ticket


def test_put_without_ticket_404(client, store, temp_dir):
    res = client.put("/api/uploads", content=b"audio")
    assert res.status_code == 404
    assert os.listdir(temp_dir) == []


def test_put_unknown_ticket_404(client, store, temp_dir):
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": "bogus"}, content=b"audio")
    assert res.status_code == 404
    assert os.listdir(temp_dir) == []


def test_put_stores_file_and_spawns_job(client, store, job_store, temp_dir, no_limit, fake_spawn):
    ticket = asyncio.run(_ticket(store, filename="rec.mp3", timecodes=False))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"audio-bytes")
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    assert fake_spawn == [111]
    job = asyncio.run(job_store.get(job_id, 111))
    assert job.kind == "file" and job.source == "rec.mp3"
    # файл захвачен job'ом (mcpclaim_), содержимое целое
    claimed = [p for p in os.listdir(temp_dir) if p.startswith("mcpclaim_111_")]
    assert len(claimed) == 1 and claimed[0].endswith("_rec.mp3")
    assert (temp_dir / claimed[0]).read_bytes() == b"audio-bytes"
    # тикет сгорел
    res2 = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"again")
    assert res2.status_code == 404


def test_put_passes_timecodes_to_runner(client, store, job_store, temp_dir, no_limit, monkeypatch):
    seen = {}

    def fake_run_file_job(job_id, user_id, path, *, filename_hint, timecodes, **_):
        seen.update(user_id=user_id, filename_hint=filename_hint, timecodes=timecodes)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(server_module, "run_file_job", fake_run_file_job)
    monkeypatch.setattr(
        server_module,
        "spawn_transcription",
        lambda u, f, *, task_id=None: (f("t").close(), "t")[1],
    )
    ticket = asyncio.run(_ticket(store, filename="x.m4a", timecodes=False))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"a")
    assert res.status_code == 200, res.text
    assert seen == {"user_id": 111, "filename_hint": "x.m4a", "timecodes": False}


def test_put_oversized_releases_ticket(client, store, temp_dir, monkeypatch, job_store, no_limit, fake_spawn):
    monkeypatch.setattr(main_module, "MAX_AGENT_UPLOAD_BYTES", 10)
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"x" * 50)
    assert res.status_code == 413
    assert [p for p in os.listdir(temp_dir) if p.startswith("mcp")] == []
    # тикет освобождён — повтор тем же тикетом проходит
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"x" * 5)
    assert res.status_code == 200, res.text


def test_put_content_length_precheck(client, store, temp_dir, monkeypatch):
    monkeypatch.setattr(main_module, "_MAX_REQUEST_BODY_BYTES", 10)
    ticket = asyncio.run(_ticket(store))
    res = client.put(
        "/api/uploads",
        headers={"X-Upload-Ticket": ticket, "Content-Length": "100000"},
        content=b"x" * 100000,
    )
    assert res.status_code == 413
    # middleware отбил до claim — тикет не тронут
    assert asyncio.run(store.claim_upload_ticket(ticket)) is not None


def test_put_stream_failure_releases_ticket(client, store, temp_dir, monkeypatch, job_store, no_limit, fake_spawn):
    calls = {"n": 0}
    real = main_module._store_agent_upload

    async def flaky(user_id, filename, chunks):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset")
        return await real(user_id, filename, chunks)

    monkeypatch.setattr(main_module, "_store_agent_upload", flaky)
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 500
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 200, res.text


def test_put_insufficient_disk_507(client, store, temp_dir, monkeypatch):
    monkeypatch.setattr(main_module, "_free_disk_bytes", lambda path: 1)
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 507
    assert os.listdir(temp_dir) == []
    assert asyncio.run(store.claim_upload_ticket(ticket)) is not None


def test_put_job_cap_returns_409_with_file_id(client, store, job_store, temp_dir, no_limit, as_user, monkeypatch, fake_spawn):
    monkeypatch.setattr(server_module, "MAX_ACTIVE_JOBS_PER_USER", 0)
    ticket = asyncio.run(_ticket(store, filename="rec.mp3"))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"audio")
    assert res.status_code == 409, res.text
    file_id = res.json()["file_id"]
    assert "too many active jobs" in res.json()["detail"]
    # файл остался незабранным, тикет использован
    assert (temp_dir / f"mcpfile_111_{file_id}_rec.mp3").read_bytes() == b"audio"
    assert client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"x").status_code == 404
    # позже агент добивает submit_file(file_id)
    monkeypatch.setattr(server_module, "MAX_ACTIVE_JOBS_PER_USER", 20)
    result = asyncio.run(submit_file(file_id))
    assert asyncio.run(job_store.get(result["job_id"], 111)).source == "rec.mp3"


def test_legacy_files_endpoint_still_works(client, temp_dir, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKENS", "file-token:111")
    settings.__dict__.pop("api_tokens", None)
    try:
        res = client.post(
            "/api/files",
            headers={"Authorization": "Bearer file-token"},
            files={"file": ("rec.mp3", b"audio-bytes")},
        )
    finally:
        settings.__dict__.pop("api_tokens", None)
    assert res.status_code == 200
    file_id = res.json()["file_id"]
    assert (temp_dir / f"mcpfile_111_{file_id}_rec.mp3").read_bytes() == b"audio-bytes"


# ------------------------------------------------- codex review: concurrency


async def test_ticket_cap_holds_under_concurrency(store, monkeypatch):
    """Codex P2: два параллельных create на пороге cap — один должен упасть.

    Барьер держит первый поток после COUNT, чтобы второй успел дойти до
    своего COUNT, если проверка и INSERT не атомарны (6 тикетов вместо 5).
    С BEGIN IMMEDIATE второй блокируется на входе в транзакцию, барьер у
    первого истекает по таймауту, и второй видит уже 5.
    """
    import threading

    for _ in range(MAX_ACTIVE_UPLOAD_TICKETS - 1):
        await store.create_upload_ticket(111, "a.mp3", timecodes=True)

    barrier = threading.Barrier(2)
    real_count = TokenStore._count_active_tickets

    def slow_count(conn, user_id, now):
        n = real_count(conn, user_id, now)
        try:
            barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        return n

    monkeypatch.setattr(TokenStore, "_count_active_tickets", staticmethod(slow_count))
    results = await asyncio.gather(
        store.create_upload_ticket(111, "b.mp3", timecodes=True),
        store.create_upload_ticket(111, "c.mp3", timecodes=True),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, UploadTicketCapError)]
    assert len(errors) == 1, results
    monkeypatch.setattr(TokenStore, "_count_active_tickets", staticmethod(real_count))
    assert await store.count_active_upload_tickets(111) == MAX_ACTIVE_UPLOAD_TICKETS


def test_disk_reservation_counts_inflight_uploads(monkeypatch, temp_dir):
    """Codex P2: параллельные загрузки не должны все пройти disk-check по
    одному и тому же свободному месту — объём резервируется до стрима."""
    from fastapi import HTTPException

    gib = 1024 ** 3
    monkeypatch.setattr(main_module, "_free_disk_bytes", lambda path: 7 * gib)
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)

    release_a = main_module._reserve_disk(3 * gib)  # 6 GiB + headroom ≤ 7 GiB
    with pytest.raises(HTTPException) as exc:
        main_module._reserve_disk(3 * gib)  # второй уже не влезает
    assert exc.value.status_code == 507
    release_a()
    release_b = main_module._reserve_disk(3 * gib)  # после release — снова ок
    release_b()
    assert main_module._reserved_upload_bytes == 0


def test_put_keeps_output_reservation_until_job_finishes(client, store, temp_dir, job_store, no_limit, monkeypatch):
    """Codex round 2: после загрузки остаётся резерв под mp3 (≈ размер
    источника) — освобождается callback'ом on_finished из run_file_job."""
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(main_module, "probe_duration", AsyncMock(return_value=None))
    captured = {}

    def fake_run_file_job(job_id, user_id, path, *, filename_hint, timecodes, on_finished=None):
        captured["on_finished"] = on_finished

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(server_module, "run_file_job", fake_run_file_job)
    monkeypatch.setattr(
        server_module,
        "spawn_transcription",
        lambda u, f, *, task_id=None: (f("t").close(), "t")[1],
    )
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 200, res.text
    # исходник уже на диске (free его учитывает) — удержан только выход ffmpeg
    # (без длительности — fallback на размер источника)
    assert main_module._reserved_upload_bytes == 3
    captured["on_finished"]()
    assert main_module._reserved_upload_bytes == 0
    captured["on_finished"]()  # идемпотентно
    assert main_module._reserved_upload_bytes == 0


def test_put_releases_reservation_on_409(client, store, temp_dir, job_store, no_limit, fake_spawn, monkeypatch):
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(server_module, "MAX_ACTIVE_JOBS_PER_USER", 0)
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 409
    assert main_module._reserved_upload_bytes == 0


def test_put_requires_content_length(client, store, temp_dir):
    """Codex round 2: chunked PUT без Content-Length обходил бы disk-check."""
    ticket = asyncio.run(_ticket(store))
    res = client.put(
        "/api/uploads", headers={"X-Upload-Ticket": ticket}, content=iter([b"abc"])
    )
    assert res.status_code == 411
    assert os.listdir(temp_dir) == []
    # отбито до claim — тикет не тронут
    assert asyncio.run(store.claim_upload_ticket(ticket)) is not None


async def test_run_file_job_calls_on_finished(tmp_path, monkeypatch):
    import webapp.job_runner as runner_module
    from unittest.mock import MagicMock

    js = JobStore(db_path=str(tmp_path / "jobs.db"))
    job = await js.create(user_id=111, kind="file", source="a.mp3")
    bot = MagicMock()
    bot.session.close = AsyncMock()
    monkeypatch.setattr(runner_module, "Bot", MagicMock(return_value=bot))
    reporter = MagicMock()
    reporter.__aenter__ = AsyncMock(return_value=reporter)
    reporter.__aexit__ = AsyncMock(return_value=None)
    reporter.fail = AsyncMock()
    fake_cls = MagicMock()
    fake_cls.for_chat = MagicMock(return_value=reporter)
    monkeypatch.setattr(runner_module, "ProgressReporter", fake_cls)
    monkeypatch.setattr(
        runner_module,
        "prepare_audio_for_transcription",
        AsyncMock(side_effect=RuntimeError("ffmpeg down")),
    )
    src = tmp_path / "src.mp3"
    src.write_bytes(b"x")
    calls = []

    await runner_module.run_file_job(
        job.id, 111, str(src), filename_hint="a.mp3", job_store=js, on_finished=lambda: calls.append(1)
    )

    assert calls == [1]
    assert not src.exists()


def test_put_releases_disk_reservation_on_stream_error(client, store, temp_dir, job_store, no_limit, fake_spawn, monkeypatch):
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(main_module, "MAX_AGENT_UPLOAD_BYTES", 1)
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 413
    assert main_module._reserved_upload_bytes == 0


# ------------------------------------------------- codex review round 3


def test_put_releases_reservation_when_mark_used_fails(client, store, temp_dir, job_store, no_limit, fake_spawn, monkeypatch):
    """Codex R3 #1: любой сбой handoff до передачи владения runner'у — release."""
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(
        TokenStore, "mark_upload_ticket_used", AsyncMock(side_effect=RuntimeError("db locked"))
    )
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 500
    assert main_module._reserved_upload_bytes == 0


def test_put_releases_reservation_when_submit_crashes(client, store, temp_dir, job_store, no_limit, monkeypatch):
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(
        main_module, "submit_uploaded_file", AsyncMock(side_effect=RuntimeError("boom"))
    )
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 500
    assert main_module._reserved_upload_bytes == 0


def test_put_output_reservation_from_ffprobe(client, store, temp_dir, job_store, no_limit, monkeypatch):
    """Codex R3 #2: резерв под mp3 считается по длительности × битрейт,
    а не по размеру источника (8 kbps Opus даёт mp3 в 3 раза больше)."""
    from bot.services.media import estimate_transcription_audio_bytes

    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(main_module, "probe_duration", AsyncMock(return_value=3600.0))
    captured = {}

    def fake_run_file_job(job_id, user_id, path, *, filename_hint, timecodes, on_finished=None):
        captured["on_finished"] = on_finished

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(server_module, "run_file_job", fake_run_file_job)
    monkeypatch.setattr(
        server_module,
        "spawn_transcription",
        lambda u, f, *, task_id=None: (f("t").close(), "t")[1],
    )
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 200, res.text
    assert main_module._reserved_upload_bytes == estimate_transcription_audio_bytes(3600.0)
    captured["on_finished"]()
    assert main_module._reserved_upload_bytes == 0


def test_put_507_when_mp3_wont_fit(client, store, temp_dir, job_store, no_limit, fake_spawn, monkeypatch):
    """Источник влез, а оценка mp3 — нет: файл удалён, тикет и резерв отпущены."""
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(
        main_module, "_free_disk_bytes", lambda path: 3 * 2 + main_module.DISK_HEADROOM_BYTES + 1
    )
    monkeypatch.setattr(main_module, "probe_duration", AsyncMock(return_value=36_000.0))  # 10 ч
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abc")
    assert res.status_code == 507
    assert [p for p in os.listdir(temp_dir) if p.startswith("mcp")] == []
    assert main_module._reserved_upload_bytes == 0
    assert asyncio.run(store.claim_upload_ticket(ticket)) is not None


def test_put_falls_back_to_source_size_without_duration(client, store, temp_dir, job_store, no_limit, monkeypatch):
    monkeypatch.setattr(main_module, "_reserved_upload_bytes", 0)
    monkeypatch.setattr(main_module, "probe_duration", AsyncMock(return_value=None))
    captured = {}

    def fake_run_file_job(job_id, user_id, path, *, filename_hint, timecodes, on_finished=None):
        captured["on_finished"] = on_finished

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(server_module, "run_file_job", fake_run_file_job)
    monkeypatch.setattr(
        server_module,
        "spawn_transcription",
        lambda u, f, *, task_id=None: (f("t").close(), "t")[1],
    )
    ticket = asyncio.run(_ticket(store))
    res = client.put("/api/uploads", headers={"X-Upload-Ticket": ticket}, content=b"abcde")
    assert res.status_code == 200, res.text
    assert main_module._reserved_upload_bytes == 5
    captured["on_finished"]()
