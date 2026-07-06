from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from bot.handlers._timecode_prompt import ask_timecodes
from bot.services.pending_jobs import PendingJob

router = Router()


@router.message(F.voice)
async def handle_voice(message: Message) -> None:
    await ask_timecodes(
        message,
        PendingJob(
            user_id=message.from_user.id if message.from_user else 0,
            kind="tg_media",
            message=message,
            source_type="voice",
            file_id=message.voice.file_id,
            suffix=".ogg",
            label="Транскрибирую…",
        ),
    )


@router.message(F.video_note)
async def handle_video_note(message: Message) -> None:
    await ask_timecodes(
        message,
        PendingJob(
            user_id=message.from_user.id if message.from_user else 0,
            kind="tg_media",
            message=message,
            source_type="video_note",
            file_id=message.video_note.file_id,
            suffix=".mp4",
            label="Транскрибирую кружочек…",
        ),
    )
