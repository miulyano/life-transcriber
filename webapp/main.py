import logging
import os
import time
import uuid

import aiofiles
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from bot.config import settings
from bot.services.transcriber import transcribe
from webapp.auth import validate_init_data
from webapp.delivery import send_transcript_to_chat

logger = logging.getLogger(__name__)

app = FastAPI(title="life-transcriber webapp")

MAX_INIT_DATA_AGE = 24 * 3600  # 24 hours


@app.post("/api/upload")
async def upload(file: UploadFile, init_data: str = Form(...)):
    # --- Auth ---
    parsed = validate_init_data(init_data, settings.BOT_TOKEN)
    if not parsed:
        raise HTTPException(403, "Invalid auth")
    if time.time() - parsed["auth_date"] > MAX_INIT_DATA_AGE:
        raise HTTPException(403, "initData expired — reopen the app")

    user_id = parsed["user_id"]
    if user_id not in settings.allowed_user_ids:
        raise HTTPException(403, "Not whitelisted")

    # --- Save file to disk ---
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    safe_name = (file.filename or "upload").replace("/", "_")
    dest = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}_{safe_name}")

    try:
        async with aiofiles.open(dest, "wb") as f:
            while chunk := await file.read(1 << 20):  # 1 MB chunks
                await f.write(chunk)

        logger.info("Saved upload %s (user %s)", dest, user_id)

        # --- Transcribe ---
        text = await transcribe(dest)

    finally:
        if os.path.exists(dest):
            os.unlink(dest)

    # --- Deliver result to bot chat ---
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await send_transcript_to_chat(bot, user_id, text)
    finally:
        await bot.session.close()

    logger.info("Transcript delivered to user %s", user_id)
    return {"ok": True}


# Static files mount last (catchall — must be after API routes)
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
    name="static",
)
