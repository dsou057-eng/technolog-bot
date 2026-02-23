"""
Tehnolog Games — профиль, аккаунт, уровни.
/profile, /accaunt, /accountphoto, /accountobrosh, /accountinfo, /accountstatus,
/status, /checkaccount, /lvl, /lvlup, /lvlcheck, /vzortehnologa
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config


class AccountStates(StatesGroup):
    wait_avatar = State()


# Варианты обращения бота к игроку (дружок, боец, легенда и т.п.)
BOT_ADDRESS_CHOICES = [
    "дружок", "боец", "легенда", "господин", "красавчик", "чемпион",
    "тигр", "орёл", "мастер", "удалец"
]

from db import db
from utils import delete_message_after, format_message_with_username, resolve_recipient_from_message
from middlewares import set_command_cooldown
from services.balance import balance_service
from services.effects import effects_service

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Профиль игрока: username, user_id, баланс, статус, обращение, игры, победы/поражения."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    profile = await db.get_profile(user_id)
    stats = await db.get_user_game_stats(user_id)
    bot_address = profile.get("bot_address") or profile.get("vip_address") or "дружок"
    mmr = await db.get_user_mmr(user_id)
    league_info = db.get_league_info(mmr)
    achievements = await db.get_user_achievements(user_id)
    total_games = await db.get_total_games_count(user_id)
    min_games_legend = getattr(db, "MMR_MIN_GAMES_FOR_LEGEND", 60)

    # Прогресс-бар внутри лиги (5 блоков)
    p = league_info["progress"]
    filled = int(round(p * 5))
    bar = "■" * filled + "□" * (5 - filled)
    league_line = f"📊 {league_info['name']} — <b>{mmr}</b> MMR ({league_info['low']}–{league_info['high']})"
    progress_line = f"   [{bar}] {int(p * 100)}% по лиге"
    goal_parts = ["🎯 <b>Цель:</b> дойти до 🟡 Легенда"]
    if league_info["to_next_league"] is not None:
        goal_parts.append(f"📈 До следующей лиги: <b>{league_info['to_next_league']}</b> MMR")
    else:
        goal_parts.append("🏆 Ты в высшей лиге — дерзай в топ!")
    if mmr < 2000 and total_games < min_games_legend:
        goal_parts.append(f"📋 До Легенды нужно <b>{min_games_legend}</b> игр (сыграно: <b>{total_games}</b>)")
    goal_line = "\n".join(goal_parts)
    lines = [
        f"👤 <b>ПРОФИЛЬ</b>",
        f"@{username or first_name or 'user'}",
        f"🆔 ID: <code>{user_id}</code>",
        f"💰 Баланс: <b>{user.get('balance', 0)}</b> коинов",
        f"🏷️ Статус: {user.get('status') or 'нет'}",
        f"💬 Как обращаюсь: <i>{bot_address}</i>",
        f"",
        league_line,
        progress_line,
        goal_line,
        f"🎮 Игр: <b>{stats['total']}</b> (побед: {stats['wins']}, поражений: {stats['losses']})",
    ]
    if achievements:
        lines.append("")
        lines.append("🏅 Достижения: " + ", ".join(f"{a['prefix']}{a['title']}" for a in achievements[:10]))
        if len(achievements) > 10:
            lines[-1] += f" (+{len(achievements) - 10})"
    text = "\n".join(lines)
    out = format_message_with_username(text, username, first_name)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить обращение", callback_data=f"profile_addr_{user_id}")]
    ])
    sent = await message.answer(out, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.callback_query(F.data.startswith("profile_addr_"))
async def cb_profile_address_menu(callback: CallbackQuery):
    """Меню выбора обращения (дружок, боец, легенда...)."""
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("Ошибка", show_alert=True)
        return
    uid = int(parts[2])
    if callback.from_user.id != uid:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=addr, callback_data=f"setaddr_{uid}_{i}")]
        for i, addr in enumerate(BOT_ADDRESS_CHOICES)
    ])
    try:
        await callback.message.edit_text(
            format_message_with_username(
                "Выбери, как бот будет к тебе обращаться:",
                callback.from_user.username, callback.from_user.first_name
            ),
            reply_markup=keyboard
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("setaddr_"))
async def cb_set_address(callback: CallbackQuery):
    """Установка обращения (дружок, боец и т.д.)."""
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    uid = int(parts[2])
    idx = int(parts[3])
    if callback.from_user.id != uid:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    if 0 <= idx < len(BOT_ADDRESS_CHOICES):
        addr = BOT_ADDRESS_CHOICES[idx]
        await db.update_profile(uid, bot_address=addr)
        try:
            await callback.message.edit_text(
                format_message_with_username(f"Обращение сохранено: <i>{addr}</i> ✅", callback.from_user.username, callback.from_user.first_name)
            )
        except Exception:
            pass
    await callback.answer("Готово!", show_alert=False)


@router.message(Command("accaunt"))
async def cmd_accaunt(message: Message):
    """Меню управления аккаунтом. Отправляет accaunt.jpg и текст меню."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    text = format_message_with_username(
        "👤 <b>ПРОФИЛЬ</b>\n\n"
        "/profile — твой профиль (баланс, игры, обращение)\n"
        "/accountphoto — загрузка аватарки\n"
        "/accountobrosh — VIP-обращение (как бот зовёт тебя)\n"
        "/accountinfo — описание «о себе»\n"
        "/accountstatus — текущий статус и ссылка на магазин\n"
        "/statusmarket — магазин статусов (Богач, Пубертат страны и т.д.)\n"
        "/checkaccount @user — чужой профиль\n"
        "/lvl — твой уровень\n"
        "/lvlup — повысить уровень\n"
        "/lvlcheck @user — уровень другого",
        username, first_name
    )
    photo_path = config.get_image_path("accaunt.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=text)
        else:
            sent = await message.answer(text)
    except Exception as e:
        logger.warning(f"accaunt photo {e}")
        sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("accountphoto"))
