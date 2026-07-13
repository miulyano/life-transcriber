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


# ---------- render_with_timecodes ----------


def _tw(text, start_ms):
    return SimpleNamespace(text=text, start_ms=start_ms, end_ms=start_ms)


def _tutt(speaker, text, start_ms=0, words=None):
    return SimpleNamespace(
        speaker=speaker, text=text, start_ms=start_ms, end_ms=start_ms, words=words or []
    )


def test_timecodes_multi_label_on_own_line_and_blank_between_blocks():
    out = formatter.render_with_timecodes([
        _tutt("A", "Привет.", 0),
        _tutt("B", "Здравствуй.", 3250),
    ])
    assert out == "Спикер 1\n[0:00.000] Привет.\n\nСпикер 2\n[0:03.250] Здравствуй."


def test_timecodes_multi_same_speaker_each_utterance_own_line():
    out = formatter.render_with_timecodes([
        _tutt("A", "Раз.", 0),
        _tutt("A", "Два.", 3000),
        _tutt("B", "Три.", 6000),
    ])
    assert out == "Спикер 1\n[0:00.000] Раз.\n[0:03.000] Два.\n\nСпикер 2\n[0:06.000] Три."


def test_timecodes_mono_no_labels():
    out = formatter.render_with_timecodes([
        _tutt("A", "Первый.", 0),
        _tutt("A", "Второй.", 5000),
    ])
    assert out == "[0:00.000] Первый.\n[0:05.000] Второй."


def test_timecodes_every_sentence_gets_own_stamped_line():
    words = [
        _tw("Раз", 0),
        _tw("два", 1000),
        _tw("три.", 2000),
        _tw("Четыре", 3100),
        _tw("пять.", 4000),
        _tw("Шесть.", 5500),
    ]
    out = formatter.render_with_timecodes(
        [_tutt("A", "Раз два три. Четыре пять. Шесть.", 0, words=words)]
    )
    assert out == "[0:00.000] Раз два три.\n[0:03.100] Четыре пять.\n[0:05.500] Шесть."


def test_timecodes_never_split_mid_sentence():
    words = [
        _tw("раз", 0),
        _tw("два", 16_000),
        _tw("три", 17_000),
        _tw("конец.", 18_000),
    ]
    out = formatter.render_with_timecodes(
        [_tutt("A", "раз два три конец.", 0, words=words)]
    )
    assert out == "[0:00.000] раз два три конец."


def test_timecodes_hour_format_with_millis():
    out = formatter.render_with_timecodes([_tutt("A", "Поздний текст.", 3_700_250)])
    assert out == "[1:01:40.250] Поздний текст."


def test_timecodes_utterance_without_words_gets_single_stamp():
    out = formatter.render_with_timecodes([_tutt("A", "Длинная реплика без слов.", 42_000)])
    assert out == "[0:42.000] Длинная реплика без слов."


def test_timecodes_empty_returns_empty():
    assert formatter.render_with_timecodes([]) == ""


def test_timecodes_skips_blank_utterances():
    out = formatter.render_with_timecodes([
        _tutt("A", "Реплика.", 0),
        _tutt("B", "   ", 1000),
        _tutt("A", "Ещё.", 2000),
    ])
    assert out == "Спикер 1\n[0:00.000] Реплика.\n[0:02.000] Ещё."


def test_timecodes_name_map_and_order_match_render_with_speakers():
    out = formatter.render_with_timecodes(
        [_tutt("B", "Б.", 0), _tutt("A", "А.", 2000)],
        name_map={"A": "Иван"},
    )
    assert out == "Спикер 1\n[0:00.000] Б.\n\nИван\n[0:02.000] А."


# ---------- build_timecode_segments / render_timecode_segments ----------


def test_build_segments_multi_speaker_labels_resolved():
    segments = formatter.build_timecode_segments(
        [_tutt("A", "Привет.", 0), _tutt("B", "Здравствуй.", 3250)],
        name_map={"A": "Иван"},
    )
    assert segments == [
        formatter.TimecodeSegment(start_ms=0, text="Привет.", speaker="Иван"),
        formatter.TimecodeSegment(start_ms=3250, text="Здравствуй.", speaker="Спикер 2"),
    ]


