from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.services.usage_store import UsageStore, get_store
from bot.utils.pluralize import format_hm, plural_ru

router = Router()


WELCOME_TEXT = (
    "👋 Привет! Я транскрибирую аудио и видео в текст.\n"
    "\n"
    "<b>Что умею:</b>\n"
    "🎙 Голосовые сообщения\n"
    "🎥 Видео-кружочки\n"
    "📼 Видео-файлы (.mp4, .mov и т.п., в том числе пересланные)\n"
    "🔗 Ссылки на видео — YouTube, RuTube, VK Video, Vimeo и др.\n"
    "📸 Instagram Reels и видео (публичные)\n"
    "📘 Публичные видео и Reels Facebook\n"
    "☁️ Публичные ссылки на Яндекс Диск (аудио/видео)\n"
    "🎧 Выпуски подкастов Яндекс Музыки\n"
    "\n"
    "<b>Как пользоваться:</b>\n"
    "Пришли файл или ссылку. Перед стартом я спрошу, в каком виде нужен "
    "результат: с таймкодами или без.\n"
    "\n"
    "<b>Лимиты:</b>\n"
    "Через скрепку — до 20 MB (ограничение Telegram).\n"
    "Файлы больше — через Mini App «Транскрибации» (кнопка слева от поля ввода).\n"
    "\n"
    "<b>Мои записи:</b>\n"
    "Все готовые транскрибации хранятся в Mini App «Транскрибации»: можно "
    "заново прислать в чат (с таймкодами или без) или удалить.\n"
    "\n"
    "<b>После транскрибации</b> под сообщением появятся кнопки:\n"
    "📋 Скопировать текст\n"
    "📝 Краткий конспект\n"
    "🧹 Очистить текст от слов-паразитов\n"
    "\n"
    "<b>Команды:</b>\n"
    "/start, /help — это сообщение\n"
    "/limit — остаток месячного лимита транскрибации\n"
    "/mcp — подключить AI-агента (MCP) и управлять токенами"
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


@router.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    await message.answer(WELCOME_TEXT)


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
