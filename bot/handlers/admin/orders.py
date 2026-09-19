from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ... import settings
from ...callbacks import Adm, AOrder, AOrderList
from ...db import Order, User, change_balance
from ...keyboards import admin_order_kb, admin_reject_kb, btn, kb, support_kb
from ...states import AdmOrderSearch
from ...texts import admin_order_text
from ...utils import ORDER_STATUS, REJECT_REASONS, esc, money, refresh_admin_messages, safe_send, show
from .users import send_user_card

router = Router(name="admin_orders")

ORDER_ICON = {key: value.split()[0] for key, value in ORDER_STATUS.items()}


async def order_card(session: AsyncSession, order: Order) -> str:
    user = await session.get(User, order.user_id)
    admin = await session.get(User, order.admin_id) if order.admin_id else None
    return admin_order_text(order, user, admin)


async def send_order_card(bot: Bot, chat_id: int, session: AsyncSession, order: Order) -> None:
    text, markup = await order_card(session, order), admin_order_kb(order)
    try:
        if order.receipt_type == "photo":
            await bot.send_photo(chat_id, order.receipt_file_id, caption=text, reply_markup=markup)
            return
        if order.receipt_type == "document":
            await bot.send_document(chat_id, order.receipt_file_id, caption=text, reply_markup=markup)
            return
    except TelegramAPIError:
        text += "\n\n⚠️ Не удалось загрузить чек"
    await bot.send_message(chat_id, text, reply_markup=markup)


async def refresh(bot: Bot, session: AsyncSession, order: Order, call: CallbackQuery) -> None:
    await session.refresh(order)
    await refresh_admin_messages(bot, session, "order", order.id, await order_card(session, order),
                                 admin_order_kb(order), extra=call.message)


async def load(call: CallbackQuery, session: AsyncSession, order_id: int) -> Order | None:
    order = await session.get(Order, order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
    return order


# ---------- списки ----------

@router.callback_query(Adm.filter(F.to == "orders"))
async def orders_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    counts = dict((await session.execute(
        select(Order.status, func.count()).where(Order.status.in_(("review", "paid"))).group_by(Order.status)
    )).all())
    markup = kb(
        [btn(f"🔎 Чеки на проверке ({counts.get('review', 0)})", AOrderList(status="review"))],
        [btn(f"💚 Оплачены, в работе ({counts.get('paid', 0)})", AOrderList(status="paid"))],
        [btn("📜 Последние 20 заказов", AOrderList(status="all"))],
        [btn("🔍 Найти по номеру", Adm(to="order_search"))],
        [btn("⬅️ Назад", Adm(to="menu"))],
    )
    await show(call, "🧾 <b>Заказы</b>\n\nВыберите список:", markup)
    await call.answer()


@router.callback_query(AOrderList.filter())
async def orders_list(call: CallbackQuery, callback_data: AOrderList, session: AsyncSession):
    stmt = select(Order).limit(20)
    if callback_data.status == "all":
        stmt = stmt.order_by(Order.id.desc())
    else:
        stmt = stmt.where(Order.status == callback_data.status).order_by(Order.id)  # старые — первыми
    orders = list(await session.scalars(stmt))
    rows = [[btn(f"{ORDER_ICON.get(o.status, '')} №{o.id} · {money(o.amount)} · {o.title[:24]}",
                 AOrder(action="view", id=o.id))] for o in orders]
    rows.append([btn("⬅️ Назад", Adm(to="orders"))])
    text = "🧾 <b>Заказы</b>\n\n" + ("Нажмите на заказ, чтобы открыть его с чеком:" if orders else "Здесь пусто 🙌")
    await show(call, text, kb(*rows))
    await call.answer()


@router.callback_query(AOrder.filter(F.action == "view"))
async def order_view(call: CallbackQuery, callback_data: AOrder, session: AsyncSession, bot: Bot):
    order = await load(call, session, callback_data.id)
    if order:
        await send_order_card(bot, call.message.chat.id, session, order)
        await call.answer()


@router.callback_query(Adm.filter(F.to == "order_search"))
async def order_search_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdmOrderSearch.number)
    await call.message.answer("🔍 Введите номер заказа:", reply_markup=kb([btn("❌ Отмена", Adm(to="orders"))]))
    await call.answer()