def test_build_segments_mono_speaker_none():
    segments = formatter.build_timecode_segments(
        [_tutt("A", "Первый.", 0), _tutt("A", "Второй.", 5000)]
    )
    assert [s.speaker for s in segments] == [None, None]
    assert [s.start_ms for s in segments] == [0, 5000]


def test_build_segments_split_per_sentence():
    words = [
        _tw("Раз", 0),
        _tw("два.", 1000),
        _tw("Три.", 2500),
    ]
    segments = formatter.build_timecode_segments(
        [_tutt("A", "Раз два. Три.", 0, words=words)]
    )
    assert [(s.start_ms, s.text) for s in segments] == [(0, "Раз два."), (2500, "Три.")]


def test_render_segments_equals_render_with_timecodes():
    utterances = [
        _tutt(
            "A",
            "Раз два три. Четыре пять.",
            0,
            words=[
                _tw("Раз", 0),
                _tw("два", 1000),
                _tw("три.", 2000),
                _tw("Четыре", 3100),
                _tw("пять.", 4000),
            ],
        ),
        _tutt("B", "Шесть.", 6000),
        _tutt("A", "Семь.", 8000),
    ]
    name_map = {"B": "Мария"}
    segments = formatter.build_timecode_segments(utterances, name_map)
    assert formatter.render_timecode_segments(segments) == formatter.render_with_timecodes(
        utterances, name_map
    )


def test_render_segments_empty():
    assert formatter.render_timecode_segments([]) == ""


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


# ---------- split_into_paragraphs ----------


@pytest.mark.asyncio
async def test_split_into_paragraphs_returns_result_from_gpt(monkeypatch):
    paragraphed = "Первый абзац.\n\nВторой абзац."
    create = AsyncMock(return_value=_response(paragraphed))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.split_into_paragraphs("Первый абзац. Второй абзац.")
    assert result == paragraphed


@pytest.mark.asyncio
async def test_split_into_paragraphs_returns_original_on_error(monkeypatch):
    create = AsyncMock(side_effect=RuntimeError("api down"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    original = "Сплошной текст без абзацев."
    result = await formatter.split_into_paragraphs(original)
    assert result == original


@pytest.mark.asyncio
async def test_split_into_paragraphs_processes_all_chunks_for_long_text(monkeypatch):
    """Every chunk of a long text must be sent to GPT — nothing dropped."""
    create = AsyncMock(side_effect=[_response("Часть 1."), _response("Часть 2.")])
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    # Text with sentence boundaries, longer than PARA_SPLIT_MAX_INPUT
    sentence = "Это предложение. " * (formatter.PARA_SPLIT_MAX_INPUT // 17 + 10)
    result = await formatter.split_into_paragraphs(sentence)

    assert create.await_count >= 2
    assert "Часть 1." in result
    assert "Часть 2." in result


@pytest.mark.asyncio
async def test_split_into_paragraphs_reports_progress_per_chunk(monkeypatch):
    async def _identity_chunk(chunk: str) -> str:
        return chunk

    monkeypatch.setattr(formatter, "_split_chunk", _identity_chunk)

    calls: list[tuple[int, int]] = []

    async def _on_progress(done: int, total: int) -> None:
        calls.append((done, total))

    sentence = "Это предложение. " * (2 * formatter.PARA_SPLIT_MAX_INPUT // 17 + 10)
    await formatter.split_into_paragraphs(sentence, on_progress=_on_progress)

    total = calls[0][1]
    assert total > 1
    assert calls == [(i, total) for i in range(total)] + [(total, total)]


@pytest.mark.asyncio
async def test_split_into_paragraphs_single_chunk_skips_progress(monkeypatch):
    async def _identity_chunk(chunk: str) -> str:
        return chunk

    monkeypatch.setattr(formatter, "_split_chunk", _identity_chunk)

    calls: list[tuple[int, int]] = []

    async def _on_progress(done: int, total: int) -> None:
        calls.append((done, total))

    await formatter.split_into_paragraphs("Короткий текст.", on_progress=_on_progress)
    assert calls == []


@pytest.mark.asyncio
async def test_split_into_paragraphs_empty_returns_unchanged(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    assert await formatter.split_into_paragraphs("") == ""
    assert await formatter.split_into_paragraphs("   ") == "   "
    create.assert_not_awaited()
