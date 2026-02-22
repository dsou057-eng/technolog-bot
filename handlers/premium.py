"""
Команды Premium и эффектов бота YandexPticaGPT v0.5
/premium, /timeprem, /effect, /kachalka
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import config
from db import db
from utils import delete_message_after, format_message_with_username
from middlewares import set_command_cooldown
from services.balance import balance_service
from services.effects import effects_service

# Создаем роутер для Premium команд
router = Router()

logger = logging.getLogger(__name__)


@router.message(Command("premium"))
async def cmd_premium(message: Message):
    """
    Команда /premium
    Показывает тарифы Premium с фото prem.jpg
    Тарифы: 1ч (2000), 1д (20000), 7д (60000)
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    
    # Формируем текст с тарифами
    caption = format_message_with_username(
        "👑 <b>PREMIUM ТАРИФЫ:</b>\n\n"
        "1 час — 2000 коинов\n"
        "1 день — 20000 коинов\n"
        "7 дней — 60000 коинов\n\n"
        "💎 <b>ПРЕИМУЩЕСТВА PREMIUM:</b>\n"
        "• VIP-отметка\n"
        "• Cooldown 15 сек (вместо 60)\n"
        "• +1.4% к шансам выигрыша\n"
        "• -0.5% ко всем ценам\n"
        "• Доступ к VIP-командам",
        username, first_name
    )
    
    # Создаем inline-кнопки для покупки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="1 час — 2000 💰",
                callback_data=f"buy_premium_{user_id}_1h"
            )
        ],
        [
            InlineKeyboardButton(
                text="1 день — 20000 💰",
                callback_data=f"buy_premium_{user_id}_1d"
            )
        ],
        [
            InlineKeyboardButton(
                text="7 дней — 60000 💰",
                callback_data=f"buy_premium_{user_id}_7d"
            )
        ]
    ])
    
    # Отправляем фото prem.jpg
    photo_path = config.get_image_path("prem.jpg")
    
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await message.answer_photo(
                photo=photo,
                caption=caption,
                reply_markup=keyboard
            )
        else:
            sent_message = await message.answer(
                caption,
                reply_markup=keyboard
            )
            logger.warning(f"Фото prem.jpg не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки /premium для {user_id}: {e}")
        sent_message = await message.answer(caption, reply_markup=keyboard)
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    logger.info(f"Пользователь {user_id} использовал /premium")


@router.callback_query(F.data.startswith("buy_premium_"))
async def callback_buy_premium(callback: CallbackQuery):
    """
    Обработчик покупки Premium
    Формат callback_data: buy_premium_{user_id}_{duration}
    """
    # Проверяем, что callback от правильного пользователя
    callback_user_id = callback.from_user.id
    callback_data = callback.data
    
    # Извлекаем данные из callback_data
    try:
        parts = callback_data.split("_")
        tax_user_id = int(parts[2])
        duration = parts[3]  # 1h, 1d, 7d
    except (ValueError, IndexError):
        await callback.answer("Ошибка обработки запроса", show_alert=True)
        return
    
    # Проверяем, что пользователь покупает для себя
    if callback_user_id != tax_user_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    
    username = callback.from_user.username
    first_name = callback.from_user.first_name
    
    # Определяем цену и длительность
    duration_map = {
        "1h": (config.PREMIUM_PRICES["1_hour"], 3600),  # 1 час
        "1d": (config.PREMIUM_PRICES["1_day"], 86400),  # 1 день
        "7d": (config.PREMIUM_PRICES["7_days"], 604800)  # 7 дней
    }
    
    if duration not in duration_map:
        await callback.answer("Неверный тариф", show_alert=True)
        return
    
    price, duration_seconds = duration_map[duration]
    
    # Проверяем баланс
    balance = await db.get_balance(callback_user_id)
    if balance < price:
        await callback.answer(
            f"Недостаточно средств! Нужно {price} коинов, у тебя {balance}",
            show_alert=True
        )
        return
    
    # Списываем коины через сервис (автоматически отправляет уведомление на 5 сек)
    success, balance_before, balance_after, error = await balance_service.subtract_balance(
        user_id=callback_user_id,
        amount=price,
        command_source="/premium",
        comment=f"Покупка Premium ({duration})",
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        username=username,
        first_name=first_name,
        allow_negative=False
    )
    
    if not success:
        await callback.answer(error, show_alert=True)
        return
    
    # Устанавливаем Premium
    await db.set_premium(callback_user_id, duration_seconds)
    
    # Вычисляем время окончания Premium
    now = int(datetime.now().timestamp())
    premium_until = now + duration_seconds
    
    # Форматируем время окончания
    premium_end = datetime.fromtimestamp(premium_until)
    premium_end_str = premium_end.strftime("%d.%m.%Y %H:%M")
    
    # Отправляем фото kupprem.jpg с подписью
    photo_path = config.get_image_path("kupprem.jpg")
    success_text = format_message_with_username(
        f"Спасибо, что купил Premium! 👑\n\n"
        f"Premium активен до: <b>{premium_end_str}</b>\n"
        f"Твой баланс: <b>{balance_after}</b> коинов",
        username, first_name
    )
    
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
            logger.warning(f"Фото kupprem.jpg не найдено для пользователя {callback_user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения о покупке Premium для {callback_user_id}: {e}")
        sent_message = await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            text=success_text
        )
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    # Удаляем старое сообщение с кнопками
    try:
        await callback.message.delete()
    except:
        pass
    
    # Подтверждаем callback
    await callback.answer("Premium куплен!", show_alert=False)
    
    logger.info(
        f"Пользователь {callback_user_id} купил Premium "
        f"({duration}, цена: {price}, баланс: {balance_before} -> {balance_after})"
    )


