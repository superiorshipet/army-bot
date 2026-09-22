from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from armybot.infrastructure.database.session import create_schema
from armybot.presentation.telegram.handlers import router
from armybot.shared.logging import configure_logging
from armybot.shared.settings import settings


async def run_bot() -> None:
    configure_logging(settings.log_level)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    await create_schema()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)
