from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm, AOrder, AUser
from ...db import BalanceLog, Order, User, change_balance
from ...keyboards import btn, kb
from ...states import AdmUser
from ...utils import ORDER_STATUS, dt, esc, is_admin, mention, money, parse_int, render, safe_send, show

router = Router(name="admin_users")


async def user_card(session: AsyncSession, user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    user = await session.get(User, user_id)
    if not user:
        return None
    await session.refresh(user)
    refs = await session.scalar(select(func.count()).select_from(User).where(User.referrer_id == user.id))
    bought, spent = (await session.execute(
        select(func.count(), func.coalesce(func.sum(Order.amount), 0))
        .where(Order.user_id == user.id, Order.kind == "product", Order.status.in_(("paid", "done")))
    )).one()
    paid_money = await session.scalar(
        select(func.coalesce(func.sum(Order.amount), 0))
        .where(Order.user_id == user.id, Order.status.in_(("paid", "done")), Order.paid_from_balance.is_(False))
    )
    status = "🚫 забанен" if user.is_banned else "✅ активен"
    if user.is_blocked:
        status += " · 📵 заблокировал бота"

    lines = [
        "👤 <b>Пользователь</b>",
        "",
        f"🆔 <code>{user.id}</code>",
        f"👤 {mention(user.id, user.full_name, user.username)}",
        f"💰 Баланс: <b>{money(user.balance)}</b>",
        f"🛒 Покупок: {bought} на {money(spent)}",
        f"💳 Оплатил по реквизитам: {money(paid_money)}",
        f"👥 Пригласил: {refs} · заработал {money(user.ref_earned)}",
    ]
    if user.referrer_id:
        lines.append(f"🔗 Пришёл по ссылке: <code>{user.referrer_id}</code>")
    lines += [f"📅 Регистрация: {dt(user.created_at)}", f"Статус: {status}"]

    log = list(await session.scalars(
        select(BalanceLog).where(BalanceLog.user_id == user.id).order_by(BalanceLog.id.desc()).limit(5)
    ))
    if log:
        lines += ["", "<b>Последние операции:</b>"]
        lines += [f"{'+' if r.delta > 0 else '−'}{money(abs(r.delta))} — {esc(r.reason)} <i>({dt(r.created_at)})</i>"
                  for r in log]

    markup = kb(
        [btn("➕ Начислить", AUser(action="add", id=user.id)), btn("➖ Списать", AUser(action="sub", id=user.id))],
        [btn("✅ Разбанить" if user.is_banned else "🚫 Забанить", AUser(action="ban", id=user.id)),
         btn("✉️ Написать", AUser(action="msg", id=user.id))],
        [btn("📦 Заказы", AUser(action="orders", id=user.id))],
        [btn("⬅️ К пользователям", Adm(to="users"))],
    )
    return "\n".join(lines), markup


async def send_user_card(bot: Bot, chat_id: int, session: AsyncSession, user_id: int) -> bool:
    card = await user_card(session, user_id)
    if card:
        return await safe_send(bot, chat_id, card[0], reply_markup=card[1])
    return await safe_send(bot, chat_id, "Пользователь не найден")


@router.callback_query(Adm.filter(F.to == "users"))
async def users_menu(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    total = await session.scalar(select(func.count()).select_from(User))
    await state.set_state(AdmUser.search)
    await show(call, f"👥 <b>Пользователи</b>\n\nВсего: <b>{total}</b>\n\n"
                     "Отправьте <b>ID</b> или <b>@username</b>, чтобы открыть карточку пользователя.",
               kb([btn("🆕 Новые пользователи", Adm(to="users_new"))], [btn("⬅️ Назад", Adm(to="menu"))]))
    await call.answer()


@router.callback_query(Adm.filter(F.to == "users_new"))
async def users_new(call: CallbackQuery, session: AsyncSession):
    users = list(await session.scalars(select(User).order_by(User.created_at.desc()).limit(15)))
    rows = [[btn(f"{u.full_name or u.id}{' @' + u.username if u.username else ''}", AUser(action="view", id=u.id))]
            for u in users]
    rows.append([btn("⬅️ Назад", Adm(to="users"))])
    await show(call, "🆕 <b>Последние 15 пользователей</b>", kb(*rows))
    await call.answer()


@router.message(AdmUser.search)
async def users_search(message: Message, state: FSMContext, session: AsyncSession):
    query = (message.text or "").strip().lstrip("@")
    if query.isdigit():
        user = await session.get(User, int(query))
    else:
        user = await session.scalar(select(User).where(func.lower(User.username) == query.lower())) if query else None
    if not user:
        await message.answer("Пользователь не найден. Отправьте другой ID или @username.")
        return
    await state.clear()
    text, markup = await user_card(session, user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(AUser.filter(F.action == "view"))
async def user_view(call: CallbackQuery, callback_data: AUser, state: FSMContext, session: AsyncSession):
    await state.clear()  # сюда же ведёт «Отмена» из ввода суммы/сообщения
    card = await user_card(session, callback_data.id)
    if not card:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    await show(call, *card)
    await call.answer()


@router.callback_query(AUser.filter(F.action == "card"))
async def user_card_dm(call: CallbackQuery, callback_data: AUser, session: AsyncSession, bot: Bot):
    if await send_user_card(bot, call.from_user.id, session, callback_data.id):
        await call.answer("Карточка отправлена в личные сообщения")
    else:
        await call.answer("Не удалось написать вам в личку — откройте бота и нажмите /start", show_alert=True)


@router.callback_query(AUser.filter(F.action == "ban"))
async def user_ban(call: CallbackQuery, callback_data: AUser, session: AsyncSession):
    user = await session.get(User, callback_data.id)
    if not user:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    if is_admin(user.id):
        await call.answer("Нельзя забанить администратора", show_alert=True)
        return
    user.is_banned = not user.is_banned
    await session.commit()
    await call.answer("🚫 Забанен" if user.is_banned else "✅ Разбанен")
    await show(call, *await user_card(session, user.id))


@router.callback_query(AUser.filter(F.action.in_({"add", "sub"})))
async def user_balance_start(call: CallbackQuery, callback_data: AUser, state: FSMContext):
    await state.set_state(AdmUser.amount)
    await state.update_data(target=callback_data.id, sign=1 if callback_data.action == "add" else -1)
    verb = "начислить" if callback_data.action == "add" else "списать"
    await call.message.answer(f"Сколько {verb}? Введите сумму:",
                              reply_markup=kb([btn("❌ Отмена", AUser(action="view", id=callback_data.id))]))
    await call.answer()


@router.message(AdmUser.amount)
async def user_balance_set(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    amount = parse_int(message.text)
    if amount is None:
        await message.answer("Введите целое положительное число:")
        return
    await state.clear()
    target, sign = data["target"], data["sign"]
    reason = "Начисление администратором" if sign > 0 else "Списание администратором"
    if not await change_balance(session, target, sign * amount, reason):
        await message.answer("❌ Не получилось: у пользователя недостаточно средств.")
    else:
        await session.commit()
        await message.answer(f"✅ Баланс изменён на {'+' if sign > 0 else '−'}{money(amount)}")
        if sign > 0:
            await safe_send(bot, target, f"💰 Вам начислено <b>{money(amount)}</b> на баланс!")
    card = await user_card(session, target)
    if card:
        await message.answer(card[0], reply_markup=card[1])


@router.callback_query(AUser.filter(F.action == "msg"))
async def user_msg_start(call: CallbackQuery, callback_data: AUser, state: FSMContext):
    await state.set_state(AdmUser.message)
    await state.update_data(target=callback_data.id)
    await call.message.answer("✉️ Отправьте сообщение для пользователя (текст, фото — что угодно):",
                              reply_markup=kb([btn("❌ Отмена", AUser(action="view", id=callback_data.id))]))
    await call.answer()


@router.message(AdmUser.message)
async def user_msg_send(message: Message, state: FSMContext, bot: Bot):
    target = (await state.get_data()).get("target")
    await state.clear()
    try:
        await bot.send_message(target, "✉️ <b>Сообщение от администрации:</b>")
        await message.copy_to(target)
    except TelegramAPIError as e:
        await message.answer(f"❌ Не доставлено: {esc(e)}")
        return
    await message.answer("✅ Сообщение доставлено",
                         reply_markup=kb([btn("👤 К пользователю", AUser(action="view", id=target))]))


@router.callback_query(AUser.filter(F.action == "orders"))
async def user_orders(call: CallbackQuery, callback_data: AUser, session: AsyncSession):
    orders = list(await session.scalars(
        select(Order).where(Order.user_id == callback_data.id).order_by(Order.id.desc()).limit(15)
    ))
    rows = [[btn(f"{ORDER_STATUS.get(o.status, '').split()[0]} №{o.id} · {money(o.amount)} · {o.title[:24]}",
                 AOrder(action="view", id=o.id))] for o in orders]
    rows.append([btn("⬅️ Назад", AUser(action="view", id=callback_data.id))])
    await render(call, "📦 <b>Заказы пользователя</b>" + ("" if orders else "\n\nЗаказов нет."), kb(*rows))
    await call.answer()
