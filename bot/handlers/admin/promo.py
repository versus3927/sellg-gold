import re
import secrets

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm, APromo
from ...db import Promo, PromoActivation
from ...keyboards import btn, cancel_kb, kb
from ...states import AdmPromo
from ...utils import dt, esc, money, parse_int, render, show

router = Router(name="admin_promo")

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")


async def promos_view(target: Message | CallbackQuery, session: AsyncSession) -> None:
    promos = list(await session.scalars(select(Promo).order_by(Promo.id.desc()).limit(30)))
    lines = ["🎁 <b>Промокоды</b>", ""]
    if promos:
        lines.append("Нажмите на промокод, чтобы открыть или удалить:")
    else:
        lines.append("Промокодов пока нет.")
    rows = [[btn(f"{'✅' if p.uses < p.max_uses else '⛔️'} {p.code} · {money(p.amount)} · {p.uses}/{p.max_uses}",
                 APromo(action="view", id=p.id))] for p in promos]
    rows.append([btn("➕ Создать промокод", APromo(action="add"))])
    rows.append([btn("⬅️ Назад", Adm(to="menu"))])
    await render(target, "\n".join(lines), kb(*rows))


@router.callback_query(Adm.filter(F.to == "promos"))
async def promos(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    await promos_view(call, session)
    await call.answer()


@router.callback_query(APromo.filter(F.action == "view"))
async def promo_view(call: CallbackQuery, callback_data: APromo, session: AsyncSession):
    p = await session.get(Promo, callback_data.id)
    if not p:
        await call.answer("Промокод не найден", show_alert=True)
        return
    text = (f"🎁 Промокод <code>{esc(p.code)}</code>\n\n"
            f"💰 Сумма: <b>{money(p.amount)}</b>\n"
            f"👥 Активаций: <b>{p.uses} / {p.max_uses}</b>\n"
            f"📅 Создан: {dt(p.created_at)}")
    await show(call, text, kb([btn("🗑 Удалить", APromo(action="del", id=p.id))],
                              [btn("⬅️ Назад", Adm(to="promos"))]))
    await call.answer()


@router.callback_query(APromo.filter(F.action == "del"))
async def promo_delete(call: CallbackQuery, callback_data: APromo, state: FSMContext, session: AsyncSession):
    await session.execute(delete(PromoActivation).where(PromoActivation.promo_id == callback_data.id))
    await session.execute(delete(Promo).where(Promo.id == callback_data.id))
    await session.commit()
    await call.answer("🗑 Промокод удалён")
    await promos(call, state, session)


@router.callback_query(APromo.filter(F.action == "add"))
async def promo_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdmPromo.code)
    await call.message.answer("1/3. Введите промокод (латиница, цифры, _ и -, от 3 до 32 символов)\n"
                              "или «-», чтобы сгенерировать случайный:", reply_markup=cancel_kb())
    await call.answer()


@router.message(AdmPromo.code, F.text)
async def promo_add_code(message: Message, state: FSMContext, session: AsyncSession):
    code = message.text.strip().upper()
    if code == "-":
        code = secrets.token_hex(4).upper()
    if not CODE_RE.match(code):
        await message.answer("Код может содержать только латиницу, цифры, _ и - (от 3 до 32 символов):",
                             reply_markup=cancel_kb())
        return
    if await session.scalar(select(Promo.id).where(Promo.code == code)):
        await message.answer("Такой промокод уже есть. Введите другой:", reply_markup=cancel_kb())
        return
    await state.update_data(code=code)
    await state.set_state(AdmPromo.amount)
    await message.answer(f"Код: <code>{code}</code>\n\n2/3. Сколько начислять на баланс?", reply_markup=cancel_kb())


@router.message(AdmPromo.amount, F.text)
async def promo_add_amount(message: Message, state: FSMContext):
    amount = parse_int(message.text)
    if amount is None:
        await message.answer("Нужно целое положительное число:", reply_markup=cancel_kb())
        return
    await state.update_data(amount=amount)
    await state.set_state(AdmPromo.uses)
    await message.answer("3/3. Сколько раз его можно активировать (всего)?", reply_markup=cancel_kb())


@router.message(AdmPromo.uses, F.text)
async def promo_add_uses(message: Message, state: FSMContext, session: AsyncSession):
    uses = parse_int(message.text, max_value=1_000_000)
    if uses is None:
        await message.answer("Нужно целое положительное число:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    if await session.scalar(select(Promo.id).where(Promo.code == data["code"])):
        await message.answer("Такой промокод уже есть — создайте заново с другим кодом.")
        return
    session.add(Promo(code=data["code"], amount=data["amount"], max_uses=uses, uses=0))
    await session.commit()
    await message.answer(f"✅ Промокод <code>{data['code']}</code> создан: {money(data['amount'])}, "
                         f"активаций: {uses}")
    await promos_view(message, session)


@router.message(AdmPromo.code)
@router.message(AdmPromo.amount)
@router.message(AdmPromo.uses)
async def promo_wrong_input(message: Message):
    await message.answer("⚠️ Отправьте ответ текстом или нажмите «Отмена».", reply_markup=cancel_kb())
