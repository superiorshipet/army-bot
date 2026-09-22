from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from armybot.infrastructure.database.session import create_schema
from armybot.infrastructure.database.sqlite_store import SqliteStore
from armybot.presentation.telegram.handlers import router
from armybot.shared.logging import configure_logging
from armybot.shared.settings import settings


async def run_bot() -> None:
    configure_logging(settings.log_level)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        await SqliteStore(settings.database_url).create_schema()
    else:
        await create_schema()

    bot = Bot(token=settings.telegram_bot_token)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Open the bot menu"),
            BotCommand(command="setup", description="Setup credentials"),
            BotCommand(command="deploy", description="Deploy a project"),
            BotCommand(command="status", description="Latest deployments"),
            BotCommand(command="users", description="Admin: show all users"),
        ]
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)
