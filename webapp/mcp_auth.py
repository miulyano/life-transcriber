"""Bearer-auth для MCP-эндпоинта: ASGI-middleware + per-request identity.

Каждый вызов MCP-тула — отдельный HTTP POST (stateless streamable-http),
поэтому identity живёт в ``ContextVar``, выставляемом на время запроса.
Запрос без валидного токена НЕ отбивается 401: auth-тулы
(``request_access``/``check_access``) обязаны работать без токена, а
остальные тулы сами проверяют identity и возвращают понятную инструкцию.

Обёртка также ловит необработанные исключения всего /mcp-стека (резолв
токена в SQLite, транспорт SDK) и отвечает JSON-RPC error вместо сырого
500 от uvicorn: голое исключение схлопывается в ExceptionGroup в
BaseHTTPMiddleware, а его traceback в docker-логах терялся. Полный
traceback пишется одним ``logger.exception``.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Optional

from webapp.deps import resolve_bearer_token

logger = logging.getLogger(__name__)

current_user_id: ContextVar[Optional[int]] = ContextVar(
    "mcp_current_user_id", default=None
)

# Формат зеркалит _create_error_response из mcp.server.streamable_http
# (id "server-error", INTERNAL_ERROR), чтобы клиент разбирал ответ одинаково.
_INTERNAL_ERROR_BODY = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {"code": -32603, "message": "Internal Server Error"},
    }
).encode()


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

        response_started = False

        async def send_wrapper(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        reset = None
        try:
            user_id: Optional[int] = None
            if token:
                user_id = await resolve_bearer_token(token, touch=True)

            reset = current_user_id.set(user_id)
            await self._inner(scope, receive, send_wrapper)
        except Exception:
            logger.exception(
                "Unhandled error in MCP endpoint (path=%r)", scope.get("path")
            )
            if response_started:
                raise
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": _INTERNAL_ERROR_BODY})
        finally:
            if reset is not None:
                current_user_id.reset(reset)
