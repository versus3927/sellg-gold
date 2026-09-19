"""Задания «подпишись на канал — получи награду»."""

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...callbacks import Adm, ATask
from ...db import Task, TaskCompletion
from ...keyboards import btn, cancel_kb, kb
from ...states import AdmTask
from ...utils import esc, money, parse_int, render, show

router = Router(name="admin_tasks")


async def tasks_view(target: Message | CallbackQuery, session: AsyncSession) -> None:
    done = dict((await session.execute(
        select(TaskCompletion.task_id, func.count()).group_by(TaskCompletion.task_id)
    )).all())
    tasks = list(await session.scalars(select(Task).order_by(Task.id)))
    text = ("📋 <b>Задания</b>\n\n"
            "Пользователь подписывается на канал и получает награду на баланс.\n"
            "❗️ Бот должен быть <b>администратором</b> канала, иначе он не сможет проверить подписку.")
    rows = [[btn(f"{'✅' if t.is_active else '⏸'} {t.title} · +{money(t.reward)} · {done.get(t.id, 0)} вып.",
                 ATask(action="view", id=t.id))] for t in tasks]
    rows.append([btn("➕ Добавить задание", ATask(action="add"))])
    rows.append([btn("⬅️ Назад", Adm(to="menu"))])
    await render(target, text, kb(*rows))


@router.callback_query(Adm.filter(F.to == "tasks"))
async def tasks(call: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    await tasks_view(call, session)
    await call.answer()


@router.callback_query(ATask.filter(F.action == "view"))
async def task_view(call: CallbackQuery, callback_data: ATask, session: AsyncSession):
    t = await session.get(Task, callback_data.id)
    if not t:
        await call.answer("Задание не найдено", show_alert=True)
        return
    done = await session.scalar(select(func.count()).select_from(TaskCompletion).where(TaskCompletion.task_id == t.id))
    text = (f"📋 <b>{esc(t.title)}</b>\n\n"
            f"📣 Канал: <code>{esc(t.chat)}</code>\n"
            f"🔗 Ссылка: {esc(t.url)}\n"
            f"💰 Награда: <b>{money(t.reward)}</b>\n"
            f"👥 Выполнили: <b>{done}</b>\n"
            f"Статус: {'✅ активно' if t.is_active else '⏸ выключено'}")
    await show(call, text, kb(
        [btn("⏸ Выключить" if t.is_active else "▶️ Включить", ATask(action="toggle", id=t.id)),
         btn("🗑 Удалить", ATask(action="del", id=t.id))],
        [btn("⬅️ Назад", Adm(to="tasks"))],
    ))
    await call.answer()


@router.callback_query(ATask.filter(F.action == "toggle"))
async def task_toggle(call: CallbackQuery, callback_data: ATask, session: AsyncSession):
    t = await session.get(Task, callback_data.id)
    if t:
        t.is_active = not t.is_active
        await session.commit()
    await task_view(call, callback_data, session)


@router.callback_query(ATask.filter(F.action == "del"))
async def task_delete(call: CallbackQuery, callback_data: ATask, state: FSMContext, session: AsyncSession):
    await session.execute(delete(TaskCompletion).where(TaskCompletion.task_id == callback_data.id))
    await session.execute(delete(Task).where(Task.id == callback_data.id))
    await session.commit()
    await call.answer("🗑 Задание удалено")
    await tasks(call, state, session)


# ---------- добавление: канал → (ссылка) → награда ----------

@router.callback_query(ATask.filter(F.action == "add"))
async def task_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdmTask.chat)
    await call.message.answer(
        "1/2. Отправьте <b>@username</b> канала или его ID (<code>-100…</code>).\n\n"
        "❗️ Сначала добавьте бота в администраторы канала.",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(AdmTask.chat, F.text)
async def task_add_chat(message: Message, state: FSMContext, bot: Bot):
    raw = message.text.strip()
    if raw.startswith("https://t.me/"):
        raw = "@" + raw.removeprefix("https://t.me/").strip("/")
    if not (raw.startswith("@") or raw.lstrip("-").isdigit()):
        await message.answer("Отправьте @username канала или его числовой ID:", reply_markup=cancel_kb())
        return
    try:
        chat = await bot.get_chat(raw)
        me = await bot.me()
        member = await bot.get_chat_member(chat.id, me.id)
    except TelegramAPIError as e:
        await message.answer(f"❌ Не удалось открыть канал: {esc(e)}\n\n"
                             "Проверьте юзернейм и что бот добавлен в канал администратором.",
                             reply_markup=cancel_kb())
        return
    if member.status not in ("administrator", "creator"):
        await message.answer("❌ Бот не администратор этого канала — он не сможет проверять подписку.\n"
                             "Добавьте бота в админы канала и отправьте юзернейм ещё раз.", reply_markup=cancel_kb())
        return

    await state.update_data(chat=f"@{chat.username}" if chat.username else str(chat.id),
                            title=(chat.title or raw)[:128])
    if chat.username:
        await state.update_data(url=f"https://t.me/{chat.username}")
        await state.set_state(AdmTask.reward)
        await message.answer(f"Канал: <b>{esc(chat.title)}</b>\n\n2/2. Какую награду давать за подписку?",
                             reply_markup=cancel_kb())
    else:
        await state.set_state(AdmTask.url)
        await message.answer("Это приватный канал. Отправьте пригласительную ссылку (https://t.me/+...):",
                             reply_markup=cancel_kb())


@router.message(AdmTask.url, F.text)
async def task_add_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("https://t.me/"):
        await message.answer("Ссылка должна начинаться с https://t.me/", reply_markup=cancel_kb())
        return
    await state.update_data(url=url)
    await state.set_state(AdmTask.reward)
    await message.answer("2/2. Какую награду давать за подписку?", reply_markup=cancel_kb())


@router.message(AdmTask.reward, F.text)
async def task_add_reward(message: Message, state: FSMContext, session: AsyncSession):
    reward = parse_int(message.text)
    if reward is None:
        await message.answer("Нужно целое положительное число:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    session.add(Task(title=data["title"], chat=data["chat"], url=data["url"], reward=reward, is_active=True))
    await session.commit()
    await message.answer("✅ Задание добавлено!")
    await tasks_view(message, session)


@router.message(AdmTask.chat)
@router.message(AdmTask.url)
@router.message(AdmTask.reward)
async def task_wrong_input(message: Message):
    await message.answer("⚠️ Отправьте ответ текстом или нажмите «Отмена».", reply_markup=cancel_kb())
