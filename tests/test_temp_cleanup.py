import asyncio
import os

import pytest

import bot.services.temp_cleanup as temp_cleanup_module
from bot.services.temp_cleanup import (
    TEMP_CLEANUP_INTERVAL_SECONDS,
    cleanup_old_temp_files,
    run_periodic_temp_cleanup,
)


@pytest.mark.asyncio
async def test_cleanup_old_temp_files_removes_only_stale_files(tmp_path):
    now = 1_000_000
    max_age = 100
    stale = tmp_path / "stale.mp3"
    fresh = tmp_path / "fresh.mp3"
    old_dir = tmp_path / "old_dir"

    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")
    old_dir.mkdir()
    os.utime(stale, (now - max_age - 1, now - max_age - 1))
    os.utime(fresh, (now - max_age + 1, now - max_age + 1))
    os.utime(old_dir, (now - max_age - 1, now - max_age - 1))

    removed = await cleanup_old_temp_files(
        str(tmp_path),
        max_age_seconds=max_age,
        now=now,
    )

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
    assert old_dir.exists()


@pytest.mark.asyncio
async def test_cleanup_old_temp_files_missing_dir_is_noop(tmp_path):
    missing = tmp_path / "missing"

    removed = await cleanup_old_temp_files(str(missing))

    assert removed == 0


@pytest.mark.asyncio
async def test_periodic_cleanup_runs_before_sleep(monkeypatch):
    calls = []

    async def fake_cleanup(temp_dir, max_age_seconds, logger):
        calls.append(("cleanup", temp_dir, max_age_seconds, logger))
        return 0

    async def fake_sleep(interval):
        calls.append(("sleep", interval))
        raise asyncio.CancelledError

    monkeypatch.setattr(
        temp_cleanup_module,
        "cleanup_old_temp_files",
        fake_cleanup,
    )
    monkeypatch.setattr(temp_cleanup_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_periodic_temp_cleanup("/tmp/transcriber")

    assert calls == [
        (
            "cleanup",
            "/tmp/transcriber",
            temp_cleanup_module.TEMP_FILE_MAX_AGE_SECONDS,
            None,
        ),
        ("sleep", TEMP_CLEANUP_INTERVAL_SECONDS),
    ]
