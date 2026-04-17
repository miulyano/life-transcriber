import asyncio
import logging
import os
import time
import uuid

import aiofiles
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from bot.config import settings
from bot.services.transcriber import transcribe
from webapp.auth import validate_init_data
from webapp.delivery import send_transcript_to_chat

logger = logging.getLogger(__name__)

app = FastAPI(title="life-transcriber webapp")

MAX_INIT_DATA_AGE = 24 * 3600  # 24 hours


async def _process_upload(dest: str, user_id: int) -> None:
    """Transcribe file and deliver result to chat. Runs as a background task."""
    try:
        text = await transcribe(dest)
    except Exception:
        logger.exception("Transcription failed for %s (user %s)", dest, user_id)
        return
    finally:
        if os.path.exists(dest):
            os.unlink(dest)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await send_transcript_to_chat(bot, user_id, text)
        logger.info("Transcript delivered to user %s", user_id)
    except Exception:
        logger.exception("Delivery failed for user %s", user_id)
    finally:
        await bot.session.close()


@app.post("/api/upload")
async def upload(
    file: UploadFile,
    init_data: str = Form(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> dict:
    # --- Auth ---
    parsed = validate_init_data(init_data, settings.BOT_TOKEN)
    if not parsed:
        raise HTTPException(403, "Invalid auth")
    if time.time() - parsed["auth_date"] > MAX_INIT_DATA_AGE:
        raise HTTPException(403, "initData expired \u2014 reopen the app")

    user_id = parsed["user_id"]
    if user_id not in settings.allowed_user_ids:
        raise HTTPException(403, "Not whitelisted")

    # --- Save file to disk ---
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    safe_name = (file.filename or "upload").replace("/", "_")
    dest = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}_{safe_name}")

    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            await f.write(chunk)

    logger.info("Saved upload %s (user %s)", dest, user_id)

    # --- Respond immediately, transcribe in background ---
    background_tasks.add_task(_process_upload, dest, user_id)
    return {"ok": True}


# Static files mount last (catchall -- must be after API routes)
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
    name="static",
)
