"""FastAPI dependencies for the transcripts REST API.

Two auth paths, both resolving to a Telegram user_id:
- ``Authorization: Bearer <token>`` — agent tokens: primary source is the
  ``api_tokens`` table (issued via the bot's pairing flow), with the
  ``API_TOKENS`` env var (``token:user_id,...``) as a legacy fallback.
  A mapped token is authorization by itself, the whitelist does not apply.
- ``X-Telegram-Init-Data`` header — Mini App initData, validated the same
  way as /api/upload (signature, age, whitelist).  Header only, never a
  query param, so it cannot leak into access logs.
"""
from __future__ import annotations

import hmac
import time
from typing import Optional

from fastapi import Header, HTTPException

from bot.config import settings
from bot.services.token_store import get_token_store
from webapp.auth import validate_init_data

MAX_INIT_DATA_AGE = 24 * 3600  # 24 hours


async def resolve_bearer_token(token: str, *, touch: bool = False) -> Optional[int]:
    """token → user_id: сначала таблица api_tokens, затем legacy env."""
    user_id = await get_token_store().resolve_token(token, touch=touch)
    if user_id is not None:
        return user_id
    for known, env_user_id in settings.api_tokens.items():
        if hmac.compare_digest(known, token):
            return env_user_id
    return None


async def _resolve_bearer(token: str) -> int:
    user_id = await resolve_bearer_token(token)
    if user_id is None:
        raise HTTPException(401, "Invalid token")
    return user_id


def _resolve_init_data(init_data: str) -> int:
    parsed = validate_init_data(init_data, settings.BOT_TOKEN)
    if not parsed:
        raise HTTPException(403, "Invalid auth")
    if time.time() - parsed["auth_date"] > MAX_INIT_DATA_AGE:
        raise HTTPException(403, "initData expired — reopen the app")
    user_id = parsed["user_id"]
    if user_id not in settings.allowed_user_ids:
        raise HTTPException(403, "Not whitelisted")
    return user_id


async def resolve_user_id(
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> int:
    if authorization and authorization.startswith("Bearer "):
        return await _resolve_bearer(authorization[len("Bearer ") :].strip())
    if x_telegram_init_data:
        return _resolve_init_data(x_telegram_init_data)
    raise HTTPException(401, "Not authenticated")
