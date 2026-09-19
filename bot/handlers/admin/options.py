"""Настройки бота: тексты, бонусы, лимиты, обязательная подписка."""

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ... import settings
from ...callbacks import Adm, ASet
from ...keyboards import btn, cancel_kb, kb
from ...states import AdmSetting
from ...utils import esc, render, show

router = Router(name="admin_options")


def preview(opt: settings.Opt) -> str:
    value = settings.get(opt.key)
    if opt.kind == "bool":
        return "✅" if value == "1" else "❌"
    if opt.kind == "photo":
        return "есть" if value else "нет"
    if opt.kind == "text":
        return "✏️"
    if not value:
        return "—"
    return value if len(value) <= 18 else value[:17] + "…"


async def settings_view(target: Message | CallbackQuery) -> None:
    rows = [[btn(f"{opt.title}: {preview(opt)}", ASet(key=opt.key))] for opt in settings.OPTIONS]
    rows.append([btn("⬅️ Назад", Adm(to="menu"))])
    await render(target, "⚙️ <b>Настройки</b>\n\nНажмите на пункт, чтобы изменить:", kb(*rows))


@router.callback_query(Adm.filter(F.to == "settings"))
async def settings_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await settings_view(call)
    await call.answer()


@router.callback_query(ASet.filter())
async def setting_open(call: CallbackQuery, callback_data: ASet, state: FSMContext, session: AsyncSession):
    opt = settings.OPT.get(callback_data.key)
    if not opt:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    if opt.kind == "bool":
        await settings.set_value(session, opt.key, "0" if settings.get_bool(opt.key) else "1")
        await settings_view(call)
        await call.answer("Сохранено")
        return

    value = settings.get(opt.key)
    if opt.kind == "text":
        current = f"Сейчас:\n\n{value}"
    elif opt.kind == "photo":
        current = "Сейчас: " + ("картинка есть" if value else "картинки нет")
    else:
        current = f"Сейчас: <code>{esc(value) or '—'}</code>"
    hint = f"\n\nℹ️ {esc(opt.hint)}" if opt.hint else ""
    what = {"text": "новый текст (форматирование сохранится)", "photo": "картинку",
            "int": "число", "line": "новое значение"}[opt.kind]

    await state.set_state(AdmSetting.value)
    await state.update_data(key=opt.key)
    await show(call, f"<b>{opt.title}</b>\n\n{current}{hint}\n\n👉 Отправьте {what}.",
               kb([btn("⬅️ Назад", Adm(to="settings"))]))
    await call.answer()


async def validate(opt: settings.Opt, message: Message, bot: Bot) -> str:
    """Проверяет ввод и возвращает значение для сохранения; ValueError — с текстом ошибки."""
    text = (message.text or "").strip()
    if opt.kind == "photo":
        if message.photo:
            return message.photo[-1].file_id
        if text == "-":
            return ""
        raise ValueError("Отправьте картинку (как фото) или «-», чтобы убрать")
    if not message.text:
        raise ValueError("Отправьте значение текстом")
    if opt.kind == "text":
        if len(message.html_text) > 3500:
            raise ValueError("Слишком длинный текст")
        return message.html_text
    if opt.kind == "int":
        if not text.isdigit() or len(text) > 9:
            raise ValueError("Нужно целое число (0 или больше)")
        if opt.key == "ref_percent" and int(text) > 100:
            raise ValueError("Процент — от 0 до 100")
        return str(int(text))

    # line
    if text == "-":
        return ""
    if opt.key == "currency" and len(text) > 8:
        raise ValueError("Слишком длинно — до 8 символов (например: ₽, руб., G)")
    if opt.key == "support_contact":
        return text.removeprefix("https://t.me/").lstrip("@")
    if opt.key == "channel_url" and not text.startswith(("https://", "http://")):
        raise ValueError("Нужна ссылка, начинающаяся с https://")
    if opt.key == "sub_url" and not text.startswith("https://t.me/"):
        raise ValueError("Нужна ссылка, начинающаяся с https://t.me/")
    if opt.key == "sub_channel":
        channel = "@" + text.removeprefix("https://t.me/").lstrip("@") if not text.lstrip("-").isdigit() else text
        try:
            me = await bot.me()
            member = await bot.get_chat_member(channel, me.id)
        except TelegramAPIError as e:
            raise ValueError(f"Не удалось открыть канал: {e}. Проверьте юзернейм и что бот — админ канала")
        if member.status not in ("administrator", "creator"):
            raise ValueError("Бот не администратор этого канала — добавьте его в админы и повторите")
        return channel
    return text[:256]


@router.message(AdmSetting.value)
async def setting_save(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    opt = settings.OPT.get((await state.get_data()).get("key"))
    if not opt:
        await state.clear()
        return
    try:
        value = await validate(opt, message, bot)
    except ValueError as e:
        await message.answer(f"⚠️ {esc(e)}", reply_markup=cancel_kb())
        return
    await state.clear()
    await settings.set_value(session, opt.key, value)
    await message.answer("✅ Сохранено")
    await settings_view(message)
