"""
Магазины и инвентарь бота YandexPticaGPT v0.5
/market, /tehnologmarket, /inventory, /dongift, /giftplus
"""

import asyncio
import logging
import random

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import config
from db import db
from utils import delete_message_after, format_message_with_username, resolve_recipient_from_message
from middlewares import set_command_cooldown
from services.balance import balance_service
from services.effects import effects_service

router = Router()
logger = logging.getLogger(__name__)

TOY_NAMES = ["Мишка", "Отвертка", "Ключ на 32"]
QUALITY_NAMES = ["хлам", "отремонтировано", "железо", "медь", "золото"]

# Фото при покупке подарка (Мишка → mishka.jpg и т.д.)
TOY_IMAGE = {"Мишка": "mishka.jpg", "Отвертка": "otvertka.jpg", "Ключ на 32": "kluch32.jpg"}
TOY_EMOJI = {"Мишка": "🧸", "Отвертка": "🔧", "Ключ на 32": "🔑"}


@router.message(Command("market"))
async def cmd_market(message: Message):
    """
    /market — Светофор, зелья удачи (1 мин): x1.5 1000, x2 4000, x5 8000, x10 30000.
    Риск отравления, лечение 320.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    prices = config.POTION_PRICES
    rows = []
    for key, price in prices.items():
        final_price = await effects_service.apply_price_discount(user_id, price)
        rows.append(InlineKeyboardButton(
            text=f"{key} — {final_price} коинов",
            callback_data=f"buy_potion_{user_id}_{key}"
        ))

    caption = format_message_with_username(
        "🛒 <b>СВЕТОФОР</b> — зелья удачи (1 мин)\n\n"
        "x1.5 — 1000 | x2 — 4000 | x5 — 8000 | x10 — 30000\n"
        "⚠️ Есть риск отравления, лечение 320 коинов",
        username, first_name
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[b] for b in rows])

    photo_path = config.get_image_path("market.jpg")
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent = await message.answer_photo(photo=photo, caption=caption, reply_markup=keyboard)
        else:
            sent = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"market photo {e}")
        sent = await message.answer(caption, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent))


@router.callback_query(F.data.startswith("buy_potion_"))
async def cb_buy_potion(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    cb_user_id = callback.from_user.id
    owner_id = int(parts[2])
    key = parts[3]
    if cb_user_id != owner_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return

    prices = config.POTION_PRICES
    if key not in prices:
        await callback.answer("Нет такого зелья", show_alert=True)
        return

    price = await effects_service.apply_price_discount(cb_user_id, prices[key])
    balance = await db.get_balance(cb_user_id)
    if balance < price:
        await callback.answer(f"Недостаточно средств: {price} коинов", show_alert=True)
        return

    mult_map = {"x1.5": 1.5, "x2": 2.0, "x5": 5.0, "x10": 10.0}
    mult = mult_map.get(key, 1.5)
    effect_type = f"potion_{key}"

    success, _, _, err = await balance_service.subtract_balance(
        user_id=cb_user_id, amount=price,
        command_source="/market", comment=f"Покупка зелья {key}",
        bot=callback.bot, chat_id=callback.message.chat.id,
        username=callback.from_user.username, first_name=callback.from_user.first_name,
        allow_negative=False
    )
    if not success:
        await callback.answer(err, show_alert=True)
        return

    await db.add_item_to_inventory(cb_user_id, "potion", effect_type, 0, 1, mult)
    await callback.answer(f"Зелье {key} куплено!", show_alert=False)
    caption = format_message_with_username(
        "🧪 Спасибо за покупку зелья!",
        callback.from_user.username, callback.from_user.first_name
    )
    photo_path = config.get_image_path("zelia.jpg")
    try:
        if photo_path.exists():
            thank = await callback.bot.send_photo(
                callback.message.chat.id, FSInputFile(str(photo_path)), caption=caption
            )
            asyncio.create_task(delete_message_after(thank))
    except Exception as e:
        logger.warning(f"zelia.jpg after buy_potion: {e}")
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(Command("tehnologmarket"))
async def cmd_tehnologmarket(message: Message):
    """
    /tehnologmarket — tehmarket.jpg, игрушки: Мишка, Отвертка, Ключ на 32 по 40000.
    5 стадий качества, апгрейд x3.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    price = await effects_service.apply_price_discount(user_id, config.TOY_PRICE)
    caption = format_message_with_username(
        "🛒 <b>ТЕХНОЛОГ МАРКЕТ</b>\n\n"
        f"Игрушки по {price} коинов: Мишка, Отвертка, Ключ на 32.\n"
        "Качества: хлам → отремонтировано → железо → медь → золото.\n"
        "Апгрейд каждый раз в 3 раза дороже. Дарение: /dongift @user название",
        username, first_name
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Мишка — {price} 💰", callback_data=f"buy_toy_{user_id}_Мишка")],
        [InlineKeyboardButton(text=f"Отвертка — {price} 💰", callback_data=f"buy_toy_{user_id}_Отвертка")],
        [InlineKeyboardButton(text=f"Ключ на 32 — {price} 💰", callback_data=f"buy_toy_{user_id}_Ключ на 32")]
    ])

    photo_path = config.get_image_path("tehmarket.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"tehnologmarket {e}")
        sent = await message.answer(caption, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent))


