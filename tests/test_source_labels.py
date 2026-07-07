"""Tests for source labels, the link platform detector, and the caption footer."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import settings
from bot.services.downloader import detect_link_source_type, is_youtube_url
from bot.utils.source_labels import format_source_line, source_label
from bot.utils.text import TELEGRAM_CAPTION_LIMIT, prepare_transcript, reply_text_or_file


# --- detect_link_source_type ---


@pytest.mark.parametrize("url,expected", [
    ("https://disk.yandex.ru/i/abc123", "yandex_disk"),
    ("https://yadi.sk/i/abc123", "yandex_disk"),
    ("https://www.instagram.com/reel/abc/", "instagram"),
    ("https://www.facebook.com/watch?v=123", "facebook"),
    ("https://music.yandex.ru/album/1/track/2", "yandex_music"),
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc123", "youtube"),
    ("https://m.youtube.com/watch?v=abc", "youtube"),
    ("https://music.youtube.com/watch?v=abc", "youtube"),
    ("https://rutube.ru/video/xyz/", "link"),
    ("https://vk.com/video-123_456", "link"),
    ("http://example.com", "link"),
])
def test_detect_link_source_type(url, expected):
    assert detect_link_source_type(url) == expected


def test_is_youtube_url_rejects_lookalike_domains():
    assert not is_youtube_url("https://notyoutube.com/watch?v=abc")
    assert not is_youtube_url("https://youtube.com.evil.example/watch")


# --- source_label / format_source_line ---


@pytest.mark.parametrize("source_type,expected", [
    ("youtube", "YouTube"),
    ("yandex_disk", "Яндекс Диск"),
    ("yandex_music", "Яндекс Музыка"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("link", "Ссылка"),
    ("voice", "Голосовое"),
    ("video_note", "Кружок"),
    ("video", "Видео"),
    ("document", "Файл"),
    ("webapp", "Файл"),
    ("unknown", None),
    (None, None),
])
def test_source_label(source_type, expected):
    assert source_label(source_type) == expected


def test_format_source_line_with_source():
    assert format_source_line("youtube", timecoded=True) == "Источник: YouTube · 🕒 с таймкодами"
    assert format_source_line("voice", timecoded=False) == "Источник: Голосовое · без таймкодов"


def test_format_source_line_without_source():
    assert format_source_line("unknown", timecoded=True) == "🕒 с таймкодами"
    assert format_source_line(None, timecoded=False) == "без таймкодов"


# --- prepare_transcript footer ---


def _long_text(header="Заголовок"):
    return f"{header}\n\n" + "а" * (settings.LONG_TEXT_THRESHOLD + 1)


def test_footer_in_caption_timecoded_file():
    d = prepare_transcript(_long_text(), source_type="youtube", has_timecoded_file=True)
    assert d.send_as_file
    assert d.caption_html.endswith("<i>Источник: YouTube · 🕒 с таймкодами</i>")
    assert d.caption_html.startswith("<b>Заголовок</b>")


def test_footer_in_caption_plain_file():
    d = prepare_transcript(_long_text(), source_type="webapp", has_timecoded_file=False)
    assert d.caption_html.endswith("<i>Источник: Файл · без таймкодов</i>")


def test_footer_in_inline_body():
    d = prepare_transcript("Короткий текст.", source_type="voice")
    assert not d.send_as_file
    # Inline messages are never timecoded, even if a timecoded variant exists.
    assert d.body_html.endswith("\n\n<i>Источник: Голосовое · без таймкодов</i>")


def test_inline_footer_ignores_has_timecoded_file():
    d = prepare_transcript("Короткий текст.", source_type="voice", has_timecoded_file=True)
    assert "без таймкодов" in d.body_html


def test_no_footer_without_source_type():
    long_ = prepare_transcript(_long_text())
    short = prepare_transcript("Короткий текст.")
    assert "таймкод" not in long_.caption_html
    assert "таймкод" not in short.body_html


def test_caption_with_footer_stays_within_limit():
    # Header alone exceeds the caption limit — footer must still fit.
    huge_header = "З" * 2000
    d = prepare_transcript(
        f"{huge_header}\n\n" + "а" * (settings.LONG_TEXT_THRESHOLD + 1),
        source_type="youtube",
        has_timecoded_file=True,
    )
    footer = "Источник: YouTube · 🕒 с таймкодами"
    assert d.caption_html.endswith(f"<i>{footer}</i>")
    # Plain-visible length (strip the b/i tags) must fit Telegram's limit.
    visible = (
        d.caption_html.replace("<b>", "").replace("</b>", "")
        .replace("<i>", "").replace("</i>", "")
    )
    assert len(visible) <= TELEGRAM_CAPTION_LIMIT


def test_caption_only_footer_when_no_header():
    d = prepare_transcript(
        "а" * (settings.LONG_TEXT_THRESHOLD + 1), source_type="link"
    )
    assert d.caption_html == "<i>Источник: Ссылка · без таймкодов</i>"


# --- reply_text_or_file passes the flags through ---


async def test_reply_text_or_file_file_footer_reflects_file_text():
    message = MagicMock()
    message.reply_document = AsyncMock()

    await reply_text_or_file(
        message, _long_text(), "[0:00.000] тайм", source_type="youtube"
    )

    caption = message.reply_document.await_args.kwargs["caption"]
    assert caption.endswith("<i>Источник: YouTube · 🕒 с таймкодами</i>")


async def test_reply_text_or_file_no_file_text_means_plain():
    message = MagicMock()
    message.reply_document = AsyncMock()

    await reply_text_or_file(message, _long_text(), None, source_type="youtube")

    caption = message.reply_document.await_args.kwargs["caption"]
    assert caption.endswith("<i>Источник: YouTube · без таймкодов</i>")
