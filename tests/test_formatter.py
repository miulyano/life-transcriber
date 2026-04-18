from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services import formatter


def _response(content: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


def _long_raw(chars: int = 20_000) -> str:
    """Build a Russian-looking transcript of full sentences, long enough to
    force chunking at FORMATTER_CHUNK_CHARS=8000.
    """
    sentence = "Это предложение длинной транскрипции для теста разбиения на чанки."
    # ~70 chars each → ~285 sentences for 20k chars.
    needed = (chars // len(sentence)) + 10
    return " ".join([sentence] * needed)


# ---------- single-call path ----------


async def test_short_input_single_call_returns_formatted_text(monkeypatch):
    create = AsyncMock(return_value=_response("Title\n\nbody paragraph"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.format_transcript("short input text")

    assert result == "Title\n\nbody paragraph"
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["messages"][0]["content"] == formatter.SYSTEM_PROMPT


async def test_short_input_with_truncation_returns_raw(monkeypatch, caplog):
    # finish_reason=length on the single-call path → fall back to raw_text.
    create = AsyncMock(return_value=_response("partial", finish_reason="length"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    raw = "short input text"
    result = await formatter.format_transcript(raw)

    assert result == raw
    create.assert_awaited_once()


async def test_short_input_api_exception_returns_raw(monkeypatch):
    create = AsyncMock(side_effect=RuntimeError("openai down"))
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    raw = "short input text"
    result = await formatter.format_transcript(raw)

    assert result == raw


async def test_empty_text_short_circuits(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    assert await formatter.format_transcript("") == ""
    assert await formatter.format_transcript("   ") == "   "
    create.assert_not_awaited()


# ---------- chunked path ----------


async def test_long_input_chunked_flow_title_plus_parts(monkeypatch):
    raw = _long_raw(20_000)

    # Expected order of calls:
    #   1) title generation (TITLE_SYSTEM_PROMPT)
    #   2) first chunk (CHUNK_SYSTEM_PROMPT)
    #   3..N) continuation chunks (CONTINUATION_SYSTEM_PROMPT)
    create = AsyncMock(
        side_effect=[
            _response("Short Podcast Title"),
            _response("Иван: первая реплика.\n\nМария: вторая реплика."),
            _response("Иван: третья реплика продолжения."),
            _response("Мария: финальная реплика."),
        ]
    )
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    progress_reports: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        progress_reports.append((done, total))

    result = await formatter.format_transcript(
        raw, filename_hint="podcast.mp3", on_progress=on_progress
    )

    # Call count is at least 1 (title) + N chunk calls. Tune expectation to the
    # actual chunk count the splitter produced.
    chunks = formatter.split_long_text(
        raw,
        max_chars=formatter.FORMATTER_CHUNK_CHARS,
        overlap_chars=0,
        prefer_boundaries=formatter.SENTENCE_BOUNDARIES,
    )
    assert len(chunks) >= 2, "test precondition: input must actually chunk"
    assert create.await_count == 1 + len(chunks)

    calls = create.await_args_list
    assert calls[0].kwargs["messages"][0]["content"] == formatter.TITLE_SYSTEM_PROMPT
    assert calls[1].kwargs["messages"][0]["content"] == formatter.CHUNK_SYSTEM_PROMPT
    for call in calls[2:]:
        assert (
            call.kwargs["messages"][0]["content"]
            == formatter.CONTINUATION_SYSTEM_PROMPT
        )

    # Result must start with title + blank line.
    assert result.startswith("Short Podcast Title\n\n")
    # And must contain all chunk bodies.
    assert "Иван: первая реплика." in result
    assert "Мария: финальная реплика." in result

    # Progress must be reported with total = len(chunks) + 1 (title step).
    assert progress_reports
    totals = {total for _, total in progress_reports}
    assert totals == {len(chunks) + 1}
    # Final report should be (total, total).
    assert progress_reports[-1] == (len(chunks) + 1, len(chunks) + 1)


async def test_long_input_speaker_labels_passed_to_continuation(monkeypatch):
    raw = _long_raw(20_000)
    create = AsyncMock(
        side_effect=[
            _response("Title"),
            _response("Иван: привет.\n\nМария: здравствуй."),
            # Continuation responses: we only care about what we feed in.
            _response("Иван: продолжение."),
            _response("Мария: ещё реплика."),
        ]
    )
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    await formatter.format_transcript(raw, filename_hint="hint.mp3")

    # Third and later calls must carry the speaker-labels block referencing
    # both labels extracted from the first formatted chunk.
    for call in create.await_args_list[2:]:
        user_msg = call.kwargs["messages"][1]["content"]
        assert "Метки спикеров из предыдущих фрагментов:" in user_msg
        assert "Иван" in user_msg
        assert "Мария" in user_msg


async def test_long_input_chunk_retry_then_success(monkeypatch):
    raw = _long_raw(20_000)
    chunks = formatter.split_long_text(
        raw,
        max_chars=formatter.FORMATTER_CHUNK_CHARS,
        overlap_chars=0,
        prefer_boundaries=formatter.SENTENCE_BOUNDARIES,
    )
    expected_chunk_calls = len(chunks)

    # Call plan: title OK, first chunk FAILS once then OK, rest OK.
    responses: list = [_response("Title")]  # title
    responses.append(RuntimeError("transient"))  # first chunk attempt 1
    responses.append(_response("first chunk OK"))  # first chunk attempt 2 (retry)
    for i in range(expected_chunk_calls - 1):
        responses.append(_response(f"cont {i}"))

    create = AsyncMock(side_effect=responses)
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.format_transcript(raw)
    # Must not fall back to raw — retry succeeded.
    assert "first chunk OK" in result


async def test_long_input_chunk_fails_twice_returns_raw(monkeypatch):
    raw = _long_raw(20_000)

    responses = [
        _response("Title"),
        RuntimeError("fail 1"),
        RuntimeError("fail 2"),  # retry also fails → whole chunked flow aborts
    ]
    create = AsyncMock(side_effect=responses)
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.format_transcript(raw)
    assert result == raw


async def test_long_input_title_failure_falls_back_to_filename(monkeypatch):
    raw = _long_raw(20_000)
    chunks = formatter.split_long_text(
        raw,
        max_chars=formatter.FORMATTER_CHUNK_CHARS,
        overlap_chars=0,
        prefer_boundaries=formatter.SENTENCE_BOUNDARIES,
    )
    responses: list = [RuntimeError("title api down")]
    responses.append(_response("first body"))
    for i in range(len(chunks) - 1):
        responses.append(_response(f"cont {i}"))
    create = AsyncMock(side_effect=responses)
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.format_transcript(raw, filename_hint="My Podcast.mp3")
    assert result.startswith("My Podcast.mp3\n\n")


async def test_long_input_title_truncation_ok(monkeypatch):
    # Title call returning finish_reason=length is not treated as error by
    # _generate_title (it ignores finish_reason — short output window).
    raw = _long_raw(20_000)
    chunks = formatter.split_long_text(
        raw,
        max_chars=formatter.FORMATTER_CHUNK_CHARS,
        overlap_chars=0,
        prefer_boundaries=formatter.SENTENCE_BOUNDARIES,
    )
    responses: list = [_response("Clipped Title", finish_reason="length")]
    responses.append(_response("first body"))
    for i in range(len(chunks) - 1):
        responses.append(_response(f"cont {i}"))
    create = AsyncMock(side_effect=responses)
    monkeypatch.setattr(formatter.client.chat.completions, "create", create)

    result = await formatter.format_transcript(raw)
    assert result.startswith("Clipped Title\n\n")


# ---------- speaker-label extraction ----------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Иван: привет.\n\nМария: пока.", ["Иван", "Мария"]),
        ("Спикер 1: a\nСпикер 2: b\nСпикер 1: c", ["Спикер 1", "Спикер 2"]),
        ("No speakers here, just prose.", []),
        ("http://example.com: not a label", []),
        ("Alex: one\nBob: two\nAlex: three", ["Alex", "Bob"]),
    ],
)
def test_extract_speaker_labels(text, expected):
    assert formatter._extract_speaker_labels(text) == expected
