from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm, ACat, AProd
from ...db import Category, Product
from ...keyboards import btn, cancel_kb, kb
from ...states import AdmCat, AdmProd
from ...utils import esc, money, parse_int, render, show
from .edit import RENDERERS, parse_value, start_edit

router = Router(name="admin_catalog")


# ---------- категории ----------

@router.callback_query(Adm.filter(F.to == "catalog"))
async def catalog(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    counts = dict((await session.execute(
        select(Product.category_id, func.count()).group_by(Product.category_id)
    )).all())
    cats = list(await session.scalars(select(Category).order_by(Category.sort, Category.id)))
    rows = [[btn(f"{'' if c.is_active else '⏸ '}{c.title} ({counts.get(c.id, 0)})", ACat(action="view", id=c.id))]
            for c in cats]
    rows.append([btn("➕ Добавить категорию", ACat(action="add"))])
    rows.append([btn("⬅️ Назад", Adm(to="menu"))])
    text = ("📦 <b>Каталог</b>\n\nКатегории — это игры или разделы. Внутри них — товары.\n"
            + ("Нажмите на категорию, чтобы управлять ею и товарами:" if cats else "\nКатегорий пока нет — добавьте первую 👇"))
    await show(call, text, kb(*rows))
    await call.answer()


async def category_view(target: Message | CallbackQuery, session: AsyncSession, cat_id: int) -> None:
    cat = await session.get(Category, cat_id)
    if not cat:
        await render(target, "Категория не найдена.", kb([btn("⬅️ К каталогу", Adm(to="catalog"))]))
        return
    products = list(await session.scalars(
        select(Product).where(Product.category_id == cat.id).order_by(Product.sort, Product.price, Product.id)
    ))
    text = (
        f"📁 <b>{esc(cat.title)}</b>\n\n"
        f"📝 Описание: {cat.description or '—'}\n"
        f"🔢 Порядок: {cat.sort}\n"
        f"Статус: {'✅ показывается' if cat.is_active else '⏸ скрыта'}\n\n"
        + (f"Товары ({len(products)}):" if products else "Товаров пока нет.")
    )
    rows = [[btn(f"{'' if p.is_active else '⏸ '}{p.title} — {money(p.price)}", AProd(action="view", id=p.id))]
            for p in products]
    rows += [
        [btn("➕ Добавить товар", AProd(action="add", id=cat.id))],
        [btn("✏️ Название", ACat(action="edit", id=cat.id, field="title")),
         btn("📝 Описание", ACat(action="edit", id=cat.id, field="description"))],
        [btn("🔢 Порядок", ACat(action="edit", id=cat.id, field="sort")),
         btn("⏸ Скрыть" if cat.is_active else "▶️ Показать", ACat(action="toggle", id=cat.id))],
        [btn("🗑 Удалить категорию", ACat(action="del", id=cat.id))],
        [btn("⬅️ К каталогу", Adm(to="catalog"))],
    ]
    await render(target, text, kb(*rows))


async def _category_after_edit(message: Message, session: AsyncSession, obj_id: int) -> None:
    await category_view(message, session, obj_id)

RENDERERS["category"] = _category_after_edit


@router.callback_query(ACat.filter(F.action == "view"))
async def cat_view(call: CallbackQuery, callback_data: ACat, state: FSMContext, session: AsyncSession):
    await state.clear()
    await category_view(call, session, callback_data.id)
    await call.answer()


@router.callback_query(ACat.filter(F.action == "add"))
async def cat_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdmCat.title)
    await call.message.answer("Введите название категории (например: <b>🔫 Standoff 2</b>):", reply_markup=cancel_kb())
    await call.answer()


@router.message(AdmCat.title, F.text)
async def cat_add_title(message: Message, state: FSMContext, session: AsyncSession):
    title = message.text.strip()
    if not title or len(title) > 128:
        await message.answer("Название должно быть от 1 до 128 символов:", reply_markup=cancel_kb())
        return
    await state.clear()
    cat = Category(title=title, description="", is_active=True, sort=0)
    session.add(cat)
    await session.commit()
    await message.answer("✅ Категория создана. Теперь добавьте в неё товары 👇")
    await category_view(message, session, cat.id)


@router.callback_query(ACat.filter(F.action == "edit"))
async def cat_edit(call: CallbackQuery, callback_data: ACat, state: FSMContext):
    await start_edit(call, state, "category", callback_data.id, callback_data.field)


