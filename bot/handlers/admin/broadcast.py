import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update

from ... import db
from ...callbacks import ABcast, Adm
from ...db import User
from ...keyboards import btn, kb
from ...states import AdmBroadcast
from ...utils import show

log = logging.getLogger(__name__)

router = Router(name="admin_broadcast")

_running: set[asyncio.Task] = set()
_started: set[tuple[int, int]] = set()

RECIPIENTS = (User.is_banned.is_(False), User.is_blocked.is_(False))


@router.callback_query(Adm.filter(F.to == "broadcast"))
async def bc_start(call: CallbackQuery, state: FSMContext, session):
    total = await session.scalar(select(func.count()).select_from(User).where(*RECIPIENTS))
    await state.set_state(AdmBroadcast.message)
    await show(call, f"📢 <b>Рассылка</b>\n\nПолучателей: <b>{total}</b>\n\n"
                     "Отправьте сообщение для рассылки — текст, фото, видео, что угодно.\n"
                     "Форматирование и вложения сохранятся.",
               kb([btn("❌ Отмена", Adm(to="menu"))]))
    await call.answer()


@router.message(AdmBroadcast.message)
async def bc_preview(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👆 Так сообщение увидят пользователи. Запускаем рассылку?",
        reply_markup=kb([btn("✅ Отправить всем", ABcast(action="send", msg_id=message.message_id))],
                        [btn("❌ Отмена", ABcast(action="cancel"))]),
    )


@router.callback_query(ABcast.filter(F.action == "cancel"))
async def bc_cancel(call: CallbackQuery):
    await call.message.edit_text("Рассылка отменена.")
    await call.answer()


@router.callback_query(ABcast.filter(F.action == "send"))
async def bc_send(call: CallbackQuery, callback_data: ABcast, session, bot: Bot):
    key = (call.message.chat.id, callback_data.msg_id)
    if key in _started:  # защита от двойного нажатия
        await call.answer("Эта рассылка уже запущена")
        return
    _started.add(key)
    user_ids = list(await session.scalars(select(User.id).where(*RECIPIENTS)))
    status = await call.message.edit_text(f"⏳ Рассылка запущена: 0 / {len(user_ids)}")
    await call.answer()
    task = asyncio.create_task(run_broadcast(bot, call.message.chat.id, callback_data.msg_id,
                                             user_ids, status.message_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def run_broadcast(bot: Bot, chat_id: int, msg_id: int, user_ids: list[int], status_id: int) -> None:
    try:
        await _broadcast(bot, chat_id, msg_id, user_ids, status_id)
    except Exception:
        log.exception("Рассылка упала")
        await bot.send_message(chat_id, "❌ Рассылка прервалась из-за ошибки — подробности в логах.")


async def _broadcast(bot: Bot, chat_id: int, msg_id: int, user_ids: list[int], status_id: int) -> None:
    sent = failed = 0
    blocked: list[int] = []
    for i, uid in enumerate(user_ids, 1):
        for _attempt in range(3):
            try:
                await bot.copy_message(uid, chat_id, msg_id)
                sent += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                continue
            except TelegramForbiddenError:
                failed += 1
                blocked.append(uid)
            except TelegramAPIError:
                failed += 1
            break
        else:  # три раза подряд упёрлись в лимит
            failed += 1
        await asyncio.sleep(0.05)  # ~20 сообщений в секунду — в пределах лимитов Telegram
        if i % 100 == 0:
            try:
                await bot.edit_message_text(f"⏳ Рассылка: {i} / {len(user_ids)}", chat_id=chat_id, message_id=status_id)
            except TelegramAPIError:
                pass

    if blocked:
        async with db.session_maker() as session:
            for start in range(0, len(blocked), 500):
                chunk = blocked[start:start + 500]
                await session.execute(update(User).where(User.id.in_(chunk)).values(is_blocked=True))
            await session.commit()

    text = f"✅ <b>Рассылка завершена</b>\n\nДоставлено: <b>{sent}</b>\nНе доставлено: <b>{failed}</b>"
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=status_id)
    except TelegramAPIError:
        await bot.send_message(chat_id, text)
    log.info("Рассылка: доставлено %s, ошибок %s", sent, failed)
