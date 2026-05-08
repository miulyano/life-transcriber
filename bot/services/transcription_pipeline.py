from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol

from bot.services.transcriber import transcribe
from bot.services.usage_store import UsageStore, get_store


class Reporter(Protocol):
    async def set_phase(self, label: str) -> None: ...

    async def set_progress(self, current: int, total: int) -> None: ...

    async def set_progress_fraction(self, fraction: float) -> None: ...


DeliverText = Callable[[str], Awaitable[None]]
PhaseCallback = Callable[[str], Awaitable[None]]


async def run_transcription_pipeline(
    audio_path: str,
    *,
    reporter: Reporter,
    deliver_text: DeliverText,
    user_id: int,
    filename_hint: Optional[str] = None,
    on_phase_change: Optional[PhaseCallback] = None,
    usage_store: Optional[UsageStore] = None,
) -> None:
    """Transcribe audio (AssemblyAI) and deliver the formatted result.

    Перед запуском проверяет месячный лимит часов для ``user_id`` —
    кидает :class:`bot.services.usage_store.LimitExceededError`, если расход
    уже не вмещается. После успешной транскрибации списывает реальный
    duration из ответа AssemblyAI.

    Phases visible to the user:
    - "Транскрибирую…" — set by the caller before invoking this function.
    - "Форматирую…"    — emitted by transcribe() when the GPT step begins.
    - "Отправляю результат…" — set here after transcription completes.
    """
    store = usage_store or get_store()
    await store.assert_within_limit(user_id)

    result = await transcribe(
        audio_path,
        filename_hint=filename_hint,
        on_phase=reporter.set_phase,
        on_progress=reporter.set_progress,
        on_progress_fraction=reporter.set_progress_fraction,
    )
    await store.add_seconds(user_id, result.audio_duration_sec)
    if on_phase_change is not None:
        await on_phase_change("Отправляю результат…")
    await reporter.set_phase("Отправляю результат…")
    await deliver_text(result.body)
