import html
import logging
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import settings
from .config import get_config
from .db import AdminMessage

log = logging.getLogger(__name__)

try:
    TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
except ZoneInfoNotFoundError:  # нет базы часовых поясов — считаем по Москве
    TZ = timezone(timedelta(hours=3))

ORDER_STATUS = {
    "new": "🕓 Ожидает оплаты",
    "review": "🔎 Чек на проверке",
    "paid": "💚 Оплачен, в работе",
    "done": "✅ Выполнен",
    "rejected": "❌ Оплата отклонена",
    "cancelled": "🚫 Отменён",
}

WD_STATUS = {
    "new": "🕓 Ожидает выплаты",
    "done": "✅ Выплачено",
    "rejected": "❌ Отклонено",
}

REJECT_REASONS = [
    "Без причины",
    "Оплата не поступила",
    "Неверная сумма",
    "Чек недействителен",
]


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + f" {esc(settings.currency())}"


def dt(value: datetime | None) -> str:
    if not value:
        return "—"
    return value.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def is_admin(user_id: int) -> bool:
    return user_id in get_config().admin_ids


def mention(user_id: int, full_name: str | None, username: str | None = None) -> str:
    text = f'<a href="tg://user?id={user_id}">{esc(full_name or username or user_id)}</a>'
    if username:
        text += f" (@{esc(username)})"
    return text


def parse_int(text: str | None, max_value: int = 10_000_000) -> int | None:
    """Целое положительное число из ввода пользователя: «1 500», «1500₽» → 1500."""
    if not text:
        return None
    cleaned = text.replace(" ", "").replace(" ", "").replace("₽", "").replace(settings.currency(), "")
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if 0 < value <= max_value else None


def copy_value(requisites: str) -> str:
    """Что положить в буфер по кнопке «Скопировать»: номер карты/телефона — без пробелов."""
    compact = requisites.replace(" ", "")
    if compact and all(ch.isdigit() or ch in "+-()" for ch in compact):
        return compact
    return requisites[:256]


async def show(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None,
               photo: str | None = None) -> None:
    """Показать экран вместо сообщения, на котором нажали кнопку."""
    bot, chat_id, mid = call.bot, call.message.chat.id, call.message.message_id
    if photo and len(text) > 1024:
        photo = None  # подпись к фото ограничена 1024 символами
    is_text = isinstance(call.message, Message) and call.message.text is not None

    if photo or not is_text:
        with suppress(TelegramBadRequest):
            await bot.delete_message(chat_id, mid)
        if photo:
            await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb)
        else:
            await bot.send_message(chat_id, text, reply_markup=kb)
        return

    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=mid, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await bot.send_message(chat_id, text, reply_markup=kb)


async def render(target: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None,
                 photo: str | None = None) -> None:
    """Экран по нажатию кнопки меню (новое сообщение) или inline-кнопки (редактируем текущее)."""
    if isinstance(target, CallbackQuery):
        await show(target, text, kb, photo)
        return
    if photo and len(text) <= 1024:
        await target.answer_photo(photo, caption=text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


async def notify_admins(bot: Bot, session: AsyncSession, kind: str, ref_id: int, text: str,
                        kb: InlineKeyboardMarkup | None = None,
                        photo: str | None = None, document: str | None = None) -> int:
    """Рассылает уведомление админам (или в админ-чат) и запоминает сообщения. Коммит — на вызывающем."""
    cfg = get_config()
    targets = [cfg.admin_chat_id] if cfg.admin_chat_id else list(cfg.admin_ids)
    delivered = 0
    for chat_id in targets:
        try:
            if photo:
                msg = await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb)
            elif document:
                msg = await bot.send_document(chat_id, document, caption=text, reply_markup=kb)
            else:
                msg = await bot.send_message(chat_id, text, reply_markup=kb)
        except TelegramAPIError as e:
            log.warning("Не удалось уведомить админа %s: %s", chat_id, e)
            continue
        session.add(AdminMessage(kind=kind, ref_id=ref_id, chat_id=chat_id,
                                 message_id=msg.message_id, has_media=bool(photo or document)))
        delivered += 1
    if not delivered:
        log.error("Уведомление %s #%s не доставлено ни одному админу. "
                  "Админы должны написать боту /start", kind, ref_id)
    return delivered


async def refresh_admin_messages(bot: Bot, session: AsyncSession, kind: str, ref_id: int, text: str,
                                 kb: InlineKeyboardMarkup | None = None,
                                 extra: Message | None = None) -> None:
    """Обновляет все копии уведомления у админов (и сообщение, где нажали кнопку)."""
    rows = list(await session.scalars(
        select(AdminMessage).where(AdminMessage.kind == kind, AdminMessage.ref_id == ref_id)
    ))
    targets = [(r.chat_id, r.message_id, r.has_media) for r in rows]
    if isinstance(extra, Message) and (extra.chat.id, extra.message_id) not in {(c, m) for c, m, _ in targets}:
        targets.append((extra.chat.id, extra.message_id, extra.text is None))

    for chat_id, message_id, has_media in targets:
        try:
            if has_media:
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id,
                                               caption=text, reply_markup=kb)
            else:
                await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id,
                                            reply_markup=kb)
        except TelegramAPIError as e:
            if "not modified" not in str(e):
                log.debug("Не удалось обновить сообщение %s/%s: %s", chat_id, message_id, e)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except TelegramAPIError as e:
        log.info("Не удалось отправить сообщение %s: %s", chat_id, e)
        return False
