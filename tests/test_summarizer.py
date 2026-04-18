from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.services import summarizer


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
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


