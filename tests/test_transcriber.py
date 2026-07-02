"""Tests for bot.services.transcriber — AssemblyAI integration.

We mock:
- aai.Transcriber.submit → returns a fake transcript object with _impl.transcript_id
- assemblyai.api.get_transcript → returns status progression (queued → completed)
- analyze_transcript → returns ("Test Title", {})
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import bot.services.transcriber as transcriber_module


def _fake_raw(utterances, *, text=None, language="ru", status="completed", error=None):
    return SimpleNamespace(
        utterances=utterances,
        text=text if text is not None else " ".join(u.text for u in utterances),
        language_code=language,
        status=status,
        error=error,
    )


def _utt(speaker, text, start=0, end=0, words=None):
    return SimpleNamespace(speaker=speaker, text=text, start=start, end=end, words=words)


def _make_status(status_str: str, utterances, *, text=None, language="ru", error=None):
    """Build a fake status object matching what _aai_api.get_transcript returns."""
    status_obj = getattr(transcriber_module.aai.TranscriptStatus, status_str)
    return SimpleNamespace(
        status=status_obj,
        utterances=utterances,
        text=text if text is not None else " ".join(u.text for u in utterances),
        language_code=language,
        error=error,
    )


@pytest.fixture(autouse=True)
def _silence_analyze(monkeypatch):
    async def _no_analyze(*args, **kwargs):
        return "Test Title", {}
    monkeypatch.setattr(transcriber_module, "analyze_transcript", _no_analyze)


@pytest.fixture(autouse=True)
def _silence_split(monkeypatch):
    async def _no_split(text: str) -> str:
        return text
    monkeypatch.setattr(transcriber_module, "split_into_paragraphs", _no_split)


@pytest.fixture
def fake_polling(monkeypatch):
    """Patch submit + get_transcript for polling-based transcription.

    Returns (submit_calls, status_sequence_holder).
    status_sequence_holder["statuses"] = list of SimpleNamespace returned in order.
    """
    submit_calls: list[dict] = []
    holder: dict = {}

    class FakeImpl:
        transcript_id = "fake-id-123"

    class FakeHttpClient:
        pass

    class FakeTranscriptObj:
        _impl = FakeImpl()
        _client = SimpleNamespace(http_client=FakeHttpClient())

    class FakeTranscriber:
        def __init__(self, config):
            submit_calls.append({"config": config})

        def submit(self, audio_path):
            submit_calls[-1]["audio_path"] = audio_path
            return FakeTranscriptObj()

    monkeypatch.setattr(transcriber_module.aai, "Transcriber", FakeTranscriber)
    monkeypatch.setattr(
        transcriber_module.aai,
        "TranscriptStatus",
        SimpleNamespace(
            error="error",
            completed="completed",
            queued="queued",
            processing="processing",
        ),
    )

    call_count = [0]

    def _get_transcript(http_client, transcript_id):
        statuses = holder.get("statuses", [])
        idx = min(call_count[0], len(statuses) - 1)
        call_count[0] += 1
        return statuses[idx]

    monkeypatch.setattr(transcriber_module._aai_api, "get_transcript", _get_transcript)

    return submit_calls, holder


@pytest.mark.asyncio
async def test_transcribe_two_speakers_renders_with_labels(tmp_path, fake_polling):
    submit_calls, holder = fake_polling
    utterances = [
        _utt("A", "Привет."),
        _utt("B", "Здравствуй."),
        _utt("A", "Как дела?"),
    ]
    holder["statuses"] = [_make_status("completed", utterances)]

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert result.speaker_count == 2
    assert "Спикер 1: Привет." in result.body
    assert "Спикер 2: Здравствуй." in result.body
    assert "Спикер 1: Как дела?" in result.body
    assert result.title == "Test Title"
    assert result.body.startswith("Test Title\n\n")
    assert result.raw_text == "Привет. Здравствуй. Как дела?"


@pytest.mark.asyncio
async def test_transcribe_single_speaker_no_prefix(tmp_path, fake_polling):
    _submit_calls, holder = fake_polling
    utterances = [_utt("A", "Первый абзац."), _utt("A", "Второй абзац.")]
    holder["statuses"] = [_make_status("completed", utterances)]

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert result.speaker_count == 1
    assert "Спикер" not in result.body
    assert "Первый абзац." in result.body
    assert "Второй абзац." in result.body


@pytest.mark.asyncio
async def test_transcribe_passes_speaker_labels_and_word_boost(tmp_path, fake_polling, monkeypatch):
    submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    monkeypatch.setattr(transcriber_module, "_WORD_BOOST", ["aiogram", "yt-dlp"])

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    config = submit_calls[0]["config"]
    assert config.speaker_labels is True
    assert config.punctuate is True
    assert config.format_text is True
    assert config.disfluencies is False
    assert config.word_boost == ["aiogram", "yt-dlp"]
    assert str(config.boost_param).endswith("high")


@pytest.mark.asyncio
async def test_transcribe_force_language_skips_autodetect(tmp_path, fake_polling, monkeypatch):
    submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    monkeypatch.setattr(transcriber_module.settings, "FORCE_LANGUAGE_CODE", "ru")

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    config = submit_calls[0]["config"]
    assert config.language_code == "ru"
    assert config.language_detection in (False, None)


@pytest.mark.asyncio
async def test_transcribe_autodetect_when_no_force(tmp_path, fake_polling, monkeypatch):
    submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    monkeypatch.setattr(transcriber_module.settings, "FORCE_LANGUAGE_CODE", None)

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    config = submit_calls[0]["config"]
    assert config.language_detection is True


@pytest.mark.asyncio
async def test_transcribe_applies_custom_spelling(tmp_path, fake_polling, monkeypatch):
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "Это ассемблиай.")])]
    monkeypatch.setattr(
        transcriber_module, "_CUSTOM_SPELLING", {"ассемблиай": "AssemblyAI"}
    )

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert "AssemblyAI" in result.body
    assert "AssemblyAI" in result.raw_text
    assert "ассемблиай" not in result.body


@pytest.mark.asyncio
async def test_transcribe_raises_on_assemblyai_error(tmp_path, fake_polling):
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("error", [], text="", error="audio too short")]

    with pytest.raises(RuntimeError, match="AssemblyAI error: audio too short"):
        await transcriber_module.transcribe(str(tmp_path / "a.mp3"))


@pytest.mark.asyncio
async def test_transcribe_prefers_original_source_title_over_gpt(tmp_path, fake_polling):
    """If source_meta.title is meaningful, use it verbatim instead of GPT-generated title."""
    from bot.services.source_meta import SourceMetadata
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    result = await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"),
        source_meta=SourceMetadata(
            title="Подкаст про AI: как обучать модели",
            uploader="ШАД",
        ),
    )

    assert result.title == "Подкаст про AI: как обучать модели"
    assert result.uploader == "ШАД"
    # Body header: title + "📺 Канал: ..." + blank line + body
    assert result.body.startswith(
        "Подкаст про AI: как обучать модели\n📺 Канал: ШАД\n\n"
    )


@pytest.mark.asyncio
async def test_transcribe_falls_back_to_gpt_on_hash_title(tmp_path, fake_polling, monkeypatch):
    """If source_meta.title looks like an id/hash (e.g. dQw4w9WgXcQ), call GPT for a real title."""
    from bot.services.source_meta import SourceMetadata
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    async def _gpt(raw, utterances, hint):
        return "Generated Title", {}
    monkeypatch.setattr(transcriber_module, "analyze_transcript", _gpt)

    result = await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"),
        source_meta=SourceMetadata(title="dQw4w9WgXcQ"),
    )

    assert result.title == "Generated Title"
    assert result.body.startswith("Generated Title\n\n")


@pytest.mark.asyncio
async def test_transcribe_uploader_omitted_when_absent(tmp_path, fake_polling):
    """Without uploader the body has no 'Канал:' line."""
    from bot.services.source_meta import SourceMetadata
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    result = await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"),
        source_meta=SourceMetadata(title="Просто заголовок"),
    )

    assert result.title == "Просто заголовок"
    assert "Канал:" not in result.body
    assert result.uploader is None


def test_is_hash_like_title():
    f = transcriber_module._is_hash_like_title
    # Hash-like: mixed alnum, no spaces, ≥ 8 chars
    assert f("dQw4w9WgXcQ") is True
    assert f("abc12345") is True
    # Not hash-like: spaces, plain words, non-ASCII letters, too short
    assert f("Встреча команды") is False
    assert f("meeting") is False  # too short
    assert f("meetings") is False  # letters only, no digits
    assert f("Episode 42") is False  # contains space
    assert f("Подкаст") is False  # cyrillic
    assert f("") is False


@pytest.mark.asyncio
async def test_transcribe_title_falls_back_to_filename_on_error(tmp_path, fake_polling, monkeypatch):
    from bot.services.source_meta import SourceMetadata
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    async def _boom(*a, **k):
        raise RuntimeError("title api down")
    monkeypatch.setattr(transcriber_module, "analyze_transcript", _boom)

    result = await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"),
        source_meta=SourceMetadata(title="meeting.mp3"),
    )

    assert result.title == "meeting.mp3"
    assert result.body.startswith("meeting.mp3\n\n")


@pytest.mark.asyncio
async def test_transcribe_emits_format_phase_via_on_phase(tmp_path, fake_polling):
    """on_phase("Форматирую…") must be called before analyze_transcript."""
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    phases: list[str] = []

    async def record_phase(label: str) -> None:
        phases.append(label)

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"), on_phase=record_phase)

    assert "Форматирую…" in phases


@pytest.mark.asyncio
async def test_transcribe_progress_fraction_called(tmp_path, fake_polling):
    """on_progress_fraction should be called during polling and reach 1.0 at end."""
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "ok")])]

    fractions: list[float] = []

    async def record_fraction(f: float) -> None:
        fractions.append(f)

    await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"), on_progress_fraction=record_fraction
    )

    assert fractions[-1] == 1.0


@pytest.mark.asyncio
async def test_transcribe_single_speaker_calls_split_when_no_paragraphs(tmp_path, fake_polling, monkeypatch):
    """split_into_paragraphs is called for a single long utterance; skipped when \n\n present."""
    _submit_calls, holder = fake_polling
    long_text = "Слово " * 60  # 360 chars, no \n\n
    holder["statuses"] = [_make_status("completed", [_utt("A", long_text.strip())])]

    split_calls: list[str] = []

    async def _track_split(text: str) -> str:
        split_calls.append(text)
        return text

    monkeypatch.setattr(transcriber_module, "split_into_paragraphs", _track_split)

    await transcriber_module.transcribe(str(tmp_path / "a.mp3"))
    assert len(split_calls) == 1

    # Multiple utterances → already has \n\n → split not called
    split_calls.clear()
    holder["statuses"] = [_make_status("completed", [_utt("A", "Первый."), _utt("A", "Второй.")])]
    monkeypatch.setattr(transcriber_module, "split_into_paragraphs", _track_split)

    await transcriber_module.transcribe(str(tmp_path / "b.mp3"))
    assert len(split_calls) == 0


@pytest.mark.asyncio
async def test_transcribe_builds_timecoded_body(tmp_path, fake_polling):
    """body_timecoded carries [m:ss] stamps; plain body stays clean."""
    _submit_calls, holder = fake_polling
    utterances = [
        _utt("A", "Привет.", start=0, end=1000),
        _utt("B", "Здравствуй.", start=3000, end=4000),
    ]
    holder["statuses"] = [_make_status("completed", utterances)]

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert result.body_timecoded.startswith("Test Title\n\n")
    assert "Спикер 1\n[0:00] Привет." in result.body_timecoded
    assert "Спикер 2\n[0:03] Здравствуй." in result.body_timecoded
    assert "[0:00]" not in result.body


@pytest.mark.asyncio
async def test_transcribe_timecoded_falls_back_to_body_without_utterances(tmp_path, fake_polling):
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [], text="сплошной текст")]

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert result.body_timecoded == result.body


@pytest.mark.asyncio
async def test_transcribe_applies_custom_spelling_to_timecoded(tmp_path, fake_polling, monkeypatch):
    _submit_calls, holder = fake_polling
    holder["statuses"] = [_make_status("completed", [_utt("A", "Это ассемблиай.")])]
    monkeypatch.setattr(
        transcriber_module, "_CUSTOM_SPELLING", {"ассемблиай": "AssemblyAI"}
    )

    result = await transcriber_module.transcribe(str(tmp_path / "a.mp3"))

    assert "AssemblyAI" in result.body_timecoded
    assert "ассемблиай" not in result.body_timecoded


def test_utterances_from_response_captures_words():
    words = [
        SimpleNamespace(text="Привет,", start=100, end=500),
        SimpleNamespace(text="мир.", start=600, end=900),
    ]
    raw = _fake_raw(
        [SimpleNamespace(speaker="A", text="Привет, мир.", start=100, end=900, words=words)]
    )
    out = transcriber_module._utterances_from_response(raw)
    assert out[0].words == [
        transcriber_module.Word(text="Привет,", start_ms=100, end_ms=500),
        transcriber_module.Word(text="мир.", start_ms=600, end_ms=900),
    ]


def test_utterances_from_response_tolerates_missing_words():
    raw = _fake_raw([_utt("A", "ok")])
    out = transcriber_module._utterances_from_response(raw)
    assert out[0].words == []


@pytest.mark.asyncio
async def test_transcribe_polls_through_queued_and_processing(tmp_path, fake_polling):
    """Polling loop should iterate through queued → processing → completed."""
    _submit_calls, holder = fake_polling
    utterances = [_utt("A", "ok")]
    holder["statuses"] = [
        _make_status("queued", utterances),
        _make_status("processing", utterances),
        _make_status("completed", utterances),
    ]

    fractions: list[float] = []

    async def record_fraction(f: float) -> None:
        fractions.append(f)

    result = await transcriber_module.transcribe(
        str(tmp_path / "a.mp3"), on_progress_fraction=record_fraction
    )

    assert result.raw_text == "ok"
    # queued → 0.05, processing → some value in (0.05, 0.90], completed → 1.0
    assert fractions[0] == pytest.approx(0.05)
    assert fractions[-1] == pytest.approx(1.0)
