"""Tests for bot.services.download_precheck — pre-download size/disk checks."""
from types import SimpleNamespace

import pytest

from bot.config import settings
from bot.services import download_precheck
from bot.services.download_precheck import (
    DISK_SPACE_FACTOR,
    DISK_SPACE_RESERVE_BYTES,
    ensure_downloadable,
)

GB = 1024**3
MB = 1024**2


def _set_free_disk(monkeypatch, free_bytes: int) -> None:
    monkeypatch.setattr(
        download_precheck.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=100 * GB, used=0, free=free_bytes),
    )


def test_size_over_hard_limit_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 4096)
    _set_free_disk(monkeypatch, 1000 * GB)
    with pytest.raises(RuntimeError, match=r"^yandex-disk:.*слишком большой"):
        ensure_downloadable(28 * GB, str(tmp_path), "yandex-disk")


def test_hard_limit_message_contains_both_sizes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 4096)
    _set_free_disk(monkeypatch, 1000 * GB)
    with pytest.raises(RuntimeError) as exc_info:
        ensure_downloadable(int(28.5 * GB), str(tmp_path), "yandex-disk")
    msg = str(exc_info.value)
    assert "28.5 GB" in msg  # file size
    assert "4.0 GB" in msg  # the limit


def test_size_under_limit_with_enough_disk_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 4096)
    _set_free_disk(monkeypatch, 1000 * GB)
    ensure_downloadable(1 * GB, str(tmp_path), "yandex-disk")  # no raise


def test_zero_limit_disables_hard_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 0)
    _set_free_disk(monkeypatch, 1000 * GB)
    ensure_downloadable(100 * GB, str(tmp_path), "yandex-disk")  # no raise


def test_insufficient_disk_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 0)
    size = 10 * GB
    # Free disk covers the raw file but not file + extraction workspace.
    _set_free_disk(monkeypatch, size + 1 * GB)
    with pytest.raises(RuntimeError, match=r"^yandex-disk:.*не хватит места"):
        ensure_downloadable(size, str(tmp_path), "yandex-disk")


def test_disk_boundary_exact_requirement_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 0)
    size = 5 * GB
    required = size * DISK_SPACE_FACTOR + DISK_SPACE_RESERVE_BYTES
    _set_free_disk(monkeypatch, required)
    ensure_downloadable(size, str(tmp_path), "yandex-disk")  # no raise

    _set_free_disk(monkeypatch, required - 1)
    with pytest.raises(RuntimeError, match=r"^yandex-disk:"):
        ensure_downloadable(size, str(tmp_path), "yandex-disk")


def test_insufficient_disk_message_contains_sizes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 0)
    _set_free_disk(monkeypatch, 10 * GB)
    with pytest.raises(RuntimeError) as exc_info:
        ensure_downloadable(20 * GB, str(tmp_path), "yandex-disk")
    msg = str(exc_info.value)
    assert "20.0 GB" in msg  # file size
    assert "10.0 GB" in msg  # free disk


def test_unknown_size_skips_all_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 1)

    def _boom(path):
        raise AssertionError("disk_usage must not be called for unknown size")

    monkeypatch.setattr(download_precheck.shutil, "disk_usage", _boom)
    ensure_downloadable(None, str(tmp_path), "yandex-disk")
    ensure_downloadable(0, str(tmp_path), "yandex-disk")


def test_provider_prefix_used_in_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_DOWNLOAD_MB", 1)
    _set_free_disk(monkeypatch, 1000 * GB)
    with pytest.raises(RuntimeError, match=r"^yandex-music:"):
        ensure_downloadable(1 * GB, str(tmp_path), "yandex-music")
