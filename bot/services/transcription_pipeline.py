from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol

from bot.services.formatter import format_transcript
from bot.services.transcriber import transcribe


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
    filename_hint: Optional[str] = None,
    on_phase_change: Optional[PhaseCallback] = None,
) -> None:
    text = await transcribe(
        audio_path,
        on_progress=reporter.set_progress,
        on_progress_fraction=reporter.set_progress_fraction,
    )
    if on_phase_change is not None:
        await on_phase_change("Форматирую…")
    await reporter.set_phase("Форматирую…")
    text = await format_transcript(
        text,
        filename_hint=filename_hint,
        on_progress=reporter.set_progress,
        on_progress_fraction=reporter.set_progress_fraction,
    )
    if on_phase_change is not None:
        await on_phase_change("Отправляю результат…")
    await reporter.set_phase("Отправляю результат…")
    await deliver_text(text)
