from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

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
    "<b>Лимиты:</b>\n"
    "Через скрепку — до 20 MB (ограничение Telegram).\n"
    "Больше — через Mini App (кнопка 📤 слева от поля ввода).\n"
    "\n"
    "<b>Как пользоваться:</b>\n"
    "Просто пришли файл или ссылку — в ответ получишь текст.\n"
    "\n"
    "<b>После транскрибации</b> под сообщением появятся кнопки:\n"
    "📋 Скопировать текст\n"
    "📝 Краткий конспект\n"
    "🧹 Очистить текст от слов-паразитов"
)


@router.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    await message.answer(WELCOME_TEXT)
