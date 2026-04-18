from __future__ import annotations

import os
import re

from aiogram import F, Router
from aiogram.types import Message

from bot.config import settings
from bot.services.downloader import download_audio
from bot.services.formatter import format_transcript
from bot.services.transcriber import transcribe
from bot.utils.progress import ProgressReporter
from bot.utils.text import reply_text_or_file

router = Router()

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _friendly_error(error_msg: str) -> str:
    if error_msg.startswith("instagram:"):
        detail = error_msg.split(":", 1)[1].strip()
        if not detail:
            return "Ошибка при обработке Instagram"
        return detail[:1].upper() + detail[1:]
    if error_msg.startswith("yandex-disk:"):
        detail = error_msg.split(":", 1)[1].strip()
        if not detail:
            return "Ошибка Яндекс Диска"
        return detail[:1].upper() + detail[1:]
    if error_msg.startswith("yandex-music:"):
        detail = error_msg.split(":", 1)[1].strip()
        if not detail:
            return "Ошибка Яндекс Музыки"
        return detail[:1].upper() + detail[1:]
    if error_msg.startswith("facebook:"):
        detail = error_msg.split(":", 1)[1].strip()
        if not detail:
            return "Ошибка при обработке Facebook"
        return detail[:1].upper() + detail[1:]
    if "yt-dlp" in error_msg:
        return "Не удалось скачать видео с этой платформы. Попробуй другую ссылку."
    return f"Ошибка: {error_msg}"


@router.message(F.text.regexp(URL_RE))
async def handle_link(message: Message) -> None:
    urls = URL_RE.findall(message.text)
    url = urls[0]

    audio_path: str | None = None
    source_title: str | None = None
    text: str | None = None
    async with ProgressReporter(message, "Скачиваю аудио по ссылке…") as reporter:
        try:
            try:
                audio_path, source_title = await download_audio(url, settings.TEMP_DIR)
            except RuntimeError as e:
                await reporter.fail(_friendly_error(str(e)))
                return
            await reporter.set_phase("Транскрибирую…")
            text = await transcribe(
                audio_path,
                on_progress=reporter.set_progress,
                on_progress_fraction=reporter.set_progress_fraction,
            )
        finally:
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
        if text is not None:
            await reporter.set_phase("Форматирую…")
            text = await format_transcript(
                text,
                filename_hint=source_title,
                on_progress_fraction=reporter.set_progress_fraction,
            )
            await reporter.set_phase("Отправляю результат…")
            await reply_text_or_file(message, text)
        await reporter.finish()
