"""Tests for bot.services.word_boost — loaders and custom_spelling apply."""
import json

from bot.services.word_boost import (
    apply_custom_spelling,
    load_custom_spelling,
    load_word_boost,
)


def test_load_word_boost_skips_blanks_and_comments(tmp_path):
    p = tmp_path / "wb.txt"
    p.write_text(
        "# header comment\n"
        "\n"
        "aiogram\n"
        "  yt-dlp  \n"
        "# inline comment\n"
        "AssemblyAI\n",
        encoding="utf-8",
    )
    assert load_word_boost(str(p)) == ["aiogram", "yt-dlp", "AssemblyAI"]


def test_load_word_boost_dedupes(tmp_path):
    p = tmp_path / "wb.txt"
    p.write_text("foo\nbar\nfoo\n", encoding="utf-8")
    assert load_word_boost(str(p)) == ["foo", "bar"]


def test_load_word_boost_missing_file_returns_empty(tmp_path):
    assert load_word_boost(str(tmp_path / "does-not-exist.txt")) == []


def test_load_custom_spelling_returns_dict(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"ассемблиай": "AssemblyAI"}), encoding="utf-8")
    assert load_custom_spelling(str(p)) == {"ассемблиай": "AssemblyAI"}


def test_load_custom_spelling_invalid_json_returns_empty(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text("not json {{", encoding="utf-8")
    assert load_custom_spelling(str(p)) == {}


def test_load_custom_spelling_non_object_returns_empty(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    assert load_custom_spelling(str(p)) == {}


def test_apply_custom_spelling_replaces_occurrences():
    text = "это ассемблиай и ещё раз ассемблиай"
    result = apply_custom_spelling(text, {"ассемблиай": "AssemblyAI"})
    assert result == "это AssemblyAI и ещё раз AssemblyAI"


def test_apply_custom_spelling_empty_mapping_no_op():
    text = "anything"
    assert apply_custom_spelling(text, {}) == text
