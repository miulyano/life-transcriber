import os
import re

from aiogram import F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import download_audio
from bot.services.transcriber import transcribe
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file

router = Router()

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _friendly_error(error_msg: str) -> str:
    if error_msg.startswith("yandex-disk:"):
        detail = error_msg.split(":", 1)[1].strip()
        if not detail:
            return "Ошибка Яндекс Диска"
        return detail[:1].upper() + detail[1:]
    if "yt-dlp" in error_msg:
        return "Не удалось скачать видео с этой платформы. Попробуй другую ссылку."
    return f"Ошибка: {error_msg}"


@router.message(F.text.regexp(URL_RE))
async def handle_link(message: Message) -> None:
    urls = URL_RE.findall(message.text)
    url = urls[0]

    audio_path: str | None = None
    text: str | None = None
    async with ProgressReporter(message, "Скачиваю аудио по ссылке…") as reporter:
        try:
            try:
                audio_path = await download_audio(url, settings.TEMP_DIR)
            except RuntimeError as e:
                await reporter.fail(_friendly_error(str(e)))
                return
            await reporter.set_phase("Транскрибирую…")
            text = await transcribe(audio_path, on_progress=reporter.set_progress)
        finally:
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
        await reporter.finish()

    if text is not None:
        await reply_text_or_file(message, text)
