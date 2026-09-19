import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from sqlalchemy import func, select

from . import db, settings
from .config import get_config
from .db import PaymentMethod
from .handlers import build_router
from .middlewares import DbSessionMiddleware, SubscriptionMiddleware, UserMiddleware

log = logging.getLogger("bot")

# Реквизиты по умолчанию — создаются один раз при первом запуске, дальше меняются в админке
DEFAULT_METHOD = {
    "title": "💳 Перевод на карту",
    "requisites": "2204 3206 1762 2990",
    "holder": "Максим Ш.",
}


async def seed(session) -> None:
    if not await session.scalar(select(func.count()).select_from(PaymentMethod)):
        session.add(PaymentMethod(**DEFAULT_METHOD, note="", is_active=True, sort=0))
        await session.commit()
        log.info("Добавлен способ оплаты по умолчанию: %s", DEFAULT_METHOD["title"])


async def set_commands(bot: Bot, admin_ids: tuple[int, ...]) -> None:
    user_cmds = [BotCommand(command="start", description="Главное меню")]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(user_cmds + [BotCommand(command="admin", description="Админ-панель")],
                                      scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:  # админ ещё не писал боту
            log.warning("Не удалось задать команды для админа %s: %s", admin_id, e)


async def prepare_db(database_url: str) -> None:
    db.setup_db(database_url)
    await db.init_db()
    async with db.session_maker() as session:
        await seed(session)
        await settings.load(session)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(DbSessionMiddleware())
    subscription = SubscriptionMiddleware()
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(UserMiddleware())
        observer.outer_middleware(subscription)
    dp.include_router(build_router())
    return dp


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_config()

    await prepare_db(cfg.database_url)
    if cfg.is_sqlite and os.getenv("RAILWAY_ENVIRONMENT") and not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        log.warning("⚠️ База SQLite без Volume — данные пропадут при следующем деплое! "
                    "Подключите PostgreSQL или Volume (см. README).")
    log.info("База данных: %s", "SQLite" if cfg.is_sqlite else "PostgreSQL")

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                          link_preview_is_disabled=True))
    dp = create_dispatcher()

    me = await bot.me()
    log.info("Бот @%s запущен. Админы: %s", me.username, ", ".join(map(str, cfg.admin_ids)) or "—")
    await set_commands(bot, cfg.admin_ids)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
