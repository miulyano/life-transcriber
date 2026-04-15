import os
import uuid

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import extract_audio
from bot.services.transcriber import transcribe
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file

router = Router()


async def _process(message: Message, bot: Bot, file_id: str, suffix: str) -> None:
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    video_path = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}{suffix}")
    audio_path: str | None = None
    text: str | None = None

    async with ProgressReporter(message, "Скачиваю видео из Telegram…") as reporter:
        try:
            await bot.download(file_id, destination=video_path)
            await reporter.set_phase("Извлекаю аудио…")
            audio_path = await extract_audio(video_path, settings.TEMP_DIR)
            await reporter.set_phase("Транскрибирую…")
            text = await transcribe(audio_path, on_progress=reporter.set_progress)
        finally:
            if os.path.exists(video_path):
                os.unlink(video_path)
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
        await reporter.finish()

    if text is not None:
        await reply_text_or_file(message, text)


@router.message(F.video)
async def handle_video(message: Message, bot: Bot) -> None:
    await _process(message, bot, message.video.file_id, ".mp4")


@router.message(F.document.mime_type.startswith("video/"))
async def handle_video_document(message: Message, bot: Bot) -> None:
    ext = os.path.splitext(message.document.file_name or "")[1] or ".mp4"
    await _process(message, bot, message.document.file_id, ext)
