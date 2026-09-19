"""Универсальное редактирование одного поля категории / товара / способа оплаты."""

from typing import Awaitable, Callable

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import Category, PaymentMethod, Product
from ...keyboards import cancel_kb
from ...states import AdmEdit
from ...utils import parse_int

router = Router(name="admin_edit")

MODELS = {"category": Category, "product": Product, "method": PaymentMethod}

SORT_PROMPT = "Введите порядковый номер (чем меньше число — тем выше в списке):"

# (сущность, поле) -> (подсказка, тип значения, макс. длина)
FIELDS: dict[tuple[str, str], tuple[str, str, int]] = {
    ("category", "title"): ("Введите новое название категории:", "line", 128),
    ("category", "description"): ("Отправьте описание категории — форматирование сохранится.\n"
                                  "«-» — убрать описание.", "html", 2000),
    ("category", "sort"): (SORT_PROMPT, "int", 0),
    ("product", "title"): ("Введите новое название товара:", "line", 128),
    ("product", "price"): ("Введите новую цену (целое число):", "price", 0),
    ("product", "description"): ("Отправьте описание товара — форматирование сохранится.\n"
                                 "«-» — убрать описание.", "html", 3000),
    ("product", "input_prompt"): ("Что спросить у покупателя перед оплатой?\n"
                                  "Например: «Введите ваш ID в игре».\n«-» — ничего не спрашивать.", "optline", 300),
    ("product", "photo"): ("Отправьте фото товара.\n«-» — убрать фото.", "photo", 0),
    ("product", "sort"): (SORT_PROMPT, "int", 0),
    ("method", "title"): ("Введите название способа оплаты (например: 💳 Сбербанк):", "line", 128),
    ("method", "requisites"): ("Введите реквизиты — номер карты, телефона или кошелька:", "line", 256),
    ("method", "holder"): ("Введите получателя (например: Максим Ш.).\n«-» — убрать.", "optline", 128),
    ("method", "note"): ("Введите примечание для покупателя (например: «Комментарий к переводу не писать»).\n"
                         "«-» — убрать.", "optline", 500),
    ("method", "sort"): (SORT_PROMPT, "int", 0),
}

NULLABLE = {"input_prompt", "photo"}

# После сохранения показываем обновлённую карточку: модули каталога/реквизитов регистрируют сюда свои функции
Renderer = Callable[[Message, AsyncSession, int], Awaitable[None]]
RENDERERS: dict[str, Renderer] = {}


async def start_edit(call: CallbackQuery, state: FSMContext, entity: str, obj_id: int, field: str) -> None:
    if (entity, field) not in FIELDS:
        await call.answer("Это поле нельзя изменить", show_alert=True)
        return
    await state.set_state(AdmEdit.value)
    await state.update_data(entity=entity, obj_id=obj_id, field=field)
    await call.message.answer(f"✏️ {FIELDS[(entity, field)][0]}", reply_markup=cancel_kb())
    await call.answer()


def parse_value(kind: str, field: str, max_len: int, message: Message):
    """Возвращает новое значение поля или бросает ValueError с понятным текстом."""
    text = (message.text or "").strip()
    empty = None if field in NULLABLE else ""

    if kind == "photo":
        if message.photo:
            return message.photo[-1].file_id
        if text == "-":
            return empty
        raise ValueError("Отправьте фото или «-»")

    if not message.text:
        raise ValueError("Отправьте значение текстом")
    if kind == "int":
        if not text.lstrip("-").isdigit() or len(text) > 6:
            raise ValueError("Нужно целое число")
        return int(text)
    if kind == "price":
        value = parse_int(text)
        if value is None:
            raise ValueError("Нужно целое положительное число")
        return value
    if text == "-" and kind in ("html", "optline"):
        return empty
    value = message.html_text if kind == "html" else text
    if not value:
        raise ValueError("Значение не может быть пустым")
    if len(value) > max_len:
        raise ValueError(f"Слишком длинно — максимум {max_len} символов")
    return value


@router.message(AdmEdit.value)
async def edit_value(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    entity, obj_id, field = data["entity"], data["obj_id"], data["field"]
    _, kind, max_len = FIELDS[(entity, field)]
    try:
        value = parse_value(kind, field, max_len, message)
    except ValueError as e:
        await message.answer(f"⚠️ {e}", reply_markup=cancel_kb())
        return
    await state.clear()
    obj = await session.get(MODELS[entity], obj_id)
    if not obj:
        await message.answer("Не найдено — возможно, уже удалено.")
        return
    setattr(obj, field, value)
    await session.commit()
    await message.answer("✅ Сохранено")
    await RENDERERS[entity](message, session, obj_id)
