import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest

from bot.utils.progress import (
    BAR_WIDTH,
    EMPTY,
    FILLED,
    ProgressReporter,
    position_at_tick,
    render_determinate,
    render_indeterminate,
)

# ---------------------------------------------------------------------------
# Fraction-mode tests (set_progress_fraction)
# ---------------------------------------------------------------------------


# --- Pure rendering ---

def test_render_indeterminate_has_one_runner_on_empty_track():
    bar = render_indeterminate(3, width=10)
    # split by grapheme — all our cells are single-emoji, so char-by-char works here
    cells = [c for c in bar if c in (FILLED, EMPTY)]
    assert len(cells) == 10
    assert cells[3] == FILLED
    assert cells.count(FILLED) == 1
    assert cells.count(EMPTY) == 9


def test_render_indeterminate_position_out_of_range_is_all_empty():
    bar = render_indeterminate(-1, width=10)
    assert FILLED not in bar
    bar = render_indeterminate(10, width=10)
    assert FILLED not in bar


def test_render_determinate_zero_of_n_is_all_empty():
    bar = render_determinate(0, 5, width=10)
    assert FILLED not in bar
    assert bar.count(EMPTY) == 10


def test_render_determinate_n_of_n_is_all_filled():
    bar = render_determinate(5, 5, width=10)
    assert EMPTY not in bar
    assert bar.count(FILLED) == 10


def test_render_determinate_rounds_proportionally():
    # 3/10 width=10 → 3 filled
    assert render_determinate(3, 10, width=10).count(FILLED) == 3
    # 1/3 width=10 → round(3.33) = 3
    assert render_determinate(1, 3, width=10).count(FILLED) == 3
    # 2/3 width=10 → round(6.66) = 7
    assert render_determinate(2, 3, width=10).count(FILLED) == 7


def test_render_determinate_zero_total_falls_back_to_indeterminate():
    bar = render_determinate(0, 0, width=10)
    # indeterminate at pos=0 has exactly one filled cell at index 0
    assert bar.count(FILLED) == 1
    assert bar.count(EMPTY) == 9


def test_render_determinate_clamps_overshoot():
    # current > total should clamp to total (not overflow)
    bar = render_determinate(10, 5, width=10)
    assert bar.count(FILLED) == 10
    assert EMPTY not in bar


# --- Ping-pong bouncer ---

def test_position_at_tick_covers_cycle():
    # width=10 → period = 18. Go 0..9 up, then 8..1 down.
    seq = [position_at_tick(t, width=10) for t in range(19)]
    assert seq == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]


def test_position_at_tick_width_one_degenerate():
    assert position_at_tick(0, width=1) == 0
    assert position_at_tick(100, width=1) == 0


# --- ProgressReporter integration ---

class FakeChat:
    def __init__(self, chat_id: int = 42):
        self.id = chat_id


class FakeBot:
    def __init__(self):
        self.edits: list[dict] = []
        self.deleted: list[dict] = []
        self.raise_not_modified_next = False
        self.delete_should_fail = False

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        if self.raise_not_modified_next:
            self.raise_not_modified_next = False
            raise TelegramBadRequest(method=None, message="message is not modified")

    async def delete_message(self, chat_id, message_id):
        self.deleted.append({"chat_id": chat_id, "message_id": message_id})
        if self.delete_should_fail:
            raise TelegramBadRequest(method=None, message="message can't be deleted")


class FakeStatusMessage:
    def __init__(self, chat_id: int, message_id: int, text: str):
        self.chat = FakeChat(chat_id)
        self.message_id = message_id
        self.text = text


class FakeMessage:
    def __init__(self, bot: FakeBot, chat_id: int = 42):
        self.bot = bot
        self.chat = FakeChat(chat_id)
        self._next_id = 100
        self.replies: list[str] = []

    async def reply(self, text: str, **kwargs):
        self.replies.append(text)
        self.reply_markups = getattr(self, "reply_markups", [])
        self.reply_markups.append(kwargs.get("reply_markup"))
        self._next_id += 1
        return FakeStatusMessage(self.chat.id, self._next_id, text)


async def _noop_sleep(_seconds: float) -> None:
    # Yield control once so background task can be cancelled cleanly.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_reporter_sends_initial_reply_and_finish_deletes():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.finish()
    assert len(msg.replies) == 1
    assert "Скачиваю…" in msg.replies[0]
    assert len(bot.deleted) == 1


@pytest.mark.asyncio
async def test_set_phase_edits_immediately():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_phase("Транскрибирую…")
        await r.finish()
    # At least one edit with the new label BEFORE the delete
    texts = [e["text"] for e in bot.edits]
    assert any("Транскрибирую…" in t for t in texts)


