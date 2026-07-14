"""MCP-сервер life-transcriber: streamable-http внутри webapp.

Stateless-режим: каждый вызов тула — отдельный HTTP POST со своим
``Authorization``-заголовком, identity кладёт :class:`webapp.mcp_auth.MCPBearerAuth`
в ContextVar. Auth-тулы (request_access/check_access) работают без токена —
это агент-инициируемый pairing-флоу с подтверждением в Telegram-боте.

Все статусы и результаты дублируются в TG-чат пользователя (chat_id =
user_id из токена) через существующие ProgressReporter/delivery-механизмы.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.parse
import uuid
from glob import glob
from typing import Awaitable, Callable, Optional

from aiogram import Bot
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from bot.config import settings
from bot.handlers.commands import _build_limit_text
from bot.services.formatter import render_timecode_segments
from bot.services.job_store import get_job_store
from bot.services.task_registry import cancel_task, new_task_id, spawn_transcription
from bot.services.token_store import AUTH_REQUEST_TTL_SEC, get_token_store
from bot.services.transcript_store import get_transcript_store
from bot.services.usage_store import LimitExceededError, get_store
from bot.utils.filename import split_header_and_body
from bot.utils.source_labels import source_label
from webapp.delivery import build_resend_payload, deliver_transcript_with_status
from webapp.job_runner import (
    run_cleanup_job,
    run_file_job,
    run_summary_job,
    run_url_job,
)
from webapp.mcp_auth import current_user_id

logger = logging.getLogger(__name__)

# Проверено 2026-07: telegram.me отдаёт страницу напрямую (HTTP 200), без
# 301 на t.me — поэтому выбран основным доменом deep link'а (у t.me бывают
# DNS-проблемы). Если зеркало начнёт редиректить на t.me — преимущество
# исчезает, сменить домен здесь.
DEEP_LINK_DOMAIN = "telegram.me"

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

UNAUTHORIZED_MESSAGE = (
    "unauthorized: no valid bearer token. Call request_access(agent_name) to "
    "start pairing, have the user approve it in the Telegram bot, then poll "
    "check_access and reconnect with the returned token."
)
NOT_FOUND_JOB = "job not found"
NOT_FOUND_TRANSCRIPT = "transcript not found"

CHECK_ACCESS_RETRY_SEC = 5

# Rate-limit request_access: анонимный публичный тул. Ключ — последний
# элемент X-Forwarded-For (его аппендит Caddy; левые элементы
# attacker-controlled при uvicorn --forwarded-allow-ips "*").
_RATE_LIMIT_MAX_PER_WINDOW = 5
_RATE_LIMIT_WINDOW_SEC = 60.0
_rate_buckets: dict[str, list[float]] = {}

INSTRUCTIONS = """life-transcriber: транскрибация аудио/видео в текст (AssemblyAI + GPT), \
связанная с Telegram-ботом. Каждый токен жёстко привязан к Telegram-аккаунту; все \
статусы прогресса и результаты ДУБЛИРУЮТСЯ в Telegram-чат пользователя.

Авторизация (нет токена): 1) request_access(agent_name) → покажи пользователю deep_link \
(если не открылась — fallback_links, затем pairing_code, который он шлёт боту сообщением); \
2) пользователь подтверждает в боте кнопкой; 3) поллинг check_access(request_id, poll_secret) \
→ token + connect_snippet; 4) переподключись с header Authorization: Bearer <token> \
(выполни connect_snippet — в живое соединение header добавить нельзя).