async def cmd_accountphoto(message: Message, state: FSMContext):
    """Бот просит фото на аватарку. Аватар меняется ТОЛЬКО после этой команды."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    await state.set_state(AccountStates.wait_avatar)
    sent = await message.answer(
        format_message_with_username(
            "Отправь одно фото — оно будет установлено как аватарка профиля.",
            username, first_name
        )
    )
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(StateFilter(AccountStates.wait_avatar), F.photo)
async def on_photo_for_avatar(message: Message, state: FSMContext):
    """Аватар меняется ТОЛЬКО здесь — после команды /accountphoto. Любые другие фото в чате не обрабатываются как аватар (нет иных хендлеров на F.photo для профиля)."""
    user_id = message.from_user.id
    if not message.photo:
        return
    photo = message.photo[-1]
    file_id = photo.file_id
    await db.update_profile(user_id, avatar_path=file_id)
    await state.clear()
    username = message.from_user.username
    first_name = message.from_user.first_name
    sent = await message.answer(
        format_message_with_username("Аватарка обновлена ✅", username, first_name)
    )
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("accountobrosh"))
async def cmd_accountobrosh(message: Message):
    """VIP: как бот обращается к пользователю (господин и т.д.)."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    is_premium = await db.is_premium(user_id)
    if not is_premium:
        sent = await message.answer(
            format_message_with_username(
                "Обращение к VIP настраивается только с активным Premium.",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    profile = await db.get_profile(user_id)
    current = profile.get("vip_address") or "не задано"
    variants = ["господин", "госпожа", "ваше величество", "босс", "царь"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=v, callback_data=f"vip_addr_{user_id}_{v}")] for v in variants
    ])
    sent = await message.answer(
        format_message_with_username(
            f"Текущее обращение: {current}\nВыбери или напиши новое:",
            username, first_name
        ),
        reply_markup=keyboard
    )
    asyncio.create_task(delete_message_after(sent))


