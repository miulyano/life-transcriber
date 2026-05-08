import json

import pytest

from bot.handlers.commands import START_TEXT, _build_limit_text
from bot.services.usage_store import UsageStore


@pytest.fixture
def tmp_paths(tmp_path):
    return {
        "usage": str(tmp_path / "usage.json"),
        "limits": str(tmp_path / "limits.json"),
    }


def test_start_text_contains_commands_block():
    assert "/start" in START_TEXT
    assert "/limit" in START_TEXT
    assert "Доступные команды:" in START_TEXT


@pytest.mark.asyncio
async def test_limit_text_no_limit(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    text = await _build_limit_text(123, store)
    assert text == "Лимит на транскрибации не установлен."


@pytest.mark.asyncio
async def test_limit_text_with_room(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"123": 10}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(123, 3600 * 3 + 60 * 24)  # 3h 24m
    text = await _build_limit_text(123, store)
    assert "Текущий лимит — 10 часов" in text
    assert "Использовано — 3 часа 24 минуты" in text
    assert "Осталось — 6 часов 36 минут" in text
    assert "Лимит исчерпан" not in text


@pytest.mark.asyncio
async def test_limit_text_exhausted(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"123": 1}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(123, 7200.0)  # 2h, over limit
    text = await _build_limit_text(123, store)
    assert "Текущий лимит — 1 час" in text
    assert "Лимит исчерпан" in text


@pytest.mark.asyncio
async def test_limit_text_singular_pluralization(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"123": 1}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    text = await _build_limit_text(123, store)
    assert "Текущий лимит — 1 час." in text


@pytest.mark.asyncio
async def test_limit_text_few_pluralization(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"123": 2}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    text = await _build_limit_text(123, store)
    assert "Текущий лимит — 2 часа." in text
