from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm
from ...db import Order, User, Withdrawal, utcnow
from ...keyboards import BTN_ADMIN, btn, kb
from ...utils import money, render, show

router = Router(name="admin_panel")


async def count(session: AsyncSession, stmt) -> int:
    return int(await session.scalar(stmt) or 0)


async def open_panel(target: Message | CallbackQuery, session: AsyncSession) -> None:
    review = await count(session, select(func.count()).select_from(Order).where(Order.status == "review"))
    in_work = await count(session, select(func.count()).select_from(Order).where(Order.status == "paid"))
    wds = await count(session, select(func.count()).select_from(Withdrawal).where(Withdrawal.status == "new"))
    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        f"🔎 Чеков на проверке: <b>{review}</b>\n"
        f"💚 Заказов в работе: <b>{in_work}</b>\n"
        f"💸 Заявок на вывод: <b>{wds}</b>"
    )
    markup = kb(
        [btn("📊 Статистика", Adm(to="stats"))],
        [btn(f"🧾 Заказы ({review})", Adm(to="orders")), btn(f"💸 Выводы ({wds})", Adm(to="withdrawals"))],
        [btn("📦 Каталог", Adm(to="catalog")), btn("💳 Реквизиты", Adm(to="methods"))],
        [btn("👥 Пользователи", Adm(to="users")), btn("📢 Рассылка", Adm(to="broadcast"))],
        [btn("🎁 Промокоды", Adm(to="promos")), btn("📋 Задания", Adm(to="tasks"))],
        [btn("⚙️ Настройки", Adm(to="settings"))],
    )
    await render(target, text, markup)


@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN)
async def cmd_admin(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await open_panel(message, session)


@router.callback_query(Adm.filter(F.to == "menu"))
async def nav_panel(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    await open_panel(call, session)
    await call.answer()


@router.callback_query(Adm.filter(F.to == "stats"))
async def stats(call: CallbackQuery, session: AsyncSession):
    now = utcnow()
    day, week, month = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)

    users_total = await count(session, select(func.count()).select_from(User))
    users_blocked = await count(session, select(func.count()).select_from(User).where(User.is_blocked))
    users_day = await count(session, select(func.count()).select_from(User).where(User.created_at >= day))
    users_week = await count(session, select(func.count()).select_from(User).where(User.created_at >= week))

    paid = (Order.status.in_(("paid", "done")), Order.paid_from_balance.is_(False))

    async def income(since=None) -> tuple[int, int]:
        stmt = select(func.count(), func.coalesce(func.sum(Order.amount), 0)).where(*paid)
        if since is not None:
            stmt = stmt.where(Order.created_at >= since)
        cnt, total = (await session.execute(stmt)).one()
        return int(cnt), int(total)

    d_cnt, d_sum = await income(day)
    w_cnt, w_sum = await income(week)
    m_cnt, m_sum = await income(month)
    a_cnt, a_sum = await income()

    done = await count(session, select(func.count()).select_from(Order)
                       .where(Order.kind == "product", Order.status == "done"))
    balances = await count(session, select(func.coalesce(func.sum(User.balance), 0)))
    paid_out = await count(session, select(func.coalesce(func.sum(Withdrawal.amount), 0))
                           .where(Withdrawal.status == "done"))

    text = (
        "📊 <b>Статистика</b>\n\n"
        "👥 <b>Пользователи</b>\n"
        f"• Всего: <b>{users_total}</b> (заблокировали бота: {users_blocked})\n"
        f"• Новых за 24 часа: <b>+{users_day}</b>\n"
        f"• Новых за 7 дней: <b>+{users_week}</b>\n\n"
        "💰 <b>Оплаты по реквизитам</b>\n"
        f"• За 24 часа: {d_cnt} на <b>{money(d_sum)}</b>\n"
        f"• За 7 дней: {w_cnt} на <b>{money(w_sum)}</b>\n"
        f"• За 30 дней: {m_cnt} на <b>{money(m_sum)}</b>\n"
        f"• Всего: {a_cnt} на <b>{money(a_sum)}</b>\n\n"
        f"✅ Выполнено заказов: <b>{done}</b>\n"
        f"💼 На балансах пользователей: <b>{money(balances)}</b>\n"
        f"💸 Выплачено по выводам: <b>{money(paid_out)}</b>"
    )
    await show(call, text, kb([btn("🔄 Обновить", Adm(to="stats"))], [btn("⬅️ Назад", Adm(to="menu"))]))
    await call.answer()
