import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.handlers import callbacks, links, video, voice
from bot.middlewares.auth import AuthMiddleware


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.makedirs(settings.TEMP_DIR, exist_ok=True)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.middleware(AuthMiddleware())

    dp.include_router(voice.router)
    dp.include_router(video.router)
    dp.include_router(links.router)
    dp.include_router(callbacks.router)

    logging.info("Bot started. Allowed users: %s", settings.ALLOWED_USER_IDS)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
