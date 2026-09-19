from aiogram.filters.callback_data import CallbackData


# ---------- пользователь ----------

class Nav(CallbackData, prefix="nav"):
    to: str  # shop | profile | earn | orders | topup | withdraw | promo | bonus | tasks | info | close


class CatCB(CallbackData, prefix="cat"):
    id: int


class ProdCB(CallbackData, prefix="prod"):
    id: int


class BuyCB(CallbackData, prefix="buy"):
    id: int


class PayCB(CallbackData, prefix="pay"):
    order_id: int
    method_id: int  # 0 — оплата с баланса


class OrderCB(CallbackData, prefix="ord"):
    action: str  # view | methods | paid | cancel
    id: int


class TaskCB(CallbackData, prefix="task"):
    id: int


class SubCheck(CallbackData, prefix="subchk"):
    pass


# ---------- админ ----------

class Adm(CallbackData, prefix="adm"):
    to: str


class AOrder(CallbackData, prefix="aord"):
    action: str  # ok | no | rej | back | done | view | user
    id: int
    extra: int = 0


class AOrderList(CallbackData, prefix="aordl"):
    status: str


class ACat(CallbackData, prefix="acat"):
    action: str  # view | add | edit | toggle | del | delok
    id: int = 0
    field: str = ""


class AProd(CallbackData, prefix="aprod"):
    action: str  # view | add | edit | toggle | del | delok
    id: int = 0
    field: str = ""


class AMethod(CallbackData, prefix="ameth"):
    action: str  # view | add | edit | toggle | del | delok
    id: int = 0
    field: str = ""


class AUser(CallbackData, prefix="ausr"):
    action: str  # view | card | add | sub | ban | msg | orders
    id: int


class APromo(CallbackData, prefix="aprm"):
    action: str  # view | add | del
    id: int = 0


class ATask(CallbackData, prefix="atsk"):
    action: str  # view | add | toggle | del
    id: int = 0


class AWd(CallbackData, prefix="awd"):
    action: str  # view | ok | no
    id: int


class ASet(CallbackData, prefix="aset"):
    key: str


class ABcast(CallbackData, prefix="abc"):
    action: str  # send | cancel
    msg_id: int = 0  # сообщение админа, которое копируем пользователям
