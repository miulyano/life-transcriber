"""Месячный лимит часов транскрибации на пользователя.

Лимиты — в JSON-файле, формат `{"<user_id>": <hours_int>}`. Пользователи без
записи имеют безлимит. Расход считается по календарным месяцам UTC и
сохраняется в JSON-файл `{"<user_id>": {"YYYY-MM": <seconds_float>}}`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from bot.config import settings
from bot.utils.pluralize import plural_ru

logger = logging.getLogger(__name__)


class LimitExceededError(Exception):
    """Raised when a user has reached or exceeded their monthly limit."""

    def __init__(self, limit_hours: int) -> None:
        super().__init__(f"limit exceeded: {limit_hours}h/month")
        self.limit_hours = limit_hours


def format_limit_exceeded_message(limit_hours: int) -> str:
    word = plural_ru(limit_hours, "час", "часа", "часов")
    return (
        "Превышен текущий лимит по часам транскрибации в месяц. "
        f"Ваш текущий лимит — {limit_hours} {word}."
    )


def current_period(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def load_limits(path: Optional[str] = None) -> dict[int, int]:
    """Load per-user monthly hour limits. Missing/invalid file → {}."""
    p = path or settings.LIMITS_FILE
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read limits file %s: %s", p, e)
        return {}
    out: dict[int, int] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            logger.warning("Skipping invalid limit entry %r=%r in %s", k, v, p)
    return out


class UsageStore:
    """Async-safe JSON store of per-user transcription seconds.

    All public methods serialize through a single asyncio.Lock so concurrent
    requests for the same user (or any users) cannot lose updates.
    """

    def __init__(self, path: Optional[str] = None, limits_path: Optional[str] = None) -> None:
        self._path = path or settings.USAGE_FILE
        self._limits_path = limits_path or settings.LIMITS_FILE
        self._lock = asyncio.Lock()
        # Seconds reserved by in-flight jobs — closes the check-then-debit gap
        # when several transcriptions of one user run concurrently.
        self._inflight: dict[int, float] = {}

    def _read(self) -> dict[str, dict[str, float]]:
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read usage file %s: %s — starting fresh", self._path, e)
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write(self, data: dict[str, dict[str, float]]) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        dir_ = os.path.dirname(self._path) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".usage-", suffix=".json", dir=dir_)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    async def get_used_seconds(self, user_id: int, period: Optional[str] = None) -> float:
        period = period or current_period()
        async with self._lock:
            data = self._read()
            return float(data.get(str(user_id), {}).get(period, 0.0))

    async def add_seconds(
        self, user_id: int, seconds: float, period: Optional[str] = None
    ) -> None:
        if seconds <= 0:
            return
        period = period or current_period()
        async with self._lock:
            data = self._read()
            user_key = str(user_id)
            user_data = data.setdefault(user_key, {})
            user_data[period] = float(user_data.get(period, 0.0)) + float(seconds)
            self._write(data)

    def _limit_for(self, user_id: int) -> Optional[int]:
        limits = load_limits(self._limits_path)
        return limits.get(user_id)

    def _add_seconds_locked(self, user_id: int, seconds: float) -> None:
        if seconds <= 0:
            return
        data = self._read()
        user_data = data.setdefault(str(user_id), {})
        period = current_period()
        user_data[period] = float(user_data.get(period, 0.0)) + float(seconds)
        self._write(data)

    def _release_locked(self, user_id: int, estimated_seconds: float) -> None:
        left = self._inflight.get(user_id, 0.0) - max(0.0, float(estimated_seconds))
        if left > 0:
            self._inflight[user_id] = left
        else:
            self._inflight.pop(user_id, None)

    async def reserve(self, user_id: int, estimated_seconds: float) -> None:
        """Reserve estimated seconds before a job starts.

        Raises LimitExceededError if recorded usage plus already-reserved
        in-flight seconds have reached the user's monthly limit. Like the
        historical check, the job that fits under the limit may still overrun
        the remainder — only its actual duration is debited on commit.
        """
        limit_hours = self._limit_for(user_id)
        est = max(0.0, float(estimated_seconds))
        async with self._lock:
            if limit_hours is not None:
                used = float(
                    self._read().get(str(user_id), {}).get(current_period(), 0.0)
                )
                if used + self._inflight.get(user_id, 0.0) >= limit_hours * 3600:
                    raise LimitExceededError(limit_hours)
            self._inflight[user_id] = self._inflight.get(user_id, 0.0) + est

    async def release(self, user_id: int, estimated_seconds: float) -> None:
        """Drop a reservation without debiting (job failed or was cancelled)."""
        async with self._lock:
            self._release_locked(user_id, estimated_seconds)

    async def commit(
        self, user_id: int, estimated_seconds: float, actual_seconds: float
    ) -> None:
        """Atomically swap the reservation for the actual recorded usage."""
        async with self._lock:
            self._release_locked(user_id, estimated_seconds)
            self._add_seconds_locked(user_id, actual_seconds)

    async def assert_within_limit(self, user_id: int) -> None:
        """Raise LimitExceededError(limit_hours) if user has used >= limit this month."""
        limit_hours = self._limit_for(user_id)
        if limit_hours is None:
            return
        used = await self.get_used_seconds(user_id)
        if used >= limit_hours * 3600:
            raise LimitExceededError(limit_hours)

    async def get_status(self, user_id: int) -> "LimitStatus":
        """Snapshot of limit/used/remaining for the current period."""
        limit_hours = self._limit_for(user_id)
        used = await self.get_used_seconds(user_id)
        return LimitStatus(limit_hours=limit_hours, used_seconds=used)


class LimitStatus:
    __slots__ = ("limit_hours", "used_seconds")

    def __init__(self, limit_hours: Optional[int], used_seconds: float) -> None:
        self.limit_hours = limit_hours
        self.used_seconds = used_seconds

    @property
    def has_limit(self) -> bool:
        return self.limit_hours is not None

    @property
    def limit_seconds(self) -> Optional[int]:
        return self.limit_hours * 3600 if self.limit_hours is not None else None

    @property
    def remaining_seconds(self) -> float:
        if self.limit_hours is None:
            return float("inf")
        return max(0.0, self.limit_hours * 3600 - self.used_seconds)

    @property
    def is_exhausted(self) -> bool:
        return self.has_limit and self.remaining_seconds <= 0


_default_store: Optional[UsageStore] = None


def get_store() -> UsageStore:
    """Module-level singleton used by the running bot/webapp."""
    global _default_store
    if _default_store is None:
        _default_store = UsageStore()
    return _default_store
