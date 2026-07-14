"""Тесты webapp/job_runner: JobReporter (tee в TG + jobs-таблица) и раннеры."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import webapp.job_runner as runner_module
from bot.services.job_store import JobStore
from bot.services.usage_store import LimitExceededError
from webapp.job_runner import JobReporter


@pytest.fixture()
def job_store(tmp_path):
    return JobStore(db_path=str(tmp_path / "jobs.db"))


class _FakeTgReporter:
    """Подменяет ProgressReporter: пишет события, работает как async CM."""

    def __init__(self):
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is asyncio.CancelledError:
            self.events.append(("cancelled",))
        return None

    async def set_phase(self, label):
        self.events.append(("phase", label))

    async def set_progress(self, current, total):
        self.events.append(("progress", current, total))

    async def set_progress_fraction(self, fraction):
        self.events.append(("fraction", fraction))

    async def finish(self):
        self.events.append(("finish",))

    async def fail(self, text):
        self.events.append(("fail", text))


@pytest.fixture()
def fake_tg(monkeypatch):
    """ProgressReporter.for_chat → фейк; Bot → мок с закрываемой сессией."""
    reporter = _FakeTgReporter()
    fake_cls = MagicMock()
    fake_cls.for_chat = MagicMock(return_value=reporter)
    monkeypatch.setattr(runner_module, "ProgressReporter", fake_cls)

    bot = MagicMock()
    bot.session.close = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_document = AsyncMock()
    monkeypatch.setattr(runner_module, "Bot", MagicMock(return_value=bot))
    return SimpleNamespace(reporter=reporter, bot=bot)


# ------------------------------------------------------------- JobReporter


async def test_job_reporter_tee_phase(job_store):
    job = await job_store.create(user_id=111, kind="url")
    inner = _FakeTgReporter()
    reporter = JobReporter(inner, job_store, job.id)

    await reporter.set_phase("Транскрибирую…")

    assert inner.events == [("phase", "Транскрибирую…")]
    got = await job_store.get(job.id, 111)
    assert got.status == "running"
    assert got.phase == "Транскрибирую…"
    assert got.progress is None


async def test_job_reporter_tee_progress(job_store):
    job = await job_store.create(user_id=111, kind="url")
    reporter = JobReporter(_FakeTgReporter(), job_store, job.id)

    await reporter.set_progress(3, 10)

    got = await job_store.get(job.id, 111)
    assert got.progress == 0.3


async def test_job_reporter_fraction_throttled(job_store):
    job = await job_store.create(user_id=111, kind="url")
    clock = SimpleNamespace(value=100.0)
    reporter = JobReporter(
        _FakeTgReporter(), job_store, job.id, now=lambda: clock.value
    )

    await reporter.set_progress_fraction(0.10)  # первая запись проходит
    await reporter.set_progress_fraction(0.20)  # <1 сек — в БД не пишется
    got = await job_store.get(job.id, 111)
    assert got.progress == 0.10

    clock.value += 1.5
    await reporter.set_progress_fraction(0.30)  # прошла секунда — пишется
    got = await job_store.get(job.id, 111)
    assert got.progress == 0.30

    await reporter.set_progress_fraction(1.0)  # завершение пишется всегда
    got = await job_store.get(job.id, 111)
    assert got.progress == 1.0


# -------------------------------------------------------------- run_url_job


async def test_run_url_job_success(job_store, fake_tg, monkeypatch):
    job = await job_store.create(user_id=111, kind="url", source="https://x/v")

    monkeypatch.setattr(
        runner_module,
        "download_audio",
        AsyncMock(return_value=("/tmp/a.mp3", SimpleNamespace(title="t"))),
    )
    monkeypatch.setattr(
        runner_module,
        "run_transcription_pipeline",
        AsyncMock(return_value=SimpleNamespace(id="rec-42")),
    )
    monkeypatch.setattr(runner_module.os.path, "exists", lambda p: False)

    await runner_module.run_url_job(
        job.id, 111, "https://x/v", timecodes=True, task_id="tid-1", job_store=job_store
    )

    got = await job_store.get(job.id, 111)
    assert got.status == "done"
    assert got.transcript_id == "rec-42"
    assert got.task_id == "tid-1"
    assert ("finish",) in fake_tg.reporter.events


async def test_run_url_job_download_error(job_store, fake_tg, monkeypatch):
    job = await job_store.create(user_id=111, kind="url", source="u")
    monkeypatch.setattr(
        runner_module, "download_audio", AsyncMock(side_effect=RuntimeError("boom"))
    )

    await runner_module.run_url_job(job.id, 111, "https://x/v", job_store=job_store)

    got = await job_store.get(job.id, 111)
    assert got.status == "error"
    assert got.error  # friendly-текст
    assert any(e[0] == "fail" for e in fake_tg.reporter.events)


async def test_run_url_job_limit_exceeded(job_store, fake_tg, monkeypatch):
    job = await job_store.create(user_id=111, kind="url", source="u")
    monkeypatch.setattr(
        runner_module,
        "download_audio",
        AsyncMock(return_value=("/tmp/a.mp3", SimpleNamespace(title="t"))),
    )
    monkeypatch.setattr(
        runner_module,
        "run_transcription_pipeline",
        AsyncMock(side_effect=LimitExceededError(5)),
    )
    monkeypatch.setattr(runner_module.os.path, "exists", lambda p: False)

    await runner_module.run_url_job(job.id, 111, "https://x/v", job_store=job_store)

    got = await job_store.get(job.id, 111)
    assert got.status == "error"
    assert "лимит" in got.error.lower() or "5" in got.error


async def test_run_url_job_cancelled(job_store, fake_tg, monkeypatch):
    job = await job_store.create(user_id=111, kind="url", source="u")
    monkeypatch.setattr(
        runner_module,
        "download_audio",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await runner_module.run_url_job(job.id, 111, "https://x/v", job_store=job_store)

    got = await job_store.get(job.id, 111)
    assert got.status == "cancelled"
    assert fake_tg.bot.session.close.await_count == 1


# ---------------------------------------------------------- run_summary_job


class _FakeTranscriptStore:
    def __init__(self, text="Заголовок\n\nСпикер 1: текст", source_type="voice"):
        self._text = text
        self._record = SimpleNamespace(
            id="rec-1", user_id=111, source_type=source_type
        )

    async def get(self, record_id, user_id):
        if record_id == "rec-1" and user_id == 111:
            return self._record
        return None

    async def read_text(self, record):
        return self._text


async def test_run_summary_job_success(job_store, fake_tg, monkeypatch, tmp_path):
    job = await job_store.create(user_id=111, kind="summary", source="rec-1")
    monkeypatch.setattr(
        runner_module, "summarize", AsyncMock(return_value="краткий конспект")
    )
    monkeypatch.setattr(
        runner_module, "get_transcript_store", lambda: _FakeTranscriptStore()
    )
    monkeypatch.setattr(runner_module.settings, "TRANSCRIPTS_DIR", str(tmp_path))

    await runner_module.run_summary_job(job.id, 111, "rec-1", job_store=job_store)

    got = await job_store.get(job.id, 111)
    assert got.status == "done"
    assert got.result_path
    with open(got.result_path, encoding="utf-8") as f:
        assert f.read() == "краткий конспект"
    # короткий конспект уходит сообщением в TG
    fake_tg.bot.send_message.assert_awaited_once()
    assert ("finish",) in fake_tg.reporter.events


async def test_run_summary_job_transcript_missing(job_store, fake_tg, monkeypatch):
    job = await job_store.create(user_id=111, kind="summary", source="nope")
    monkeypatch.setattr(
        runner_module, "get_transcript_store", lambda: _FakeTranscriptStore()
    )

    await runner_module.run_summary_job(job.id, 111, "nope", job_store=job_store)

    got = await job_store.get(job.id, 111)
    assert got.status == "error"


async def test_run_cleanup_job_success(job_store, fake_tg, monkeypatch, tmp_path):
    job = await job_store.create(user_id=111, kind="cleanup", source="rec-1")
    monkeypatch.setattr(
        runner_module, "cleanup_transcript", AsyncMock(return_value="чистый текст")
    )
    monkeypatch.setattr(
        runner_module, "get_transcript_store", lambda: _FakeTranscriptStore()
    )
    monkeypatch.setattr(runner_module.settings, "TRANSCRIPTS_DIR", str(tmp_path))

    await runner_module.run_cleanup_job(job.id, 111, "rec-1", job_store=job_store)

    got = await job_store.get(job.id, 111)
    assert got.status == "done"
    with open(got.result_path, encoding="utf-8") as f:
        content = f.read()
    assert content.startswith("Заголовок\n\n")
    assert content.endswith("чистый текст")
    # cleanup всегда файлом
    fake_tg.bot.send_document.assert_awaited_once()
