from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..callbacks import BuyCB, CatCB, Nav, OrderCB, PayCB, ProdCB
from ..db import Category, Order, PaymentMethod, Product, User, change_balance
from ..keyboards import admin_order_kb, btn, cancel_kb, kb, methods_kb, requisites_kb
from ..states import Buy
from ..texts import admin_order_text, methods_text, requisites_text, user_order_text
from ..utils import ORDER_STATUS, esc, money, notify_admins, render, show

router = Router(name="shop")
router.message.filter(F.chat.type == "private")

ORDER_ICON = {key: value.split()[0] for key, value in ORDER_STATUS.items()}


# ---------- каталог ----------

async def open_shop(target: Message | CallbackQuery, session: AsyncSession) -> None:
    cats = list(await session.scalars(
        select(Category).where(Category.is_active).order_by(Category.sort, Category.id)
    ))
    if not cats:
        await render(target, "🛒 <b>Донат</b>\n\nКаталог пока пуст — загляните чуть позже 🙌")
        return
    rows = [[btn(c.title, CatCB(id=c.id))] for c in cats]
    await render(target, "🛒 <b>Донат</b>\n\nВыберите игру или категорию 👇", kb(*rows))


@router.callback_query(Nav.filter(F.to == "shop"))
async def nav_shop(call: CallbackQuery, session: AsyncSession):
    await open_shop(call, session)
    await call.answer()


@router.callback_query(CatCB.filter())
async def open_category(call: CallbackQuery, callback_data: CatCB, session: AsyncSession):
    cat = await session.get(Category, callback_data.id)
    if not cat or not cat.is_active:
        await call.answer("Категория недоступна", show_alert=True)
        return
    products = list(await session.scalars(
        select(Product)
        .where(Product.category_id == cat.id, Product.is_active)
        .order_by(Product.sort, Product.price, Product.id)
    ))
    text = f"<b>{esc(cat.title)}</b>"
    if cat.description:
        text += f"\n\n{cat.description}"
    text += "\n\nВыберите товар 👇" if products else "\n\nТоваров пока нет."
    rows = [[btn(f"{p.title} — {money(p.price)}", ProdCB(id=p.id))] for p in products]
    rows.append([btn("⬅️ Назад", Nav(to="shop"))])
    await show(call, text, kb(*rows))
    await call.answer()


@router.callback_query(ProdCB.filter())
async def open_product(call: CallbackQuery, callback_data: ProdCB, session: AsyncSession):
    product = await session.get(Product, callback_data.id)
    if not product or not product.is_active:
        await call.answer("Товар недоступен", show_alert=True)
        return
    text = f"<b>{esc(product.title)}</b>\n\n"
    if product.description:
        text += f"{product.description}\n\n"
    text += f"💵 Цена: <b>{money(product.price)}</b>"
    markup = kb([btn("🛒 Купить", BuyCB(id=product.id))],
                [btn("⬅️ Назад", CatCB(id=product.category_id))])
    await show(call, text, markup, photo=product.photo)
    await call.answer()


# ---------- оформление заказа ----------

async def create_order(session: AsyncSession, user: User, product: Product, user_input: str | None) -> Order:
    cat = await session.get(Category, product.category_id)
    title = f"{product.title} · {cat.title}" if cat else product.title
    order = Order(user_id=user.id, kind="product", product_id=product.id, title=title[:256],
                  amount=product.price, user_input=user_input, status="new", paid_from_balance=False)
    session.add(order)
    await session.commit()
    return order


async def active_methods(session: AsyncSession) -> list[PaymentMethod]:
    return list(await session.scalars(
        select(PaymentMethod).where(PaymentMethod.is_active).order_by(PaymentMethod.sort, PaymentMethod.id)
    ))


async def show_methods(target: Message | CallbackQuery, session: AsyncSession, order: Order, user: User) -> None:
    methods = await active_methods(session)
    await render(target, methods_text(order, bool(methods)), methods_kb(order, methods, user.balance))


@router.callback_query(BuyCB.filter())
async def buy(call: CallbackQuery, callback_data: BuyCB, session: AsyncSession, state: FSMContext, user: User):
    product = await session.get(Product, callback_data.id)
    if not product or not product.is_active:
        await call.answer("Товар недоступен", show_alert=True)
        return
    if product.input_prompt:
        await state.set_state(Buy.user_input)
        await state.update_data(product_id=product.id)
        await show(call, f"📝 {esc(product.input_prompt)}", cancel_kb())
    else:
        order = await create_order(session, user, product, None)
        await show_methods(call, session, order, user)
    await call.answer()


@router.message(Buy.user_input, F.text)
async def buy_input(message: Message, session: AsyncSession, state: FSMContext, user: User):
    product_id = (await state.get_data()).get("product_id")
    product = await session.get(Product, product_id) if product_id else None
    if not product or not product.is_active:
        await state.clear()
        await message.answer("Товар больше недоступен.")
        return
    value = message.text.strip()
    if len(value) > 200:
        await message.answer("Слишком длинно — максимум 200 символов. Попробуйте ещё раз:", reply_markup=cancel_kb())
        return
    await state.clear()
    order = await create_order(session, user, product, value)
    await show_methods(message, session, order, user)


