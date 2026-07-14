"""REST API для управления сохранёнными транскрибациями.

Все руты авторизуются через ``resolve_user_id`` (initData или bearer) и
работают только с записями этого пользователя. Чужой/несуществующий id —
всегда 404, чтобы нельзя было перебором обнаружить чужие записи.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bot.config import settings
from bot.services.transcript_store import get_transcript_store
from bot.utils.filename import build_filename
from bot.utils.source_labels import source_label
from webapp.delivery import build_resend_payload, deliver_transcript_with_status
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
                "source_label": source_label(r.source_type),
                "has_timecodes": r.segments_path is not None,
                "timecodes_available": r.segments_path is not None
                and r.char_count > settings.LONG_TEXT_THRESHOLD,
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

    text, file_text = await build_resend_payload(
        record, store, timecoded=bool(body and body.timecoded)
    )

    # Respond immediately; delivery happens in the background with an
    # in-chat status message (same model as the upload flow).
    background_tasks.add_task(
        deliver_transcript_with_status, user_id, text, file_text, record.source_type
    )
    return {"ok": True}
