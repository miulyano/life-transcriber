from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.services.usage_store import UsageStore, get_store
from bot.utils.pluralize import format_hm, plural_ru

router = Router()


START_TEXT = (
    "Привет! Я транскрибирую голосовые, кружочки, видео, аудиофайлы "
    "и ссылки на ролики (YouTube, Instagram, TikTok, Yandex Music, "
    "Facebook и др.).\n"
    "\n"
    "Пришли мне:\n"
    "• голосовое сообщение или видео-кружок\n"
    "• видео или аудиофайл\n"
    "• ссылку на ролик из поддерживаемого источника\n"
    "\n"
    "Доступные команды:\n"
    "/start — это сообщение\n"
    "/limit — узнать остаток месячного лимита транскрибации"
)


def _format_limit_status(limit_hours: int, used_seconds: float, remaining_seconds: float) -> str:
    limit_word = plural_ru(limit_hours, "час", "часа", "часов")
    if remaining_seconds <= 0:
        return (
            f"Текущий лимит — {limit_hours} {limit_word}. "
            f"Использовано — {format_hm(used_seconds)}. Лимит исчерпан."
        )
    return (
        f"Текущий лимит — {limit_hours} {limit_word}. "
        f"Использовано — {format_hm(used_seconds)}. "
        f"Осталось — {format_hm(remaining_seconds)}."
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def _build_limit_text(user_id: int, store: UsageStore) -> str:
    status = await store.get_status(user_id)
    if not status.has_limit:
        return "Лимит на транскрибации не установлен."
    return _format_limit_status(
        limit_hours=status.limit_hours or 0,
        used_seconds=status.used_seconds,
        remaining_seconds=status.remaining_seconds,
    )


@router.message(Command("limit"))
async def cmd_limit(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text = await _build_limit_text(user_id, get_store())
    await message.answer(text)
