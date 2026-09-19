"""Пользователь нажал «Я оплатил» и присылает чек → заказ уходит админам на проверку."""

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..callbacks import OrderCB
from ..db import Order, User, utcnow
from ..keyboards import admin_order_kb, cancel_kb
from ..states import Pay
from ..texts import admin_order_text
from ..utils import ORDER_STATUS, notify_admins

router = Router(name="payment")
router.message.filter(F.chat.type == "private")


@router.callback_query(OrderCB.filter(F.action == "paid"))
async def i_paid(call: CallbackQuery, callback_data: OrderCB, session: AsyncSession, state: FSMContext, user: User):
    order = await session.get(Order, callback_data.id)
    if not order or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order.status != "new":
        await call.answer(f"Заказ №{order.id}: {ORDER_STATUS.get(order.status, order.status)}", show_alert=True)
        return
    if not order.method_id:
        await call.answer("Сначала выберите способ оплаты", show_alert=True)
        return
    await state.set_state(Pay.receipt)
    await state.update_data(order_id=order.id)
    await call.message.answer(
        f"📸 Отправьте <b>скриншот</b> или <b>PDF-файл</b> чека об оплате заказа №{order.id}.",
        reply_markup=cancel_kb(),
    )
    await call.answer()


async def accept_receipt(message: Message, session: AsyncSession, user: User, bot: Bot, order: Order) -> None:
    if message.photo:
        file_id, file_type = message.photo[-1].file_id, "photo"
    else:
        file_id, file_type = message.document.file_id, "document"

    result = await session.execute(
        update(Order).where(Order.id == order.id, Order.user_id == user.id, Order.status == "new")
        .values(status="review", receipt_file_id=file_id, receipt_type=file_type)
    )
    if result.rowcount != 1:
        await session.rollback()
        if not message.media_group_id:  # остальные фото из альбома молча пропускаем
            await message.answer("Этот заказ уже обработан или отменён.")
        return
    await session.commit()
    await session.refresh(order)

    await message.answer(
        f"✅ Чек получен! Заказ №{order.id} отправлен на проверку.\n\n"
        "Как только администратор подтвердит оплату, вы получите уведомление 🔔"
    )
    await notify_admins(
        bot, session, "order", order.id, admin_order_text(order, user), admin_order_kb(order),
        photo=file_id if file_type == "photo" else None,
        document=file_id if file_type == "document" else None,
    )
    await session.commit()


@router.message(Pay.receipt, F.photo | F.document)
async def got_receipt(message: Message, session: AsyncSession, state: FSMContext, user: User, bot: Bot):
    order_id = (await state.get_data()).get("order_id")
    await state.clear()
    order = await session.get(Order, order_id) if order_id else None
    if not order or order.user_id != user.id or order.status != "new":
        if not message.media_group_id:
            await message.answer("Этот заказ уже обработан или отменён.")
        return
    await accept_receipt(message, session, user, bot, order)


@router.message(Pay.receipt)
async def receipt_wrong(message: Message):
    await message.answer(
        "📸 Пришлите чек <b>фотографией</b> или <b>файлом</b> (PDF).\nЕсли передумали — нажмите «Отмена».",
        reply_markup=cancel_kb(),
    )


@router.message(StateFilter(None), F.photo | F.document)
async def receipt_without_button(message: Message, session: AsyncSession, user: User, bot: Bot):
    """Чек прислали, не нажав «Я оплатил», — привязываем к последнему неоплаченному заказу."""
    order = await session.scalar(
        select(Order)
        .where(Order.user_id == user.id, Order.status == "new", Order.method_id.is_not(None),
               Order.created_at >= utcnow() - timedelta(days=1))
        .order_by(Order.id.desc())
        .limit(1)
    )
    if not order:
        if not message.media_group_id:
            await message.answer("🧾 Чтобы отправить чек, сначала оформите заказ и выберите способ оплаты.")
        return
    await accept_receipt(message, session, user, bot, order)