Работа: submit_url/submit_file → job_id → поллинг get_job_status до done/error \
(при done в ответе полный текст). Файлы загружаются отдельно: POST /api/files \
(multipart, тот же bearer) → file_id → submit_file(file_id). Ответы get_transcript/\
get_job_status для длинных записей могут быть сотни КБ."""

mcp = FastMCP(
    "life-transcriber",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    # Host-валидация (анти-DNS-rebinding) отключена: публичный HTTPS-эндпоинт
    # за Caddy, Host зависит от деплоя (WEBAPP_URL), а всё чувствительное и
    # так закрыто bearer-токеном. Иначе — 421 на любой не-localhost Host.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)


# ------------------------------------------------------------------ helpers


def _require_user_id() -> int:
    user_id = current_user_id.get()
    if user_id is None:
        raise ToolError(UNAUTHORIZED_MESSAGE)
    return user_id


def _client_ip(ctx: Optional[Context]) -> str:
    try:
        request = ctx.request_context.request  # type: ignore[union-attr]
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Последний элемент добавлен нашим прокси — ему и верим.
            return xff.split(",")[-1].strip()
        if request.client:
            return request.client.host
    except Exception:
        pass
    return "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_LIMIT_WINDOW_SEC]
    if len(bucket) >= _RATE_LIMIT_MAX_PER_WINDOW:
        raise ToolError("rate limited: too many access requests, retry in a minute")
    bucket.append(now)
    _rate_buckets[ip] = bucket


_bot_username: Optional[str] = None


async def _get_bot_username() -> str:
    """Ленивый memoized get_me: недоступность TG API не должна ронять webapp."""
    global _bot_username
    if _bot_username:
        return _bot_username
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        me = await bot.get_me()
    except Exception as e:
        raise ToolError(f"cannot reach Telegram API to build the deep link, retry: {e}")
    finally:
        await bot.session.close()
    _bot_username = me.username
    return _bot_username


def _mcp_url() -> str:
    if settings.WEBAPP_URL:
        return settings.WEBAPP_URL.rstrip("/") + "/mcp"
    return "<WEBAPP_URL>/mcp"


def _connect_snippet(token: str) -> str:
    return (
        "claude mcp remove transcriber; "
        f"claude mcp add --transport http transcriber {_mcp_url()} "
        f'--header "Authorization: Bearer {token}"'
    )


async def _spawn_job(
    user_id: int,
    *,
    kind: str,
    source: str,
    runner: Callable[[str], Awaitable[None]],
) -> dict:
    """Create a job, persist its task_id, THEN spawn the runner.

    Ordering matters: the token is written before the asyncio task exists,
    so (a) cancel_job never sees a queued job without a cancel token, and
    (b) a failed persist leaves the job queued but with no running task
    (swept by mark_stale_interrupted) rather than a hidden runner the agent
    never got a job_id for.
    """
    store = get_job_store()
    job = None
    try:
        job = await store.create(user_id, kind=kind, source=source)
        task_id = new_task_id()
        await store.set_task_id(job.id, task_id)
        # Spawn last: once the task exists, only the runner owns cleanup.
        spawn_transcription(user_id, lambda _tid: runner(job.id), task_id=task_id)
    except Exception:
        if job is not None:
            await store.finalize(job.id, status="error", error="failed to start job")
        raise ToolError("failed to start job, please retry")
    return {"job_id": job.id}


# --------------------------------------------------------------- auth tools


@mcp.tool()
async def request_access(agent_name: str, ctx: Context = None) -> dict:
    """Start pairing with a Telegram account. No token needed. Show the user
    deep_link; if it does not open — fallback_links, then pairing_code (the
    user sends the code to the bot as a message). Then poll check_access."""
    agent_name = (agent_name or "agent").strip()[:64] or "agent"
    ip = _client_ip(ctx)
    _check_rate_limit(ip)
    username = await _get_bot_username()

    try:
        request, poll_secret = await get_token_store().create_request(
            agent_name, source=ip
        )
    except RuntimeError as exc:
        raise ToolError(str(exc))
    payload = f"mcpauth_{request.id}"
    tg_uri = f"tg://resolve?domain={username}&start={payload}"
    return {
        "request_id": request.id,
        "poll_secret": poll_secret,
        "pairing_code": request.pairing_code,
        "deep_link": f"https://{DEEP_LINK_DOMAIN}/{username}?start={payload}",
        "fallback_links": [
            tg_uri,
            f"https://t.me/{username}?start={payload}",
            # Telegram Web (не нужно приложение; требуется вход в Web-клиент)
            "https://web.telegram.org/k/#?tgaddr=" + urllib.parse.quote(tg_uri, safe=""),
        ],
        "expires_in": AUTH_REQUEST_TTL_SEC,
        "instructions": (
            "Покажи пользователю deep_link. Если ссылка не открылась — предложи "
            "fallback_links по порядку (tg:// требует приложение, web-ссылка — "
            "вход в Telegram Web). Последний резерв: pairing_code — пользователь "
            "отправляет его боту обычным сообщением. Затем поллинг "
            "check_access(request_id, poll_secret)."
        ),
    }


@mcp.tool()
async def check_access(request_id: str, poll_secret: str) -> dict:
    """Poll pairing status. On approve returns the bearer token exactly once
    plus connect_snippet — re-add the server with the Authorization header."""
    result = await get_token_store().poll(request_id, poll_secret)
    if result is None:
        raise ToolError("access request not found")
    if result.status == "pending":
        return {"status": "pending", "retry_after_sec": CHECK_ACCESS_RETRY_SEC}
    if result.status == "approved" and result.token:
        return {
            "status": "approved",
            "token": result.token,
            "connect_snippet": _connect_snippet(result.token),
            "note": (
                "Токен показан один раз. Переподключи MCP-сервер командой из "
                "connect_snippet (header нельзя добавить в живое соединение)."
            ),
        }
    return {"status": result.status}


# ------------------------------------------------------------ working tools


@mcp.tool()
async def submit_url(url: str, timecodes: bool = True) -> dict:
    """Transcribe audio/video by URL (YouTube, Yandex Disk/Music, Instagram,
    Facebook, direct links). Returns job_id for get_job_status polling.
    Progress and the result are mirrored to the user's Telegram chat."""
    user_id = _require_user_id()
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise ToolError("invalid url: expected http(s)://…")
    try:
        await get_store().assert_within_limit(user_id)
    except LimitExceededError as exc:
        raise ToolError(f"monthly limit exhausted ({exc.limit_hours}h)")

    return await _spawn_job(
        user_id,
        kind="url",
        source=url,
        runner=lambda job_id: run_url_job(job_id, user_id, url, timecodes=timecodes),
    )


