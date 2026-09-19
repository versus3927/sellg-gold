from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from ...utils import is_admin
from . import broadcast, catalog, edit, methods, options, orders, panel, promo, tasks, users, withdrawals


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and is_admin(event.from_user.id)


router = Router(name="admin")
router.message.filter(IsAdmin(), F.chat.type == "private")
router.callback_query.filter(IsAdmin())
router.include_routers(
    panel.router,
    orders.router,
    withdrawals.router,
    catalog.router,
    methods.router,
    edit.router,
    users.router,
    broadcast.router,
    promo.router,
    tasks.router,
    options.router,
)
