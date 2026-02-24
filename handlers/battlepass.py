"""
Боевой пропуск от технолога (как Brawl Pass).
/bp, /battlepass — уровень, квесты, награды free/premium.
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import config
from db import db
from utils import format_message_with_username, delete_message_after

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("bp", "battlepass"))
async def cmd_bp(message: Message):
    """Боевой пропуск: сезон, уровень, XP, квесты, кнопки забрать награды."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""

    season = await db.get_current_bp_season()
    if not season:
        text = format_message_with_username("Сезон боевого пропуска не активен. Скоро новый.", username, first_name)
        sent = await message.answer(text)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    progress = await db.get_user_bp_progress(user_id, season["id"])
    level, xp = progress["level"], progress["xp"]
    levels_data = await db.get_bp_levels(season["id"], max_level=50)
    xp_sum_curr = sum(L["xp_required"] for L in sorted(levels_data, key=lambda x: x["level"]) if L["level"] <= level)
    xp_sum_next = sum(L["xp_required"] for L in sorted(levels_data, key=lambda x: x["level"]) if L["level"] <= level + 1)
    xp_to_next = max(0, xp_sum_next - xp) if level < 50 else 0
    ends_ts = season["ends_at"]
    ends_str = datetime.fromtimestamp(ends_ts).strftime("%d.%m.%Y") if ends_ts else "—"
    is_premium = await db.is_premium(user_id)

    lines = [
        f"🎫 <b>Боевой пропуск</b> — {season['name']}",
        f"Окончание: {ends_str}",
        "",
        f"📊 Твой уровень: <b>{level}</b>  ·  XP: <b>{xp}</b>",
        f"До след. уровня: {xp_to_next} XP" if xp_to_next > 0 else "Макс. уровень по прогрессу.",
        "",
        "👑 Premium-пропуск: " + ("есть — забирай доп. награды" if is_premium else "нет (купи в /premium)"),
        "",
        "📋 <b>Квесты</b> (выполняй — получай XP):",
    ]
    quests = await db.get_bp_quests(season["id"])
    qprogress = await db.get_user_bp_quest_progress(user_id, season["id"])
    for q in quests[:10]:
        prog = qprogress.get(q["quest_key"], 0)
        t = q["target_value"]
        done = "✅" if prog >= t else f"{prog}/{t}"
        lines.append(f"• {q['title']} — {done} (+{q['xp_reward']} XP)")

    text = format_message_with_username("\n".join(lines), username, first_name)
    claimed = await db.get_bp_claimed_levels(user_id, season["id"])
    buttons = []
    for lvl in range(max(1, level - 1), min(level + 3, 51)):
        if lvl > level:
            break
        if (lvl, False) not in claimed:
            buttons.append(InlineKeyboardButton(text=f"🎁 Ур.{lvl} (free)", callback_data=f"bp_claim:{season['id']}:{lvl}:0"))
        if is_premium and (lvl, True) not in claimed:
            buttons.append(InlineKeyboardButton(text=f"👑 Ур.{lvl} (prem)", callback_data=f"bp_claim:{season['id']}:{lvl}:1"))
    kb = InlineKeyboardMarkup(inline_keyboard=[buttons[i:i+2] for i in range(0, len(buttons), 2)]) if buttons else None
    sent = await message.answer(text, reply_markup=kb)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.callback_query(F.data.startswith("bp_claim:"))
async def cb_bp_claim(callback: CallbackQuery):
    """Забрать награду за уровень БП."""
    user_id = callback.from_user.id
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    try:
        season_id = int(parts[1])
        level = int(parts[2])
        is_premium = int(parts[3]) == 1
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    progress = await db.get_user_bp_progress(user_id, season_id)
    if level > progress["level"]:
        await callback.answer("Сначала достигни этого уровня", show_alert=True)
        return
    if is_premium and not await db.is_premium(user_id):
        await callback.answer("Нужен Premium для доп. награды", show_alert=True)
        return
    ok = await db.claim_bp_level_reward(user_id, season_id, level, is_premium)
    if ok:
        await callback.answer(f"Награда за уровень {level} получена!")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    else:
        await callback.answer("Уже получено", show_alert=True)
