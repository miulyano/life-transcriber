from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.handlers._tg_media import process_tg_media

router = Router()


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    await process_tg_media(message, bot, message.voice.file_id, ".ogg", label="Транскрибирую…")


@router.message(F.video_note)
async def handle_video_note(message: Message, bot: Bot) -> None:
    await process_tg_media(message, bot, message.video_note.file_id, ".mp4", label="Транскрибирую кружочек…")
