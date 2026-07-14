"""Тесты JobStore: персистентные MCP-джобы со статусами."""
from datetime import datetime, timedelta, timezone

import pytest

from bot.services.job_store import JobStore


@pytest.fixture()
def store(tmp_path):
    return JobStore(db_path=str(tmp_path / "jobs.db"))


async def test_create_defaults(store):
    job = await store.create(user_id=111, kind="url", source="https://x.test/v")
    assert job.status == "queued"
    assert job.kind == "url"
    assert job.phase == ""
    assert job.progress is None
    assert job.transcript_id is None


async def test_get_isolated_by_user(store):
    job = await store.create(user_id=111, kind="url", source="s")
    assert (await store.get(job.id, user_id=111)) is not None
    assert (await store.get(job.id, user_id=222)) is None
    assert (await store.get("nonexistent", user_id=111)) is None


async def test_update_fields(store):
    job = await store.create(user_id=111, kind="url", source="s")
    await store.update(job.id, status="running", phase="Транскрибирую…", progress=0.5)
    got = await store.get(job.id, user_id=111)
    assert got.status == "running"
    assert got.phase == "Транскрибирую…"
    assert got.progress == 0.5
    assert got.updated_at >= got.created_at


async def test_update_terminal_with_result(store):
    job = await store.create(user_id=111, kind="url", source="s")
    await store.update(job.id, status="done", transcript_id="abc123")
    got = await store.get(job.id, user_id=111)
    assert got.status == "done"
    assert got.transcript_id == "abc123"


async def test_set_task_id(store):
    job = await store.create(user_id=111, kind="url", source="s")
    await store.set_task_id(job.id, "task-1")
    got = await store.get(job.id, user_id=111)
    assert got.task_id == "task-1"


async def test_mark_stale_interrupted(store):
    j1 = await store.create(user_id=111, kind="url", source="s")
    j2 = await store.create(user_id=111, kind="file", source="s")
    j3 = await store.create(user_id=111, kind="url", source="s")
    await store.update(j2.id, status="running")
    await store.update(j3.id, status="done", transcript_id="t")

    n = await store.mark_stale_interrupted()
    assert n == 2  # queued + running; done не трогается

    assert (await store.get(j1.id, 111)).status == "interrupted"
    assert (await store.get(j2.id, 111)).status == "interrupted"
    assert (await store.get(j3.id, 111)).status == "done"


async def test_cleanup_old(store, tmp_path):
    job = await store.create(user_id=111, kind="summary", source="t1")
    result_file = tmp_path / "job_result.txt"
    result_file.write_text("data", encoding="utf-8")
    await store.update(job.id, status="done", result_path=str(result_file))
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    await store._set_created_at(job.id, old)

    fresh = await store.create(user_id=111, kind="url", source="s")

    removed = await store.cleanup_old(days=30)
    assert removed == 1
    assert (await store.get(job.id, 111)) is None
    assert not result_file.exists()  # result_path подчищен
    assert (await store.get(fresh.id, 111)) is not None
