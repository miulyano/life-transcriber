from unittest.mock import MagicMock

import pytest

from bot.services import pending_jobs
from bot.services.pending_jobs import PendingJob, pop_job, put_job


@pytest.fixture(autouse=True)
def _clean_registry():
    pending_jobs._pending.clear()
    yield
    pending_jobs._pending.clear()


def _job(user_id=111, **kw):
    return PendingJob(
        user_id=user_id,
        kind="link",
        message=MagicMock(),
        source_type="link",
        url="https://example.com",
        **kw,
    )


def test_put_pop_roundtrip():
    job = _job()
    pending_id = put_job(job)
    assert pop_job(pending_id, 111) is job
    # removed on pop
    assert pop_job(pending_id, 111) is None


def test_pop_foreign_user_refused():
    job = _job(user_id=111)
    pending_id = put_job(job)
    assert pop_job(pending_id, 222) is None
    # job stays for the owner
    assert pop_job(pending_id, 111) is job


def test_pop_unknown_id():
    assert pop_job("missing", 111) is None


def test_ttl_expiry(monkeypatch):
    job = _job()
    pending_id = put_job(job)
    monkeypatch.setattr(
        pending_jobs.time,
        "monotonic",
        lambda: job.created_at + pending_jobs.PENDING_TTL + 1,
    )
    assert pop_job(pending_id, 111) is None
