from __future__ import annotations

import os

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.handlers._tg_media import process_tg_media

router = Router()


@router.message(F.video)
async def handle_video(message: Message, bot: Bot) -> None:
    await process_tg_media(message, bot, message.video.file_id, ".mp4", label="Скачиваю видео из Telegram…", extract_audio_first=True)


@router.message(F.document.mime_type.startswith("video/"))
async def handle_video_document(message: Message, bot: Bot) -> None:
    file_name = message.document.file_name or ""
    ext = os.path.splitext(file_name)[1] or ".mp4"
    await process_tg_media(
        message,
        bot,
        message.document.file_id,
        ext,
        label="Скачиваю видео из Telegram…",
        extract_audio_first=True,
        filename_hint=file_name or None,
    )
