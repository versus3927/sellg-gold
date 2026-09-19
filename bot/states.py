from aiogram.fsm.state import State, StatesGroup


class Buy(StatesGroup):
    user_input = State()  # данные для заказа (ID в игре и т.п.)


class Pay(StatesGroup):
    receipt = State()  # ждём чек


class Topup(StatesGroup):
    amount = State()


class Withdraw(StatesGroup):
    amount = State()
    details = State()


class PromoInput(StatesGroup):
    code = State()


# ---------- админ ----------

class AdmEdit(StatesGroup):
    """Универсальное редактирование одного поля: данные лежат в FSM (entity, id, field)."""
    value = State()


class AdmCat(StatesGroup):
    title = State()


class AdmProd(StatesGroup):
    title = State()
    price = State()
    description = State()
    input_prompt = State()
    photo = State()


class AdmMethod(StatesGroup):
    title = State()
    requisites = State()
    holder = State()


class AdmUser(StatesGroup):
    search = State()
    amount = State()
    message = State()


class AdmOrderSearch(StatesGroup):
    number = State()


class AdmBroadcast(StatesGroup):
    message = State()
    confirm = State()


class AdmPromo(StatesGroup):
    code = State()
    amount = State()
    uses = State()


class AdmTask(StatesGroup):
    chat = State()
    url = State()
    reward = State()


class AdmSetting(StatesGroup):
    value = State()