@router.message(Buy.user_input)
async def buy_input_wrong(message: Message):
    await message.answer("Отправьте ответ текстом 👆", reply_markup=cancel_kb())


async def get_own_order(call: CallbackQuery, session: AsyncSession, order_id: int, user: User,
                        need_new: bool = True) -> Order | None:
    order = await session.get(Order, order_id)
    if not order or order.user_id != user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return None
    if need_new and order.status != "new":
        await call.answer(f"Заказ №{order.id}: {ORDER_STATUS.get(order.status, order.status)}", show_alert=True)
        return None
    return order


@router.callback_query(PayCB.filter())
async def choose_method(call: CallbackQuery, callback_data: PayCB, session: AsyncSession, user: User, bot: Bot):
    order = await get_own_order(call, session, callback_data.order_id, user)
    if not order:
        return
    if callback_data.method_id == 0:
        await pay_from_balance(call, session, order, user, bot)
        return
    method = await session.get(PaymentMethod, callback_data.method_id)
    if not method or not method.is_active:
        await call.answer("Этот способ оплаты сейчас недоступен", show_alert=True)
        return
    order.method_id, order.method_title = method.id, method.title
    await session.commit()
    await show(call, requisites_text(order, method), requisites_kb(order, method))
    await call.answer()


async def pay_from_balance(call: CallbackQuery, session: AsyncSession, order: Order, user: User, bot: Bot):
    if order.kind != "product":
        await call.answer("Пополнение нельзя оплатить с баланса", show_alert=True)
        return
    if not await change_balance(session, user.id, -order.amount, f"Оплата заказа №{order.id}"):
        await call.answer(f"❌ Недостаточно средств на балансе.\nБаланс: {money(user.balance)}", show_alert=True)
        return
    result = await session.execute(
        update(Order).where(Order.id == order.id, Order.status == "new")
        .values(status="paid", paid_from_balance=True, method_id=None, method_title="Баланс")
    )
    if result.rowcount != 1:
        await session.rollback()
        await call.answer("Заказ уже обработан", show_alert=True)
        return
    await session.commit()
    await session.refresh(order)

    await show(call, "✅ <b>Заказ оплачен с баланса!</b>\n\n" + user_order_text(order)
               + "\n\nОжидайте выполнения — мы пришлём уведомление 🔔")
    await call.answer()
    await notify_admins(bot, session, "order", order.id, admin_order_text(order, user), admin_order_kb(order))
    await session.commit()


@router.callback_query(OrderCB.filter(F.action == "methods"))
async def back_to_methods(call: CallbackQuery, callback_data: OrderCB, session: AsyncSession, user: User):
    order = await get_own_order(call, session, callback_data.id, user)
    if order:
        await show_methods(call, session, order, user)
        await call.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel"))
async def cancel_order(call: CallbackQuery, callback_data: OrderCB, session: AsyncSession, user: User):
    order = await get_own_order(call, session, callback_data.id, user)
    if not order:
        return
    await session.execute(
        update(Order).where(Order.id == order.id, Order.status == "new").values(status="cancelled")
    )
    await session.commit()
    await show(call, f"🚫 Заказ №{order.id} отменён.", kb([btn("🛒 В каталог", Nav(to="shop"))]))
    await call.answer()


# ---------- мои заказы ----------

async def open_orders(target: Message | CallbackQuery, session: AsyncSession, user: User) -> None:
    orders = list(await session.scalars(
        select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(10)
    ))
    if not orders:
        await render(target, "📦 У вас пока нет заказов.", kb([btn("🛒 Перейти в каталог", Nav(to="shop"))]))
        return
    rows = []
    for o in orders:
        title = o.title if len(o.title) <= 22 else o.title[:21] + "…"
        rows.append([btn(f"{ORDER_ICON.get(o.status, '')} №{o.id} · {title} · {money(o.amount)}",
                         OrderCB(action="view", id=o.id))])
    await render(target, "📦 <b>Мои заказы</b>\n\nПоследние 10 заказов — нажмите, чтобы открыть:", kb(*rows))


@router.callback_query(Nav.filter(F.to == "orders"))
async def nav_orders(call: CallbackQuery, session: AsyncSession, user: User):
    await open_orders(call, session, user)
    await call.answer()


@router.callback_query(OrderCB.filter(F.action == "view"))
async def view_order(call: CallbackQuery, callback_data: OrderCB, session: AsyncSession, user: User):
    order = await get_own_order(call, session, callback_data.id, user, need_new=False)
    if not order:
        return
    if order.status == "new":
        method = await session.get(PaymentMethod, order.method_id) if order.method_id else None
        if method and method.is_active:
            await show(call, requisites_text(order, method), requisites_kb(order, method))
        else:
            await show_methods(call, session, order, user)
    else:
        await show(call, user_order_text(order), kb([btn("⬅️ К заказам", Nav(to="orders"))]))
    await call.answer()