@pytest.mark.asyncio
async def test_set_progress_switches_to_determinate():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress(3, 10)
        await r.finish()
    # Find the determinate edit
    determinate_edits = [e for e in bot.edits if "3/10" in e["text"]]
    assert determinate_edits, f"no determinate edit found in {bot.edits}"
    # Exactly 3 filled cells
    edit_text = determinate_edits[0]["text"]
    assert edit_text.count(FILLED) == 3


@pytest.mark.asyncio
async def test_fail_edits_with_cross_prefix_and_no_delete():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.fail("всё плохо")
    assert bot.deleted == []
    fail_edits = [e for e in bot.edits if "всё плохо" in e["text"]]
    assert fail_edits
    assert fail_edits[-1]["text"].startswith("❌ ")


@pytest.mark.asyncio
async def test_exception_without_resolve_triggers_fail():
    bot = FakeBot()
    msg = FakeMessage(bot)
    with pytest.raises(RuntimeError):
        async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep):
            raise RuntimeError("boom")
    # fail() was called: no delete, at least one edit with ❌
    assert bot.deleted == []
    assert any(e["text"].startswith("❌") and "boom" in e["text"] for e in bot.edits)


@pytest.mark.asyncio
async def test_not_modified_is_swallowed():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep) as r:
        bot.raise_not_modified_next = True
        # Triggers edit which will raise — should not propagate
        await r.set_phase("Новая фаза…")
        await r.finish()
    # Reporter survived; finish still deleted the message
    assert len(bot.deleted) == 1


@pytest.mark.asyncio
async def test_finish_falls_back_to_edit_when_delete_fails():
    bot = FakeBot()
    bot.delete_should_fail = True
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.finish()
    # delete was attempted once, then edit "✅ Готово" was sent as fallback
    assert len(bot.deleted) == 1
    assert any("✅ Готово" in e["text"] for e in bot.edits)


# ---------------------------------------------------------------------------
# set_progress_fraction
# ---------------------------------------------------------------------------

async def _let_ticker_render() -> None:
    """set_progress_fraction only stores the value; the background ticker
    (running on _noop_sleep) renders it on its next iteration."""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_fraction_zero_all_empty_with_percent():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress_fraction(0.0)
        await _let_ticker_render()
        await r.finish()
    fraction_edits = [e for e in bot.edits if "%" in e["text"]]
    assert fraction_edits
    last = fraction_edits[-1]["text"]
    assert FILLED not in last
    assert EMPTY * BAR_WIDTH in last
    assert last.endswith(" 0%")


@pytest.mark.asyncio
async def test_fraction_one_all_filled_100_percent():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress_fraction(1.0)
        await _let_ticker_render()
        await r.finish()
    fraction_edits = [e for e in bot.edits if "%" in e["text"]]
    assert fraction_edits
    last = fraction_edits[-1]["text"]
    assert EMPTY not in last
    assert FILLED * BAR_WIDTH in last
    assert last.endswith(" 100%")


@pytest.mark.asyncio
async def test_fraction_below_one_does_not_render_full_bar_or_100():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress_fraction(0.95)
        await _let_ticker_render()
        await r.finish()
    fraction_edits = [e for e in bot.edits if "%" in e["text"]]
    assert fraction_edits
    last = fraction_edits[-1]["text"]
    assert last.count(FILLED) == BAR_WIDTH - 1
    assert last.count(EMPTY) == 1
    assert last.endswith(" 95%")


@pytest.mark.asyncio
async def test_fraction_just_below_one_caps_at_99_percent():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress_fraction(0.995)
        await _let_ticker_render()
        await r.finish()
    fraction_edits = [e for e in bot.edits if "%" in e["text"]]
    assert fraction_edits
    assert fraction_edits[-1]["text"].endswith(" 99%")


@pytest.mark.asyncio
async def test_fraction_half_five_cells_50_percent():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress_fraction(0.5)
        await _let_ticker_render()
        await r.finish()
    fraction_edits = [e for e in bot.edits if "%" in e["text"]]
    assert fraction_edits
    last = fraction_edits[-1]["text"]
    assert last.count(FILLED) == 5
    assert last.count(EMPTY) == 5
    assert "/" not in last
    assert last.endswith(" 50%")


@pytest.mark.asyncio
async def test_fraction_overrides_progress_counter():
    """Switching from set_progress (with counter) to set_progress_fraction (percent)."""
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress(3, 10)        # determinate with counter
        await r.set_progress_fraction(0.7)  # switch to fraction — percent
        await _let_ticker_render()
        await r.finish()
    fraction_edits = [e for e in bot.edits if "%" in e["text"] and "/" not in e["text"]]
    assert fraction_edits
    assert fraction_edits[-1]["text"].count(FILLED) == round(0.7 * BAR_WIDTH)
    assert fraction_edits[-1]["text"].endswith(" 70%")


