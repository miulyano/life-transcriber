import os
import uuid

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import extract_audio
from bot.services.transcriber import transcribe
from bot.utils.text import reply_text_or_file

router = Router()


async def _download_and_transcribe(bot: Bot, file_id: str, suffix: str, status: Message) -> str:
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    video_path = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}{suffix}")
    await bot.download(file_id, destination=video_path)

    await status.edit_text("🔊 Извлекаю аудио...")
    audio_path = None
    try:
        audio_path = await extract_audio(video_path, settings.TEMP_DIR)

        async def on_progress(current: int, total: int) -> None:
            await status.edit_text(f"🎙 Транскрибирую часть {current} из {total}...")

        await status.edit_text("🎙 Транскрибирую...")
        return await transcribe(audio_path, progress_callback=on_progress)
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)


@router.message(F.video)
async def handle_video(message: Message, bot: Bot) -> None:
    status = await message.reply("⬇️ Скачиваю видео...")
    try:
        text = await _download_and_transcribe(bot, message.video.file_id, ".mp4", status)
        await status.delete()
        await reply_text_or_file(message, text)
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")


@router.message(F.document.mime_type.startswith("video/"))
async def handle_video_document(message: Message, bot: Bot) -> None:
    status = await message.reply("⬇️ Скачиваю видео...")
    ext = os.path.splitext(message.document.file_name or "")[1] or ".mp4"
    try:
        text = await _download_and_transcribe(bot, message.document.file_id, ext, status)
        await status.delete()
        await reply_text_or_file(message, text)
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")
