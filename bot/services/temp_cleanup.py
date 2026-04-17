from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path


TEMP_FILE_MAX_AGE_SECONDS = 6 * 3600
TEMP_CLEANUP_INTERVAL_SECONDS = 3600


async def cleanup_old_temp_files(
    temp_dir: str,
    max_age_seconds: int = TEMP_FILE_MAX_AGE_SECONDS,
    now: float | None = None,
    logger: logging.Logger | None = None,
) -> int:
    """Remove stale files from temp_dir and return the number of deleted files."""
    cutoff = (time.time() if now is None else now) - max_age_seconds
    path = Path(temp_dir)
    removed = 0

    try:
        entries = list(path.iterdir())
    except FileNotFoundError:
        return 0
    except OSError as e:
        if logger is not None:
            logger.warning("Failed to list temp dir %s: %s", temp_dir, e)
        return 0

    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.stat().st_mtime > cutoff:
                continue
            entry.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as e:
            if logger is not None:
                logger.warning("Failed to remove temp file %s: %s", entry, e)

    if removed and logger is not None:
        logger.info("Removed %s stale temp files from %s", removed, temp_dir)
    return removed


async def run_periodic_temp_cleanup(
    temp_dir: str,
    interval_seconds: int = TEMP_CLEANUP_INTERVAL_SECONDS,
    max_age_seconds: int = TEMP_FILE_MAX_AGE_SECONDS,
    logger: logging.Logger | None = None,
) -> None:
    while True:
        try:
            await cleanup_old_temp_files(
                temp_dir,
                max_age_seconds=max_age_seconds,
                logger=logger,
            )
        except Exception:
            if logger is not None:
                logger.exception("Unexpected temp cleanup failure")
        await asyncio.sleep(interval_seconds)
