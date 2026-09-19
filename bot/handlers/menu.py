"""Главное меню. Роутер подключается первым: кнопки меню прерывают любой ввод (FSM)."""

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..callbacks import Nav, SubCheck
from ..db import User
from ..keyboards import (
    BTN_EARN,
    BTN_INFO,
    BTN_ORDERS,
    BTN_PROFILE,
    BTN_SHOP,
    BTN_SUPPORT,
    main_kb,
    support_kb,
)
from ..utils import esc, is_admin
from .earn import open_earn
from .profile import open_profile
from .shop import open_orders, open_shop

router = Router(name="menu")
router.message.filter(F.chat.type == "private")


async def send_welcome(message: Message, user: User) -> None:
    first_name = user.full_name.split()[0] if user.full_name else "друг"
    text = settings.get("welcome_text").replace("{name}", esc(first_name))
    markup = main_kb(is_admin(user.id))
    photo = settings.get("welcome_photo")
    if photo and len(text) <= 1024:
        try:
            await message.answer_photo(photo, caption=text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass  # картинка устарела/удалена — покажем текст
    await message.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: User):
    await state.clear()
    await send_welcome(message, user)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user: User):
    await state.clear()
    await message.answer("Действие отменено 👌", reply_markup=main_kb(is_admin(user.id)))


@router.message(F.text == BTN_SHOP)
async def menu_shop(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await open_shop(message, session)


@router.message(F.text == BTN_EARN)
async def menu_earn(message: Message, state: FSMContext, session: AsyncSession, user: User, bot: Bot):
    await state.clear()
    await open_earn(message, session, user, bot)


@router.message(F.text == BTN_PROFILE)
async def menu_profile(message: Message, state: FSMContext, session: AsyncSession, user: User):
    await state.clear()
    await open_profile(message, session, user)


@router.message(F.text == BTN_ORDERS)
async def menu_orders(message: Message, state: FSMContext, session: AsyncSession, user: User):
    await state.clear()
    await open_orders(message, session, user)


@router.message(F.text == BTN_INFO)
async def menu_info(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(settings.get("info_text"), reply_markup=support_kb())


@router.message(F.text == BTN_SUPPORT)
async def menu_support(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(settings.get("support_text"), reply_markup=support_kb())


@router.callback_query(Nav.filter(F.to == "cancel"))
async def nav_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Отменено")
    with suppress(TelegramBadRequest):
        await call.message.delete()


@router.callback_query(Nav.filter(F.to == "close"))
async def nav_close(call: CallbackQuery):
    await call.answer()
    with suppress(TelegramBadRequest):
        await call.message.delete()


@router.callback_query(SubCheck.filter())
async def sub_checked(call: CallbackQuery, user: User):
    # Сюда попадаем, только если SubscriptionMiddleware убедился, что подписка есть
    await call.answer("✅ Спасибо за подписку!")
    with suppress(TelegramBadRequest):
        await call.message.delete()
    await send_welcome(call.message, user)
