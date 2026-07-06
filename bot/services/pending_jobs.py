"""In-memory реестр задач, ждущих выбора «с таймкодами / без».

Хендлер, получив ссылку или медиа, кладёт сюда задачу и отвечает вопросом
с inline-клавиатурой; callback ``tc:<0|1>:<pending_id>`` забирает задачу и
запускает пайплайн. Реестр процессный (как text-кэш в bot/utils/text.py):
рестарт бота теряет неотвеченные вопросы — кнопка ответит «Запрос устарел».
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from aiogram.types import Message

PENDING_TTL = 3600  # 1 hour

_pending: dict[str, "PendingJob"] = {}


@dataclass
class PendingJob:
    user_id: int
    kind: str  # "tg_media" | "link"
    message: Message  # original user message — reply anchor for progress/result
    source_type: str
    created_at: float = field(default_factory=time.monotonic)
    # kind == "link"
    url: str = ""
    # kind == "tg_media"
    file_id: str = ""
    suffix: str = ""
    label: str = ""
    extract_audio_first: bool = False
    filename_hint: Optional[str] = None


def _evict_expired() -> None:
    now = time.monotonic()
    expired = [k for k, job in _pending.items() if now - job.created_at > PENDING_TTL]
    for k in expired:
        del _pending[k]


def put_job(job: PendingJob) -> str:
    _evict_expired()
    pending_id = uuid.uuid4().hex[:16]
    _pending[pending_id] = job
    return pending_id


def pop_job(pending_id: str, user_id: int) -> Optional[PendingJob]:
    """Remove and return the job; None if missing, expired or foreign user."""
    _evict_expired()
    job = _pending.get(pending_id)
    if job is None or job.user_id != user_id:
        return None
    del _pending[pending_id]
    return job
