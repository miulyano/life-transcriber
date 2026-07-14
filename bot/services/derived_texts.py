"""Композиция доставки производных текстов (конспект, очистка).

Вынесено из bot/handlers/callbacks.py, чтобы webapp/MCP доставлял результат
в чат ровно так же, как бот по inline-кнопке. Модуль чистый от aiogram:
решает message-vs-file, собирает filename/caption и итоговый текст файла;
саму отправку делает вызывающий.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bot.constants import TELEGRAM_TEXT_LIMIT
from bot.utils.filename import build_filename, extract_title, split_header_and_body
from bot.utils.markdown import markdown_to_telegram_html
from bot.utils.source_labels import format_source_line


@dataclass
class SummaryDelivery:
    as_file: bool
    message_html: Optional[str]  # короткий конспект — HTML-сообщение
    file_text: Optional[str]  # длинный — raw summary файлом
    filename: Optional[str]
    caption: Optional[str]


@dataclass
class CleanupDelivery:
    as_file: bool
    file_text: str
    filename: str
    caption: str


def build_summary_delivery(source_text: str, summary: str) -> SummaryDelivery:
    """Как доставлять конспект: сообщением или файлом (лимит Telegram)."""
    body = markdown_to_telegram_html(summary)
    message = f"📝 Краткий конспект:\n\n{body}"
    if len(message) <= TELEGRAM_TEXT_LIMIT:
        return SummaryDelivery(
            as_file=False,
            message_html=message,
            file_text=None,
            filename=None,
            caption=None,
        )
    # Telegram text-message limit is ~4096; long summaries go as a
    # plain-text file instead so the user still gets the full thing.
    original_title = extract_title(source_text)
    filename = build_filename(
        f"{original_title} summary" if original_title else "summary",
    )
    caption = (
        f"📝 Краткий конспект: {original_title}"
        if original_title
        else "📝 Краткий конспект (длинный — прислал файлом)"
    )
    return SummaryDelivery(
        as_file=True,
        message_html=None,
        file_text=summary,
        filename=filename,
        caption=caption,
    )


def build_cleanup_delivery(
    source_text: str, cleaned_body: str, source_type: Optional[str]
) -> CleanupDelivery:
    """Собрать файл очищенного текста: шапка + cleaned, filename, caption.

    ``source_type`` передаётся параметром: бот берёт его из кэша по
    text_hash, MCP — из ``TranscriptRecord``.
    """
    header, body = split_header_and_body(source_text)
    if header and body:
        final_text = f"{header}\n\n{cleaned_body.lstrip()}"
    else:
        final_text = cleaned_body

    original_title = extract_title(source_text)
    filename = build_filename(
        f"{original_title} clean" if original_title else "clean transcript",
    )
    caption = (
        f"Очищенный текст: {original_title}" if original_title else "Очищенный текст"
    )
    # Cleanup always works on the plain (non-timecoded) variant.
    source_line = format_source_line(source_type, timecoded=False)
    caption = f"{caption}\n{source_line}"
    return CleanupDelivery(
        as_file=True,
        file_text=final_text,
        filename=filename,
        caption=caption,
    )
