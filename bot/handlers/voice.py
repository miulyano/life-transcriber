import os
import uuid

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.transcriber import transcribe
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file

router = Router()


async def _download_tg_file(bot: Bot, file_id: str, suffix: str) -> str:
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    dest = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}{suffix}")
    await bot.download(file_id, destination=dest)
    return dest


async def _handle(message: Message, bot: Bot, file_id: str, suffix: str, label: str) -> None:
    text: str | None = None
    async with ProgressReporter(message, label) as reporter:
        dest = await _download_tg_file(bot, file_id, suffix)
        try:
            text = await transcribe(dest, on_progress=reporter.set_progress)
        finally:
            if os.path.exists(dest):
                os.unlink(dest)
        await reporter.finish()
    if text is not None:
        await reply_text_or_file(message, text)


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    await _handle(message, bot, message.voice.file_id, ".ogg", "Транскрибирую…")


@router.message(F.video_note)
async def handle_video_note(message: Message, bot: Bot) -> None:
    await _handle(message, bot, message.video_note.file_id, ".mp4", "Транскрибирую кружочек…")
