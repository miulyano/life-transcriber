import pytest

from bot.utils.filename import build_filename, extract_title


def test_cyrillic_transliterated_and_slugified():
    assert build_filename("Интервью Иванов Петров") == "intervyu-ivanov-petrov.txt"


def test_limits_to_four_words():
    assert (
        build_filename("Один два три четыре пять шесть")
        == "odin-dva-tri-chetyre.txt"
    )


def test_strips_punctuation():
    assert (
        build_filename("Подкаст: «Что будет с разработкой?»")
        == "podkast-chto-budet-s.txt"
    )


def test_empty_title_falls_back():
    assert build_filename("") == "transcript.txt"
    assert build_filename("   ") == "transcript.txt"
    assert build_filename(None) == "transcript.txt"


def test_latin_input_kept():
    assert build_filename("AI in Podcasts") == "ai-in-podcasts.txt"


def test_only_punctuation_falls_back():
    assert build_filename("???!!!") == "transcript.txt"


def test_length_capped():
    long_title = "очень " * 20
    result = build_filename(long_title)
    assert result.endswith(".txt")
    assert len(result) <= 64  # 60 + ".txt"


def test_custom_suffix():
    assert build_filename("Тест", suffix=".md") == "test.md"


def test_extract_title_skips_blank_lines():
    text = "\n\nЗаголовок\n\nтело\n"
    assert extract_title(text) == "Заголовок"


def test_extract_title_empty():
    assert extract_title("") is None
    assert extract_title("\n\n\n") is None
