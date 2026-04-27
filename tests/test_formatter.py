"""Tests for bot.services.formatter — render_with_speakers + title generation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services import formatter


def _utt(speaker, text):
    return SimpleNamespace(speaker=speaker, text=text, start=0, end=0)


# ---------- render_with_speakers ----------


def test_render_two_speakers_maps_to_russian_labels():
    out = formatter.render_with_speakers([
        _utt("A", "Привет."),
        _utt("B", "Здравствуй."),
        _utt("A", "Как дела?"),
    ])
    assert "Спикер 1: Привет." in out
    assert "Спикер 2: Здравствуй." in out
    assert "Спикер 1: Как дела?" in out


def test_render_three_speakers_in_appearance_order():
    out = formatter.render_with_speakers([
        _utt("B", "Б."),
        _utt("A", "А."),
        _utt("C", "В."),
    ])
    # First seen is B → Спикер 1, then A → Спикер 2, then C → Спикер 3.
    assert out.split("\n\n") == [
        "Спикер 1: Б.",
        "Спикер 2: А.",
        "Спикер 3: В.",
    ]


def test_render_single_speaker_no_prefix():
    out = formatter.render_with_speakers([
        _utt("A", "Первый абзац."),
        _utt("A", "Второй абзац."),
    ])
    assert "Спикер" not in out
    assert out == "Первый абзац.\n\nВторой абзац."


def test_render_merges_adjacent_same_speaker():
    out = formatter.render_with_speakers([
        _utt("A", "Первая часть."),
        _utt("A", "Вторая часть."),
        _utt("B", "Ответ."),
    ])
    # First two A-utterances must collapse into a single labelled block.
    assert "Спикер 1: Первая часть. Вторая часть." in out
    assert "Спикер 2: Ответ." in out


def test_render_empty_returns_empty():
    assert formatter.render_with_speakers([]) == ""


def test_render_skips_blank_utterances():
    out = formatter.render_with_speakers([
        _utt("A", "Реплика."),
        _utt("B", "   "),
        _utt("A", "Ещё."),
    ])
    assert "B" not in out
    assert "Спикер" in out


# ---------- generate_title ----------


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


@pytest.mark.asyncio
async def test_generate_title_returns_clean_string(monkeypatch):
    create = AsyncMock(return_value=_response('"  Подкаст про AI.  "'))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    title = await formatter.generate_title("длинный текст транскрипции", None)

    assert title == "Подкаст про AI"


@pytest.mark.asyncio
async def test_generate_title_uses_full_text_under_cap(monkeypatch):
    create = AsyncMock(return_value=_response("Заголовок"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    raw = "А" * 500  # well under TITLE_MAX_INPUT_CHARS
    await formatter.generate_title(raw, None)

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    assert raw in user_msg


@pytest.mark.asyncio
async def test_generate_title_truncates_when_too_long(monkeypatch):
    create = AsyncMock(return_value=_response("Заголовок"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    raw = "А" * (formatter.TITLE_MAX_INPUT_CHARS + 1000)
    await formatter.generate_title(raw, None)

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    # We didn't send the whole giant raw text.
    assert len(user_msg) < formatter.TITLE_MAX_INPUT_CHARS + 200
    assert ("А" * formatter.TITLE_MAX_INPUT_CHARS) in user_msg


@pytest.mark.asyncio
async def test_generate_title_empty_input_returns_empty(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    assert await formatter.generate_title("", None) == ""
    assert await formatter.generate_title("   ", None) == ""
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_title_includes_filename_hint(monkeypatch):
    create = AsyncMock(return_value=_response("T"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    await formatter.generate_title("text", "meeting.mp3")

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    assert "Source: meeting.mp3" in user_msg


@pytest.mark.asyncio
async def test_generate_title_uses_temperature_zero(monkeypatch):
    create = AsyncMock(return_value=_response("T"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    await formatter.generate_title("text", None)

    assert create.await_args.kwargs["temperature"] == 0.0
