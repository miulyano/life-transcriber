"""FastAPI dependencies for the transcripts REST API.

Two auth paths, both resolving to a Telegram user_id:
- ``Authorization: Bearer <token>`` — static agent tokens from the
  ``API_TOKENS`` env var (``token:user_id,...``).  A mapped token is
  authorization by itself, the whitelist does not apply.
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
from webapp.auth import validate_init_data

MAX_INIT_DATA_AGE = 24 * 3600  # 24 hours


def _resolve_bearer(token: str) -> int:
    for known, user_id in settings.api_tokens.items():
        if hmac.compare_digest(known, token):
            return user_id
    raise HTTPException(401, "Invalid token")


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
        return _resolve_bearer(authorization[len("Bearer ") :].strip())
    if x_telegram_init_data:
        return _resolve_init_data(x_telegram_init_data)
    raise HTTPException(401, "Not authenticated")
