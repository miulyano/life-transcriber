"""Хранилище MCP-токенов и pairing-запросов авторизации.

Две таблицы в общей SQLite (``data/transcripts.db``):

- ``api_tokens`` — именованные bearer-токены агентов, привязанные к TG
  user_id. В БД хранится только sha256-хэш; plaintext показывается один раз
  при выдаче.
- ``auth_requests`` — pairing-запросы агент-инициируемого флоу
  (request_access → подтверждение в боте → check_access). Создаёт их
  webapp-процесс, approve/decline пишет bot-процесс — связь через общую БД,
  все статусные переходы только conditional UPDATE (гонки между процессами).

Паттерн доступа — как в transcript_store: короткоживущие соединения,
WAL, busy_timeout, sync-код через ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import sqlite3
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from bot.config import settings

AUTH_REQUEST_TTL_SEC = 600  # 10 минут на подтверждение pairing-запроса
# /mcp публичен (auth-тулы без токена). Cap — per-source (IP), чтобы один
# источник флуда не блокировал вход другим пользователям; глобальный cap —
# только защита БД от неограниченного роста, не admission gate.
MAX_PENDING_PER_SOURCE = 3
MAX_PENDING_REQUESTS = 100
_PAIRING_ALPHABET = string.ascii_uppercase + string.digits

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_tokens (
    id            TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    label         TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    revoked       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);

CREATE TABLE IF NOT EXISTS auth_requests (
    id               TEXT PRIMARY KEY,
    pairing_code     TEXT NOT NULL UNIQUE,
    user_id          INTEGER,
    agent_name       TEXT NOT NULL,
    poll_secret_hash TEXT NOT NULL,
    status           TEXT NOT NULL,
    token_plain      TEXT,
    source           TEXT NOT NULL DEFAULT 'unknown',
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL
);
"""

_TOKEN_COLUMNS = "id, user_id, label, token_hash, created_at, last_used_at, revoked"
_REQUEST_COLUMNS = (
    "id, pairing_code, user_id, agent_name, poll_secret_hash, status, "
    "token_plain, source, created_at, expires_at"
)


@dataclass
class ApiToken:
    id: str
    user_id: int
    label: str
    token_hash: str
    created_at: str  # ISO-8601 UTC
    last_used_at: Optional[str]
    revoked: int


@dataclass
class AuthRequest:
    id: str
    pairing_code: str
    user_id: Optional[int]
    agent_name: str
    poll_secret_hash: str
    status: str  # pending|approved|declined|expired|delivered
    token_plain: Optional[str]
    source: str
    created_at: str
    expires_at: str


