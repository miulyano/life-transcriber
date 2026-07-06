"""REST API для управления сохранёнными транскрибациями.

Все руты авторизуются через ``resolve_user_id`` (initData или bearer) и
работают только с записями этого пользователя. Чужой/несуществующий id —
всегда 404, чтобы нельзя было перебором обнаружить чужие записи.
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bot.config import settings
from bot.services.formatter import render_timecode_segments
from bot.services.transcript_store import get_transcript_store
from bot.utils.filename import build_filename, split_header_and_body
from bot.utils.progress import ProgressReporter
from webapp.delivery import send_transcript_to_chat
from webapp.deps import resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transcripts")


class ResendRequest(BaseModel):
    timecoded: bool = False


@router.get("")
async def list_transcripts(user_id: int = Depends(resolve_user_id)) -> dict:
    records = await get_transcript_store().list_for_user(user_id)
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "channel": r.channel,
                "created_at": r.created_at,
                "source_type": r.source_type,
                "duration_sec": r.duration_sec,
                "char_count": r.char_count,
            }
            for r in records
        ]
    }


@router.get("/{record_id}/file")
async def download_transcript(
    record_id: str, user_id: int = Depends(resolve_user_id)
) -> FileResponse:
    record = await get_transcript_store().get(record_id, user_id)
    if record is None:
        raise HTTPException(404, "Not found")
    return FileResponse(
        record.txt_path,
        media_type="text/plain; charset=utf-8",
        filename=build_filename(record.title),
    )


@router.delete("/{record_id}")
async def delete_transcript(
    record_id: str, user_id: int = Depends(resolve_user_id)
) -> dict:
    deleted = await get_transcript_store().delete(record_id, user_id)
    if not deleted:
        raise HTTPException(404, "Not found")
    return {"ok": True}


async def _deliver_resend(user_id: int, text: str, file_text: str | None) -> None:
    """Send a stored transcript to chat. Runs as a background task.

    Same status model as _process_upload: a ProgressReporter status message
    in chat shows the phase; on failure it turns into an in-chat error.
    """
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with ProgressReporter.for_chat(
            bot, user_id, "Отправляю результат…"
        ) as reporter:
            try:
                await send_transcript_to_chat(bot, user_id, text, file_text)
                await reporter.finish()
            except Exception:
                logger.exception("Resend to chat failed for user %s", user_id)
                await reporter.fail(
                    "Не удалось отправить транскрибацию. Попробуй ещё раз."
                )
    finally:
        await bot.session.close()


@router.post("/{record_id}/resend")
async def resend_transcript(
    record_id: str,
    background_tasks: BackgroundTasks,
    body: Optional[ResendRequest] = None,
    user_id: int = Depends(resolve_user_id),
) -> dict:
    store = get_transcript_store()
    record = await store.get(record_id, user_id)
    if record is None:
        raise HTTPException(404, "Not found")

    text = await store.read_text(record)
    file_text: str | None = None
    if body and body.timecoded:
        segments = await store.read_segments(record)
        if segments:
            rendered = render_timecode_segments(segments)
            header, _ = split_header_and_body(text)
            file_text = f"{header}\n\n{rendered}".strip() if header else rendered

    # Respond immediately; delivery happens in the background with an
    # in-chat status message (same model as the upload flow).
    background_tasks.add_task(_deliver_resend, user_id, text, file_text)
    return {"ok": True}
