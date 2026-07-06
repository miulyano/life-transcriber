from __future__ import annotations

import os

from aiogram import F, Router
from aiogram.types import Message

from bot.handlers._timecode_prompt import ask_timecodes
from bot.services.pending_jobs import PendingJob

router = Router()


@router.message(F.video)
async def handle_video(message: Message) -> None:
    await ask_timecodes(
        message,
        PendingJob(
            user_id=message.from_user.id if message.from_user else 0,
            kind="tg_media",
            message=message,
            source_type="video",
            file_id=message.video.file_id,
            suffix=".mp4",
            label="Скачиваю видео из Telegram…",
            extract_audio_first=True,
        ),
    )


@router.message(F.document.mime_type.startswith("video/"))
async def handle_video_document(message: Message) -> None:
    file_name = message.document.file_name or ""
    ext = os.path.splitext(file_name)[1] or ".mp4"
    await ask_timecodes(
        message,
        PendingJob(
            user_id=message.from_user.id if message.from_user else 0,
            kind="tg_media",
            message=message,
            source_type="document",
            file_id=message.document.file_id,
            suffix=ext,
            label="Скачиваю видео из Telegram…",
            extract_audio_first=True,
            filename_hint=file_name or None,
        ),
    )