@router.callback_query(F.data.startswith("buy_toy_"))
async def cb_buy_toy(callback: CallbackQuery):
    parts = callback.data.split("_", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    cb_user_id = callback.from_user.id
    owner_id = int(parts[2])
    item_name = parts[3]
    if cb_user_id != owner_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    if item_name not in TOY_NAMES:
        await callback.answer("Нет такой игрушки", show_alert=True)
        return

    inv = await db.get_user_inventory(cb_user_id)
    for it in inv:
        if it["item_type"] == "toy" and it["item_name"] == item_name:
            await callback.answer("У тебя уже есть такой подарок (1 на вид)", show_alert=True)
            return

    price = await effects_service.apply_price_discount(cb_user_id, config.TOY_PRICE)
    balance = await db.get_balance(cb_user_id)
    if balance < price:
        await callback.answer(f"Недостаточно средств: {price} коинов", show_alert=True)
        return

    success, _, _, err = await balance_service.subtract_balance(
        user_id=cb_user_id, amount=price,
        command_source="/tehnologmarket", comment=f"Покупка {item_name}",
        bot=callback.bot, chat_id=callback.message.chat.id,
        username=callback.from_user.username, first_name=callback.from_user.first_name,
        allow_negative=False
    )
    if not success:
        await callback.answer(err, show_alert=True)
        return

    await db.add_item_to_inventory(cb_user_id, "toy", item_name, 0, 1, 1.0)
    await callback.answer(f"{item_name} куплен!", show_alert=False)
    emoji = TOY_EMOJI.get(item_name, "🎁")
    caption = format_message_with_username(
        f"🎁 Спасибо за покупку! {emoji}",
        callback.from_user.username, callback.from_user.first_name
    )
    img_name = TOY_IMAGE.get(item_name)
    if img_name:
        photo_path = config.get_image_path(img_name)
        try:
            if photo_path.exists():
                thank = await callback.bot.send_photo(
                    callback.message.chat.id, FSInputFile(str(photo_path)), caption=caption
                )
                asyncio.create_task(delete_message_after(thank))
        except Exception as e:
            logger.warning(f"toy image after buy_toy: {e}")
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(Command("inventory"))
async def cmd_inventory(message: Message):
    """
    /inventory — inventory.jpg, просмотр, использование зелий, крафт.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    inv = await db.get_user_inventory(user_id)
    lines = ["📦 <b>ИНВЕНТАРЬ</b>\n"]
    if not inv:
        lines.append("Пусто.")
    else:
        for it in inv:
            name = it["item_name"]
            q = it["quantity"]
            mult = it.get("multiplier") or 1.0
            if it["item_type"] == "potion":
                lines.append(f"• Зелье {name} x{mult} — {q} шт.")
            else:
                ql = it.get("quality_level") or 0
                qname = QUALITY_NAMES[ql] if ql < len(QUALITY_NAMES) else str(ql)
                lines.append(f"• {name} ({qname}) — {q} шт.")

    caption = format_message_with_username("\n".join(lines), username, first_name)
    photo_path = config.get_image_path("inventory.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption)
        else:
            sent = await message.answer(caption)
    except Exception as e:
        sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("use_potion"))
async def cmd_use_potion(message: Message):
    """Использование зелья из инвентаря по названию, например /use_potion potion_x1.5"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        sent = await message.answer(
            format_message_with_username(
                "Формат: /use_potion potion_x1.5 (или x2, x5, x10)",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    key = parts[1].strip().lower()
    inv = await db.get_user_inventory(user_id)
    item = None
    item_id = None
    for it in inv:
        if it["item_type"] == "potion" and (it["item_name"] or "").lower() == key:
            item = it
            item_id = it["id"]
            break
    if not item:
        sent = await message.answer(
            format_message_with_username("Нет такого зелья в инвентаре.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    mult = item.get("multiplier") or 1.0
    poison = random.random() < config.POTION_POISON_CHANCE
    if poison:
        balance = await db.get_balance(user_id)
        cure = config.POTION_CURE_COST
        if balance < cure:
            sent = await message.answer(
                format_message_with_username(
                    f"Зелье оказалось отравой! Нужно {cure} коинов на лечение, у тебя {balance}.",
                    username, first_name
                )
            )
            asyncio.create_task(delete_message_after(sent))
            return
        await balance_service.subtract_balance(
            user_id=user_id, amount=cure,
            command_source="/use_potion", comment="Лечение от отравы",
            message=message, username=username, first_name=first_name, allow_negative=False
        )
        await db.remove_item_from_inventory(item_id, user_id)
        sent = await message.answer(
            format_message_with_username(
                f"Зелье оказалось отравой! С тебя списано {cure} коинов за лечение 💀",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    await db.add_effect(user_id, item["item_name"], config.POTION_DURATION, multiplier=mult)
    await db.remove_item_from_inventory(item_id, user_id)
    sent = await message.answer(
        format_message_with_username(
            f"Зелье удачи x{mult} активировано на 1 минуту 🍀",
            username, first_name
        )
    )
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("dongift"))
async def cmd_dongift(message: Message):
    """Подарить подарок: /dongift @user название (Мишка / Отвертка / Ключ на 32)."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        sent = await message.answer(
            format_message_with_username(
                "Формат: /dongift @user название подарка\nНапример: /dongift @user Мишка",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    _, raw_user, item_name = parts
    item_name = item_name.strip()
    if item_name not in TOY_NAMES:
        sent = await message.answer(
            format_message_with_username(
                "Подарок: Мишка, Отвертка или Ключ на 32.",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    resolved_id, resolved_username = resolve_recipient_from_message(message)
    if resolved_id is not None:
        receiver_id = resolved_id
        raw_user = resolved_username or str(receiver_id)
        u = await db.get_user(receiver_id)
        if not u:
            await db.create_user(receiver_id, raw_user if isinstance(raw_user, str) else None)
    else:
        raw_user = raw_user.lstrip("@").strip().lower()
        receiver_id = await db.get_user_id_by_username(raw_user)
    if not receiver_id:
        sent = await message.answer(
            format_message_with_username(f"Пользователь @{raw_user} не найден. Пусть напишет боту.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    if receiver_id == user_id:
        sent = await message.answer(
            format_message_with_username("Нельзя подарить самому себе.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    rec_user = await db.get_user(receiver_id)
    receiver_display = rec_user.get("username") or str(receiver_id)
    receiver_display = f"@{receiver_display}" if not str(receiver_display).startswith("@") else receiver_display

    inv = await db.get_user_inventory(user_id)
    gift_item = None
    gift_id = None
    for it in inv:
        if it["item_type"] == "toy" and it["item_name"] == item_name:
            gift_item = it
            gift_id = it["id"]
            break
    if not gift_item:
        sent = await message.answer(
            format_message_with_username(f"У тебя нет подарка «{item_name}».", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    receiver_inv = await db.get_user_inventory(receiver_id)
    for it in receiver_inv:
        if it["item_type"] == "toy" and it["item_name"] == item_name:
            sent = await message.answer(
                format_message_with_username(
                    f"У {receiver_display} уже есть такой подарок (1 на вид).",
                    username, first_name
                )
            )
            asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
            return

    await db.remove_item_from_inventory(gift_id, user_id)
    ql = gift_item.get("quality_level") or 0
    await db.add_item_to_inventory(receiver_id, "toy", item_name, ql, 1, 1.0)
    await db.log_gift(user_id, receiver_id, item_name, ql)

    caption = format_message_with_username(
        f"Подарок «{item_name}» передан {receiver_display} 🎁",
        username, first_name
    )
    photo_path = config.get_image_path("gift.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption)
        else:
            sent = await message.answer(caption)
    except Exception:
        sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("giftplus"))
async def cmd_giftplus(message: Message):
    """Крафт: улучшение зелья подарком — /giftplus название_подарка множитель_зелья (дешевле чем 5000)."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        sent = await message.answer(
            format_message_with_username(
                "Формат: /giftplus название_подарка множитель\nПример: /giftplus Мишка x2",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    _, toy_name, mult_str = parts
    toy_name = toy_name.strip()
    mult_str = mult_str.strip().lower()
    if toy_name not in TOY_NAMES:
        sent = await message.answer(
            format_message_with_username("Подарок: Мишка, Отвертка, Ключ на 32.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    inv = await db.get_user_inventory(user_id)
    toy_item = None
    toy_id = None
    for it in inv:
        if it["item_type"] == "toy" and it["item_name"] == toy_name:
            toy_item = it
            toy_id = it["id"]
            break
    if not toy_item:
        sent = await message.answer(
            format_message_with_username(f"Нет подарка «{toy_name}» в инвентаре.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    mult_map = {"x1.5": 1.5, "x2": 2.0, "x5": 5.0, "x10": 10.0}
    target_mult = mult_map.get(mult_str)
    if target_mult is None:
        sent = await message.answer(
            format_message_with_username("Множитель: x1.5, x2, x5, x10.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent))
        return

    ql = toy_item.get("quality_level") or 0
    cost_map = {0: 100, 1: 150, 2: 200, 3: 250, 4: 300}
    craft_cost = await effects_service.apply_price_discount(user_id, cost_map.get(ql, 100))
    balance = await db.get_balance(user_id)
    if balance < craft_cost:
        sent = await message.answer(
            format_message_with_username(
                f"Нужно {craft_cost} коинов на крафт (по редкости подарка).",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    success_craft = random.random() < 0.85
    if not success_craft:
        await db.remove_item_from_inventory(toy_id, user_id)
        sent = await message.answer(
            format_message_with_username(
                "Крафт не удался, подарок потрачен впустую 😢",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    success, _, _, err = await balance_service.subtract_balance(
        user_id=user_id, amount=craft_cost,
        command_source="/giftplus", comment=f"Крафт зелья x{target_mult}",
        message=message, username=username, first_name=first_name, allow_negative=False
    )
    if not success:
        sent = await message.answer(format_message_with_username(err, username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    await db.remove_item_from_inventory(toy_id, user_id)
    effect_name = f"potion_x{target_mult}" if target_mult != 1.5 else "potion_x1.5"
    await db.add_item_to_inventory(user_id, "potion", effect_name, 0, 1, target_mult)
    sent = await message.answer(
        format_message_with_username(
            f"Зелье x{target_mult} создано с подарком «{toy_name}» 🍀",
            username, first_name
        )
    )
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("freedurev"))
async def cmd_freedurev(message: Message):
    """
    Одноразовый промокод: СТРОГО 1 раз на ВСЕГО бота.
    Первый активировавший получает награду. Остальные видят: «Команда уже была активирована @username».
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    # Уже кто-то активировал (один раз на всего бота)
    activator_id = await db.get_freedurev_global_activator()
    if activator_id is not None:
        text = "этот промокод уже применён"
        sent = await message.answer(format_message_with_username(text, username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    # Пытаемся записать себя как первого
    inserted = await db.set_freedurev_global(user_id)
    if not inserted:
        text = "этот промокод уже применён"
        sent = await message.answer(format_message_with_username(text, username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    await balance_service.add_balance(
        user_id=user_id, amount=1000,
        command_source="/freedurev", comment="Поддержка Дурова",
        message=message, username=username, first_name=first_name
    )
    caption = format_message_with_username(
        "Ты поддержал Дурова! +1000 технолог-коинов 💰",
        username, first_name
    )
    photo_path = config.get_image_path("durev.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption)
        else:
            sent = await message.answer(caption)
    except Exception as e:
        logger.warning("freedurev photo: %s", e)
        sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("User %s activated /freedurev (first and only for bot)", user_id)
