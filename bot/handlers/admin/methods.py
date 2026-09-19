"""Способы оплаты (реквизиты), которые видит покупатель."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm, AMethod
from ...db import PaymentMethod
from ...keyboards import btn, cancel_kb, kb
from ...states import AdmMethod
from ...utils import esc, render, show
from .edit import RENDERERS, start_edit

router = Router(name="admin_methods")


@router.callback_query(Adm.filter(F.to == "methods"))
async def methods_list(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    methods = list(await session.scalars(select(PaymentMethod).order_by(PaymentMethod.sort, PaymentMethod.id)))
    rows = [[btn(f"{'✅' if m.is_active else '⏸'} {m.title}", AMethod(action="view", id=m.id))] for m in methods]
    rows.append([btn("➕ Добавить способ оплаты", AMethod(action="add"))])
    rows.append([btn("⬅️ Назад", Adm(to="menu"))])
    text = ("💳 <b>Способы оплаты</b>\n\n"
            "Покупатель видит все включённые (✅) способы и реквизиты к ним.\n"
            "Нажмите на способ, чтобы изменить реквизиты или получателя.")
    await show(call, text, kb(*rows))
    await call.answer()


async def method_view(target: Message | CallbackQuery, session: AsyncSession, method_id: int) -> None:
    m = await session.get(PaymentMethod, method_id)
    if not m:
        await render(target, "Способ оплаты не найден.", kb([btn("⬅️ Назад", Adm(to="methods"))]))
        return
    text = (
        f"💳 <b>{esc(m.title)}</b>\n\n"
        f"🔢 Реквизиты: <code>{esc(m.requisites)}</code>\n"
        f"👤 Получатель: {esc(m.holder) or '—'}\n"
        f"ℹ️ Примечание: {esc(m.note) or '—'}\n"
        f"🔢 Порядок: {m.sort}\n"
        f"Статус: {'✅ включён' if m.is_active else '⏸ выключен'}"
    )
    rows = [
        [btn("✏️ Название", AMethod(action="edit", id=m.id, field="title")),
         btn("🔢 Реквизиты", AMethod(action="edit", id=m.id, field="requisites"))],
        [btn("👤 Получатель", AMethod(action="edit", id=m.id, field="holder")),
         btn("ℹ️ Примечание", AMethod(action="edit", id=m.id, field="note"))],
        [btn("⏸ Выключить" if m.is_active else "▶️ Включить", AMethod(action="toggle", id=m.id)),
         btn("🗑 Удалить", AMethod(action="del", id=m.id))],
        [btn("⬅️ Назад", Adm(to="methods"))],
    ]
    await render(target, text, kb(*rows))


async def _after_edit(message: Message, session: AsyncSession, obj_id: int) -> None:
    await method_view(message, session, obj_id)

RENDERERS["method"] = _after_edit


@router.callback_query(AMethod.filter(F.action == "view"))
async def m_view(call: CallbackQuery, callback_data: AMethod, state: FSMContext, session: AsyncSession):
    await state.clear()
    await method_view(call, session, callback_data.id)
    await call.answer()


@router.callback_query(AMethod.filter(F.action == "edit"))
async def m_edit(call: CallbackQuery, callback_data: AMethod, state: FSMContext):
    await start_edit(call, state, "method", callback_data.id, callback_data.field)


@router.callback_query(AMethod.filter(F.action == "toggle"))
async def m_toggle(call: CallbackQuery, callback_data: AMethod, session: AsyncSession):
    m = await session.get(PaymentMethod, callback_data.id)
    if m:
        m.is_active = not m.is_active
        await session.commit()
    await method_view(call, session, callback_data.id)
    await call.answer()


@router.callback_query(AMethod.filter(F.action == "del"))
async def m_delete_ask(call: CallbackQuery, callback_data: AMethod, session: AsyncSession):
    m = await session.get(PaymentMethod, callback_data.id)
    if not m:
        await call.answer("Уже удалён", show_alert=True)
        return
    await show(call, f"🗑 Удалить способ оплаты <b>{esc(m.title)}</b>?",
               kb([btn("✅ Да, удалить", AMethod(action="delok", id=m.id)),
                   btn("❌ Нет", AMethod(action="view", id=m.id))]))
    await call.answer()


@router.callback_query(AMethod.filter(F.action == "delok"))
async def m_delete(call: CallbackQuery, callback_data: AMethod, state: FSMContext, session: AsyncSession):
    m = await session.get(PaymentMethod, callback_data.id)
    if m:
        await session.delete(m)
        await session.commit()
    await call.answer("🗑 Удалено")
    await methods_list(call, state, session)


# ---------- добавление: название → реквизиты → получатель ----------

@router.callback_query(AMethod.filter(F.action == "add"))
async def m_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdmMethod.title)
    await call.message.answer("1/3. Название способа оплаты (например: <b>💳 Сбербанк</b> или <b>📱 СБП</b>):",
                              reply_markup=cancel_kb())
    await call.answer()


@router.message(AdmMethod.title, F.text)
async def m_add_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title or len(title) > 128:
        await message.answer("От 1 до 128 символов:", reply_markup=cancel_kb())
        return
    await state.update_data(title=title)
    await state.set_state(AdmMethod.requisites)
    await message.answer("2/3. Реквизиты — номер карты, телефона или кошелька:", reply_markup=cancel_kb())


@router.message(AdmMethod.requisites, F.text)
async def m_add_requisites(message: Message, state: FSMContext):
    requisites = message.text.strip()
    if not requisites or len(requisites) > 256:
        await message.answer("От 1 до 256 символов:", reply_markup=cancel_kb())
        return
    await state.update_data(requisites=requisites)
    await state.set_state(AdmMethod.holder)
    await message.answer("3/3. Получатель (например: <b>Максим Ш.</b>) или «-», если не нужно:",
                         reply_markup=cancel_kb())


@router.message(AdmMethod.holder, F.text)
async def m_add_holder(message: Message, state: FSMContext, session: AsyncSession):
    holder = message.text.strip()
    if len(holder) > 128:
        await message.answer("Максимум 128 символов:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    method = PaymentMethod(title=data["title"], requisites=data["requisites"],
                           holder="" if holder == "-" else holder, note="", is_active=True, sort=0)
    session.add(method)
    await session.commit()
    await message.answer("✅ Способ оплаты добавлен!")
    await method_view(message, session, method.id)


@router.message(AdmMethod.title)
@router.message(AdmMethod.requisites)
@router.message(AdmMethod.holder)
async def m_wrong_input(message: Message):
    await message.answer("⚠️ Отправьте ответ текстом или нажмите «Отмена».", reply_markup=cancel_kb())
