"""Tests for bot.services.transcriber — AssemblyAI integration.

We mock AssemblyAI's Transcriber.transcribe to a synthetic transcript with
known utterances so we can verify config, label mapping, custom_spelling and
title generation.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.services.transcriber as transcriber_module


def _fake_transcript(utterances, *, text=None, language="ru", status="completed", error=None):
    return SimpleNamespace(
        utterances=utterances,
        text=text if text is not None else " ".join(u.text for u in utterances),
        language_code=language,
        status=status,
        error=error,
    )


def _utt(speaker, text, start=0, end=0):
    return SimpleNamespace(speaker=speaker, text=text, start=start, end=end)


@pytest.fixture(autouse=True)
def _silence_title(monkeypatch):
    async def _no_title(*args, **kwargs):
        return "Test Title"
    monkeypatch.setattr(transcriber_module, "generate_title", _no_title)


@pytest.fixture
def fake_transcribe(monkeypatch):
    """Patch the SDK call. Returns a list to which the test appends a result."""
    calls: list[dict] = []
    holder: dict = {}

    class FakeTranscriber:
        def __init__(self, config):
            calls.append({"config": config})

        def transcribe(self, audio_path):
            calls[-1]["audio_path"] = audio_path
            return holder["result"]

    monkeypatch.setattr(transcriber_module.aai, "Transcriber", FakeTranscriber)
    # Status enum surrogate
    monkeypatch.setattr(
        transcriber_module.aai,
        "TranscriptStatus",
        SimpleNamespace(error="error", completed="completed"),
    )
    return calls, holder


@pytest.mark.asyncio
async def test_transcribe_two_speakers_renders_with_labels(tmp_path, fake_transcribe):
    calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([
        _utt("A", "Привет."),
        _utt("B", "Здравствуй."),
        _utt("A", "Как дела?"),
    ])

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert result.speaker_count == 2
    assert "Спикер 1: Привет." in result.body
    assert "Спикер 2: Здравствуй." in result.body
    assert "Спикер 1: Как дела?" in result.body
    assert result.title == "Test Title"
    assert result.body.startswith("Test Title\n\n")
    assert result.raw_text == "Привет. Здравствуй. Как дела?"


@pytest.mark.asyncio
async def test_transcribe_single_speaker_no_prefix(tmp_path, fake_transcribe):
    _calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([
        _utt("A", "Первый абзац."),
        _utt("A", "Второй абзац."),
    ])

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert result.speaker_count == 1
    assert "Спикер" not in result.body
    assert "Первый абзац." in result.body
    assert "Второй абзац." in result.body


@pytest.mark.asyncio
async def test_transcribe_passes_speaker_labels_and_word_boost(tmp_path, fake_transcribe, monkeypatch):
    calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([_utt("A", "ok")])

    # Inject deterministic word_boost so the assertion is meaningful.
    monkeypatch.setattr(transcriber_module, "_WORD_BOOST", ["aiogram", "yt-dlp"])

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    config = calls[0]["config"]
    assert config.speaker_labels is True
    assert config.punctuate is True
    assert config.format_text is True
    assert config.disfluencies is False
    assert config.word_boost == ["aiogram", "yt-dlp"]
    assert str(config.boost_param).endswith("high")


@pytest.mark.asyncio
async def test_transcribe_force_language_skips_autodetect(tmp_path, fake_transcribe, monkeypatch):
    calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([_utt("A", "ok")])

    monkeypatch.setattr(transcriber_module.settings, "FORCE_LANGUAGE_CODE", "ru")

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    config = calls[0]["config"]
    assert config.language_code == "ru"
    # language_detection should NOT be set when we pin the language.
    assert config.language_detection in (False, None)


@pytest.mark.asyncio
async def test_transcribe_autodetect_when_no_force(tmp_path, fake_transcribe, monkeypatch):
    calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([_utt("A", "ok")])

    monkeypatch.setattr(transcriber_module.settings, "FORCE_LANGUAGE_CODE", None)

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    config = calls[0]["config"]
    assert config.language_detection is True


@pytest.mark.asyncio
async def test_transcribe_applies_custom_spelling(tmp_path, fake_transcribe, monkeypatch):
    _calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([_utt("A", "Это ассемблиай.")])
    monkeypatch.setattr(
        transcriber_module, "_CUSTOM_SPELLING", {"ассемблиай": "AssemblyAI"}
    )

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert "AssemblyAI" in result.body
    assert "AssemblyAI" in result.raw_text
    assert "ассемблиай" not in result.body


@pytest.mark.asyncio
async def test_transcribe_raises_on_assemblyai_error(tmp_path, fake_transcribe):
    _calls, holder = fake_transcribe
    holder["result"] = _fake_transcript(
        [], text="", status="error", error="audio too short"
    )

    with pytest.raises(RuntimeError, match="AssemblyAI error: audio too short"):
        await transcriber_module.transcribe(str(tmp_path / "a.mp3"))


@pytest.mark.asyncio
async def test_transcribe_title_falls_back_to_filename_on_error(tmp_path, fake_transcribe, monkeypatch):
    _calls, holder = fake_transcribe
    holder["result"] = _fake_transcript([_utt("A", "ok")])

    async def _boom(*a, **k):
        raise RuntimeError("title api down")
    monkeypatch.setattr(transcriber_module, "generate_title", _boom)

    result = await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"), filename_hint="meeting.mp3"
    )

    assert result.title == "meeting.mp3"
    assert result.body.startswith("meeting.mp3\n\n")
