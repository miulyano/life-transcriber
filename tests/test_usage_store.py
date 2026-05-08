import asyncio
import json
import os

import pytest

from bot.services.usage_store import (
    LimitExceededError,
    UsageStore,
    current_period,
    load_limits,
)


@pytest.fixture
def tmp_paths(tmp_path):
    return {
        "usage": str(tmp_path / "usage.json"),
        "limits": str(tmp_path / "limits.json"),
    }


def test_current_period_format():
    p = current_period()
    assert len(p) == 7
    assert p[4] == "-"
    int(p[:4])
    int(p[5:])


def test_load_limits_missing_file(tmp_paths):
    assert load_limits(tmp_paths["limits"]) == {}


def test_load_limits_valid(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 10, "222": 5}, f)
    assert load_limits(tmp_paths["limits"]) == {111: 10, 222: 5}


def test_load_limits_invalid_json(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        f.write("{not valid json")
    assert load_limits(tmp_paths["limits"]) == {}


def test_load_limits_skips_bad_entries(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 10, "abc": 5, "222": "bad"}, f)
    assert load_limits(tmp_paths["limits"]) == {111: 10}


@pytest.mark.asyncio
async def test_add_and_get_seconds(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    assert await store.get_used_seconds(111) == 0.0
    await store.add_seconds(111, 100.0)
    assert await store.get_used_seconds(111) == 100.0
    await store.add_seconds(111, 50.0)
    assert await store.get_used_seconds(111) == 150.0


@pytest.mark.asyncio
async def test_add_seconds_zero_or_negative_no_op(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 0)
    await store.add_seconds(111, -10)
    assert await store.get_used_seconds(111) == 0.0
    assert not os.path.exists(tmp_paths["usage"])


@pytest.mark.asyncio
async def test_different_periods_kept_separate(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 100.0, period="2026-04")
    await store.add_seconds(111, 50.0, period="2026-05")
    assert await store.get_used_seconds(111, period="2026-04") == 100.0
    assert await store.get_used_seconds(111, period="2026-05") == 50.0


@pytest.mark.asyncio
async def test_corrupted_usage_file_starts_fresh(tmp_paths):
    with open(tmp_paths["usage"], "w") as f:
        f.write("not json")
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    assert await store.get_used_seconds(111) == 0.0
    await store.add_seconds(111, 42.0)
    assert await store.get_used_seconds(111) == 42.0


@pytest.mark.asyncio
async def test_concurrent_add_seconds_no_loss(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await asyncio.gather(*[store.add_seconds(111, 1.0) for _ in range(50)])
    assert await store.get_used_seconds(111) == 50.0


@pytest.mark.asyncio
async def test_assert_within_limit_no_limit_no_op(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.assert_within_limit(999)


@pytest.mark.asyncio
async def test_assert_within_limit_with_room(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 10}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 3600.0)
    await store.assert_within_limit(111)


@pytest.mark.asyncio
async def test_assert_within_limit_exhausted(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 2}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 7200.0)
    with pytest.raises(LimitExceededError) as exc_info:
        await store.assert_within_limit(111)
    assert exc_info.value.limit_hours == 2


@pytest.mark.asyncio
async def test_assert_within_limit_overdraft(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 1}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 7200.0)
    with pytest.raises(LimitExceededError):
        await store.assert_within_limit(111)


@pytest.mark.asyncio
async def test_get_status_no_limit(tmp_paths):
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    status = await store.get_status(999)
    assert not status.has_limit
    assert status.limit_hours is None


@pytest.mark.asyncio
async def test_get_status_with_limit(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 10}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 3600.0)
    status = await store.get_status(111)
    assert status.has_limit
    assert status.limit_hours == 10
    assert status.used_seconds == 3600.0
    assert status.remaining_seconds == 9 * 3600
    assert not status.is_exhausted


@pytest.mark.asyncio
async def test_get_status_exhausted(tmp_paths):
    with open(tmp_paths["limits"], "w") as f:
        json.dump({"111": 1}, f)
    store = UsageStore(path=tmp_paths["usage"], limits_path=tmp_paths["limits"])
    await store.add_seconds(111, 7200.0)
    status = await store.get_status(111)
    assert status.is_exhausted
    assert status.remaining_seconds == 0
