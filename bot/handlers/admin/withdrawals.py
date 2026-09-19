from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm, AWd
from ...db import User, Withdrawal, change_balance
from ...keyboards import admin_wd_kb, btn, kb
from ...texts import admin_wd_text
from ...utils import money, refresh_admin_messages, safe_send, show

router = Router(name="admin_withdrawals")


async def refresh(bot: Bot, session: AsyncSession, wd: Withdrawal, call: CallbackQuery) -> None:
    await session.refresh(wd)
    user = await session.get(User, wd.user_id)
    await refresh_admin_messages(bot, session, "withdrawal", wd.id, admin_wd_text(wd, user),
                                 admin_wd_kb(wd.id, wd.user_id, wd.status == "new"), extra=call.message)


@router.callback_query(Adm.filter(F.to == "withdrawals"))
async def wd_list(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    wds = list(await session.scalars(
        select(Withdrawal).where(Withdrawal.status == "new").order_by(Withdrawal.id).limit(20)
    ))
    rows = [[btn(f"№{w.id} · {money(w.amount)} · ID {w.user_id}", AWd(action="view", id=w.id))] for w in wds]
    rows.append([btn("⬅️ Назад", Adm(to="menu"))])
    text = "💸 <b>Заявки на вывод</b>\n\n" + ("Нажмите на заявку, чтобы обработать:" if wds else "Новых заявок нет 🙌")
    await show(call, text, kb(*rows))
    await call.answer()


@router.callback_query(AWd.filter(F.action == "view"))
async def wd_view(call: CallbackQuery, callback_data: AWd, session: AsyncSession, bot: Bot):
    wd = await session.get(Withdrawal, callback_data.id)
    if not wd:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    user = await session.get(User, wd.user_id)
    await bot.send_message(call.message.chat.id, admin_wd_text(wd, user),
                           reply_markup=admin_wd_kb(wd.id, wd.user_id, wd.status == "new"))
    await call.answer()


@router.callback_query(AWd.filter(F.action.in_({"ok", "no"})))
async def wd_process(call: CallbackQuery, callback_data: AWd, session: AsyncSession, bot: Bot):
    wd = await session.get(Withdrawal, callback_data.id)
    if not wd:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    approve = callback_data.action == "ok"
    result = await session.execute(
        update(Withdrawal).where(Withdrawal.id == wd.id, Withdrawal.status == "new")
        .values(status="done" if approve else "rejected", admin_id=call.from_user.id)
    )
    if result.rowcount != 1:
        await session.rollback()
        await call.answer("Заявка уже обработана", show_alert=True)
        await refresh(bot, session, wd, call)
        return
    if not approve:
        await change_balance(session, wd.user_id, wd.amount, f"Возврат: вывод №{wd.id} отклонён")
    await session.commit()

    await call.answer("✅ Отмечено как выплачено" if approve else "❌ Заявка отклонена, деньги возвращены")
    await refresh(bot, session, wd, call)
    if approve:
        text = f"✅ Выплата по заявке №{wd.id} на <b>{money(wd.amount)}</b> отправлена! Спасибо, что вы с нами ❤️"
    else:
        text = (f"❌ Заявка на вывод №{wd.id} отклонена.\n"
                f"<b>{money(wd.amount)}</b> возвращены на ваш баланс.")
    await safe_send(bot, wd.user_id, text)