@router.callback_query(F.data.startswith("vip_addr_"))
async def cb_vip_address(callback: CallbackQuery):
    parts = callback.data.split("_", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    cb_user_id = callback.from_user.id
    owner_id = int(parts[2])
    addr = parts[3]
    if cb_user_id != owner_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    await db.update_profile(cb_user_id, vip_address=addr)
    await callback.answer(f"Обращение: {addr}", show_alert=False)
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(Command("accountinfo"))
async def cmd_accountinfo(message: Message):
    """Описание «о себе» — всё после команды считается текстом профиля."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    text = (message.text or "").strip()
    after_cmd = text.replace("/accountinfo", "").strip() if text.startswith("/accountinfo") else ""
    if not after_cmd:
        profile = await db.get_profile(user_id)
        current = (profile.get("about_info") or "пока пусто")
        sent = await message.answer(
            format_message_with_username(
                f"Сейчас в «о себе»: {current}\n\nНапиши /accountinfo и текст описания.",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    await db.update_profile(user_id, about_info=after_cmd[:500])
    sent = await message.answer(
        format_message_with_username("Описание профиля сохранено ✅", username, first_name)
    )
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("accountstatus"))
async def cmd_accountstatus(message: Message):
    """Текущий статус и выбор купленного."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    current = user.get("status") or "нет"
    statuses = await db.get_all_statuses()
    lines = [f"Текущий статус: {current}\n", "Купить в /statusmarket:"]
    for s in statuses:
        lines.append(f"• {s['status_name']} — {s['price']} коинов")
    sent = await message.answer(
        format_message_with_username("\n".join(lines), username, first_name)
    )
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("statusmarket"))
async def cmd_statusmarket(message: Message):
    """Магазин статусов (Богач, Хомяк, Пубертат страны и т.д.) — покупка и установка. Обращение: @user, царь/дружок."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    profile = await db.get_profile(user_id)
    bot_address = (profile.get("bot_address") or profile.get("vip_address")) if profile else None
    bot_address = bot_address or "дружок"

    statuses = await db.get_all_statuses()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{s['status_name']} — {s['price']} 💰",
            callback_data=f"buy_st_{user_id}_{i}"
        )] for i, s in enumerate(statuses)
    ])
    caption = format_message_with_username(
        f"{bot_address}, 🏷️ <b>МАГАЗИН СТАТУСОВ</b>\n\n"
        f"Богач, Хомяк, Легенда, Потужномэн, Главный пубертат страны, Технолог и др. — покупай и носи в профиле.",
        username, first_name
    )
    photo_path = config.get_image_path("status.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"statusmarket photo {e}")
        sent = await message.answer(caption, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent))


@router.callback_query(F.data.startswith("buy_st_"))
async def cb_buy_status(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    cb_user_id = callback.from_user.id
    owner_id = int(parts[2])
    try:
        idx = int(parts[3])
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    if cb_user_id != owner_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return

    statuses = await db.get_all_statuses()
    if idx < 0 or idx >= len(statuses):
        await callback.answer("Нет такого статуса", show_alert=True)
        return
    status_name = statuses[idx]["status_name"]

    price = await effects_service.apply_price_discount(cb_user_id, config.STATUS_PRICE)
    balance = await db.get_balance(cb_user_id)
    if balance < price:
        await callback.answer(f"Недостаточно средств: {price} коинов", show_alert=True)
        return

    success, _, _, err = await balance_service.subtract_balance(
        user_id=cb_user_id, amount=price,
        command_source="/statusmarket", comment=f"Покупка статуса {status_name}",
        bot=callback.bot, chat_id=callback.message.chat.id,
        username=callback.from_user.username, first_name=callback.from_user.first_name,
        allow_negative=False
    )
    if not success:
        await callback.answer(err, show_alert=True)
        return

    await db.set_user_status(cb_user_id, status_name)
    await callback.answer(f"Статус «{status_name}» установлен!", show_alert=False)
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.message(Command("checkaccount"))
async def cmd_checkaccount(message: Message):
    """Просмотр чужого профиля: /checkaccount @user. Аватарка показывается, если загружена."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    resolved_id, resolved_username = resolve_recipient_from_message(message)
    if resolved_id is not None:
        target_id = resolved_id
        u = await db.get_user(target_id)
        if not u:
            await db.create_user(target_id, resolved_username if isinstance(resolved_username, str) else None)
    elif resolved_username:
        target_id = await db.get_user_id_by_username(resolved_username)
    else:
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            sent = await message.answer(
                format_message_with_username("Формат: /checkaccount @user", username, first_name)
            )
            asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
            return
        target_username = parts[1].strip().lstrip("@").lower()
        target_id = await db.get_user_id_by_username(target_username)

    if not target_id:
        sent = await message.answer(
            format_message_with_username("Пользователь не найден. Пусть сначала напишет боту.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    target = await db.get_user(target_id)
    profile = await db.get_profile(target_id)
    stats = await db.get_user_game_stats(target_id)
    t_username = target.get("username") or str(target_id)
    balance = target.get("balance", 0)
    level = target.get("level", 1)
    status = target.get("status") or "нет"
    is_premium = await db.is_premium(target_id)
    prem = "👑 Premium" if is_premium else "—"
    about = (profile.get("about_info") or "—")[:200]
    vip_addr = profile.get("vip_address") or "—"
    mmr = await db.get_user_mmr(target_id)
    league_info = db.get_league_info(mmr)
    p = league_info["progress"]
    filled = int(round(p * 5))
    bar = "■" * filled + "□" * (5 - filled)

    text = (
        f"👤 @{t_username}\n"
        f"💰 Баланс: {balance} | LVL: {level}\n"
        f"🏷️ Статус: {status} | {prem}\n"
        f"Обращение: {vip_addr}\n"
        f"📊 {league_info['name']} — {mmr} MMR [{bar}]\n"
        f"🎮 Игр: {stats['total']} (побед: {stats['wins']}, поражений: {stats['losses']})\n"
        f"О себе: {about}"
    )
    caption = format_message_with_username(text, username, first_name)

    avatar_path = profile.get("avatar_path")
    try:
        if avatar_path and "/" not in str(avatar_path) and "\\" not in str(avatar_path):
            sent = await message.answer_photo(photo=avatar_path, caption=caption)
        elif avatar_path:
            from pathlib import Path
            from aiogram.types import FSInputFile
            p = Path(avatar_path)
            if p.exists():
                sent = await message.answer_photo(FSInputFile(str(p)), caption=caption)
            else:
                sent = await message.answer(caption)
        else:
            sent = await message.answer(caption)
    except Exception as e:
        logger.error(f"checkaccount avatar {e}")
        sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("lvl"))
async def cmd_lvl(message: Message):
    """Текущий уровень."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    level_info = await db.get_user_level(user_id)
    lvl = level_info["level"]
    cost_next = level_info["level_up_cost"]
    total = level_info["total_coins_earned"]
    sent = await message.answer(
        format_message_with_username(
            f"Твой уровень: <b>{lvl}</b>\n"
            f"Всего заработано коинов: {total}\n"
            f"Следующий уровень: {cost_next} коинов (/lvlup)",
            username, first_name
        )
    )
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("lvlup"))
async def cmd_lvlup(message: Message):
    """Повышение уровня за коины (500, затем x2 каждый раз) или за 10000 заработанных."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    level_info = await db.get_user_level(user_id)
    cost = level_info["level_up_cost"]
    total = level_info["total_coins_earned"]
    need_coins = config.LEVEL_UP_COINS_REQUIREMENT
    next_trigger = level_info["level"] * need_coins
    if total >= next_trigger:
        old_lvl, new_lvl = await db.level_up(user_id)
        sent = await message.answer(
            format_message_with_username(
                f"Уровень повышен за заработанные коины! {old_lvl} → {new_lvl} 🎉",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    balance = await db.get_balance(user_id)
    cost = await effects_service.apply_price_discount(user_id, cost)
    if balance < cost:
        sent = await message.answer(
            format_message_with_username(
                f"Недостаточно коинов. Нужно {cost} (или заработай {need_coins} коинов для бесплатного lvlup).",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent))
        return

    success, _, _, err = await balance_service.subtract_balance(
        user_id=user_id, amount=cost,
        command_source="/lvlup", comment="Повышение уровня",
        message=message, username=username, first_name=first_name, allow_negative=False
    )
    if not success:
        sent = await message.answer(format_message_with_username(err, username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    old_lvl, new_lvl = await db.level_up(user_id)
    sent = await message.answer(
        format_message_with_username(
            f"Уровень повышен! {old_lvl} → {new_lvl} 🎉",
            username, first_name
        )
    )
    asyncio.create_task(delete_message_after(sent))


@router.message(Command("lvlcheck"))
async def cmd_lvlcheck(message: Message):
    """Уровень другого: /lvlcheck @user."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    resolved_id, resolved_username = resolve_recipient_from_message(message)
    if resolved_id is not None:
        target_id = resolved_id
        u = await db.get_user(target_id)
        if not u:
            await db.create_user(target_id, resolved_username if isinstance(resolved_username, str) else None)
    elif resolved_username:
        target_id = await db.get_user_id_by_username(resolved_username)
    else:
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            sent = await message.answer(
                format_message_with_username("Формат: /lvlcheck @user", username, first_name)
            )
            asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
            return
        target_id = await db.get_user_id_by_username(parts[1].strip().lstrip("@").lower())

    if not target_id:
        sent = await message.answer(
            format_message_with_username("Пользователь не найден.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    level_info = await db.get_user_level(target_id)
    target_user = await db.get_user(target_id)
    t_username = target_user.get("username") or str(target_id)
    sent = await message.answer(
        format_message_with_username(
            f"LVL @{t_username}: <b>{level_info['level']}</b>",
            username, first_name
        )
    )
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("vzortehnologa"))
async def cmd_vzortehnologa(message: Message):
    """VIP-only: показывает инвентарь пользователя. Без упоминания имени файла."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    is_premium = await db.is_premium(user_id)
    if not is_premium:
        sent = await message.answer(
            format_message_with_username(
                "Команда только для VIP. Купи Premium в /premium.",
                username, first_name
            )
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    resolved_id, resolved_username = resolve_recipient_from_message(message)
    if resolved_id is not None:
        target_id = resolved_id
        u = await db.get_user(target_id)
        if not u:
            await db.create_user(target_id, resolved_username if isinstance(resolved_username, str) else None)
    elif resolved_username:
        target_id = await db.get_user_id_by_username(resolved_username)
    else:
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            sent = await message.answer(
                format_message_with_username("Формат: /vzortehnologa @user", username, first_name)
            )
            asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
            return
        target_id = await db.get_user_id_by_username(parts[1].strip().lstrip("@").lower())

    if not target_id:
        sent = await message.answer(
            format_message_with_username("Пользователь не найден.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    target_user = await db.get_user(target_id)
    t_uname = target_user.get("username") or str(target_id)
    inv = await db.get_user_inventory(target_id)
    lines = [f"📦 Инвентарь @{t_uname}:"]
    if not inv:
        lines.append("Пусто.")
    else:
        for it in inv:
            lines.append(f"• {it['item_type']} {it['item_name']} x{it.get('quantity',1)}")
    caption = format_message_with_username("\n".join(lines), username, first_name)
    photo_path = config.get_image_path("vzor.jpg")
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption)
        else:
            sent = await message.answer(caption)
    except Exception as e:
        logger.error("vzortehnologa photo: %s", e)
        sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
