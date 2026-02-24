"""
Экономические команды бота YandexPticaGPT v0.5
/refill, обработка налога Технолога
"""

import asyncio
import logging
import time

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import config
from db import db
from utils import delete_message_after, format_message_with_username, resolve_recipient_from_message
from middlewares import set_command_cooldown
from services.balance import balance_service

# Создаем роутер для экономических команд
router = Router()

logger = logging.getLogger(__name__)


def format_time_remaining(seconds: int) -> str:
    """
    Форматирование оставшегося времени в читаемый вид
    
    Args:
        seconds: Количество секунд
        
    Returns:
        Отформатированная строка времени
    """
    if seconds <= 0:
        return "0 сек"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0:
        parts.append(f"{minutes} мин")
    if secs > 0 or not parts:
        parts.append(f"{secs} сек")
    
    return " ".join(parts)


@router.message(Command("refill"))
async def cmd_refill(message: Message):
    """
    Команда /refill
    Пополнение баланса на 100 коинов
    КД: 2 часа
    При КД: norefill.jpg + текст реального отсчёта
    Может быть сброшен кодом #PADLOPLAY
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    
    # Проверяем cooldown для /refill
    last_used = await db.get_cooldown(user_id, "/refill")
    now = int(time.time())
    cooldown_seconds = config.REFILL_COOLDOWN
    
    if last_used:
        time_passed = now - last_used
        
        if time_passed < cooldown_seconds:
            # Cooldown еще активен
            remaining = cooldown_seconds - time_passed
            time_str = format_time_remaining(remaining)
            
            # Отправляем фото norefill.jpg с подписью
            photo_path = config.get_image_path("norefill.jpg")
            caption = format_message_with_username(
                f"Приходи через {time_str} ⏳",
                username, first_name
            )
            
            try:
                if photo_path.exists():
                    photo = FSInputFile(str(photo_path))
                    sent_message = await message.answer_photo(
                        photo=photo,
                        caption=caption
                    )
                else:
                    sent_message = await message.answer(caption)
                    logger.warning(f"Фото norefill.jpg не найдено для пользователя {user_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки /refill (cooldown) для {user_id}: {e}")
                sent_message = await message.answer(caption)
            
            # Автоудаление через 30 секунд
            asyncio.create_task(delete_message_after(sent_message))
            
            logger.info(f"Пользователь {user_id} попытался использовать /refill (cooldown {remaining} сек)")
            return
    
    # Cooldown прошел или его нет - начисляем коины
    refill_amount = config.REFILL_AMOUNT
    
    # Начисляем баланс через сервис (автоматически отправляет уведомление на 5 сек)
    success, balance_before, balance_after = await balance_service.add_balance(
        user_id=user_id,
        amount=refill_amount,
        command_source="/refill",
        comment="Пополнение баланса",
        message=message,
        username=username,
        first_name=first_name
    )
    
    if not success:
        error_msg = await message.answer(
            format_message_with_username(
                "Ошибка при пополнении баланса. Попробуй позже.",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(error_msg))
        logger.error(f"Ошибка начисления баланса для пользователя {user_id}")
        return
    
    # Устанавливаем cooldown
    await set_command_cooldown(user_id, "/refill")
    
    # Отправляем фото refill.jpg с подписью
    photo_path = config.get_image_path("refill.jpg")
    caption = format_message_with_username(
        f"+{refill_amount} коинов 💰\n"
        f"Твой баланс: <b>{balance_after}</b> коинов",
        username, first_name
    )
    
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await message.answer_photo(
                photo=photo,
                caption=caption
            )
        else:
            sent_message = await message.answer(caption)
            logger.warning(f"Фото refill.jpg не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки /refill для {user_id}: {e}")
        sent_message = await message.answer(caption)
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    logger.info(f"Пользователь {user_id} использовал /refill (+{refill_amount} коинов, баланс: {balance_after})")


@router.message(Command("pererozhd"))
async def cmd_pererozhd(message: Message):
    """
    Перерождение: сброс баланса на 0, +0.5x к удаче за каждое.
    Первое — 1M коинов, каждое следующее в 2 раза дороже.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
    cost = await db.get_rebirth_cost(user_id)
    count = await db.get_rebirth_count(user_id)
    balance = await db.get_balance(user_id)

    if balance < cost:
        text = format_message_with_username(
            f"🔄 <b>Перерождение</b>\n\n"
            f"Следующее перерождение стоит <b>{cost:,}</b> коинов.\n"
            f"У тебя: <b>{balance:,}</b>.\n\n"
            f"Перерождений: <b>{count}</b> (+0.5x удачи за каждое).",
            username, first_name
        )
        sent = await message.answer(text)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    ok, new_count, err = await db.do_rebirth(user_id)
    if not ok:
        sent = await message.answer(format_message_with_username(err, username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    luck_bonus = 1.0 + new_count * 0.5
    text = format_message_with_username(
        f"🔄 <b>Перерождение #{new_count}</b>\n\n"
        f"Баланс обнулён. Бонус удачи: <b>+{(new_count * 0.5):.1f}x</b> (итого x{luck_bonus:.1f}).\n\n"
        f"Следующее перерождение: <b>{await db.get_rebirth_cost(user_id):,}</b> коинов.",
        username, first_name
    )
    if new_count == 1:
        await db.unlock_achievement(user_id, "rebirth_first")
        text += "\n\n🔄 <b>Достижение:</b> Первое перерождение!"
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("pererozhd: user_id=%s rebirth_count=%s", user_id, new_count)


@router.callback_query(F.data.startswith("pay_tax_"))
async def callback_pay_tax(callback: CallbackQuery):
    """
    Обработчик callback для оплаты налога Технолога
    Формат callback_data: pay_tax_{user_id}
    """
    # Проверяем, что callback от правильного пользователя
    callback_user_id = callback.from_user.id
    callback_data = callback.data
    
    # Извлекаем user_id из callback_data
    try:
        tax_user_id = int(callback_data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка обработки запроса", show_alert=True)
        return
    
    # Проверяем, что пользователь оплачивает свой налог
    if callback_user_id != tax_user_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    
    username = callback.from_user.username
    first_name = callback.from_user.first_name
    
    # Получаем состояние налога
    tax_state = await db.get_tax_state(callback_user_id)
    
    # Проверяем, есть ли налог к оплате
    if tax_state["is_paid"]:
        await callback.answer("Налог уже оплачен!", show_alert=True)
        return
    
    # Получаем баланс пользователя
    balance = await db.get_balance(callback_user_id)
    tax_amount = tax_state["tax_due"]
    
    # Если налог не установлен, вычисляем его
    if tax_amount == 0:
        tax_amount = int(balance * config.TAX_PERCENTAGE)
    
    # Проверяем баланс
    if balance < tax_amount:
        await callback.answer(
            f"Недостаточно средств! Нужно {tax_amount} коинов, у тебя {balance}",
            show_alert=True
        )
        return
    
    if balance == 0:
        # Баланс = 0, налог пропадает
        await db.pay_tax(callback_user_id)
        await callback.answer("С тебя нечего взять, нищий", show_alert=True)
        
        # Удаляем сообщение с кнопкой
        try:
            await callback.message.delete()
        except:
            pass
        
        logger.info(f"Пользователь {callback_user_id} имеет баланс 0, налог отменен")
        return
    
    # Списываем налог через сервис (автоматически отправляет уведомление на 5 сек)
    success, balance_before, balance_after, error = await balance_service.subtract_balance(
        user_id=callback_user_id,
        amount=tax_amount,
        command_source="tax_payment",
        comment="Оплата налога Технолога",
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        username=username,
        first_name=first_name,
        allow_negative=False
    )
    
    if not success:
        await callback.answer(error, show_alert=True)
        return
    
    # Отмечаем налог как оплаченный
    await db.pay_tax(callback_user_id)
    logger.info(f"Налог Технолог: user_id={callback_user_id}, списано {tax_amount}, баланс {balance_before} -> {balance_after}")
    
    # Сообщение игроку: «@user, дружок, Технолог забрал налог ⚙️»
    success_text = format_message_with_username(
        "Технолог забрал налог ⚙️\n\n"
        f"Списано {tax_amount} коинов. Твой баланс: <b>{balance_after}</b> коинов",
        username, first_name
    )
    
    # Отправляем фото zl.jpg (как указано в README)
    photo_path = config.get_image_path("zl.jpg")
    
    # Используем bot для отправки сообщения (через callback.bot)
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await callback.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=photo,
                caption=success_text
            )
        else:
            sent_message = await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text=success_text
            )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения об оплате налога для {callback_user_id}: {e}")
        sent_message = await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            text=success_text
        )
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    # Удаляем старое сообщение с кнопкой
    try:
        await callback.message.delete()
    except:
        pass
    
    # Подтверждаем callback
    await callback.answer("Налог оплачен!", show_alert=False)
    
    logger.info(
        f"Пользователь {callback_user_id} оплатил налог "
        f"({tax_amount} коинов, баланс: {balance_before} -> {balance_after})"
    )