@dataclass
class PollResult:
    status: str
    token: Optional[str] = None
    agent_name: str = ""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TokenStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or settings.TRANSCRIPTS_DB_FILE

    def _connect(self) -> sqlite3.Connection:
        import os

        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        # Migration: auth_requests created before the source column.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(auth_requests)")}
        if "source" not in cols:
            conn.execute(
                "ALTER TABLE auth_requests ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'"
            )
        return conn

    # ------------------------------------------------------------ api_tokens

    async def create_token(self, user_id: int, label: str) -> tuple[str, ApiToken]:
        """Выдать новый токен. Возвращает (plaintext, запись с хэшем)."""

        def _run() -> tuple[str, ApiToken]:
            token = secrets.token_urlsafe(32)
            with self._connect() as conn:
                final_label = self._dedup_label(conn, user_id, label)
                record = ApiToken(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    label=final_label,
                    token_hash=_sha256(token),
                    created_at=_now().isoformat(),
                    last_used_at=None,
                    revoked=0,
                )
                conn.execute(
                    f"INSERT INTO api_tokens ({_TOKEN_COLUMNS}) VALUES (?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.user_id,
                        record.label,
                        record.token_hash,
                        record.created_at,
                        record.last_used_at,
                        record.revoked,
                    ),
                )
            return token, record

        return await asyncio.to_thread(_run)

    @staticmethod
    def _dedup_label(conn: sqlite3.Connection, user_id: int, label: str) -> str:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT label FROM api_tokens WHERE user_id=? AND revoked=0",
                (user_id,),
            )
        }
        if label not in existing:
            return label
        n = 2
        while f"{label}-{n}" in existing:
            n += 1
        return f"{label}-{n}"

    async def resolve_token(self, token: str, touch: bool = False) -> Optional[int]:
        """token → user_id, либо None (неизвестен/отозван)."""

        def _run() -> Optional[int]:
            token_hash = _sha256(token)
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT user_id FROM api_tokens WHERE token_hash=? AND revoked=0",
                    (token_hash,),
                ).fetchone()
                if row is None:
                    return None
                if touch:
                    conn.execute(
                        "UPDATE api_tokens SET last_used_at=? WHERE token_hash=?",
                        (_now().isoformat(), token_hash),
                    )
                return int(row[0])

        return await asyncio.to_thread(_run)

    async def revoke_token(self, token_id: str, user_id: int) -> bool:
        def _run() -> bool:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE api_tokens SET revoked=1 WHERE id=? AND user_id=? AND revoked=0",
                    (token_id, user_id),
                )
            return cur.rowcount > 0

        return await asyncio.to_thread(_run)

    async def list_tokens(self, user_id: int) -> list[ApiToken]:
        def _run() -> list[ApiToken]:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT {_TOKEN_COLUMNS} FROM api_tokens "
                    "WHERE user_id=? AND revoked=0 ORDER BY created_at",
                    (user_id,),
                ).fetchall()
            return [ApiToken(*row) for row in rows]

        return await asyncio.to_thread(_run)

    # --------------------------------------------------------- auth_requests

    async def create_request(
        self, agent_name: str, source: str = "unknown"
    ) -> tuple[AuthRequest, str]:
        """Новый pairing-запрос. Возвращает (запись, plaintext poll_secret).

        ``source`` — идентификатор источника (IP за прокси); лимит pending
        применяется per-source, чтобы один флудер не блокировал вход другим.
        """

        def _run() -> tuple[AuthRequest, str]:
            poll_secret = uuid.uuid4().hex
            now = _now()
            with self._connect() as conn:
                self._expire_stale(conn, now)
                self._enforce_pending_cap(conn, source)
                record = AuthRequest(
                    id=uuid.uuid4().hex,
                    pairing_code="",
                    user_id=None,
                    agent_name=agent_name,
                    poll_secret_hash=_sha256(poll_secret),
                    status="pending",
                    token_plain=None,
                    source=source,
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(seconds=AUTH_REQUEST_TTL_SEC)).isoformat(),
                )
                # UNIQUE-коллизия pairing_code маловероятна (36^6), но retry дёшев
                for _ in range(5):
                    record.pairing_code = "".join(
                        secrets.choice(_PAIRING_ALPHABET) for _ in range(6)
                    )
                    try:
                        conn.execute(
                            f"INSERT INTO auth_requests ({_REQUEST_COLUMNS}) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (
                                record.id,
                                record.pairing_code,
                                record.user_id,
                                record.agent_name,
                                record.poll_secret_hash,
                                record.status,
                                record.token_plain,
                                record.source,
                                record.created_at,
                                record.expires_at,
                            ),
                        )
                        break
                    except sqlite3.IntegrityError:
                        continue
                else:
                    raise RuntimeError("Failed to generate a unique pairing code")
            return record, poll_secret

        return await asyncio.to_thread(_run)

    @staticmethod
    def _expire_stale(conn: sqlite3.Connection, now: datetime) -> None:
        conn.execute(
            "UPDATE auth_requests SET status='expired' "
            "WHERE status='pending' AND expires_at<=?",
            (now.isoformat(),),
        )

    @staticmethod
    def _enforce_pending_cap(conn: sqlite3.Connection, source: str) -> None:
        # Per-source admission control: a single flooding source can hold at
        # most MAX_PENDING_PER_SOURCE slots, so it cannot lock legitimate
        # users out of pairing. The global cap only guards the table against
        # unbounded growth across many sources. Live requests are never
        # evicted — slots free up via TTL expiry (_expire_stale, called first).
        per_source = conn.execute(
            "SELECT COUNT(*) FROM auth_requests WHERE status='pending' AND source=?",
            (source,),
        ).fetchone()[0]
        if per_source >= MAX_PENDING_PER_SOURCE:
            raise RuntimeError("Too many pending authorization requests from this source")
        total = conn.execute(
            "SELECT COUNT(*) FROM auth_requests WHERE status='pending'"
        ).fetchone()[0]
        if total >= MAX_PENDING_REQUESTS:
            raise RuntimeError("Too many pending authorization requests, try later")

    async def get_request(self, request_id: str) -> Optional[AuthRequest]:
        def _run() -> Optional[AuthRequest]:
            with self._connect() as conn:
                self._expire_stale(conn, _now())
                row = conn.execute(
                    f"SELECT {_REQUEST_COLUMNS} FROM auth_requests WHERE id=?",
                    (request_id,),
                ).fetchone()
            return AuthRequest(*row) if row else None

        return await asyncio.to_thread(_run)

    async def find_by_pairing_code(self, code: str) -> Optional[AuthRequest]:
        def _run() -> Optional[AuthRequest]:
            with self._connect() as conn:
                self._expire_stale(conn, _now())
                row = conn.execute(
                    f"SELECT {_REQUEST_COLUMNS} FROM auth_requests WHERE pairing_code=?",
                    (code,),
                ).fetchone()
            return AuthRequest(*row) if row else None

        return await asyncio.to_thread(_run)

    async def bind_user(self, request_id: str, user_id: int) -> bool:
        """Привязать запрос к отправителю. Bind-once: чужой повторный тап
        не перепривязывает (идемпотентно для того же юзера)."""

        def _run() -> bool:
            now = _now().isoformat()
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE auth_requests SET user_id=? "
                    "WHERE id=? AND status='pending' AND expires_at>? "
                    "AND (user_id IS NULL OR user_id=?)",
                    (user_id, request_id, now, user_id),
                )
            return cur.rowcount > 0

        return await asyncio.to_thread(_run)

    async def approve(self, request_id: str, user_id: int) -> Optional[str]:
        """Атомарный approve: pending → approved + выдача токена.

        Возвращает plaintext-токен либо None (устарел/чужой/уже обработан) —
        rowcount-гейт закрывает гонки approve-vs-expire и двойной тап.
        """

        def _run() -> Optional[str]:
            now = _now().isoformat()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT agent_name FROM auth_requests "
                    "WHERE id=? AND user_id=?",
                    (request_id, user_id),
                ).fetchone()
                if row is None:
                    return None
                agent_name = row[0]
                cur = conn.execute(
                    "UPDATE auth_requests SET status='approved' "
                    "WHERE id=? AND user_id=? AND status='pending' AND expires_at>?",
                    (request_id, user_id, now),
                )
                if cur.rowcount == 0:
                    return None
                token = secrets.token_urlsafe(32)
                label = self._dedup_label(conn, user_id, agent_name)
                conn.execute(
                    f"INSERT INTO api_tokens ({_TOKEN_COLUMNS}) VALUES (?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, user_id, label, _sha256(token), now, None, 0),
                )
                conn.execute(
                    "UPDATE auth_requests SET token_plain=? WHERE id=?",
                    (token, request_id),
                )
            return token

        return await asyncio.to_thread(_run)

    async def decline(self, request_id: str, user_id: int) -> bool:
        def _run() -> bool:
            now = _now().isoformat()
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE auth_requests SET status='declined' "
                    "WHERE id=? AND user_id=? AND status='pending' AND expires_at>?",
                    (request_id, user_id, now),
                )
            return cur.rowcount > 0

        return await asyncio.to_thread(_run)

    async def poll(self, request_id: str, poll_secret: str) -> Optional[PollResult]:
        """check_access: статус запроса; approved-токен отдаётся ровно один раз."""

        def _run() -> Optional[PollResult]:
            with self._connect() as conn:
                self._expire_stale(conn, _now())
                row = conn.execute(
                    "SELECT poll_secret_hash, status, token_plain, agent_name "
                    "FROM auth_requests WHERE id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    return None
                secret_hash, status, token_plain, agent_name = row
                if not hmac.compare_digest(secret_hash, _sha256(poll_secret)):
                    return None
                if status == "approved":
                    # выдача токена строго однократная
                    cur = conn.execute(
                        "UPDATE auth_requests SET status='delivered', token_plain=NULL "
                        "WHERE id=? AND status='approved'",
                        (request_id,),
                    )
                    if cur.rowcount > 0 and token_plain:
                        return PollResult(
                            status="approved", token=token_plain, agent_name=agent_name
                        )
                    return PollResult(status="delivered", agent_name=agent_name)
                return PollResult(status=status, agent_name=agent_name)

        return await asyncio.to_thread(_run)

    async def cleanup_old(self, days: int = 30) -> int:
        """Housekeeping: удалить запросы старше N дней (любой статус)."""

        def _run() -> int:
            cutoff = (_now() - timedelta(days=days)).isoformat()
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM auth_requests WHERE created_at<?", (cutoff,)
                )
            return cur.rowcount

        return await asyncio.to_thread(_run)

    # -------------------------------------------------------------- test aid

    async def _set_expires_at(self, request_id: str, value: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE auth_requests SET expires_at=? WHERE id=?",
                    (value, request_id),
                )

        await asyncio.to_thread(_run)

    async def _set_created_at(self, request_id: str, value: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE auth_requests SET created_at=? WHERE id=?",
                    (value, request_id),
                )

        await asyncio.to_thread(_run)


_default_store: Optional[TokenStore] = None


def get_token_store() -> TokenStore:
    """Module-level singleton used by the running bot/webapp."""
    global _default_store
    if _default_store is None:
        _default_store = TokenStore()
    return _default_store
