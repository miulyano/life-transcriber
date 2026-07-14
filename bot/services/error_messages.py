from bot.services.user_facing_error import UserFacingError

# Fallback message shown when a provider error has no detail text.
_PROVIDER_FALLBACKS: dict[str, str] = {
    "instagram": "Ошибка при обработке Instagram",
    "yandex-disk": "Ошибка Яндекс Диска",
    "yandex-music": "Ошибка Яндекс Музыки",
    "facebook": "Ошибка при обработке Facebook",
}


def format_download_error(error: "Exception | str") -> str:
    """Convert a download exception into a user-facing Russian error message."""
    if isinstance(error, UserFacingError):
        error_msg = f"{error.provider}: {error.detail}"
    else:
        error_msg = str(error)

    for provider, fallback in _PROVIDER_FALLBACKS.items():
        if error_msg.startswith(f"{provider}:"):
            detail = error_msg.split(":", 1)[1].strip()
            return (detail[:1].upper() + detail[1:]) if detail else fallback

    if "yt-dlp" in error_msg:
        return "Не удалось скачать видео с этой платформы. Попробуй другую ссылку."

    return f"Ошибка: {error_msg}"


def format_exception_chain(exc: BaseException) -> str:
    """Render an exception and its ``raise ... from`` cause chain as one line
    for diagnostic logs: ``A: b | причина: C: d``.

    The friendly message shown to the user hides the real cause (e.g. a yt-dlp
    ``TypeError`` wrapped in a provider ``UserFacingError``); this keeps the whole
    chain visible in the logs without dumping a full traceback.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | причина: ".join(parts)
