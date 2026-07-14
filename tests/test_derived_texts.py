"""Тесты derived_texts: композиция доставки summary/cleanup.

Логика вынесена из bot/handlers/callbacks.py — поведение должно совпадать
с прежним 1-в-1 (test_callbacks.py остаётся зелёным без правок).
"""
from bot.constants import TELEGRAM_TEXT_LIMIT
from bot.services.derived_texts import build_cleanup_delivery, build_summary_delivery


SOURCE = "Заголовок беседы\n\nСпикер 1: привет\nСпикер 2: привет-привет"


# ------------------------------------------------------------------ summary


def test_summary_short_goes_as_message():
    d = build_summary_delivery(SOURCE, "- пункт один\n- пункт два")
    assert d.as_file is False
    assert d.message_html.startswith("📝 Краткий конспект:\n\n")
    assert "пункт один" in d.message_html
    assert d.file_text is None


def test_summary_message_html_converts_markdown():
    d = build_summary_delivery(SOURCE, "**жирный** пункт")
    assert "<b>жирный</b>" in d.message_html


def test_summary_long_goes_as_file():
    long_summary = "x" * (TELEGRAM_TEXT_LIMIT + 1)
    d = build_summary_delivery(SOURCE, long_summary)
    assert d.as_file is True
    assert d.message_html is None
    assert d.file_text == long_summary  # файл — raw summary, не HTML
    assert d.filename.endswith(".txt")
    assert "summary" in d.filename
    assert d.caption == "📝 Краткий конспект: Заголовок беседы"


def test_summary_long_without_title():
    # extract_title даёт None только на пустом тексте
    long_summary = "x" * (TELEGRAM_TEXT_LIMIT + 1)
    d = build_summary_delivery("", long_summary)
    assert d.caption == "📝 Краткий конспект (длинный — прислал файлом)"


# ------------------------------------------------------------------ cleanup


def test_cleanup_always_file():
    d = build_cleanup_delivery(SOURCE, "чистый текст", source_type="voice")
    assert d.as_file is True
    assert d.filename.endswith(".txt")
    assert "clean" in d.filename


def test_cleanup_preserves_header():
    d = build_cleanup_delivery(SOURCE, "  чистое тело", source_type="voice")
    assert d.file_text.startswith("Заголовок беседы\n\n")
    assert d.file_text.endswith("чистое тело")


def test_cleanup_no_header_uses_cleaned_as_is():
    d = build_cleanup_delivery("plain text no header", "cleaned", source_type=None)
    assert d.file_text == "cleaned"


def test_cleanup_caption_with_title_and_source():
    d = build_cleanup_delivery(SOURCE, "b", source_type="voice")
    lines = d.caption.split("\n")
    assert lines[0] == "Очищенный текст: Заголовок беседы"
    assert len(lines) == 2  # source line из format_source_line


def test_cleanup_caption_without_title():
    d = build_cleanup_delivery("", "b", source_type=None)
    assert d.caption.split("\n")[0] == "Очищенный текст"