@mcp.tool()
async def submit_file(file_id: str, timecodes: bool = True) -> dict:
    """Transcribe a previously uploaded file. Upload first: POST /api/files
    (multipart field "file", same bearer token) → {"file_id"}. The uploaded
    file lives up to 6 hours and is consumed by this call."""
    user_id = _require_user_id()
    safe_id = re.sub(r"[^0-9a-f]", "", (file_id or "").lower())
    if not safe_id:
        raise ToolError("file not found")
    matches = glob(f"{settings.TEMP_DIR}/mcpfile_{user_id}_{safe_id}_*")
    if not matches:
        raise ToolError("file not found")
    source_path = matches[0]
    filename_hint = os.path.basename(source_path).split(
        f"mcpfile_{user_id}_{safe_id}_", 1
    )[-1]

    # Atomically claim the upload before creating the job: os.replace is
    # atomic, so a concurrent submit with the same file_id loses the race
    # and gets "file not found" instead of a duplicate job on the same file.
    claimed_path = os.path.join(
        settings.TEMP_DIR, f"mcpclaim_{user_id}_{uuid.uuid4().hex}_{filename_hint}"
    )
    try:
        os.replace(source_path, claimed_path)
    except OSError:
        raise ToolError("file not found")

    async def _restore_claim() -> None:
        # Return the upload to its original mcpfile_ name so the agent can
        # retry with the same file_id. An unlinked or leaked mcpclaim_ file
        # would no longer match submit_file's glob (data loss).
        if os.path.exists(claimed_path):
            os.replace(claimed_path, source_path)

    # ANY failure/cancellation between the claim and a successful spawn must
    # restore the upload — catch BaseException so CancelledError cannot
    # strand it either. On success the runner owns and deletes claimed_path.
    try:
        try:
            await get_store().assert_within_limit(user_id)
        except LimitExceededError as exc:
            raise ToolError(f"monthly limit exhausted ({exc.limit_hours}h)")
        return await _spawn_job(
            user_id,
            kind="file",
            source=filename_hint,
            runner=lambda job_id: run_file_job(
                job_id,
                user_id,
                claimed_path,
                filename_hint=filename_hint,
                timecodes=timecodes,
            ),
        )
    except BaseException:
        await _restore_claim()
        raise


