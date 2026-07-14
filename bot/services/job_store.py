"""Персистентные MCP-джобы со статусами для опроса агентом.

Таблица ``jobs`` в общей SQLite (``data/transcripts.db``). Пишет её только
webapp-процесс (исполнитель MCP-задач), но паттерн доступа тот же, что в
transcript_store: короткоживущие соединения, WAL, busy_timeout,
``asyncio.to_thread``.

Статусы: queued → running → done|error|cancelled. ``interrupted`` ставится
на старте webapp всем висящим queued/running — после рестарта они
гарантированно мертвы (task-реестр процессный).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from bot.config import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    phase         TEXT NOT NULL DEFAULT '',
    progress      REAL,
    error         TEXT,
    transcript_id TEXT,
    result_path   TEXT,
    task_id       TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
"""

_COLUMNS = (
    "id, user_id, kind, source, status, phase, progress, error, "
    "transcript_id, result_path, task_id, created_at, updated_at"
)

_UPDATABLE_FIELDS = {
    "status",
    "phase",
    "progress",
    "error",
    "transcript_id",
    "result_path",
    "task_id",
}


@dataclass
class JobRecord:
    id: str
    user_id: int
    kind: str  # url | file | summary | cleanup
    source: str
    status: str  # queued|running|done|error|cancelled|interrupted
    phase: str
    progress: Optional[float]
    error: Optional[str]
    transcript_id: Optional[str]
    result_path: Optional[str]
    task_id: Optional[str]
    created_at: str  # ISO-8601 UTC
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or settings.TRANSCRIPTS_DB_FILE

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        return conn

    async def create(self, user_id: int, kind: str, source: str = "") -> JobRecord:
        def _run() -> JobRecord:
            now = _now_iso()
            record = JobRecord(
                id=uuid.uuid4().hex,
                user_id=user_id,
                kind=kind,
                source=source,
                status="queued",
                phase="",
                progress=None,
                error=None,
                transcript_id=None,
                result_path=None,
                task_id=None,
                created_at=now,
                updated_at=now,
            )
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO jobs ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.user_id,
                        record.kind,
                        record.source,
                        record.status,
                        record.phase,
                        record.progress,
                        record.error,
                        record.transcript_id,
                        record.result_path,
                        record.task_id,
                        record.created_at,
                        record.updated_at,
                    ),
                )
            return record

        return await asyncio.to_thread(_run)

    async def update(self, job_id: str, **fields) -> None:
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown job fields: {unknown}")

        def _run() -> None:
            assignments = ", ".join(f"{name}=?" for name in fields)
            values = list(fields.values())
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE jobs SET {assignments}, updated_at=? WHERE id=?",
                    (*values, _now_iso(), job_id),
                )

        await asyncio.to_thread(_run)

    async def set_task_id(self, job_id: str, task_id: str) -> None:
        await self.update(job_id, task_id=task_id)

    async def get(self, job_id: str, user_id: int) -> Optional[JobRecord]:
        def _run() -> Optional[JobRecord]:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM jobs WHERE id=? AND user_id=?",
                    (job_id, user_id),
                ).fetchone()
            return JobRecord(*row) if row else None

        return await asyncio.to_thread(_run)

    async def mark_stale_interrupted(self) -> int:
        """Пометить висящие джобы после рестарта webapp."""

        def _run() -> int:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE jobs SET status='interrupted', "
                    "error='Сервер перезапущен во время выполнения', updated_at=? "
                    "WHERE status IN ('queued','running')",
                    (_now_iso(),),
                )
            return cur.rowcount

        return await asyncio.to_thread(_run)

    async def cleanup_old(self, days: int = 30) -> int:
        """Housekeeping: удалить джобы старше N дней вместе с result_path."""

        def _run() -> int:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, result_path FROM jobs WHERE created_at<?", (cutoff,)
                ).fetchall()
                if not rows:
                    return 0
                conn.execute("DELETE FROM jobs WHERE created_at<?", (cutoff,))
            for _job_id, result_path in rows:
                if not result_path:
                    continue
                try:
                    os.unlink(result_path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning("Failed to remove job result %s: %s", result_path, e)
            return len(rows)

        return await asyncio.to_thread(_run)

    # -------------------------------------------------------------- test aid

    async def _set_created_at(self, job_id: str, value: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET created_at=? WHERE id=?", (value, job_id)
                )

        await asyncio.to_thread(_run)


_default_store: Optional[JobStore] = None


def get_job_store() -> JobStore:
    """Module-level singleton used by the running webapp."""
    global _default_store
    if _default_store is None:
        _default_store = JobStore()
    return _default_store