@router.message(Command("timeprem"))
async def cmd_timeprem(message: Message):
    """
    Команда /timeprem
    Показывает дату и время окончания Premium
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    
    premium_until = user["premium_until"]
    is_premium = await db.is_premium(user_id)
    
    if not is_premium or not premium_until:
        response_text = format_message_with_username(
            "У тебя нет активного Premium 👑\n"
            "Используй /premium для покупки",
            username, first_name
        )
    else:
        # Вычисляем время окончания
        now = int(datetime.now().timestamp())
        time_left = premium_until - now
        
        if time_left <= 0:
            response_text = format_message_with_username(
                "Твой Premium истек 👑\n"
                "Используй /premium для покупки нового",
                username, first_name
            )
        else:
            # Форматируем время окончания
            premium_end = datetime.fromtimestamp(premium_until)
            premium_end_str = premium_end.strftime("%d.%m.%Y %H:%M")
            
            # Форматируем оставшееся время
            hours = time_left // 3600
            minutes = (time_left % 3600) // 60
            
            if hours > 0:
                time_left_str = f"{hours}ч {minutes}м"
            else:
                time_left_str = f"{minutes}м"
            
            response_text = format_message_with_username(
                f"👑 <b>PREMIUM АКТИВЕН</b>\n\n"
                f"Окончание: <b>{premium_end_str}</b>\n"
                f"Осталось: {time_left_str}",
                username, first_name
            )
    
    # Отправляем сообщение
    sent_message = await message.answer(response_text)
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    logger.info(f"Пользователь {user_id} использовал /timeprem")


@router.message(Command("effect"))
async def cmd_effect(message: Message):
    """
    Команда /effect
    Показывает ВСЕ активные эффекты с таймерами
    Premium, зелья удачи, баффы от /kachalka
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    
    # Получаем список активных эффектов через сервис
    effects_text = await effects_service.format_effects_list(user_id)
    
    # Форматируем сообщение с username
    response_text = format_message_with_username(
        effects_text,
        username, first_name
    )
    
    # Отправляем сообщение
    sent_message = await message.answer(response_text)
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    logger.info(f"Пользователь {user_id} использовал /effect")


@router.message(Command("kachalka"))
async def cmd_kachalka(message: Message):
    """
    Команда /kachalka
    Cooldown 2 часа
    Эффект: снижает cooldown всех команд до 30 сек на 10 минут
    Отправляет фото kachalk.jpg
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    
    # Проверяем cooldown для /kachalka (2 часа)
    last_used = await db.get_cooldown(user_id, "/kachalka")
    now = int(time.time())
    cooldown_seconds = 7200  # 2 часа
    
    if last_used:
        time_passed = now - last_used
        
        if time_passed < cooldown_seconds:
            # Cooldown еще активен
            remaining = cooldown_seconds - time_passed
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            seconds = remaining % 60
            
            # Форматируем время
            if hours > 0:
                time_str = f"{hours}ч {minutes}м {seconds}с"
            elif minutes > 0:
                time_str = f"{minutes}м {seconds}с"
            else:
                time_str = f"{seconds}с"
            
            response_text = format_message_with_username(
                f"Качалка в cooldown! Приходи через {time_str} ⏳",
                username, first_name
            )
            
            sent_message = await message.answer(response_text)
            asyncio.create_task(delete_message_after(sent_message))
            
            logger.info(f"Пользователь {user_id} попытался использовать /kachalka (cooldown {remaining} сек)")
            return
    
    # Cooldown прошел - добавляем эффект
    duration_seconds = config.KACHALKA_DURATION  # 10 минут
    
    # Добавляем эффект через сервис
    await effects_service.add_effect(
        user_id=user_id,
        effect_type="kachalka",
        duration_seconds=duration_seconds,
        multiplier=1.0
    )
    
    # Устанавливаем cooldown для /kachalka
    await set_command_cooldown(user_id, "/kachalka")
    
    # Отправляем фото kachalk.jpg с подписью
    photo_path = config.get_image_path("kachalk.jpg")
    caption = format_message_with_username(
        "Ты подкачался и получил сниженное КД на команды с 1 минуты до 30 сек 💪\n"
        f"Эффект действует 10 минут",
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
            logger.warning(f"Фото kachalk.jpg не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки /kachalka для {user_id}: {e}")
        sent_message = await message.answer(caption)
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    logger.info(
        f"Пользователь {user_id} использовал /kachalka "
        f"(эффект на {duration_seconds} сек)"
    )
