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

    await message.reply(f"Скачиваю аудио по ссылке...")
    audio_path = None
    try:
        audio_path = await download_audio(url, settings.TEMP_DIR)
        text = await transcribe(audio_path)
        await reply_text_or_file(message, text)
    except RuntimeError as e:
        error_msg = str(e)
        if error_msg.startswith("yandex-disk:"):
            detail = error_msg.split(":", 1)[1].strip()
            await message.reply(detail[:1].upper() + detail[1:] if detail else "Ошибка Яндекс Диска")
        elif "yt-dlp" in error_msg:
            await message.reply("Не удалось скачать видео с этой платформы. Попробуй другую ссылку.")
        else:
            await message.reply(f"Ошибка: {error_msg}")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")
    finally:
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)
