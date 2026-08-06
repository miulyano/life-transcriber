from __future__ import annotations

import logging
import shutil

from bot.config import settings
from bot.services.user_facing_error import UserFacingError

logger = logging.getLogger(__name__)

# Extracted 16 kHz mono audio and transcription chunks live next to the source
# file on the same volume, so the download must fit with workspace to spare.
DISK_SPACE_FACTOR = 2

# Free space that must survive the download: a 100%-full disk breaks
# docker-exec healthchecks for every container on the host, not just this job.
DISK_SPACE_RESERVE_BYTES = 1024**3  # 1 GiB


def ensure_downloadable(size_bytes: int | None, dest_dir: str, provider: str) -> None:
    """Fail fast — before a single byte is downloaded — when a file of
    ``size_bytes`` can't be processed.

    Two checks: the optional ``MAX_DOWNLOAD_MB`` hard cap (0 disables it) and
    whether ``dest_dir``'s filesystem holds the file plus extraction workspace.
    Unknown size (``None``/0) skips both — the download proceeds as before.

    Raises :class:`UserFacingError` tagged with ``provider`` so handlers render
    a friendly message.
    """
    if not size_bytes or size_bytes <= 0:
        return

    max_mb = settings.MAX_DOWNLOAD_MB
    if max_mb > 0 and size_bytes > max_mb * 1024**2:
        logger.warning(
            "%s: file rejected by MAX_DOWNLOAD_MB: %s > %s MB",
            provider,
            _human_size(size_bytes),
            max_mb,
        )
        raise UserFacingError(
            provider,
            f"файл слишком большой: {_human_size(size_bytes)}, "
            f"лимит {_human_size(max_mb * 1024**2)}",
        )

    free = shutil.disk_usage(dest_dir).free
    required = size_bytes * DISK_SPACE_FACTOR + DISK_SPACE_RESERVE_BYTES
    if required > free:
        logger.warning(
            "%s: file rejected by free-disk check: size %s, free %s, required %s",
            provider,
            _human_size(size_bytes),
            _human_size(free),
            _human_size(required),
        )
        raise UserFacingError(
            provider,
            f"файлу не хватит места на сервере: размер {_human_size(size_bytes)}, "
            f"свободно {_human_size(free)}",
        )


def _human_size(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    return f"{n / 1024**2:.0f} MB"
