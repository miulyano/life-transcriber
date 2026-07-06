import json
import os

import pytest

from bot.services.formatter import TimecodeSegment
from bot.services.transcript_store import TranscriptStore


@pytest.fixture
def store(tmp_path):
    return TranscriptStore(
        db_path=str(tmp_path / "transcripts.db"),
        files_dir=str(tmp_path / "transcripts"),
    )


def _segments():
    return [
        TimecodeSegment(start_ms=0, text="Привет.", speaker="Иван"),
        TimecodeSegment(start_ms=1500, text="Привет!", speaker="Спикер 2"),
    ]


@pytest.mark.asyncio
async def test_save_writes_files_and_row(store):
    record = await store.save(
        111,
        title="Тест",
        source_type="voice",
        duration_sec=42.5,
        body="Тест\n\nПривет.",
        segments=_segments(),
    )

    assert os.path.exists(record.txt_path)
    assert os.path.exists(record.segments_path)
    with open(record.txt_path, encoding="utf-8") as f:
        assert f.read() == "Тест\n\nПривет."
    with open(record.segments_path, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["version"] == 1
    assert payload["segments"][0] == {"start_ms": 0, "speaker": "Иван", "text": "Привет."}

    loaded = await store.get(record.id, 111)
    assert loaded == record
    assert loaded.char_count == len("Тест\n\nПривет.")
    assert loaded.duration_sec == 42.5
    assert loaded.source_type == "voice"


@pytest.mark.asyncio
async def test_save_empty_segments_no_json(store):
    record = await store.save(
        111, title="", source_type="link", duration_sec=0, body="text", segments=[]
    )
    assert record.segments_path is None
    assert await store.read_segments(record) == []


@pytest.mark.asyncio
async def test_read_text_and_segments_roundtrip(store):
    record = await store.save(
        111,
        title="T",
        source_type="link",
        duration_sec=1,
        body="T\n\nbody",
        segments=_segments(),
    )
    assert await store.read_text(record) == "T\n\nbody"
    assert await store.read_segments(record) == _segments()


@pytest.mark.asyncio
async def test_list_newest_first_and_user_isolated(store):
    first = await store.save(
        111, title="a", source_type="voice", duration_sec=1, body="a", segments=[]
    )
    second = await store.save(
        111, title="b", source_type="voice", duration_sec=1, body="b", segments=[]
    )
    await store.save(
        222, title="foreign", source_type="voice", duration_sec=1, body="x", segments=[]
    )

    records = await store.list_for_user(111)
    assert [r.title for r in records] == ["b", "a"]
    assert {r.id for r in records} == {first.id, second.id}


@pytest.mark.asyncio
async def test_get_foreign_user_returns_none(store):
    record = await store.save(
        111, title="a", source_type="voice", duration_sec=1, body="a", segments=[]
    )
    assert await store.get(record.id, 222) is None


@pytest.mark.asyncio
async def test_delete_removes_row_and_files(store):
    record = await store.save(
        111, title="a", source_type="voice", duration_sec=1, body="a", segments=_segments()
    )
    assert await store.delete(record.id, 111) is True
    assert await store.get(record.id, 111) is None
    assert not os.path.exists(record.txt_path)
    assert not os.path.exists(record.segments_path)


@pytest.mark.asyncio
async def test_delete_foreign_user_refused(store):
    record = await store.save(
        111, title="a", source_type="voice", duration_sec=1, body="a", segments=[]
    )
    assert await store.delete(record.id, 222) is False
    assert await store.get(record.id, 111) is not None
    assert os.path.exists(record.txt_path)


@pytest.mark.asyncio
async def test_delete_survives_missing_file(store):
    record = await store.save(
        111, title="a", source_type="voice", duration_sec=1, body="a", segments=[]
    )
    os.unlink(record.txt_path)
    assert await store.delete(record.id, 111) is True
    assert await store.get(record.id, 111) is None


@pytest.mark.asyncio
async def test_delete_missing_id_returns_false(store):
    assert await store.delete("nope", 111) is False
