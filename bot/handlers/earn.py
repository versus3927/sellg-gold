import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..callbacks import Nav, TaskCB
from ..db import Task, TaskCompletion, User, change_balance, utcnow
from ..keyboards import btn, earn_kb, kb
from ..utils import esc, money, render, show

log = logging.getLogger(__name__)

router = Router(name="earn")
router.message.filter(F.chat.type == "private")

MEMBER_STATUSES = {"creator", "administrator", "member"}


async def available_tasks(session: AsyncSession, user_id: int) -> list[Task]:
    done = select(TaskCompletion.task_id).where(TaskCompletion.user_id == user_id)
    return list(await session.scalars(
        select(Task).where(Task.is_active, Task.id.not_in(done)).order_by(Task.id)
    ))


async def open_earn(target: Message | CallbackQuery, session: AsyncSession, user: User, bot: Bot) -> None:
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=r{user.id}"
    refs = await session.scalar(select(func.count()).select_from(User).where(User.referrer_id == user.id))
    tasks_count = len(await available_tasks(session, user.id))
    percent, bonus, daily = (settings.get_int(k) for k in ("ref_percent", "ref_bonus", "daily_bonus"))

    lines = ["💰 <b>Заработок</b>", ""]
    if percent > 0 or bonus > 0:
        lines.append("👥 Приглашайте друзей по своей ссылке и получайте:")
        if percent > 0:
            lines.append(f"• <b>{percent}%</b> с каждой их оплаты")
        if bonus > 0:
            lines.append(f"• <b>{money(bonus)}</b> за каждого нового друга")
    else:
        lines.append("👥 Приглашайте друзей по своей ссылке!")
    lines += [
        "",
        "🔗 Ваша ссылка:",
        f"<code>{link}</code>",
        "",
        f"👥 Приглашено: <b>{refs}</b>",
        f"💸 Заработано с друзей: <b>{money(user.ref_earned)}</b>",
        f"💰 Баланс: <b>{money(user.balance)}</b>",
    ]
    if daily > 0:
        lines += ["", f"🎰 Ежедневный бонус — <b>{money(daily)}</b> каждые 24 часа"]
    if tasks_count:
        lines.append(f"📋 Доступно заданий: <b>{tasks_count}</b>")
    await render(target, "\n".join(lines), earn_kb(link, tasks_count))


@router.callback_query(Nav.filter(F.to == "earn"))
async def nav_earn(call: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    await open_earn(call, session, user, bot)
    await call.answer()


@router.callback_query(Nav.filter(F.to == "bonus"))
async def daily_bonus(call: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    amount = settings.get_int("daily_bonus")
    if amount <= 0:
        await call.answer("Ежедневный бонус сейчас недоступен", show_alert=True)
        return
    now = utcnow()
    result = await session.execute(
        update(User)
        .where(User.id == user.id, or_(User.last_bonus_at.is_(None), User.last_bonus_at <= now - timedelta(hours=24)))
        .values(last_bonus_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        await session.refresh(user)
        left = int((user.last_bonus_at + timedelta(hours=24) - now).total_seconds()) // 60
        await call.answer(f"⏳ Следующий бонус через {left // 60} ч {left % 60} мин", show_alert=True)
        return
    await change_balance(session, user.id, amount, "Ежедневный бонус")
    await session.commit()
    await session.refresh(user)
    await call.answer(f"🎁 Вы получили {money(amount)}!", show_alert=True)
    await open_earn(call, session, user, bot)


# ---------- задания: подписки на каналы ----------

async def open_tasks(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    tasks = await available_tasks(session, user.id)
    back = [btn("⬅️ Назад", Nav(to="earn"))]
    if not tasks:
        await show(call, "📋 Новых заданий пока нет — загляните позже!", kb(back))
        return
    lines = ["📋 <b>Задания</b>", "",
             "Подпишитесь на канал и нажмите «✅ Проверить» — награда сразу придёт на баланс.", ""]
    rows = []
    for task in tasks:
        lines.append(f"• {esc(task.title)} — <b>+{money(task.reward)}</b>")
        rows.append([InlineKeyboardButton(text=f"➡️ {task.title}", url=task.url),
                     btn("✅ Проверить", TaskCB(id=task.id))])
    rows.append(back)
    await show(call, "\n".join(lines), kb(*rows))


@router.callback_query(Nav.filter(F.to == "tasks"))
async def nav_tasks(call: CallbackQuery, session: AsyncSession, user: User):
    await open_tasks(call, session, user)
    await call.answer()


@router.callback_query(TaskCB.filter())
async def check_task(call: CallbackQuery, callback_data: TaskCB, session: AsyncSession, user: User, bot: Bot):
    task = await session.get(Task, callback_data.id)
    if not task or not task.is_active:
        await call.answer("Задание больше недоступно", show_alert=True)
        return
    try:
        member = await bot.get_chat_member(task.chat, user.id)
    except TelegramAPIError as e:
        log.warning("Проверка задания %s (%s) не удалась: %s", task.id, task.chat, e)
        await call.answer("⚠️ Не получилось проверить подписку. Сообщите администратору.", show_alert=True)
        return
    if member.status not in MEMBER_STATUSES and not getattr(member, "is_member", False):
        await call.answer("❌ Вы ещё не подписались на канал", show_alert=True)
        return

    session.add(TaskCompletion(task_id=task.id, user_id=user.id))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        await call.answer("Вы уже выполнили это задание", show_alert=True)
        return
    await change_balance(session, user.id, task.reward, f"Задание: {task.title}")
    await session.commit()
    await call.answer(f"✅ Задание выполнено! +{money(task.reward)}", show_alert=True)
    await open_tasks(call, session, user)
