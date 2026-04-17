import os
import uuid

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.transcriber import transcribe
from bot.utils.text import reply_text_or_file

router = Router()


async def _transcribe_tg_file(bot: Bot, file_id: str, suffix: str, status: Message) -> str:
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    dest = os.path.join(settings.TEMP_DIR, f"{uuid.uuid4().hex}{suffix}")
    await bot.download(file_id, destination=dest)

    async def on_progress(current: int, total: int) -> None:
        await status.edit_text(f"🎙 Транскрибирую часть {current} из {total}...")

    await status.edit_text("🎙 Транскрибирую...")
    try:
        return await transcribe(dest, progress_callback=on_progress)
    finally:
        if os.path.exists(dest):
            os.unlink(dest)


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    status = await message.reply("⬇️ Скачиваю...")
    try:
        text = await _transcribe_tg_file(bot, message.voice.file_id, ".ogg", status)
        await status.delete()
        await reply_text_or_file(message, text)
    except Exception as e:
        await status.edit_text(f"Ошибка транскрибации: {e}")


@router.message(F.video_note)
async def handle_video_note(message: Message, bot: Bot) -> None:
    status = await message.reply("⬇️ Скачиваю кружочек...")
    try:
        text = await _transcribe_tg_file(bot, message.video_note.file_id, ".mp4", status)
        await status.delete()
        await reply_text_or_file(message, text)
    except Exception as e:
        await status.edit_text(f"Ошибка транскрибации: {e}")
