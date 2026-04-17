import os
import re

from aiogram import F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import download_audio
from bot.services.transcriber import transcribe
from bot.utils.text import reply_text_or_file

router = Router()

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


@router.message(F.text.regexp(URL_RE))
async def handle_link(message: Message) -> None:
    urls = URL_RE.findall(message.text)
    url = urls[0]

    status = await message.reply("⬇️ Скачиваю аудио...")
    audio_path = None
    try:
        audio_path = await download_audio(url, settings.TEMP_DIR)

        async def on_progress(current: int, total: int) -> None:
            await status.edit_text(f"🎙 Транскрибирую часть {current} из {total}...")

        await status.edit_text("🎙 Транскрибирую...")
        text = await transcribe(audio_path, progress_callback=on_progress)
        await status.delete()
        await reply_text_or_file(message, text)
    except RuntimeError as e:
        error_msg = str(e)
        if "yt-dlp" in error_msg:
            await status.edit_text("Не удалось скачать видео с этой платформы. Попробуй другую ссылку.")
        else:
            await status.edit_text(f"Ошибка: {error_msg}")
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")
    finally:
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)
