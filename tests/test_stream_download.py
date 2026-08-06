"""Tests for bot.services.stream_download — shared streaming download helper."""
import asyncio
import os

import aiohttp
import pytest
from aioresponses import aioresponses

from bot.services.stream_download import stream_download_to_file

HREF = "https://example.com/file.mp3"


def _http_error(status: int) -> Exception:
    return RuntimeError(f"http {status}")


def _network_error() -> Exception:
    return RuntimeError("network")


@pytest.mark.asyncio
async def test_precheck_receives_content_length_and_aborts(tmp_path):
    """When the response advertises Content-Length, precheck runs BEFORE any
    byte is written — a too-big file leaves no partial file behind."""
    out_path = str(tmp_path / "out.mp3")
    seen: list[int] = []

    def precheck(size: int) -> None:
        seen.append(size)
        raise RuntimeError("provider: файл слишком большой")

    with aioresponses() as m:
        m.get(
            HREF,
            status=200,
            body=b"0123456789",
            headers={"Content-Length": "10"},
        )
        async with aiohttp.ClientSession() as session:
            with pytest.raises(RuntimeError, match="слишком большой"):
                await stream_download_to_file(
                    session,
                    HREF,
                    out_path,
                    chunk_size=4,
                    http_error=_http_error,
                    network_error=_network_error,
                    precheck=precheck,
                )

    assert seen == [10]
    assert os.listdir(tmp_path) == []


@pytest.mark.asyncio
async def test_precheck_passing_downloads_file(tmp_path):
    out_path = str(tmp_path / "out.mp3")
    with aioresponses() as m:
        m.get(
            HREF,
            status=200,
            body=b"payload",
            headers={"Content-Length": "7"},
        )
        async with aiohttp.ClientSession() as session:
            await stream_download_to_file(
                session,
                HREF,
                out_path,
                chunk_size=4,
                http_error=_http_error,
                network_error=_network_error,
                precheck=lambda size: None,
            )
    with open(out_path, "rb") as f:
        assert f.read() == b"payload"


@pytest.mark.asyncio
async def test_no_precheck_keeps_old_behavior(tmp_path):
    out_path = str(tmp_path / "out.mp3")
    with aioresponses() as m:
        m.get(HREF, status=200, body=b"payload")
        async with aiohttp.ClientSession() as session:
            await stream_download_to_file(
                session,
                HREF,
                out_path,
                chunk_size=4,
                http_error=_http_error,
                network_error=_network_error,
            )
    assert os.path.exists(out_path)


@pytest.mark.asyncio
async def test_cancellation_mid_write_cleans_partial_file(tmp_path, monkeypatch):
    """BaseException (e.g. task cancellation) during the write must remove the
    partial file — the old ``except Exception`` let CancelledError leak it."""
    import builtins

    out_path = str(tmp_path / "out.mp3")
    real_open = builtins.open

    def failing_open(path, mode="r", *args, **kwargs):
        f = real_open(path, mode, *args, **kwargs)
        if "wb" not in mode or not str(path).startswith(str(tmp_path)):
            return f

        class _Exploder:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                f.close()
                return False

            def write(self, data):
                f.write(data[:1])
                raise asyncio.CancelledError()

        return _Exploder()

    monkeypatch.setattr(builtins, "open", failing_open)

    with aioresponses() as m:
        m.get(HREF, status=200, body=b"payload")
        async with aiohttp.ClientSession() as session:
            with pytest.raises(asyncio.CancelledError):
                await stream_download_to_file(
                    session,
                    HREF,
                    out_path,
                    chunk_size=4,
                    http_error=_http_error,
                    network_error=_network_error,
                )
    assert os.listdir(tmp_path) == []
