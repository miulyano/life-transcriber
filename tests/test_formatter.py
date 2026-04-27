"""Tests for bot.services.formatter — render_with_speakers + analyze_transcript."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services import formatter


def _utt(speaker, text):
    return SimpleNamespace(speaker=speaker, text=text, start=0, end=0)


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


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


def test_render_uses_name_map_when_provided():
    out = formatter.render_with_speakers(
        [_utt("A", "Привет."), _utt("B", "Здравствуй.")],
        name_map={"A": "Иван", "B": "Маша"},
    )
    assert "Иван: Привет." in out
    assert "Маша: Здравствуй." in out


def test_render_falls_back_to_speaker_n_for_unknown_label():
    out = formatter.render_with_speakers(
        [_utt("A", "Текст А."), _utt("B", "Текст Б.")],
        name_map={"A": "Иван"},  # B not in map
    )
    assert "Иван: Текст А." in out
    assert "Спикер 2: Текст Б." in out


# ---------- analyze_transcript ----------


@pytest.mark.asyncio
async def test_analyze_transcript_returns_title_and_empty_speakers_for_mono(monkeypatch):
    payload = json.dumps({"title": "Подкаст про AI", "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    title, name_map = await formatter.analyze_transcript(
        "длинный текст", [_utt("A", "длинный текст")], None
    )
    assert title == "Подкаст про AI"
    assert name_map == {}


@pytest.mark.asyncio
async def test_analyze_transcript_returns_speaker_names(monkeypatch):
    payload = json.dumps({"title": "Встреча", "speakers": {"A": "Иван", "B": "Маша"}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    title, name_map = await formatter.analyze_transcript(
        "raw text",
        [_utt("A", "Привет, я Иван."), _utt("B", "Привет, я Маша.")],
        None,
    )
    assert title == "Встреча"
    assert name_map == {"A": "Иван", "B": "Маша"}


@pytest.mark.asyncio
async def test_analyze_transcript_sends_labeled_text_for_multi_speaker(monkeypatch):
    payload = json.dumps({"title": "T", "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    utterances = [_utt("A", "Первый."), _utt("B", "Второй.")]
    await formatter.analyze_transcript("raw", utterances, None)

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    assert "A: Первый." in user_msg
    assert "B: Второй." in user_msg


@pytest.mark.asyncio
async def test_analyze_transcript_sends_raw_text_for_mono(monkeypatch):
    payload = json.dumps({"title": "T", "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    raw = "просто текст без меток"
    await formatter.analyze_transcript(raw, [_utt("A", raw)], None)

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    assert raw in user_msg
    assert "A:" not in user_msg


@pytest.mark.asyncio
async def test_analyze_transcript_empty_input_returns_empty(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    assert await formatter.analyze_transcript("", [], None) == ("", {})
    assert await formatter.analyze_transcript("   ", [], None) == ("", {})
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_transcript_cleans_title(monkeypatch):
    payload = json.dumps({"title": '"  Подкаст про AI.  "', "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    title, _ = await formatter.analyze_transcript("text", [_utt("A", "text")], None)
    assert title == "Подкаст про AI"


@pytest.mark.asyncio
async def test_analyze_transcript_includes_filename_hint(monkeypatch):
    payload = json.dumps({"title": "T", "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    await formatter.analyze_transcript("text", [_utt("A", "text")], "meeting.mp3")

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    assert "Source: meeting.mp3" in user_msg


@pytest.mark.asyncio
async def test_analyze_transcript_uses_json_mode_and_temperature_zero(monkeypatch):
    payload = json.dumps({"title": "T", "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    await formatter.analyze_transcript("text", [_utt("A", "text")], None)

    kwargs = create.await_args.kwargs
    assert kwargs["temperature"] == 0.0
    assert kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_analyze_transcript_truncates_when_too_long(monkeypatch):
    payload = json.dumps({"title": "T", "speakers": {}})
    create = AsyncMock(return_value=_response(payload))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    raw = "А" * (formatter.ANALYSIS_MAX_INPUT_CHARS + 1000)
    await formatter.analyze_transcript(raw, [_utt("A", raw)], None)

    user_msg = create.await_args.kwargs["messages"][1]["content"]
    assert len(user_msg) < formatter.ANALYSIS_MAX_INPUT_CHARS + 200


@pytest.mark.asyncio
async def test_analyze_transcript_returns_empty_on_api_error(monkeypatch):
    create = AsyncMock(side_effect=RuntimeError("api down"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.analyze_transcript("text", [_utt("A", "text")], None)
    assert result == ("", {})