@router.message(AdmOrderSearch.number)
async def order_search(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    number = (message.text or "").strip().lstrip("№#")
    order = await session.get(Order, int(number)) if number.isdigit() and len(number) < 10 else None
    if not order:
        await message.answer("Заказ не найден. Введите другой номер или нажмите «Отмена».",
                             reply_markup=kb([btn("❌ Отмена", Adm(to="orders"))]))
        return
    await state.clear()
    await send_order_card(bot, message.chat.id, session, order)


# ---------- действия с заказом ----------

async def referral_reward(session: AsyncSession, order: Order) -> tuple[int, int] | None:
    """% пригласившему с реальной оплаты (не с баланса)."""
    percent = settings.get_int("ref_percent")
    if percent <= 0 or order.paid_from_balance:
        return None
    buyer = await session.get(User, order.user_id)
    if not buyer or not buyer.referrer_id:
        return None
    reward = order.amount * percent // 100
    if reward <= 0:
        return None
    if not await change_balance(session, buyer.referrer_id, reward, f"Реф. отчисление, заказ №{order.id}",
                                referral=True):
        return None
    return buyer.referrer_id, reward


@router.callback_query(AOrder.filter(F.action == "ok"))
async def order_confirm(call: CallbackQuery, callback_data: AOrder, session: AsyncSession, bot: Bot):
    order = await load(call, session, callback_data.id)
    if not order:
        return
    new_status = "done" if order.kind == "topup" else "paid"
    result = await session.execute(
        update(Order).where(Order.id == order.id, Order.status == "review")
        .values(status=new_status, admin_id=call.from_user.id)
    )
    if result.rowcount != 1:
        await session.rollback()
        await call.answer("Заказ уже обработан", show_alert=True)
        await refresh(bot, session, order, call)
        return
    if order.kind == "topup":
        await change_balance(session, order.user_id, order.amount, f"Пополнение, заказ №{order.id}")
    ref = await referral_reward(session, order)
    await session.commit()

    await call.answer("✅ Оплата подтверждена")
    await refresh(bot, session, order, call)

    if order.kind == "topup":
        buyer = await session.get(User, order.user_id)
        await session.refresh(buyer)
        text = (f"✅ <b>Оплата подтверждена!</b>\n\nБаланс пополнен на <b>{money(order.amount)}</b>.\n"
                f"💰 Текущий баланс: <b>{money(buyer.balance)}</b>")
    else:
        text = (f"✅ <b>Оплата заказа №{order.id} подтверждена!</b>\n\n📦 {esc(order.title)}\n\n"
                "Заказ передан в работу — сообщим, когда он будет выполнен 🔔")
    await safe_send(bot, order.user_id, text)
    if ref:
        await safe_send(bot, ref[0], f"💸 Ваш друг совершил оплату — вам начислено <b>{money(ref[1])}</b>!")


@router.callback_query(AOrder.filter(F.action == "no"))
async def order_reject_ask(call: CallbackQuery, callback_data: AOrder, session: AsyncSession, bot: Bot):
    order = await load(call, session, callback_data.id)
    if not order:
        return
    if order.status != "review":
        await call.answer("Заказ уже обработан", show_alert=True)
        await refresh(bot, session, order, call)
        return
    await call.message.edit_reply_markup(reply_markup=admin_reject_kb(order))
    await call.answer("Выберите причину отказа")


@router.callback_query(AOrder.filter(F.action == "back"))
async def order_reject_back(call: CallbackQuery, callback_data: AOrder, session: AsyncSession):
    order = await load(call, session, callback_data.id)
    if order:
        await call.message.edit_reply_markup(reply_markup=admin_order_kb(order))
        await call.answer()


@router.callback_query(AOrder.filter(F.action == "rej"))
async def order_reject(call: CallbackQuery, callback_data: AOrder, session: AsyncSession, bot: Bot):
    order = await load(call, session, callback_data.id)
    if not order:
        return
    reason = REJECT_REASONS[callback_data.extra] if 0 < callback_data.extra < len(REJECT_REASONS) else None
    result = await session.execute(
        update(Order).where(Order.id == order.id, Order.status == "review")
        .values(status="rejected", reject_reason=reason, admin_id=call.from_user.id)
    )
    if result.rowcount != 1:
        await session.rollback()
        await call.answer("Заказ уже обработан", show_alert=True)
        await refresh(bot, session, order, call)
        return
    await session.commit()
    await call.answer("❌ Оплата отклонена")
    await refresh(bot, session, order, call)

    text = f"❌ <b>Оплата заказа №{order.id} не подтверждена.</b>"
    if reason:
        text += f"\nПричина: {esc(reason)}"
    text += "\n\nЕсли вы уверены, что оплатили, — напишите в поддержку и приложите чек."
    await safe_send(bot, order.user_id, text, reply_markup=support_kb())


@router.callback_query(AOrder.filter(F.action == "done"))
async def order_done(call: CallbackQuery, callback_data: AOrder, session: AsyncSession, bot: Bot):
    order = await load(call, session, callback_data.id)
    if not order:
        return
    result = await session.execute(
        update(Order).where(Order.id == order.id, Order.status == "paid")
        .values(status="done", admin_id=call.from_user.id)
    )
    if result.rowcount != 1:
        await session.rollback()
        await call.answer("Заказ уже обработан", show_alert=True)
        await refresh(bot, session, order, call)
        return
    await session.commit()
    await call.answer("📦 Заказ выполнен")
    await refresh(bot, session, order, call)
    await safe_send(bot, order.user_id,
                    f"🎉 <b>Заказ №{order.id} выполнен!</b>\n\n📦 {esc(order.title)}\n\nСпасибо за покупку ❤️")


@router.callback_query(AOrder.filter(F.action == "user"))
async def order_buyer(call: CallbackQuery, callback_data: AOrder, session: AsyncSession, bot: Bot):
    order = await load(call, session, callback_data.id)
    if order:
        # Карточку шлём в личку админу — даже если нажали в общем админ-чате
        if await send_user_card(bot, call.from_user.id, session, order.user_id):
            await call.answer("Карточка покупателя отправлена в личные сообщения")
        else:
            await call.answer("Не удалось написать вам в личку — откройте бота и нажмите /start", show_alert=True)