@router.message(Command("donate"))
async def cmd_donate(message: Message):
    """
    /donate @user сумма комментарий — перевод коинов с комментарием.
    @user распознаётся по упоминанию (text_mention) или по @username из текста.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    parts = (message.text or "").strip().split(maxsplit=3)
    if len(parts) < 3:
        msg = format_message_with_username(
            "Формат: /donate @user сумма комментарий\nПример: /donate @username 100 на кофе",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    _, raw_mention, raw_amount, comment = (parts + [""])[:4]
    comment = (comment or "").strip()

    try:
        amount = int(raw_amount)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительной")
    except ValueError:
        sent = await message.answer(
            format_message_with_username("Укажи корректную сумму (целое число).", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    # Разрешаем получателя: по entity (text_mention) или по @username
    resolved_id, resolved_username = resolve_recipient_from_message(message)
    if resolved_id is not None:
        receiver_id = resolved_id
        receiver_username = resolved_username or str(receiver_id)
        u = await db.get_user(receiver_id)
        if not u:
            await db.create_user(receiver_id, receiver_username if isinstance(receiver_username, str) else None)
    elif resolved_username:
        receiver_id = await db.get_user_id_by_username(resolved_username)
        receiver_username = resolved_username
    else:
        receiver_username = raw_mention.lstrip("@").strip().lower()
        receiver_id = await db.get_user_id_by_username(receiver_username) if receiver_username else None

    if not receiver_id:
        sent = await message.answer(
            format_message_with_username(
                f"Пользователь @{receiver_username} не найден. Пусть сначала напишет боту.",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if receiver_id == user_id:
        sent = await message.answer(
            format_message_with_username("Нельзя переводить самому себе.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, err = await balance_service.transfer_balance(
        sender_id=user_id,
        receiver_id=receiver_id,
        amount=amount,
        command_source="/donate",
        comment=comment,
        message=message,
        bot=message.bot,
        chat_id=message.chat.id,
        sender_username=username,
        sender_first_name=first_name,
        receiver_username=receiver_username,
        receiver_first_name=None
    )

    if not success:
        sent = await message.answer(format_message_with_username(err, username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    display_name = f"@{receiver_username}" if receiver_username and not str(receiver_username).isdigit() else f"id{receiver_id}"
    text = format_message_with_username(
        f"Перевод {display_name} — {amount} коинов 💰"
        + (f"\nКомментарий: {comment}" if comment else ""),
        username, first_name
    )
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Donate: {user_id} -> {receiver_id}, amount={amount}, comment={comment}")


@router.message(Command("ref"))
async def cmd_ref(message: Message):
    """
    /ref #КОД — активация одноразового реф-кода.
    Код активирует только первый пользователь; остальные видят «Код уже активирован. Доступен только @user».
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        msg = format_message_with_username(
            "Укажи код: /ref #КОД\nПример: /ref #YANDEXPTICA",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent))
        return

    code_raw = parts[1].strip().lstrip("#").upper()
    if not code_raw:
        sent = await message.answer(
            format_message_with_username("Укажи код после #.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    refcode = await db.get_refcode(code_raw)
    if not refcode:
        sent = await message.answer(
            format_message_with_username("Такого кода нет.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    if refcode["activated_by"] is not None:
        activated_user = await db.get_user(refcode["activated_by"])
        act_username = (activated_user or {}).get("username") or "пользователь"
        msg = format_message_with_username(
            f"Код уже активирован. Доступен только @{act_username}",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent))
        return

    activated = await db.activate_refcode(code_raw, user_id)
    if not activated:
        sent = await message.answer(
            format_message_with_username("Не удалось активировать код.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    reward_type = refcode["reward_type"]
    reward_value = refcode["reward_value"]

    if reward_type == "coins":
        amount = int(reward_value)
        await balance_service.add_balance(
            user_id=user_id, amount=amount,
            command_source="/ref", comment=f"Реф-код #{code_raw}",
            message=message, username=username, first_name=first_name
        )
        msg = format_message_with_username(f"Код активирован! +{amount} коинов 💰", username, first_name)

    elif reward_type == "premium":
        duration = int(reward_value)
        await db.set_premium(user_id, duration)
        mins = duration // 60
        msg = format_message_with_username(f"Код активирован! Premium на {mins} мин 👑", username, first_name)

    elif reward_type == "random_potion":
        import random
        potions = [("potion_x1.5", 1.5), ("potion_x2", 2.0), ("potion_x5", 5.0), ("potion_x10", 10.0)]
        name, mult = random.choice(potions)
        await db.add_item_to_inventory(user_id, "potion", name, 0, 1, mult)
        msg = format_message_with_username(
            f"Код активирован! Случайное зелье удачи x{mult} в инвентарь 🍀",
            username, first_name
        )

    elif reward_type == "coins_spins":
        sub = reward_value.split(":")
        coins_amount = int(sub[0])
        spins = int(sub[1]) if len(sub) > 1 else 0
        await balance_service.add_balance(
            user_id=user_id, amount=coins_amount,
            command_source="/ref", comment=f"Реф-код #{code_raw}",
            message=message, username=username, first_name=first_name
        )
        if spins > 0:
            from datetime import datetime as dt, timedelta
            exp = int((dt.now() + timedelta(days=30)).timestamp())
            await db.add_free_spins(user_id, spins, exp)
        msg = format_message_with_username(
            f"Код активирован! +{coins_amount} коинов и {spins} фриспинов /slot 💰",
            username, first_name
        )

    elif reward_type == "reset_refill":
        await db.reset_cooldown(user_id, "/refill")
        msg = format_message_with_username("Код активирован! Cooldown /refill сброшен 🔄", username, first_name)

    elif reward_type == "steal_balance":
        fraction = float(reward_value)
        all_users = await db.fetchall(
            "SELECT user_id, balance FROM users WHERE user_id != ? AND balance > 0 AND is_banned = 0",
            (user_id,)
        )
        if not all_users:
            msg = format_message_with_username("Код активирован, но не у кого красть — все нищие 💸", username, first_name)
        else:
            import random
            row = random.choice(all_users)
            victim_id, victim_balance = row[0], row[1]
            steal_amount = max(1, int(victim_balance * fraction))
            await db.update_balance(victim_id, -steal_amount, "expense", "ref_steal", f"VECNA кража активатором {user_id}")
            await db.update_balance(user_id, steal_amount, "income", "ref_steal", f"VECNA кража у {victim_id}")
            victim_user = await db.get_user(victim_id)
            v_username = (victim_user or {}).get("username") or str(victim_id)
            msg = format_message_with_username(
                f"Код активирован! Украдено {steal_amount} коинов у @{v_username} 💀",
                username, first_name
            )

    elif reward_type == "fake_reset":
        msg = format_message_with_username(
            "Код активирован! Доступна команда /skinna0 @user — фейковое обнуление для рофла 🎭",
            username, first_name
        )

    else:
        msg = format_message_with_username("Код активирован!", username, first_name)

    sent = await message.answer(msg)
    asyncio.create_task(delete_message_after(sent))
    logger.info(f"Ref code {code_raw} activated by {user_id}")


# ---------- /birzh — биржа коинов (шарага для новичков, Mr.Kris, ЖД, MR.lisayaderektrisa) ----------

BIRZH_COIN_LABELS = {"sharaga": "Шарага", "kris": "Mr.Kris", "jd": "ЖД", "lisaya": "MR.lisayaderektrisa"}


async def _birzh_caption(prices: dict, balances: dict, balance: int, user_id: int, username: str, first_name: str) -> str:
    lines = [
        "📈 <b>Биржа</b>",
        f"🪙 Шарага: <b>{prices['sharaga']}</b> коинов за 100  ·  Твои: <b>{balances.get('sharaga', 0)}</b>",
        f"🪙 Mr.Kris: <b>{prices['kris']}</b> коинов за 100  ·  Твои: <b>{balances.get('kris', 0)}</b>",
        f"🪙 ЖД: <b>{prices['jd']}</b> коинов за 100  ·  Твои: <b>{balances.get('jd', 0)}</b>",
        f"🪙 MR.lisayaderektrisa: <b>{prices['lisaya']}</b> коинов за 100  ·  Твои: <b>{balances.get('lisaya', 0)}</b>",
        f"💵 Технолог-коин: <b>{prices['technolog_rub']:.1f}</b> ₽",
        f"💰 Баланс: <b>{balance}</b> коинов",
    ]
    quest = await db.get_birzh_daily_quest(user_id)
    if quest:
        status = "✅ Выполнено" if quest["completed"] else "⏳ Осталось"
        lines.append(f"📋 <b>Задание на сегодня:</b> {quest['title']}. {status}. Награда: {quest['reward_coins']} коинов.")
    return format_message_with_username("\n\n".join(lines), username, first_name)


def _birzh_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить 100 Шарага", callback_data=f"birzh_buy|{user_id}|sharaga"), InlineKeyboardButton(text="Продать 100 Шарага", callback_data=f"birzh_sell|{user_id}|sharaga")],
        [InlineKeyboardButton(text="Купить 100 Mr.Kris", callback_data=f"birzh_buy|{user_id}|kris"), InlineKeyboardButton(text="Продать 100 Mr.Kris", callback_data=f"birzh_sell|{user_id}|kris")],
        [InlineKeyboardButton(text="Купить 100 ЖД", callback_data=f"birzh_buy|{user_id}|jd"), InlineKeyboardButton(text="Продать 100 ЖД", callback_data=f"birzh_sell|{user_id}|jd")],
        [InlineKeyboardButton(text="Купить 100 MR.lisaya", callback_data=f"birzh_buy|{user_id}|lisaya"), InlineKeyboardButton(text="Продать 100 MR.lisaya", callback_data=f"birzh_sell|{user_id}|lisaya")],
        [InlineKeyboardButton(text="🔄 Обновить курс", callback_data=f"birzh_refresh|{user_id}")],
    ])


@router.message(Command("birzh"))
async def cmd_birzh(message: Message):
    """Биржа: Шарага (для новичков), Mr.Kris, ЖД, MR.lisayaderektrisa. Купить/продать по 100."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)

    prices = await db.get_birzh_all_prices()
    balances = await db.get_user_birzh_all(user_id)
    balance = await db.get_balance(user_id)

    caption = await _birzh_caption(prices, balances, balance, user_id, username, first_name)
    keyboard = _birzh_keyboard(user_id)
    photo_path = config.get_image_path("birzh.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent = await message.answer(caption, reply_markup=keyboard)
    except Exception:
        sent = await message.answer(caption, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.callback_query(F.data.startswith("birzh_buy|"))
async def cb_birzh_buy(callback: CallbackQuery):
    data = callback.data
    if not data.startswith("birzh_buy|"):
        return
    parts = data.split("|")
    try:
        uid = int(parts[1])
        coin_type = parts[2] if len(parts) > 2 else "sharaga"
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != uid:
        await callback.answer("Не твоя кнопка", show_alert=True)
        return
    if coin_type not in db.BIRZH_COINS:
        coin_type = "sharaga"
    prices = await db.get_birzh_all_prices()
    price = prices.get(coin_type, prices["sharaga"])
    ok = await db.birzh_buy_100(uid, price, coin_type)
    if not ok:
        await callback.answer(f"Нужно {price} коинов. Не хватает.", show_alert=True)
        return
    try:
        bp = await db.get_current_bp_season()
        if bp:
            await db.progress_bp_quest(uid, bp["id"], "birzh_1", 1)
    except Exception:
        pass
    completed_quest = await db.complete_birzh_quest(uid, "buy", coin_type)
    if completed_quest:
        claimed = await db.claim_birzh_quest_reward(uid, completed_quest["quest_type"])
        if claimed:
            await balance_service.add_balance(uid, completed_quest["reward_coins"], command_source="/birzh", comment=f"Награда за задание: {completed_quest['title']}", bot=callback.bot, chat_id=callback.message.chat.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
    balances = await db.get_user_birzh_all(uid)
    balance = await db.get_balance(uid)
    from datetime import date
    today = date.today().isoformat()
    portfolio = db._birzh_portfolio_value(balances, prices)
    morning = await db.get_birzh_morning_snapshot(uid, today)
    if morning is None:
        await db.ensure_birzh_morning_snapshot(uid, today, portfolio)
    unlocked = await db.check_birzh_10pct_achievement(uid, portfolio)
    if unlocked:
        try:
            await callback.bot.send_message(callback.message.chat.id, "📈 Достижение: Биржа +10% за день!")
        except Exception:
            pass
    un = callback.from_user.username or ""
    fn = callback.from_user.first_name or ""
    caption = await _birzh_caption(prices, balances, balance, uid, un, fn)
    try:
        await callback.message.edit_caption(caption=caption, reply_markup=_birzh_keyboard(uid))
    except Exception:
        await callback.message.edit_text(caption, reply_markup=_birzh_keyboard(uid))
    label = BIRZH_COIN_LABELS.get(coin_type, coin_type)
    await callback.answer(f"Куплено 100 {label} за {price} коинов ✅")


@router.callback_query(F.data.startswith("birzh_sell|"))
async def cb_birzh_sell(callback: CallbackQuery):
    data = callback.data
    if not data.startswith("birzh_sell|"):
        return
    parts = data.split("|")
    try:
        uid = int(parts[1])
        coin_type = parts[2] if len(parts) > 2 else "sharaga"
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != uid:
        await callback.answer("Не твоя кнопка", show_alert=True)
        return
    if coin_type not in db.BIRZH_COINS:
        coin_type = "sharaga"
    prices = await db.get_birzh_all_prices()
    price = prices.get(coin_type, prices["sharaga"])
    ok = await db.birzh_sell_100(uid, price, coin_type)
    if not ok:
        label = BIRZH_COIN_LABELS.get(coin_type, coin_type)
        await callback.answer(f"Нужно минимум 100 {label}.", show_alert=True)
        return
    try:
        bp = await db.get_current_bp_season()
        if bp:
            await db.progress_bp_quest(uid, bp["id"], "birzh_1", 1)
    except Exception:
        pass
    completed_quest = await db.complete_birzh_quest(uid, "sell", coin_type)
    if completed_quest:
        claimed = await db.claim_birzh_quest_reward(uid, completed_quest["quest_type"])
        if claimed:
            await balance_service.add_balance(uid, completed_quest["reward_coins"], command_source="/birzh", comment=f"Награда за задание: {completed_quest['title']}", bot=callback.bot, chat_id=callback.message.chat.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
    balances = await db.get_user_birzh_all(uid)
    balance = await db.get_balance(uid)
    from datetime import date
    today = date.today().isoformat()
    portfolio = db._birzh_portfolio_value(balances, prices)
    morning = await db.get_birzh_morning_snapshot(uid, today)
    if morning is None:
        await db.ensure_birzh_morning_snapshot(uid, today, portfolio)
    unlocked = await db.check_birzh_10pct_achievement(uid, portfolio)
    if unlocked:
        try:
            await callback.bot.send_message(callback.message.chat.id, "📈 Достижение: Биржа +10% за день!")
        except Exception:
            pass
    un = callback.from_user.username or ""
    fn = callback.from_user.first_name or ""
    caption = await _birzh_caption(prices, balances, balance, uid, un, fn)
    try:
        await callback.message.edit_caption(caption=caption, reply_markup=_birzh_keyboard(uid))
    except Exception:
        await callback.message.edit_text(caption, reply_markup=_birzh_keyboard(uid))
    label = BIRZH_COIN_LABELS.get(coin_type, coin_type)
    await callback.answer(f"Продано 100 {label} за {price} коинов ✅")


@router.callback_query(F.data.startswith("birzh_refresh|"))
async def cb_birzh_refresh(callback: CallbackQuery):
    data = callback.data
    if not data.startswith("birzh_refresh|"):
        return
    try:
        uid = int(data.split("|")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != uid:
        await callback.answer("Не твоя кнопка", show_alert=True)
        return
    prices = await db.get_birzh_all_prices()
    balances = await db.get_user_birzh_all(uid)
    balance = await db.get_balance(uid)
    un = callback.from_user.username or ""
    fn = callback.from_user.first_name or ""
    caption = await _birzh_caption(prices, balances, balance, uid, un, fn)
    try:
        await callback.message.edit_caption(caption=caption, reply_markup=_birzh_keyboard(uid))
    except Exception:
        await callback.message.edit_text(caption, reply_markup=_birzh_keyboard(uid))
    await callback.answer("Курс обновлён")
