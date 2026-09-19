from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..callbacks import Nav
from ..db import Order, Promo, PromoActivation, User, Withdrawal, change_balance
from ..keyboards import admin_wd_kb, cancel_kb, profile_kb
from ..states import PromoInput, Topup, Withdraw
from ..texts import admin_wd_text
from ..utils import dt, esc, money, notify_admins, parse_int, render
from .shop import show_methods

router = Router(name="profile")
router.message.filter(F.chat.type == "private")


async def open_profile(target: Message | CallbackQuery, session: AsyncSession, user: User) -> None:
    refs = await session.scalar(select(func.count()).select_from(User).where(User.referrer_id == user.id))
    bought, spent = (await session.execute(
        select(func.count(), func.coalesce(func.sum(Order.amount), 0))
        .where(Order.user_id == user.id, Order.kind == "product", Order.status.in_(("paid", "done")))
    )).one()
    text = (
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Имя: {esc(user.full_name)}\n"
        f"💰 Баланс: <b>{money(user.balance)}</b>\n\n"
        f"🛒 Покупок: <b>{bought}</b> на {money(spent)}\n"
        f"👥 Приглашено друзей: <b>{refs}</b>\n"
        f"📅 С нами с {dt(user.created_at)[:10]}"
    )
    await render(target, text, profile_kb())


@router.callback_query(Nav.filter(F.to == "profile"))
async def nav_profile(call: CallbackQuery, session: AsyncSession, user: User):
    await open_profile(call, session, user)
    await call.answer()


# ---------- пополнение ----------

@router.callback_query(Nav.filter(F.to == "topup"))
async def topup_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Topup.amount)
    await call.message.answer(
        f"💳 Введите сумму пополнения (минимум {money(settings.get_int('min_topup'))}):",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(Topup.amount, F.text)
async def topup_amount(message: Message, session: AsyncSession, state: FSMContext, user: User):
    amount = parse_int(message.text)
    minimum = settings.get_int("min_topup")
    if amount is None or amount < minimum:
        await message.answer(f"Введите целое число не меньше {money(minimum)}:", reply_markup=cancel_kb())
        return
    await state.clear()
    order = Order(user_id=user.id, kind="topup", title="Пополнение баланса", amount=amount,
                  status="new", paid_from_balance=False)
    session.add(order)
    await session.commit()
    await show_methods(message, session, order, user)


@router.message(Topup.amount)
async def topup_wrong(message: Message):
    await message.answer("Введите сумму числом, например: 500", reply_markup=cancel_kb())


# ---------- вывод ----------

@router.callback_query(Nav.filter(F.to == "withdraw"))
async def withdraw_start(call: CallbackQuery, state: FSMContext, user: User):
    if not settings.get_bool("withdraw_enabled"):
        await call.answer("Вывод средств временно недоступен", show_alert=True)
        return
    minimum = settings.get_int("min_withdraw")
    if user.balance < minimum:
        await call.answer(f"Минимальная сумма вывода — {money(minimum)}.\nВаш баланс: {money(user.balance)}",
                          show_alert=True)
        return
    await state.set_state(Withdraw.amount)
    await call.message.answer(
        f"💸 Введите сумму вывода (от {money(minimum)} до {money(user.balance)}):",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(Withdraw.amount, F.text)
async def withdraw_amount(message: Message, state: FSMContext, user: User):
    amount = parse_int(message.text)
    minimum = settings.get_int("min_withdraw")
    if amount is None or amount < minimum or amount > user.balance:
        await message.answer(f"Введите сумму от {money(minimum)} до {money(user.balance)}:",
                             reply_markup=cancel_kb())
        return
    await state.update_data(amount=amount)
    await state.set_state(Withdraw.details)
    await message.answer("🏦 Укажите реквизиты для вывода — номер карты или телефона и банк:",
                         reply_markup=cancel_kb())


@router.message(Withdraw.details, F.text)
async def withdraw_details(message: Message, session: AsyncSession, state: FSMContext, user: User, bot: Bot):
    amount = (await state.get_data()).get("amount")
    await state.clear()
    if not amount:
        return
    wd = Withdrawal(user_id=user.id, amount=amount, details=message.text.strip()[:300], status="new")
    session.add(wd)
    await session.flush()
    if not await change_balance(session, user.id, -amount, f"Заявка на вывод №{wd.id}"):
        await session.rollback()
        await message.answer("❌ Недостаточно средств на балансе.")
        return
    await session.commit()

    await message.answer(f"✅ Заявка на вывод №{wd.id} на <b>{money(amount)}</b> создана!\n\n"
                         "Средства отправим после проверки администратором — пришлём уведомление 🔔")
    await notify_admins(bot, session, "withdrawal", wd.id, admin_wd_text(wd, user),
                        admin_wd_kb(wd.id, user.id, pending=True))
    await session.commit()


@router.message(Withdraw.amount)
@router.message(Withdraw.details)
async def withdraw_wrong(message: Message):
    await message.answer("Отправьте ответ текстом 👆", reply_markup=cancel_kb())


# ---------- промокоды ----------

@router.callback_query(Nav.filter(F.to == "promo"))
async def promo_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoInput.code)
    await call.message.answer("🎁 Введите промокод:", reply_markup=cancel_kb())
    await call.answer()


@router.message(PromoInput.code, F.text)
async def promo_enter(message: Message, session: AsyncSession, state: FSMContext, user: User):
    await state.clear()
    code = message.text.strip().upper()
    promo = await session.scalar(select(Promo).where(Promo.code == code))
    if not promo:
        await message.answer("❌ Такого промокода нет.")
        return
    used = await session.scalar(select(PromoActivation.id).where(
        PromoActivation.promo_id == promo.id, PromoActivation.user_id == user.id))
    if used:
        await message.answer("⚠️ Вы уже активировали этот промокод.")
        return
    if promo.uses >= promo.max_uses:
        await message.answer("😔 Промокод закончился.")
        return

    session.add(PromoActivation(promo_id=promo.id, user_id=user.id))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        await message.answer("⚠️ Вы уже активировали этот промокод.")
        return
    result = await session.execute(
        update(Promo).where(Promo.id == promo.id, Promo.uses < Promo.max_uses).values(uses=Promo.uses + 1)
    )
    if result.rowcount != 1:
        await session.rollback()
        await message.answer("😔 Промокод закончился.")
        return
    await change_balance(session, user.id, promo.amount, f"Промокод {promo.code}")
    await session.commit()
    await message.answer(f"🎉 Промокод активирован! На баланс зачислено <b>{money(promo.amount)}</b>")


@router.message(PromoInput.code)
async def promo_wrong(message: Message):
    await message.answer("Отправьте промокод текстом 👆", reply_markup=cancel_kb())
