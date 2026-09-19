from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from ..db import User
from ..keyboards import main_kb
from ..utils import is_admin

router = Router(name="fallback")


@router.message(F.chat.type == "private", StateFilter(None))
async def unknown_message(message: Message, user: User):
    await message.answer("🤔 Не понял вас. Воспользуйтесь кнопками меню 👇", reply_markup=main_kb(is_admin(user.id)))


@router.callback_query()
async def unknown_callback(call: CallbackQuery):
    await call.answer("Кнопка устарела — откройте раздел заново через меню", show_alert=True)
