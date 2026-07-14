"""Bearer-auth для MCP-эндпоинта: ASGI-middleware + per-request identity.

Каждый вызов MCP-тула — отдельный HTTP POST (stateless streamable-http),
поэтому identity живёт в ``ContextVar``, выставляемом на время запроса.
Запрос без валидного токена НЕ отбивается 401: auth-тулы
(``request_access``/``check_access``) обязаны работать без токена, а
остальные тулы сами проверяют identity и возвращают понятную инструкцию.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from webapp.deps import resolve_bearer_token

current_user_id: ContextVar[Optional[int]] = ContextVar(
    "mcp_current_user_id", default=None
)


class MCPBearerAuth:
    """ASGI-обёртка вокруг MCP-приложения: Authorization → ContextVar."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self._inner(scope, receive, send)
            return

        token: Optional[str] = None
        for name, value in scope.get("headers") or ():
            if name == b"authorization":
                header = value.decode("latin-1")
                if header.startswith("Bearer "):
                    token = header[len("Bearer ") :].strip()
                break

        user_id: Optional[int] = None
        if token:
            user_id = await resolve_bearer_token(token, touch=True)

        reset = current_user_id.set(user_id)
        try:
            await self._inner(scope, receive, send)
        finally:
            current_user_id.reset(reset)
