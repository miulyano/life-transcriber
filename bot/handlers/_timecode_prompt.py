from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.services.pending_jobs import PendingJob, put_job


async def ask_timecodes(message: Message, job: PendingJob) -> None:
    """Register a pending job and ask how to deliver the result.

    The pipeline starts only after the user picks a button
    (``tc:<0|1>:<pending_id>`` handled in bot/handlers/callbacks.py).
    """
    pending_id = put_job(job)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🕒 С таймкодами", callback_data=f"tc:1:{pending_id}"),
                InlineKeyboardButton(text="Без таймкодов", callback_data=f"tc:0:{pending_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отменить транскрибацию", callback_data=f"tc:x:{pending_id}"
                ),
            ],
        ]
    )
    await message.reply("Как прислать результат?", reply_markup=keyboard)