@router.callback_query(ACat.filter(F.action == "toggle"))
async def cat_toggle(call: CallbackQuery, callback_data: ACat, session: AsyncSession):
    cat = await session.get(Category, callback_data.id)
    if cat:
        cat.is_active = not cat.is_active
        await session.commit()
    await category_view(call, session, callback_data.id)
    await call.answer()


@router.callback_query(ACat.filter(F.action == "del"))
async def cat_delete_ask(call: CallbackQuery, callback_data: ACat, session: AsyncSession):
    cat = await session.get(Category, callback_data.id)
    if not cat:
        await call.answer("Категория не найдена", show_alert=True)
        return
    n = await session.scalar(select(func.count()).select_from(Product).where(Product.category_id == cat.id))
    await show(call, f"🗑 Удалить категорию <b>{esc(cat.title)}</b> и все её товары ({n})?\n\n"
                     "Заказы останутся в истории. Если нужно просто убрать из каталога — лучше «Скрыть».",
               kb([btn("✅ Да, удалить", ACat(action="delok", id=cat.id)),
                   btn("❌ Нет", ACat(action="view", id=cat.id))]))
    await call.answer()


@router.callback_query(ACat.filter(F.action == "delok"))
async def cat_delete(call: CallbackQuery, callback_data: ACat, state: FSMContext, session: AsyncSession):
    await session.execute(delete(Product).where(Product.category_id == callback_data.id))
    await session.execute(delete(Category).where(Category.id == callback_data.id))
    await session.commit()
    await call.answer("🗑 Категория удалена")
    await catalog(call, state, session)


# ---------- товары ----------

async def product_view(target: Message | CallbackQuery, session: AsyncSession, product_id: int) -> None:
    p = await session.get(Product, product_id)
    if not p:
        await render(target, "Товар не найден.", kb([btn("⬅️ К каталогу", Adm(to="catalog"))]))
        return
    cat = await session.get(Category, p.category_id)
    text = (
        f"🛍 <b>{esc(p.title)}</b>\n\n"
        f"💵 Цена: <b>{money(p.price)}</b>\n"
        f"📁 Категория: {esc(cat.title) if cat else '—'}\n"
        f"📝 Описание: {p.description or '—'}\n"
        f"❓ Вопрос покупателю: {esc(p.input_prompt) if p.input_prompt else '— (не спрашиваем)'}\n"
        f"🖼 Фото: {'есть' if p.photo else 'нет'}\n"
        f"🔢 Порядок: {p.sort}\n"
        f"Статус: {'✅ в продаже' if p.is_active else '⏸ скрыт'}"
    )
    rows = [
        [btn("✏️ Название", AProd(action="edit", id=p.id, field="title")),
         btn("💵 Цена", AProd(action="edit", id=p.id, field="price"))],
        [btn("📝 Описание", AProd(action="edit", id=p.id, field="description")),
         btn("❓ Вопрос", AProd(action="edit", id=p.id, field="input_prompt"))],
        [btn("🖼 Фото", AProd(action="edit", id=p.id, field="photo")),
         btn("🔢 Порядок", AProd(action="edit", id=p.id, field="sort"))],
        [btn("⏸ Скрыть" if p.is_active else "▶️ Показать", AProd(action="toggle", id=p.id)),
         btn("🗑 Удалить", AProd(action="del", id=p.id))],
        [btn("⬅️ К категории", ACat(action="view", id=p.category_id))],
    ]
    await render(target, text, kb(*rows))


async def _product_after_edit(message: Message, session: AsyncSession, obj_id: int) -> None:
    await product_view(message, session, obj_id)

RENDERERS["product"] = _product_after_edit


@router.callback_query(AProd.filter(F.action == "view"))
async def prod_view(call: CallbackQuery, callback_data: AProd, state: FSMContext, session: AsyncSession):
    await state.clear()
    await product_view(call, session, callback_data.id)
    await call.answer()


@router.callback_query(AProd.filter(F.action == "edit"))
async def prod_edit(call: CallbackQuery, callback_data: AProd, state: FSMContext):
    await start_edit(call, state, "product", callback_data.id, callback_data.field)


@router.callback_query(AProd.filter(F.action == "toggle"))
async def prod_toggle(call: CallbackQuery, callback_data: AProd, session: AsyncSession):
    p = await session.get(Product, callback_data.id)
    if p:
        p.is_active = not p.is_active
        await session.commit()
    await product_view(call, session, callback_data.id)
    await call.answer()


