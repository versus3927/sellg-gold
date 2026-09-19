from urllib.parse import quote

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from . import settings
from .callbacks import AOrder, AUser, AWd, Nav, OrderCB, PayCB
from .db import Order, PaymentMethod
from .utils import REJECT_REASONS, copy_value, money

BTN_SHOP = "🛒 Донат"
BTN_EARN = "💰 Заработок"
BTN_PROFILE = "👤 Профиль"
BTN_ORDERS = "📦 Мои заказы"
BTN_INFO = "ℹ️ Информация"
BTN_SUPPORT = "🆘 Поддержка"
BTN_ADMIN = "⚙️ Админ-панель"


def main_kb(admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [[BTN_SHOP, BTN_EARN], [BTN_PROFILE, BTN_ORDERS], [BTN_INFO, BTN_SUPPORT]]
    if admin:
        rows.append([BTN_ADMIN])
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def btn(text: str, cb) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack() if hasattr(cb, "pack") else cb)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def cancel_kb() -> InlineKeyboardMarkup:
    return kb([btn("❌ Отмена", Nav(to="cancel"))])


def back_kb(to: str, text: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    return kb([btn(text, Nav(to=to))])


def profile_kb() -> InlineKeyboardMarkup:
    rows = [[btn("💳 Пополнить", Nav(to="topup"))]]
    if settings.get_bool("withdraw_enabled"):
        rows[0].append(btn("💸 Вывести", Nav(to="withdraw")))
    rows.append([btn("🎁 Промокод", Nav(to="promo")), btn("📦 Мои заказы", Nav(to="orders"))])
    return kb(*rows)


def earn_kb(ref_link: str, tasks_count: int) -> InlineKeyboardMarkup:
    share = "https://t.me/share/url?url=" + quote(ref_link) + "&text=" + quote("Заходи — донат и заработок 🔥")
    rows = [[InlineKeyboardButton(text="📤 Пригласить друга", url=share)]]
    if settings.get_int("daily_bonus") > 0:
        rows.append([btn("🎰 Ежедневный бонус", Nav(to="bonus"))])
    if tasks_count:
        rows.append([btn(f"📋 Задания ({tasks_count})", Nav(to="tasks"))])
    if settings.get_bool("withdraw_enabled"):
        rows.append([btn("💸 Вывести", Nav(to="withdraw"))])
    return kb(*rows)


def methods_kb(order: Order, methods: list[PaymentMethod], balance: int) -> InlineKeyboardMarkup:
    rows = [[btn(m.title, PayCB(order_id=order.id, method_id=m.id))] for m in methods]
    if order.kind == "product":
        mark = "" if balance >= order.amount else " 🔒"
        rows.append([btn(f"💰 С баланса ({money(balance)}){mark}", PayCB(order_id=order.id, method_id=0))])
    rows.append([btn("❌ Отменить заказ", OrderCB(action="cancel", id=order.id))])
    return kb(*rows)


def requisites_kb(order: Order, method: PaymentMethod) -> InlineKeyboardMarkup:
    return kb(
        [InlineKeyboardButton(text="📋 Скопировать реквизиты",
                              copy_text=CopyTextButton(text=copy_value(method.requisites)))],
        [btn("✅ Я оплатил", OrderCB(action="paid", id=order.id))],
        [btn("🔄 Другой способ", OrderCB(action="methods", id=order.id)),
         btn("❌ Отменить", OrderCB(action="cancel", id=order.id))],
    )


def support_kb() -> InlineKeyboardMarkup | None:
    rows = []
    contact = settings.get("support_contact").lstrip("@").strip()
    if contact:
        rows.append([InlineKeyboardButton(text="✍️ Написать в поддержку", url=f"https://t.me/{contact}")])
    channel = settings.get("channel_url").strip()
    if channel.startswith("https://") or channel.startswith("http://"):
        rows.append([InlineKeyboardButton(text="📣 Наш канал", url=channel)])
    return kb(*rows) if rows else None


# ---------- уведомления для админов ----------

def admin_order_kb(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.status == "review":
        rows.append([btn("✅ Подтвердить", AOrder(action="ok", id=order.id)),
                     btn("❌ Отклонить", AOrder(action="no", id=order.id))])
    elif order.status == "paid":
        rows.append([btn("📦 Заказ выполнен", AOrder(action="done", id=order.id))])
    rows.append([btn("👤 Покупатель", AOrder(action="user", id=order.id))])
    return kb(*rows)


def admin_reject_kb(order: Order) -> InlineKeyboardMarkup:
    rows = [[btn(f"❌ {reason}", AOrder(action="rej", id=order.id, extra=i))]
            for i, reason in enumerate(REJECT_REASONS)]
    rows.append([btn("↩️ Назад", AOrder(action="back", id=order.id))])
    return kb(*rows)


def admin_wd_kb(wd_id: int, user_id: int, pending: bool) -> InlineKeyboardMarkup:
    rows = []
    if pending:
        rows.append([btn("✅ Выплачено", AWd(action="ok", id=wd_id)),
                     btn("❌ Отклонить", AWd(action="no", id=wd_id))])
    rows.append([btn("👤 Пользователь", AUser(action="card", id=user_id))])
    return kb(*rows)