@mcp.tool()
async def get_job_status(job_id: str) -> dict:
    """Job status: queued|running|done|error|cancelled|interrupted with phase
    and progress (0..1). When done: transcription jobs include the full text
    (can be large); summary/cleanup jobs include the result text."""
    user_id = _require_user_id()
    job = await get_job_store().get(job_id, user_id)
    if job is None:
        raise ToolError(NOT_FOUND_JOB)

    response = {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "phase": job.phase,
        "progress": job.progress,
        "error": job.error,
        "transcript_id": job.transcript_id,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if job.status != "done":
        return response

    if job.kind in ("url", "file"):
        if job.transcript_id:
            store = get_transcript_store()
            record = await store.get(job.transcript_id, user_id)
            if record is not None:
                response["text"] = await store.read_text(record)
        if "text" not in response:
            response["text"] = None
            response["note"] = "результат доставлен только в Telegram"
    elif job.result_path:
        try:
            response["result"] = await asyncio.to_thread(
                lambda: open(job.result_path, encoding="utf-8").read()
            )
        except OSError:
            response["result"] = None
            response["note"] = "результат доставлен только в Telegram"
    return response


@mcp.tool()
async def cancel_job(job_id: str) -> dict:
    """Cancel a queued/running job. The Telegram status message shows
    «Отменено»; a job in a terminal state returns cancelled=false."""
    user_id = _require_user_id()
    store = get_job_store()
    job = await store.get(job_id, user_id)
    if job is None:
        raise ToolError(NOT_FOUND_JOB)
    if job.status not in ("queued", "running"):
        return {"cancelled": False, "status": job.status}
    cancelled = bool(job.task_id) and cancel_task(job.task_id, user_id)
    if cancelled:
        # Finalize here rather than relying on the runner: a task cancelled
        # before its first event-loop turn never enters the runner's
        # try/except, so nobody would move the job out of queued/running.
        # Conditional finalize is a no-op if the runner already finished.
        await store.finalize(job.id, status="cancelled")
    return {"cancelled": cancelled, "status": job.status}


@mcp.tool()
async def list_transcripts() -> dict:
    """List the user's stored transcripts (newest first)."""
    user_id = _require_user_id()
    records = await get_transcript_store().list_for_user(user_id)
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "channel": r.channel,
                "created_at": r.created_at,
                "source_type": r.source_type,
                "source_label": source_label(r.source_type),
                "has_timecodes": r.segments_path is not None,
                "duration_sec": r.duration_sec,
                "char_count": r.char_count,
            }
            for r in records
        ]
    }


@mcp.tool()
async def get_transcript(transcript_id: str, timecoded: bool = False) -> dict:
    """Full transcript text (can be large). timecoded=true returns the
    per-sentence timecoded variant when segments are stored."""
    user_id = _require_user_id()
    store = get_transcript_store()
    record = await store.get(transcript_id, user_id)
    if record is None:
        raise ToolError(NOT_FOUND_TRANSCRIPT)
    text = await store.read_text(record)
    response = {
        "id": record.id,
        "title": record.title,
        "source_type": record.source_type,
        "created_at": record.created_at,
        "duration_sec": record.duration_sec,
        "timecoded": False,
        "text": text,
    }
    if timecoded:
        segments = await store.read_segments(record)
        if segments:
            rendered = render_timecode_segments(segments)
            header, _ = split_header_and_body(text)
            response["text"] = (
                f"{header}\n\n{rendered}".strip() if header else rendered
            )
            response["timecoded"] = True
        else:
            response["timecoded_available"] = False
    return response


@mcp.tool()
async def make_summary(transcript_id: str) -> dict:
    """Create a short GPT summary of a stored transcript (async job — poll
    get_job_status; the result is also sent to the Telegram chat)."""
    user_id = _require_user_id()
    record = await get_transcript_store().get(transcript_id, user_id)
    if record is None:
        raise ToolError(NOT_FOUND_TRANSCRIPT)
    return await _spawn_job(
        user_id,
        kind="summary",
        source=transcript_id,
        runner=lambda job_id: run_summary_job(job_id, user_id, transcript_id),
    )


@mcp.tool()
async def cleanup_text(transcript_id: str) -> dict:
    """Clean a stored transcript from filler words via GPT (async job — poll
    get_job_status; the result file is also sent to the Telegram chat)."""
    user_id = _require_user_id()
    record = await get_transcript_store().get(transcript_id, user_id)
    if record is None:
        raise ToolError(NOT_FOUND_TRANSCRIPT)
    return await _spawn_job(
        user_id,
        kind="cleanup",
        source=transcript_id,
        runner=lambda job_id: run_cleanup_job(job_id, user_id, transcript_id),
    )


@mcp.tool()
async def resend_to_chat(transcript_id: str, timecoded: bool = False) -> dict:
    """Re-send a stored transcript to the user's Telegram chat."""
    user_id = _require_user_id()
    store = get_transcript_store()
    record = await store.get(transcript_id, user_id)
    if record is None:
        raise ToolError(NOT_FOUND_TRANSCRIPT)
    text, file_text = await build_resend_payload(record, store, timecoded=timecoded)
    asyncio.create_task(
        deliver_transcript_with_status(user_id, text, file_text, record.source_type)
    )
    return {"ok": True}


@mcp.tool()
async def get_limit() -> dict:
    """Monthly transcription limit status for the paired Telegram account."""
    user_id = _require_user_id()
    store = get_store()
    status = await store.get_status(user_id)
    return {
        "limit_hours": status.limit_hours if status.has_limit else None,
        "used_seconds": status.used_seconds,
        "remaining_seconds": status.remaining_seconds if status.has_limit else None,
        "exhausted": status.is_exhausted,
        "text": await _build_limit_text(user_id, store),
    }
