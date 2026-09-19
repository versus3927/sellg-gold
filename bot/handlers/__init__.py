from aiogram import Router

from . import earn, fallback, menu, payment, profile, shop
from .admin import router as admin_router


def build_router() -> Router:
    root = Router(name="root")
    root.include_routers(
        menu.router,       # кнопки главного меню — первыми, чтобы прерывать любой ввод
        admin_router,
        payment.router,
        shop.router,
        profile.router,
        earn.router,
        fallback.router,   # всё непонятое — последним
    )
    return root
