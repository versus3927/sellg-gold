"""Тексты карточек заказов и выводов."""

from . import settings
from .db import Order, PaymentMethod, User, Withdrawal
from .utils import ORDER_STATUS, WD_STATUS, dt, esc, mention, money


def order_head(order: Order) -> list[str]:
    lines = [f"🧾 <b>Заказ №{order.id}</b>", f"📦 {esc(order.title)}"]
    if order.user_input:
        lines.append(f"📝 Данные: <code>{esc(order.user_input)}</code>")
    lines.append(f"💵 Сумма: <b>{money(order.amount)}</b>")
    return lines


def methods_text(order: Order, has_methods: bool) -> str:
    lines = order_head(order) + [""]
    lines.append("Выберите способ оплаты 👇" if has_methods or order.kind == "product"
                 else "😔 Способы оплаты временно недоступны. Попробуйте позже.")
    return "\n".join(lines)


def requisites_text(order: Order, method: PaymentMethod) -> str:
    lines = order_head(order) + [
        "",
        f"<b>{esc(method.title)}</b>",
        f"🔢 Реквизиты: <code>{esc(method.requisites)}</code>",
    ]
    if method.holder:
        lines.append(f"👤 Получатель: <b>{esc(method.holder)}</b>")
    if method.note:
        lines.append(f"ℹ️ {esc(method.note)}")
    lines += ["", settings.get("pay_instruction")]
    return "\n".join(lines)


def user_order_text(order: Order) -> str:
    lines = order_head(order)
    if order.method_title or order.paid_from_balance:
        lines.append(f"💳 Оплата: {'с баланса' if order.paid_from_balance else esc(order.method_title)}")
    lines += [f"🕓 {dt(order.created_at)}", "", f"Статус: <b>{ORDER_STATUS.get(order.status, order.status)}</b>"]
    if order.status == "rejected" and order.reject_reason:
        lines.append(f"Причина: {esc(order.reject_reason)}")
    return "\n".join(lines)


def admin_order_text(order: Order, user: User | None, admin: User | None = None) -> str:
    kind = "пополнение баланса" if order.kind == "topup" else "покупка"
    who = mention(order.user_id, user.full_name, user.username) if user else f"<code>{order.user_id}</code>"
    pay = "💰 с баланса" if order.paid_from_balance else esc(order.method_title or "—")
    lines = [
        f"🧾 <b>Заказ №{order.id}</b> · {kind}",
        "",
        f"👤 {who}",
        f"🆔 <code>{order.user_id}</code>",
        f"📦 {esc(order.title)}",
    ]
    if order.user_input:
        lines.append(f"📝 Данные: <code>{esc(order.user_input)}</code>")
    lines += [
        f"💵 Сумма: <b>{money(order.amount)}</b>",
        f"💳 Оплата: {pay}",
        f"🕓 {dt(order.created_at)}",
        "",
        f"Статус: <b>{ORDER_STATUS.get(order.status, order.status)}</b>",
    ]
    if order.status == "rejected" and order.reject_reason:
        lines.append(f"Причина: {esc(order.reject_reason)}")
    if admin and order.status not in ("new", "review"):
        lines.append(f"Обработал: {esc(admin.full_name or admin.id)}")
    return "\n".join(lines)


def admin_wd_text(wd: Withdrawal, user: User | None) -> str:
    who = mention(wd.user_id, user.full_name, user.username) if user else f"<code>{wd.user_id}</code>"
    return "\n".join([
        f"💸 <b>Заявка на вывод №{wd.id}</b>",
        "",
        f"👤 {who}",
        f"🆔 <code>{wd.user_id}</code>",
        f"💵 Сумма: <b>{money(wd.amount)}</b>",
        f"🏦 Реквизиты: <code>{esc(wd.details)}</code>",
        f"🕓 {dt(wd.created_at)}",
        "",
        f"Статус: <b>{WD_STATUS.get(wd.status, wd.status)}</b>",
    ])