@router.callback_query(AProd.filter(F.action == "del"))
async def prod_delete_ask(call: CallbackQuery, callback_data: AProd, session: AsyncSession):
    p = await session.get(Product, callback_data.id)
    if not p:
        await call.answer("Товар не найден", show_alert=True)
        return
    await show(call, f"🗑 Удалить товар <b>{esc(p.title)}</b>?",
               kb([btn("✅ Да, удалить", AProd(action="delok", id=p.id)),
                   btn("❌ Нет", AProd(action="view", id=p.id))]))
    await call.answer()


@router.callback_query(AProd.filter(F.action == "delok"))
async def prod_delete(call: CallbackQuery, callback_data: AProd, session: AsyncSession):
    p = await session.get(Product, callback_data.id)
    if not p:
        await call.answer("Товар уже удалён", show_alert=True)
        return
    cat_id = p.category_id
    await session.delete(p)
    await session.commit()
    await call.answer("🗑 Товар удалён")
    await category_view(call, session, cat_id)


# ---------- добавление товара: название → цена → описание → вопрос → фото ----------

@router.callback_query(AProd.filter(F.action == "add"))
async def prod_add(call: CallbackQuery, callback_data: AProd, state: FSMContext):
    await state.set_state(AdmProd.title)
    await state.update_data(cat_id=callback_data.id)
    await call.message.answer("1/5. Введите название товара (например: <b>100 голды</b>):", reply_markup=cancel_kb())
    await call.answer()


@router.message(AdmProd.title, F.text)
async def prod_add_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title or len(title) > 128:
        await message.answer("Название должно быть от 1 до 128 символов:", reply_markup=cancel_kb())
        return
    await state.update_data(title=title)
    await state.set_state(AdmProd.price)
    await message.answer("2/5. Введите цену (целое число):", reply_markup=cancel_kb())


@router.message(AdmProd.price, F.text)
async def prod_add_price(message: Message, state: FSMContext):
    price = parse_int(message.text)
    if price is None:
        await message.answer("Нужно целое положительное число:", reply_markup=cancel_kb())
        return
    await state.update_data(price=price)
    await state.set_state(AdmProd.description)
    await message.answer("3/5. Отправьте описание товара (можно с форматированием) или «-» — без описания:",
                         reply_markup=cancel_kb())


@router.message(AdmProd.description, F.text)
async def prod_add_description(message: Message, state: FSMContext):
    try:
        description = parse_value("html", "description", 3000, message)
    except ValueError as e:
        await message.answer(f"⚠️ {e}", reply_markup=cancel_kb())
        return
    await state.update_data(description=description)
    await state.set_state(AdmProd.input_prompt)
    await message.answer(
        "4/5. Что спросить у покупателя перед оплатой?\n"
        "Например: <i>Введите ваш ID в игре</i> или <i>Укажите ник и сервер</i>.\n\n"
        "«-» — ничего не спрашивать.",
        reply_markup=cancel_kb(),
    )


@router.message(AdmProd.input_prompt, F.text)
async def prod_add_prompt(message: Message, state: FSMContext):
    text = message.text.strip()
    if len(text) > 300:
        await message.answer("Максимум 300 символов:", reply_markup=cancel_kb())
        return
    await state.update_data(input_prompt=None if text == "-" else text)
    await state.set_state(AdmProd.photo)
    await message.answer("5/5. Отправьте фото товара или «-» — без фото:", reply_markup=cancel_kb())


@router.message(AdmProd.photo, F.photo | (F.text == "-"))
async def prod_add_photo(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    await state.clear()
    product = Product(
        category_id=data["cat_id"], title=data["title"], price=data["price"],
        description=data["description"], input_prompt=data["input_prompt"],
        photo=message.photo[-1].file_id if message.photo else None, is_active=True, sort=0,
    )
    session.add(product)
    await session.commit()
    await message.answer("✅ Товар добавлен в каталог!")
    await product_view(message, session, product.id)


@router.message(AdmProd.title)
@router.message(AdmProd.price)
@router.message(AdmProd.description)
@router.message(AdmProd.input_prompt)
@router.message(AdmProd.photo)
@router.message(AdmCat.title)
async def catalog_wrong_input(message: Message):
    await message.answer("⚠️ Не то, что нужно. Ответьте на вопрос выше или нажмите «Отмена».",
                         reply_markup=cancel_kb())