@pytest.mark.asyncio
async def test_fraction_updates_do_not_edit_immediately():
    """Rapid fraction updates (yt-dlp) are rendered by the ticker, not per call."""
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep) as r:
        edits_before = len(bot.edits)
        await r.set_progress_fraction(0.1)
        await r.set_progress_fraction(0.2)
        await r.set_progress_fraction(0.3)
        # No yields between the calls above — no edits could have happened.
        assert len(bot.edits) == edits_before
        await _let_ticker_render()
        percent_edits = [e for e in bot.edits if "%" in e["text"]]
        assert len(percent_edits) == 1  # one render with the latest value
        assert percent_edits[0]["text"].endswith(" 30%")
        await r.finish()


class FakeChatBot(FakeBot):
    """FakeBot extended with send_message, for ProgressReporter.for_chat()."""

    def __init__(self, chat_id: int = 42, message_id: int = 500):
        super().__init__()
        self.sent: list[dict] = []
        self._next_id = message_id

    async def send_message(self, chat_id: int, text: str, reply_markup=None):
        self._next_id += 1
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "message_id": self._next_id,
                "reply_markup": reply_markup,
            }
        )
        return FakeStatusMessage(chat_id, self._next_id, text)


@pytest.mark.asyncio
async def test_for_chat_sends_initial_message_and_finishes():
    bot = FakeChatBot()
    async with ProgressReporter.for_chat(
        bot, chat_id=42, initial_label="Готовлю аудио…",
        tick_seconds=0, sleep=_noop_sleep,
    ) as r:
        await r.set_phase("Транскрибирую…")
        await r.finish()
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 42
    assert "Готовлю аудио" in bot.sent[0]["text"]
    texts = [e["text"] for e in bot.edits]
    assert any("Транскрибирую" in t for t in texts)
    assert len(bot.deleted) == 1
    assert bot.deleted[0]["chat_id"] == 42


@pytest.mark.asyncio
async def test_for_chat_fail_edits_without_delete():
    bot = FakeChatBot()
    async with ProgressReporter.for_chat(
        bot, chat_id=42, initial_label="Готовлю аудио…",
        tick_seconds=0, sleep=_noop_sleep,
    ) as r:
        await r.fail("Не удалось обработать файл.")
    assert bot.deleted == []
    fail_edits = [e for e in bot.edits if "Не удалось" in e["text"]]
    assert fail_edits
    assert fail_edits[-1]["text"].startswith("❌ ")


# ---------------------------------------------------------------------------
# reply_markup + cancelled()
# ---------------------------------------------------------------------------

def _markup():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Отменить", callback_data="cancel:abc")]
        ]
    )


@pytest.mark.asyncio
async def test_reply_markup_passed_to_initial_reply_and_edits():
    bot = FakeBot()
    msg = FakeMessage(bot)
    kb = _markup()
    async with ProgressReporter(
        msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep, reply_markup=kb
    ) as r:
        await r.set_phase("Транскрибирую…")
        await r.finish()
    assert msg.reply_markups == [kb]
    phase_edits = [e for e in bot.edits if "Транскрибирую" in e["text"]]
    assert phase_edits
    assert all(e["reply_markup"] is kb for e in phase_edits)


@pytest.mark.asyncio
async def test_cancelled_edits_without_markup_and_no_delete():
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(
        msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep, reply_markup=_markup()
    ) as r:
        await r.cancelled()
    assert bot.deleted == []
    assert bot.edits[-1]["text"] == "✖️ Отменено"
    assert bot.edits[-1]["reply_markup"] is None


@pytest.mark.asyncio
async def test_cancelled_error_triggers_cancelled_not_fail():
    bot = FakeBot()
    msg = FakeMessage(bot)
    with pytest.raises(asyncio.CancelledError):
        async with ProgressReporter(msg, "Скачиваю…", tick_seconds=0, sleep=_noop_sleep):
            raise asyncio.CancelledError()
    assert bot.deleted == []
    assert bot.edits[-1]["text"] == "✖️ Отменено"
    assert not any(e["text"].startswith("❌") for e in bot.edits)


@pytest.mark.asyncio
async def test_set_phase_resets_fraction_to_indeterminate():
    """After set_phase, bar must be indeterminate again (no counter, runner moves)."""
    bot = FakeBot()
    msg = FakeMessage(bot)
    async with ProgressReporter(msg, "Транскрибирую…", tick_seconds=0, sleep=_noop_sleep) as r:
        await r.set_progress_fraction(0.8)
        await r.set_phase("Новая фаза…")
        await r.finish()
    # edit right after set_phase should be indeterminate: no slash, exactly 1 filled cell
    phase_edits = [e for e in bot.edits if "Новая фаза" in e["text"]]
    assert phase_edits
    first_phase = phase_edits[0]["text"]
    assert "/" not in first_phase
    assert first_phase.count(FILLED) == 1  # single runner, position 0
