"""Rebuilding a stored transcript body from its persisted segments."""
import pytest

from bot.services import transcript_rebuild
from bot.services.formatter import TimecodeSegment
from bot.services.transcript_store import TranscriptStore


@pytest.fixture
def store(tmp_path):
    return TranscriptStore(
        db_path=str(tmp_path / "transcripts.db"),
        files_dir=str(tmp_path / "transcripts"),
    )


@pytest.fixture
def identity_split(monkeypatch):
    calls: list[str] = []

    async def _split(text, on_progress=None):
        calls.append(text)
        return text.replace(" Три.", "\n\nТри.")

    monkeypatch.setattr(transcript_rebuild, "split_into_paragraphs", _split)
    return calls


@pytest.mark.asyncio
async def test_rebuild_mono_body_from_segments_and_updates_store(store, identity_split):
    long_sentence = "Раз два три четыре пять шесть семь восемь девять десять. " * 8
    segments = [TimecodeSegment(i * 1000, long_sentence.strip()) for i in range(6)]
    segments.append(TimecodeSegment(9000, "Три."))
    record = await store.save(
        111,
        title="Лекция",
        source_type="webapp",
        duration_sec=100.0,
        body="Лекция\n\nВыдуманный пересказ вместо текста.",
        segments=segments,
    )

    body = await transcript_rebuild.rebuild_transcript_body(store, record)

    expected_text = " ".join(s.text for s in segments).replace(" Три.", "\n\nТри.")
    assert body == f"Лекция\n\n{expected_text}"
    assert identity_split == [" ".join(s.text for s in segments)]
    assert await store.read_text(record) == body
    updated = await store.get(record.id, 111)
    assert updated.char_count == len(body)


@pytest.mark.asyncio
async def test_rebuild_short_mono_body_skips_paragraph_split(store, identity_split):
    record = await store.save(
        111,
        title="Заметка",
        source_type="voice",
        duration_sec=5.0,
        body="мусор",
        segments=[TimecodeSegment(0, "Раз."), TimecodeSegment(500, "Два.")],
    )

    body = await transcript_rebuild.rebuild_transcript_body(store, record)

    assert body == "Заметка\n\nРаз. Два."
    assert identity_split == []


@pytest.mark.asyncio
async def test_rebuild_multi_speaker_body_keeps_header_with_channel(store, identity_split):
    record = await store.save(
        111,
        title="Интервью",
        source_type="youtube",
        duration_sec=30.0,
        body="мусор",
        segments=[
            TimecodeSegment(0, "Раз.", "Иван"),
            TimecodeSegment(1000, "Два.", "Иван"),
            TimecodeSegment(2000, "Три.", "Спикер 2"),
        ],
        channel="Канал",
    )

    body = await transcript_rebuild.rebuild_transcript_body(store, record)

    assert body == "Интервью\n📺 Канал: Канал\n\nИван: Раз. Два.\n\nСпикер 2: Три."
    assert identity_split == []  # speaker blocks are never GPT-split


@pytest.mark.asyncio
async def test_rebuild_without_segments_raises(store):
    record = await store.save(
        111, title="Т", source_type="voice", duration_sec=1.0, body="т", segments=[]
    )
    with pytest.raises(ValueError):
        await transcript_rebuild.rebuild_transcript_body(store, record)


def test_is_low_density_flags_records_with_missing_text():
    def _rec(chars, seconds):
        from bot.services.transcript_store import TranscriptRecord

        return TranscriptRecord(
            id="x", user_id=1, title="", created_at="", source_type="webapp",
            duration_sec=seconds, char_count=chars, txt_path="", segments_path=None,
        )

    assert transcript_rebuild.is_low_density(_rec(17062, 2644.0))  # the broken lecture
    assert not transcript_rebuild.is_low_density(_rec(35329, 2648.0))
    assert not transcript_rebuild.is_low_density(_rec(0, 0.0))  # no duration → no verdict
