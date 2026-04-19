from __future__ import annotations

from typing import Optional

import aiohttp

from bot.config import settings
from bot.services.user_facing_error import UserFacingError


async def request_cobalt(
    session: aiohttp.ClientSession,
    url: str,
    *,
    provider: str,
) -> dict:
    cobalt_url = f"{settings.COBALT_API_URL}/"
    body = {"url": url, "videoQuality": "720"}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    try:
        async with session.post(cobalt_url, json=body, headers=headers) as resp:
            if resp.status != 200:
                error_data = await _read_cobalt_error(resp)
                if error_data:
                    return error_data
                raise UserFacingError(provider, f"Cobalt вернул HTTP {resp.status}")
            return await resp.json()
    except (aiohttp.ClientError, ConnectionError, OSError) as exc:
        raise UserFacingError(
            provider,
            "Cobalt недоступен, попробуйте позже",
        ) from exc


def extract_video_url(data: dict, *, provider: str) -> str:
    status = data.get("status")

    if status in ("tunnel", "redirect"):
        video_url = data.get("url")
        if video_url:
            return video_url
        raise UserFacingError(provider, "неожиданный ответ от Cobalt")

    if status == "picker":
        picker = data.get("picker", [])
        for item in picker:
            if item.get("type") == "video":
                video_url = item.get("url")
                if video_url:
                    return video_url
        raise UserFacingError(provider, "в публикации нет видео")

    if status == "error":
        code = ""
        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code", "")
        if code:
            raise UserFacingError(
                provider,
                f"Cobalt не смог обработать ссылку ({code})",
            )
        raise UserFacingError(provider, "Cobalt не смог обработать ссылку")

    raise UserFacingError(provider, "неожиданный ответ от Cobalt")


async def _read_cobalt_error(resp: aiohttp.ClientResponse) -> Optional[dict]:
    try:
        data = await resp.json()
    except (aiohttp.ContentTypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("status") == "error":
        return data
    return None
