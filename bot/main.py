import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

from bot.config import settings
from bot.handlers import callbacks, links, video, voice
from bot.middlewares.auth import AuthMiddleware
from bot.services.temp_cleanup import run_periodic_temp_cleanup


logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    cleanup_task = asyncio.create_task(
        run_periodic_temp_cleanup(settings.TEMP_DIR, logger=logger)
    )

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

    if settings.WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="📤 Загрузить файл",
                web_app=WebAppInfo(url=settings.WEBAPP_URL),
            )
        )
        logging.info("Mini app menu button set: %s", settings.WEBAPP_URL)
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())

    logging.info("Bot started. Allowed users: %s", settings.allowed_user_ids)
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


if __name__ == "__main__":
    asyncio.run(main())
