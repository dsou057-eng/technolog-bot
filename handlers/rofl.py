"""
Tehnolog Games — команда /steal (кража коинов, геймплей).
/sperm и /skinna0 отключены — только игры и экономика.
"""

import asyncio
import logging
import random
import time

from aiogram import Router
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command

from config import config
from db import db
from utils import delete_message_after, format_message_with_username
from middlewares import set_command_cooldown
from services.balance import balance_service

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("steal"))
async def cmd_steal(message: Message):
    """Украсть 50 коинов у случайного пользователя. КД 24ч. steal.jpg."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    last_used = await db.get_cooldown(user_id, "/steal")
    now = int(time.time())
    if last_used and (now - last_used) < config.STEAL_COOLDOWN:
        left = config.STEAL_COOLDOWN - (now - last_used)
        sent = await message.answer(
            format_message_with_username(f"КД ещё {left // 3600}ч ⏳", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    amount = config.STEAL_AMOUNT
    victims = await db.fetchall(
        "SELECT user_id, balance FROM users WHERE user_id != ? AND balance >= ? AND is_banned = 0",
        (user_id, amount)
    )
    if not victims:
        sent = await message.answer(
            format_message_with_username("Не у кого красть — все нищие 💸", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    victim_id, _ = random.choice(victims)
    success, _, _, err = await balance_service.subtract_balance(
        user_id=victim_id, amount=amount,
        command_source="/steal", comment="Кража",
        bot=message.bot, chat_id=message.chat.id,
        username=None, first_name=None, allow_negative=False
    )
    if not success:
        sent = await message.answer(
            format_message_with_username("Кража не удалась.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    await balance_service.add_balance(
        user_id=user_id, amount=amount,
        command_source="/steal", comment="Кража",
        bot=message.bot, chat_id=message.chat.id,
        username=username, first_name=first_name
    )
    await set_command_cooldown(user_id, "/steal")

    victim_user = await db.get_user(victim_id)
    v_username = (victim_user or {}).get("username") or str(victim_id)
    text = format_message_with_username(
        f"Обокрал @{v_username} (случайно) — {amount} коинов 💸",
        username, first_name
    )

    photo_path = config.get_image_path("steal.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=text)
        else:
            sent = await message.answer(text)
    except Exception as e:
        logger.error(f"steal photo {e}")
        sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
