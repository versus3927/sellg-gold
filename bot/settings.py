"""Настройки, которые админ меняет прямо из бота. Хранятся в БД, кэшируются в памяти."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Setting

DEFAULT_WELCOME = (
    "👋 Привет, <b>{name}</b>!\n\n"
    "🩶 Добро пожаловать в бот <b>доната и заработка</b>.\n\n"
    "🛒 <b>Донат</b> — пополнение игровой валюты\n"
    "💰 <b>Заработок</b> — приглашай друзей и получай бонусы\n"
    "👤 <b>Профиль</b> — баланс, пополнение и вывод\n\n"
    "Выбери нужный раздел в меню 👇"
)

DEFAULT_INFO = (
    "ℹ️ <b>Информация</b>\n\n"
    "🔹 Оплата — переводом по реквизитам. После перевода нажмите «✅ Я оплатил» "
    "и отправьте скриншот чека.\n"
    "🔹 Администратор проверит оплату и выполнит заказ. Обычно это занимает 5–30 минут.\n"
    "🔹 Статус заказов — в разделе «📦 Мои заказы»."
)

DEFAULT_SUPPORT = (
    "🆘 <b>Поддержка</b>\n\n"
    "Возникли вопросы или проблема с заказом? Напишите администратору — поможем!"
)

DEFAULT_PAY = (
    "❗️ Переведите <b>точную сумму</b> по реквизитам выше.\n"
    "После перевода нажмите «✅ Я оплатил» и отправьте скриншот или PDF чека."
)


@dataclass(frozen=True)
class Opt:
    key: str
    title: str
    kind: str  # text (с форматированием) | line (одна строка) | int | bool | photo
    default: str
    hint: str = ""


OPTIONS: list[Opt] = [
    Opt("welcome_text", "👋 Приветствие", "text", DEFAULT_WELCOME,
        "Можно вставить {name} — подставится имя пользователя."),
    Opt("welcome_photo", "🖼 Картинка приветствия", "photo", "",
        "Отправьте фото. «-» — убрать картинку."),
    Opt("info_text", "ℹ️ Текст «Информация»", "text", DEFAULT_INFO),
    Opt("support_text", "🆘 Текст «Поддержка»", "text", DEFAULT_SUPPORT),
    Opt("support_contact", "👨‍💻 Контакт поддержки", "line", "",
        "Юзернейм без @ — появится кнопка «Написать». «-» — убрать."),
    Opt("channel_url", "📣 Ссылка на канал", "line", "",
        "Ссылка вида https://t.me/... — кнопка «Наш канал». «-» — убрать."),
    Opt("pay_instruction", "🧾 Инструкция при оплате", "text", DEFAULT_PAY),
    Opt("currency", "💱 Валюта", "line", "₽"),
    Opt("min_topup", "💳 Мин. сумма пополнения", "int", "50"),
    Opt("ref_percent", "👥 Реф. процент", "int", "10",
        "Сколько % от оплат приглашённого получает пригласивший. 0 — выкл."),
    Opt("ref_bonus", "🎁 Бонус за приглашённого", "int", "0",
        "Разовое начисление за каждого нового пользователя по ссылке. 0 — выкл."),
    Opt("daily_bonus", "🎰 Ежедневный бонус", "int", "0", "Раз в 24 часа. 0 — выкл."),
    Opt("withdraw_enabled", "💸 Вывод средств", "bool", "1"),
    Opt("min_withdraw", "💸 Мин. сумма вывода", "int", "100"),
    Opt("sub_channel", "🔒 Обязательная подписка", "line", "",
        "@username канала. Бот должен быть админом канала. «-» — выключить."),
    Opt("sub_url", "🔗 Ссылка для подписки", "line", "",
        "Нужна только для приватного канала (пригласительная ссылка). «-» — убрать."),
]

OPT: dict[str, Opt] = {o.key: o for o in OPTIONS}

_values: dict[str, str] = {}


def get(key: str) -> str:
    return _values.get(key, OPT[key].default)


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except ValueError:
        return int(OPT[key].default)


def get_bool(key: str) -> bool:
    return get(key) == "1"


def currency() -> str:
    return get("currency")


async def load(session: AsyncSession) -> None:
    rows = await session.scalars(select(Setting))
    _values.update({row.key: row.value for row in rows})


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.commit()
    _values[key] = value
