"""Bot-сторона pairing-флоу MCP: deep link, pairing-код, кнопки ✅/❌, отзыв.

Роутер подключается в bot/main.py ДО commands.router — иначе существующий
handle_start (матчит /start с любым payload) перехватит deep link.

Callback'и не покрыты AuthMiddleware (он висит только на dp.message),
поэтому каждый callback-хендлер обязан сверять from_user.id с владельцем
запроса/токена.
"""
from __future__ import annotations

import html
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.services.token_store import AuthRequest, get_token_store

router = Router()

# Строго 6 символов A-Z0-9 (регистр нормализуется): любой другой текст бот
# по-прежнему молча игнорирует.
PAIRING_CODE_RE = re.compile(r"^[A-Za-z0-9]{6}$")

STALE_REQUEST_TEXT = (
    "Запрос авторизации не найден или устарел. Попроси агента создать новый."
)
FOREIGN_REQUEST_TEXT = "Этот запрос уже привязан к другому пользователю."


def _confirm_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Разрешить", callback_data=f"mcpauth:ok:{request_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"mcpauth:no:{request_id}"
                ),
            ]
        ]
    )


def _confirm_text(agent_name: str) -> str:
    return (
        f"🔐 Агент «{html.escape(agent_name)}» запрашивает доступ к транскрибации.\n"
        "Разрешить? Агент сможет запускать транскрибации от твоего имени; "
        "статусы и результаты будут приходить в этот чат."
    )


async def _bind_and_ask(message: Message, request: AuthRequest) -> None:
    """Общий шаг deep link и pairing-кода: bind-once + вопрос с кнопками."""
    user_id = message.from_user.id if message.from_user else 0
    bound = await get_token_store().bind_user(request.id, user_id)
    if not bound:
        if request.user_id is not None and request.user_id != user_id:
            await message.answer(FOREIGN_REQUEST_TEXT)
        else:
            await message.answer(STALE_REQUEST_TEXT)
        return
    await message.answer(
        _confirm_text(request.agent_name),
        reply_markup=_confirm_keyboard(request.id),
    )


@router.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^mcpauth_")))
async def handle_deep_link(message: Message, command: CommandObject) -> None:
    request_id = (command.args or "")[len("mcpauth_") :]
    request = await get_token_store().get_request(request_id)
    if request is None or request.status != "pending":
        await message.answer(STALE_REQUEST_TEXT)
        return
    await _bind_and_ask(message, request)


@router.message(F.text.regexp(PAIRING_CODE_RE))
async def handle_pairing_code(message: Message) -> None:
    code = (message.text or "").strip().upper()
    request = await get_token_store().find_by_pairing_code(code)
    if request is None or request.status != "pending":
        await message.answer("Код не найден или истёк. Попроси агента создать новый.")
        return
    await _bind_and_ask(message, request)


@router.callback_query(F.data.startswith("mcpauth:ok:"))
async def handle_pairing_approve(callback: CallbackQuery) -> None:
    request_id = callback.data.split(":", 2)[2]
    store = get_token_store()
    user_id = callback.from_user.id
    request = await store.get_request(request_id)
    if request is None or request.user_id != user_id:
        await callback.answer("Этот запрос не для тебя.", show_alert=True)
        return
    token = await store.approve(request_id, user_id)
    await callback.answer()
    if token is None:
        await callback.message.edit_text(STALE_REQUEST_TEXT)
        return
    await callback.message.edit_text(
        f"✅ Доступ выдан агенту «{html.escape(request.agent_name)}». "
        "Агент получит токен при следующей проверке.\n"
        "Управление токенами и URL подключения: /mcp"
    )


@router.callback_query(F.data.startswith("mcpauth:no:"))
async def handle_pairing_decline(callback: CallbackQuery) -> None:
    request_id = callback.data.split(":", 2)[2]
    store = get_token_store()
    user_id = callback.from_user.id
    request = await store.get_request(request_id)
    if request is None or request.user_id != user_id:
        await callback.answer("Этот запрос не для тебя.", show_alert=True)
        return
    declined = await store.decline(request_id, user_id)
    await callback.answer()
    if not declined:
        await callback.message.edit_text(STALE_REQUEST_TEXT)
        return
    await callback.message.edit_text(
        f"❌ Запрос агента «{html.escape(request.agent_name)}» отклонён."
    )


@router.callback_query(F.data.startswith("mcpauth:revoke:"))
async def handle_token_revoke(callback: CallbackQuery) -> None:
    token_id = callback.data.split(":", 2)[2]
    revoked = await get_token_store().revoke_token(token_id, callback.from_user.id)
    if not revoked:
        await callback.answer("Токен не найден.", show_alert=True)
        return
    await callback.answer("Токен отозван.")
    await callback.message.edit_text("🗑 Токен отозван.")


# ------------------------------------------------------------- /mcp command


def _connect_block(base_url: str) -> str:
    mcp_url = base_url.rstrip("/") + "/mcp"
    return (
        "<b>Подключение агента:</b>\n"
        f"URL: <code>{mcp_url}</code>\n"
        "\n"
        "Шаг 1 — подключить без токена (для запроса доступа):\n"
        f"<code>claude mcp add --transport http transcriber {mcp_url}</code>\n"
        "\n"
        "Шаг 2 — агент вызовет request_access, ты подтвердишь здесь кнопкой, "
        "и агент получит токен с готовой командой переподключения "
        "(check_access вернёт connect_snippet с --header)."
    )


@router.message(Command("mcp"))
async def handle_mcp_command(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    parts: list[str] = []

    if settings.WEBAPP_URL:
        parts.append(_connect_block(settings.WEBAPP_URL))
    else:
        parts.append(
            "URL подключения недоступен: задай WEBAPP_URL в .env, "
            "чтобы показать адрес MCP-эндпоинта."
        )

    tokens = await get_token_store().list_tokens(user_id)
    if tokens:
        lines = ["", "<b>Активные токены:</b>"]
        keyboard_rows = []
        for t in tokens:
            used = t.last_used_at[:16].replace("T", " ") if t.last_used_at else "не использовался"
            lines.append(f"• {html.escape(t.label)} — создан {t.created_at[:10]}, {used}")
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 Отозвать «{t.label}»",
                        callback_data=f"mcpauth:revoke:{t.id}",
                    )
                ]
            )
        parts.append("\n".join(lines))
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    else:
        parts.append("\nАктивных токенов нет.")
        markup = None

    await message.answer("\n".join(parts), reply_markup=markup)
