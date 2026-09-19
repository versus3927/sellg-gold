import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, TelegramObject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import db, settings
from .callbacks import SubCheck
from .db import User, change_balance
from .keyboards import kb
from .utils import esc, is_admin, money, safe_send

log = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with db.session_maker() as session:
            data["session"] = session
            return await handler(event, data)


def _referrer_from_start(event: TelegramObject) -> int | None:
    """/start r123 → 123"""
    if not isinstance(event, Message) or not event.text or not event.text.startswith("/start "):
        return None
    arg = event.text.split(maxsplit=1)[1].strip()
    if arg.startswith("r"):
        arg = arg[1:]
    return int(arg) if arg.isdigit() else None


class UserMiddleware(BaseMiddleware):
    """Регистрирует пользователя (с учётом реф. ссылки), обновляет имя, не пускает забаненных."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        session: AsyncSession = data["session"]
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        user = await session.get(User, tg_user.id)
        is_new = user is None
        if is_new:
            user = await self._register(session, data["bot"], tg_user, _referrer_from_start(event))
        elif user.username != tg_user.username or user.full_name != tg_user.full_name or user.is_blocked:
            user.username, user.full_name, user.is_blocked = tg_user.username, tg_user.full_name, False
            await session.commit()

        if user.is_banned and not is_admin(user.id):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔️ Вы заблокированы", show_alert=True)
            elif isinstance(event, Message) and event.chat.type == "private":
                await event.answer("⛔️ Вы заблокированы администрацией бота.")
            return None

        data["user"] = user
        data["is_new_user"] = is_new
        return await handler(event, data)

    @staticmethod
    async def _register(session: AsyncSession, bot: Bot, tg_user, referrer_id: int | None) -> User:
        referrer = None
        if referrer_id and referrer_id != tg_user.id:
            referrer = await session.get(User, referrer_id)

        user = User(id=tg_user.id, username=tg_user.username, full_name=tg_user.full_name,
                    balance=0, ref_earned=0, is_banned=False, is_blocked=False,
                    referrer_id=referrer.id if referrer else None)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:  # два апдейта от нового пользователя одновременно
            await session.rollback()
            return await session.get(User, tg_user.id)

        if referrer:
            bonus = settings.get_int("ref_bonus")
            text = f"🎉 По вашей ссылке присоединился новый пользователь: <b>{esc(tg_user.full_name)}</b>!"
            if bonus > 0:
                await change_balance(session, referrer.id, bonus, f"Бонус за реферала {tg_user.id}",
                                     referral=True)
                await session.commit()
                text += f"\n💰 Вам начислено <b>{money(bonus)}</b>"
            await safe_send(bot, referrer.id, text)
        return user


class SubscriptionMiddleware(BaseMiddleware):
    """Обязательная подписка на канал (включается в настройках админки)."""

    OK_STATUSES = {"creator", "administrator", "member"}
    CACHE_TTL = 300

    def __init__(self) -> None:
        self._ok_until: dict[int, float] = {}

    async def is_subscribed(self, bot: Bot, channel: str, user_id: int) -> bool:
        if self._ok_until.get(user_id, 0) > time.monotonic():
            return True
        try:
            member = await bot.get_chat_member(channel, user_id)
        except TelegramAPIError as e:
            # Бот не админ канала или канал указан неверно — не блокируем пользователей
            log.warning("Не удалось проверить подписку на %s: %s", channel, e)
            return True
        ok = member.status in self.OK_STATUSES or getattr(member, "is_member", False)
        if ok:
            self._ok_until[user_id] = time.monotonic() + self.CACHE_TTL
        return ok

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        channel = settings.get("sub_channel").strip()
        user = data.get("user")
        if not channel or user is None or is_admin(user.id):
            return await handler(event, data)
        chat = event.message.chat if isinstance(event, CallbackQuery) and event.message else getattr(event, "chat", None)
        if chat is None or chat.type != "private":
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await self.is_subscribed(bot, channel, user.id):
            return await handler(event, data)

        url = settings.get("sub_url").strip() or f"https://t.me/{channel.lstrip('@')}"
        markup = kb(
            [InlineKeyboardButton(text="📣 Подписаться", url=url)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data=SubCheck().pack())],
        )
        text = "🔒 Чтобы пользоваться ботом, подпишитесь на наш канал и нажмите «✅ Я подписался»."
        if isinstance(event, CallbackQuery):
            if event.data == SubCheck().pack():
                await event.answer("❌ Подписка не найдена. Подпишитесь и попробуйте снова.", show_alert=True)
            else:
                await event.answer()
                await bot.send_message(user.id, text, reply_markup=markup)
        else:
            await event.answer(text, reply_markup=markup)
        return None

