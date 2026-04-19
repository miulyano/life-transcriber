from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services import summarizer


def _response(content: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


async def test_summarize_short_text_single_request(monkeypatch):
    create = AsyncMock(return_value=_response(" short summary "))
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    result = await summarizer.summarize(" source text ")

    assert result == "short summary"
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == summarizer.settings.GPT_MODEL
    assert kwargs["messages"][0]["content"] == summarizer.SYSTEM_PROMPT
    assert kwargs["messages"][1]["content"] == "source text"
    assert kwargs["max_tokens"] == 1024


async def test_cleanup_transcript_short_text_single_request(monkeypatch):
    create = AsyncMock(return_value=_response(" cleaned text "))
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    result = await summarizer.cleanup_transcript(" source text ")

    assert result == "cleaned text"
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["messages"][0]["content"] == summarizer.CLEANUP_SYSTEM_PROMPT
    assert kwargs["messages"][1]["content"] == "source text"
    assert kwargs["max_tokens"] == summarizer.CLEANUP_MAX_TOKENS


async def test_summarize_long_text_uses_chunk_notes_and_final_summary(monkeypatch):
    monkeypatch.setattr(
        summarizer,
        "_split_long_text",
        lambda text: ["chunk one text", "chunk two text"],
    )
    create = AsyncMock(
        side_effect=[
            _response("notes one"),
            _response("notes two"),
            _response("final summary"),
        ]
    )
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    result = await summarizer.summarize("long source text")

    assert result == "final summary"
    assert create.await_count == 3
    calls = create.await_args_list
    assert calls[0].kwargs["messages"][0]["content"] == summarizer.CHUNK_SYSTEM_PROMPT
    assert "Фрагмент транскрибации 1/2" in calls[0].kwargs["messages"][1]["content"]
    assert calls[1].kwargs["messages"][0]["content"] == summarizer.CHUNK_SYSTEM_PROMPT
    assert "Фрагмент транскрибации 2/2" in calls[1].kwargs["messages"][1]["content"]
    assert calls[2].kwargs["messages"][0]["content"] == summarizer.FINAL_SYSTEM_PROMPT
    assert "notes one" in calls[2].kwargs["messages"][1]["content"]
    assert "notes two" in calls[2].kwargs["messages"][1]["content"]


async def test_cleanup_transcript_long_text_processes_chunks_in_order(monkeypatch):
    monkeypatch.setattr(
        summarizer,
        "_split_cleanup_text",
        lambda text: ["chunk one", "chunk two"],
    )
    create = AsyncMock(
        side_effect=[
            _response("clean one"),
            _response("clean two"),
        ]
    )
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    result = await summarizer.cleanup_transcript("long source text")

    assert result == "clean one\n\nclean two"
    assert create.await_count == 2
    calls = create.await_args_list
    assert calls[0].kwargs["messages"][0]["content"] == summarizer.CLEANUP_SYSTEM_PROMPT
    assert calls[0].kwargs["messages"][1]["content"] == "chunk one"
    assert calls[1].kwargs["messages"][1]["content"] == "chunk two"


# ---------- progress reporting ----------


async def test_summarize_short_text_reports_progress(monkeypatch):
    create = AsyncMock(return_value=_response("short summary"))
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    reports: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        reports.append((done, total))

    await summarizer.summarize("source", on_progress=on_progress)
    assert reports == [(0, 1), (1, 1)]


async def test_summarize_long_text_reports_progress(monkeypatch):
    monkeypatch.setattr(
        summarizer, "_split_long_text", lambda text: ["a", "b", "c"]
    )
    create = AsyncMock(
        side_effect=[
            _response("note a"),
            _response("note b"),
            _response("note c"),
            _response("final"),
        ]
    )
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    reports: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        reports.append((done, total))

    await summarizer.summarize("long source", on_progress=on_progress)
    # total = 3 chunks + 1 final step = 4
    assert reports[0] == (0, 4)
    assert reports[-1] == (4, 4)
    # Each chunk completion gets reported.
    assert (1, 4) in reports
    assert (2, 4) in reports
    assert (3, 4) in reports


async def test_cleanup_transcript_reports_progress(monkeypatch):
    monkeypatch.setattr(
        summarizer, "_split_cleanup_text", lambda text: ["x", "y", "z"]
    )
    create = AsyncMock(
        side_effect=[_response("X"), _response("Y"), _response("Z")]
    )
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    reports: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        reports.append((done, total))

    await summarizer.cleanup_transcript("long source", on_progress=on_progress)
    assert reports == [(0, 3), (1, 3), (2, 3), (3, 3)]


# ---------- finish_reason retry on cleanup truncation ----------


async def test_cleanup_transcript_retries_on_truncation_then_succeeds(monkeypatch):
    monkeypatch.setattr(summarizer, "_split_cleanup_text", lambda text: ["chunk"])
    create = AsyncMock(
        side_effect=[
            _response("partial output", finish_reason="length"),
            _response("full output"),
        ]
    )
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    result = await summarizer.cleanup_transcript("source")
    assert result == "full output"
    assert create.await_count == 2


async def test_cleanup_transcript_raises_when_retry_also_truncates(monkeypatch):
    monkeypatch.setattr(summarizer, "_split_cleanup_text", lambda text: ["chunk"])
    create = AsyncMock(
        side_effect=[
            _response("partial 1", finish_reason="length"),
            _response("partial 2", finish_reason="length"),
        ]
    )
    monkeypatch.setattr(summarizer.client.chat.completions, "create", create)

    with pytest.raises(RuntimeError):
        await summarizer.cleanup_transcript("source")


# ---------- _split_cleanup_text uses sentence boundaries ----------


def test_split_cleanup_text_uses_sentence_boundaries_for_huge_paragraph():
    """Wall-of-text paragraphs (typical raw Whisper output) must split on
    sentence boundaries, not in the middle of a word."""
    sentence = (
        "Это длинное русское предложение для проверки разбиения по границам "
        "предложений вместо разбиения по словам или произвольным символам. "
    )
    huge_paragraph = sentence * 200  # one giant paragraph, no \n\n
    chunks = summarizer._split_cleanup_text(
        huge_paragraph, max_chars=summarizer.CLEANUP_CHUNK_MAX_CHARS
    )
    assert len(chunks) >= 2
    # Every chunk should end at a sentence boundary (one of SENTENCE_BOUNDARIES
    # endings) or be the very last chunk.
    for chunk in chunks[:-1]:
        assert chunk.rstrip().endswith((".", "!", "?", "…"))


