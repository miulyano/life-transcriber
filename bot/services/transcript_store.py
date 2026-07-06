"""Персистентное хранилище готовых транскрибаций.

Метаданные — в SQLite (``data/transcripts.db``), контент — файлами в
``data/transcripts/``: ``<id>.txt`` (body без таймкодов) и ``<id>.json``
(сегменты с таймингами для рендера таймкодов по запросу).

Бот и webapp — два процесса на общем volume, поэтому каждый вызов открывает
короткоживущее соединение с ``journal_mode=WAL`` и ``busy_timeout``.
Все выборки фильтруют по ``user_id`` — изоляция пользователей на уровне store.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bot.config import settings
from bot.services.formatter import TimecodeSegment

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id            TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    source_type   TEXT NOT NULL DEFAULT 'unknown',
    duration_sec  REAL NOT NULL DEFAULT 0,
    char_count    INTEGER NOT NULL DEFAULT 0,
    txt_path      TEXT NOT NULL,
    segments_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_transcripts_user_created
    ON transcripts(user_id, created_at DESC);
"""

_COLUMNS = (
    "id, user_id, title, created_at, source_type, duration_sec, "
    "char_count, txt_path, segments_path"
)


@dataclass
class TranscriptRecord:
    id: str
    user_id: int
    title: str
    created_at: str  # ISO-8601 UTC
    source_type: str
    duration_sec: float
    char_count: int
    txt_path: str
    segments_path: Optional[str]


def _atomic_write(path: str, content: str) -> None:
    dir_ = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".transcript-", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


class TranscriptStore:
    def __init__(self, db_path: Optional[str] = None, files_dir: Optional[str] = None) -> None:
        self._db_path = db_path or settings.TRANSCRIPTS_DB_FILE
        self._files_dir = files_dir or settings.TRANSCRIPTS_DIR

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        return conn

    async def save(
        self,
        user_id: int,
        *,
        title: str,
        source_type: str,
        duration_sec: float,
        body: str,
        segments: list[TimecodeSegment],
    ) -> TranscriptRecord:
        return await asyncio.to_thread(
            self._save_sync, user_id, title, source_type, duration_sec, body, segments
        )

    def _save_sync(
        self,
        user_id: int,
        title: str,
        source_type: str,
        duration_sec: float,
        body: str,
        segments: list[TimecodeSegment],
    ) -> TranscriptRecord:
        record_id = uuid.uuid4().hex
        os.makedirs(self._files_dir, exist_ok=True)
        txt_path = os.path.join(self._files_dir, f"{record_id}.txt")
        segments_path: Optional[str] = None

        _atomic_write(txt_path, body)
        if segments:
            segments_path = os.path.join(self._files_dir, f"{record_id}.json")
            payload = {
                "version": 1,
                "segments": [
                    {"start_ms": s.start_ms, "speaker": s.speaker, "text": s.text}
                    for s in segments
                ],
            }
            _atomic_write(segments_path, json.dumps(payload, ensure_ascii=False))

        record = TranscriptRecord(
            id=record_id,
            user_id=user_id,
            title=title,
            created_at=datetime.now(timezone.utc).isoformat(),
            source_type=source_type,
            duration_sec=float(duration_sec),
            char_count=len(body),
            txt_path=txt_path,
            segments_path=segments_path,
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO transcripts ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.user_id,
                        record.title,
                        record.created_at,
                        record.source_type,
                        record.duration_sec,
                        record.char_count,
                        record.txt_path,
                        record.segments_path,
                    ),
                )
        except Exception:
            for path in (txt_path, segments_path):
                if path and os.path.exists(path):
                    os.unlink(path)
            raise
        return record

    async def list_for_user(self, user_id: int, limit: int = 200) -> list[TranscriptRecord]:
        def _run() -> list[TranscriptRecord]:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM transcripts WHERE user_id=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return [TranscriptRecord(*row) for row in rows]

        return await asyncio.to_thread(_run)

    async def get(self, record_id: str, user_id: int) -> Optional[TranscriptRecord]:
        def _run() -> Optional[TranscriptRecord]:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM transcripts WHERE id=? AND user_id=?",
                    (record_id, user_id),
                ).fetchone()
            return TranscriptRecord(*row) if row else None

        return await asyncio.to_thread(_run)

    async def delete(self, record_id: str, user_id: int) -> bool:
        """Remove the DB row, then the files.

        Row-first: a dangling row pointing at a missing file is the failure
        mode to avoid; an orphan file is harmless and only logged.
        """

        def _run() -> bool:
            record = None
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM transcripts WHERE id=? AND user_id=?",
                    (record_id, user_id),
                ).fetchone()
                if row is None:
                    return False
                record = TranscriptRecord(*row)
                conn.execute(
                    "DELETE FROM transcripts WHERE id=? AND user_id=?",
                    (record_id, user_id),
                )
            for path in (record.txt_path, record.segments_path):
                if not path:
                    continue
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning("Failed to remove transcript file %s: %s", path, e)
            return True

        return await asyncio.to_thread(_run)

    async def read_text(self, record: TranscriptRecord) -> str:
        def _run() -> str:
            with open(record.txt_path, "r", encoding="utf-8") as f:
                return f.read()

        return await asyncio.to_thread(_run)

    async def read_segments(self, record: TranscriptRecord) -> list[TimecodeSegment]:
        """Load persisted segments; [] when the record has none."""
        if not record.segments_path:
            return []

        def _run() -> list[TimecodeSegment]:
            with open(record.segments_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return [
                TimecodeSegment(
                    start_ms=int(s["start_ms"]),
                    text=str(s["text"]),
                    speaker=s.get("speaker"),
                )
                for s in payload.get("segments", [])
            ]

        return await asyncio.to_thread(_run)


_default_store: Optional[TranscriptStore] = None


def get_transcript_store() -> TranscriptStore:
    """Module-level singleton used by the running bot/webapp."""
    global _default_store
    if _default_store is None:
        _default_store = TranscriptStore()
    return _default_store
