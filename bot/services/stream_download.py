from __future__ import annotations

import os
from typing import Callable

import aiohttp


ErrorFactory = Callable[[int], Exception]
NetworkErrorFactory = Callable[[], Exception]
# Receives the response Content-Length; raises to abort before any write.
SizePrecheck = Callable[[int], None]


async def stream_download_to_file(
    session: aiohttp.ClientSession,
    href: str,
    out_path: str,
    *,
    chunk_size: int,
    http_error: ErrorFactory,
    network_error: NetworkErrorFactory,
    precheck: SizePrecheck | None = None,
) -> None:
    try:
        async with session.get(href) as resp:
            if resp.status != 200:
                raise http_error(resp.status)
            if precheck is not None:
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit():
                    precheck(int(cl))
            with open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(chunk_size):
                    f.write(chunk)
    except aiohttp.ClientError as exc:
        _cleanup_partial(out_path)
        raise network_error() from exc
    except BaseException:
        # Includes cancellation — the partial file must never survive.
        _cleanup_partial(out_path)
        raise


def _cleanup_partial(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
