"""Тесты POST /api/files (загрузка для submit_file) и тула submit_file."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

import webapp.mcp_server as server_module
from bot.config import settings
from bot.services.job_store import JobStore
from webapp.main import app
from webapp.mcp_auth import current_user_id
from webapp.mcp_server import submit_file


@pytest.fixture()
def client():
    # Без контекст-менеджера: lifespan (session_manager) одноразовый на процесс
    return TestClient(app)


@pytest.fixture()
def bearer(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKENS", "file-token:111")
    settings.__dict__.pop("api_tokens", None)
    yield {"Authorization": "Bearer file-token"}
    settings.__dict__.pop("api_tokens", None)


@pytest.fixture()
def temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", str(tmp_path))
    return tmp_path


def test_upload_requires_auth(client, temp_dir):
    res = client.post("/api/files", files={"file": ("a.mp3", b"data")})
    assert res.status_code == 401


def test_upload_returns_file_id(client, bearer, temp_dir):
    res = client.post(
        "/api/files", headers=bearer, files={"file": ("рек.mp3", b"audio-bytes")}
    )
    assert res.status_code == 200
    file_id = res.json()["file_id"]
    assert file_id
    matches = [p for p in os.listdir(temp_dir) if f"mcpfile_111_{file_id}_" in p]
    assert len(matches) == 1
    with open(temp_dir / matches[0], "rb") as f:
        assert f.read() == b"audio-bytes"


def test_upload_sanitizes_filename(client, bearer, temp_dir):
    res = client.post(
        "/api/files", headers=bearer, files={"file": ("../../etc/passwd", b"x")}
    )
    file_id = res.json()["file_id"]
    matches = [p for p in os.listdir(temp_dir) if file_id in p]
    assert len(matches) == 1
    assert "/" not in matches[0].replace(str(temp_dir), "")
    assert ".." not in matches[0]


def test_upload_rejects_oversized(client, bearer, temp_dir, monkeypatch):
    import webapp.main as main_module

    monkeypatch.setattr(main_module, "MAX_AGENT_UPLOAD_BYTES", 10)
    res = client.post("/api/files", headers=bearer, files={"file": ("big.mp3", b"x" * 50)})
    assert res.status_code == 413
    # частичная запись не осталась на диске
    assert [p for p in os.listdir(temp_dir) if p.startswith("mcpfile_")] == []


def test_upload_rejects_over_quota(client, bearer, temp_dir, monkeypatch):
    import webapp.main as main_module

    monkeypatch.setattr(main_module, "MAX_AGENT_PENDING_BYTES", 5)
    (temp_dir / "mcpfile_111_existing_a.mp3").write_bytes(b"already-there")
    res = client.post("/api/files", headers=bearer, files={"file": ("more.mp3", b"x")})
    assert res.status_code == 413


# ---------------------------------------------------------------- submit_file


@pytest.fixture()
def as_user():
    token = current_user_id.set(111)
    yield 111
    current_user_id.reset(token)


@pytest.fixture()
def job_store(tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "jobs.db"))
    monkeypatch.setattr(server_module, "get_job_store", lambda: store)
    return store


async def test_submit_file_unknown_id(temp_dir, as_user, job_store):
    with pytest.raises(ToolError, match="file not found"):
        await submit_file("a" * 32)  # валидный формат, но нет файла


async def test_submit_file_rejects_truncated_id(temp_dir, as_user, job_store):
    """M2: короткий id не используется как glob-префикс для чужой загрузки."""
    (temp_dir / "mcpfile_111_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_rec.mp3").write_bytes(b"x")
    # "a" как префикс матчил бы файл выше — но требуется ровно 32 hex
    with pytest.raises(ToolError, match="file not found"):
        await submit_file("a")
    # файл на месте, не тронут
    assert (temp_dir / "mcpfile_111_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_rec.mp3").exists()


async def test_submit_file_foreign_file(temp_dir, as_user, job_store):
    # файл загружен другим пользователем — glob по user_id не найдёт
    (temp_dir / "mcpfile_222_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"x")
    with pytest.raises(ToolError, match="file not found"):
        await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")


async def test_submit_file_spawns_job(temp_dir, as_user, job_store, monkeypatch):
    (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"x")
    spawned = []

    def fake_spawn(user_id, coro_factory, *, task_id=None):
        coro_factory("t").close()
        spawned.append(user_id)
        return "t"

    monkeypatch.setattr(server_module, "spawn_transcription", fake_spawn)
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=AsyncMock()),
    )

    result = await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

    assert spawned == [111]
    job = await job_store.get(result["job_id"], 111)
    assert job.kind == "file"
    assert job.source == "rec.mp3"


async def test_submit_file_id_traversal_rejected(temp_dir, as_user, job_store):
    # символы вне [0-9a-f] вычищаются — glob-инъекция невозможна
    (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"x")
    with pytest.raises(ToolError, match="file not found"):
        await submit_file("../*")


async def test_submit_file_claims_atomically(temp_dir, as_user, job_store, monkeypatch):
    """Fix #2: второй параллельный submit того же file_id проигрывает claim."""
    (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"x")
    monkeypatch.setattr(server_module, "spawn_transcription", lambda u, f, *, task_id=None: (f("t").close(), "t")[1])
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=AsyncMock()),
    )

    first = await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    assert (await job_store.get(first["job_id"], 111)).kind == "file"
    # исходный файл захвачен (переименован) — второй вызов не найдёт
    assert not (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").exists()
    with pytest.raises(ToolError, match="file not found"):
        await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")


async def test_submit_file_retryable_after_limit(temp_dir, as_user, job_store, monkeypatch):
    """M2: сбой лимита возвращает загрузку к mcpfile-имени — file_id ретраебелен."""
    from bot.services.usage_store import LimitExceededError

    (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"data")
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(
            assert_within_limit=AsyncMock(side_effect=LimitExceededError(5))
        ),
    )

    with pytest.raises(ToolError, match="limit"):
        await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

    # тот же file_id снова резолвится (файл вернулся к mcpfile-имени)
    restored = temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3"
    assert restored.exists() and restored.read_bytes() == b"data"
    assert [p for p in os.listdir(temp_dir) if p.startswith("mcpclaim_")] == []


async def test_submit_file_retryable_after_spawn_failure(temp_dir, as_user, job_store, monkeypatch):
    """M2: сбой регистрации задачи возвращает загрузку, file_id ретраебелен."""
    (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"data")
    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=AsyncMock()),
    )

    def boom(user_id, coro_factory, *, task_id=None):
        raise RuntimeError("registry down")

    monkeypatch.setattr(server_module, "spawn_transcription", boom)

    with pytest.raises(ToolError, match="failed to start"):
        await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

    assert (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").exists()
    assert [p for p in os.listdir(temp_dir) if p.startswith("mcpclaim_")] == []


async def test_submit_file_retryable_after_cancellation(temp_dir, as_user, job_store, monkeypatch):
    """M2: CancelledError между claim и spawn тоже возвращает загрузку."""
    (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").write_bytes(b"data")

    async def cancel(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        server_module,
        "get_store",
        lambda: SimpleNamespace(assert_within_limit=cancel),
    )

    with pytest.raises(asyncio.CancelledError):
        await submit_file("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

    assert (temp_dir / "mcpfile_111_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4_rec.mp3").exists()
    assert [p for p in os.listdir(temp_dir) if p.startswith("mcpclaim_")] == []
