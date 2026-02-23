"""
Админка и баны бота Tehnolog Games.
Роли: Создатель, Админ, Модер, Младший модер.
Команды только создателя (по user_id): /admin, /stats, /economy, /logs.
Команды создателя: /addadmin, /addmoder, /addjuniormoder и т.д.
/ban @user время причина — лимиты по роли.
По всем вопросам — @DPOPTH. Создателя забанить нельзя.
"""

import asyncio
import re
import logging
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command

from config import config
from db import db
from utils import delete_message_after, format_message_with_username, get_creator_id, is_creator_by_username

router = Router()
logger = logging.getLogger(__name__)

CREATOR_USERNAME = "DPOPTH"


def _creator_only(handler):
    """Декоратор: только создатель по user_id."""
    async def wrapped(message: Message, *args, **kwargs):
        if not await _is_creator(message.from_user.id, message.from_user.username):
            await message.answer(format_message_with_username("Доступ только у создателя бота.", message.from_user.username, message.from_user.first_name))
            return
        return await handler(message, *args, **kwargs)
    return wrapped


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Меню админ-панели — только создатель."""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return
    username = message.from_user.username
    first_name = message.from_user.first_name
    text = format_message_with_username(
        "👑 <b>АДМИН-ПАНЕЛЬ</b> (только создатель)\n\n"
        "/stats — общая статистика бота (пользователи, игры, баланс)\n"
        "/economy — оборот, налог Технолога, топ выигрышей и проигрышей\n"
        "/logs [N] — последние N записей логов игр (по умолчанию 30)\n\n"
        "Роли и баны: /addadmin, /addmoder, /ban, /unban, /deladmin и т.д.\n\n"
        "/skinna0 @user — сброс баланса на 0 за жульничество (только создатель)",
        username, first_name
    )
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("skinna0"))
async def cmd_skinna0(message: Message):
    """Анти-жульничество: сброс баланса цели на 0. Только создатель."""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        sent = await message.answer("Использование: /skinna0 @username или /skinna0 user_id")
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    target_str = parts[1].strip().lstrip("@")
    target_id = None
    if target_str.isdigit():
        target_id = int(target_str)
    else:
        target_id = await db.get_user_id_by_username(target_str)
    if not target_id:
        sent = await message.answer("Пользователь не найден.")
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    if config.CREATOR_ID and target_id == config.CREATOR_ID:
        sent = await message.answer("Создателя скинуть нельзя.")
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    balance_before = await db.get_balance(target_id)
    await db.set_balance_direct(target_id, 0)
    target_user = await db.get_user(target_id)
    target_name = f"@{target_user.get('username') or target_id}" if target_user else str(target_id)
    sent = await message.answer(
        f"⚠️ Баланс {target_name} сброшен на 0 (было {balance_before:,} коинов). За жульничество."
    )
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("skinna0: target_id=%s balance_was=%s by creator", target_id, balance_before)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Общая статистика: пользователи, игры, сумма балансов — только создатель."""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return
    username = message.from_user.username
    first_name = message.from_user.first_name
    try:
        st = await db.get_bot_stats()
        text = format_message_with_username(
            f"📊 <b>СТАТИСТИКА БОТА</b>\n\n"
            f"Пользователей: <b>{st['users']}</b>\n"
            f"Всего игр: <b>{st['games_total']}</b>\n"
            f"Сумма балансов: <b>{st['total_balance']}</b> коинов",
            username, first_name
        )
    except Exception as e:
        logger.exception("stats: %s", e)
        text = format_message_with_username("Ошибка получения статистики.", username, first_name)
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("economy"))
async def cmd_economy(message: Message):
    """Оборот, налог, топ выигрышей/проигрышей — только создатель."""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return
    username = message.from_user.username
    first_name = message.from_user.first_name
    try:
        ec = await db.get_economy_stats()
        lines = [
            "💰 <b>ЭКОНОМИКА</b>",
            f"Оборот (всего операций): <b>{ec['turnover']}</b> коинов",
            f"Налог Технолога (с выигрышей): <b>{ec['total_tax']}</b> коинов",
            "",
            "🏆 <b>Топ-10 выигрышей</b>",
        ]
        for i, w in enumerate(ec["top_wins"][:10], 1):
            ts = datetime.fromtimestamp(w["created_at"]).strftime("%d.%m %H:%M") if w.get("created_at") else ""
            lines.append(f"{i}. @{w.get('username') or w['user_id']} | {w['command']} | +{w['balance_change']} | {ts}")
        if not ec["top_wins"]:
            lines.append("— нет данных")
        lines.append("")
        lines.append("📉 <b>Топ-10 проигрышей</b>")
        for i, w in enumerate(ec["top_losses"][:10], 1):
            ts = datetime.fromtimestamp(w["created_at"]).strftime("%d.%m %H:%M") if w.get("created_at") else ""
            lines.append(f"{i}. @{w.get('username') or w['user_id']} | {w['command']} | {w['balance_change']} | {ts}")
        if not ec["top_losses"]:
            lines.append("— нет данных")
        text = format_message_with_username("\n".join(lines), username, first_name)
    except Exception as e:
        logger.exception("economy: %s", e)
        text = format_message_with_username("Ошибка получения экономики.", username, first_name)
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("logs"))
async def cmd_logs(message: Message):
    """Последние N записей логов игр — только создатель. Игроку логи не показываются."""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return
    username = message.from_user.username
    first_name = message.from_user.first_name
    parts = (message.text or "").strip().split()
    limit = 30
    if len(parts) >= 2:
        try:
            limit = min(int(parts[1]), 100)
        except (ValueError, TypeError):
            pass
    try:
        rows = await db.get_admin_logs(limit=limit)
        lines = [f"📋 <b>ЛОГИ ИГР</b> (последние {len(rows)} записей)", ""]
        for r in rows:
            ts = datetime.fromtimestamp(r["created_at"]).strftime("%d.%m %H:%M") if r.get("created_at") else ""
            un = f"@{r.get('username')}" if r.get("username") else str(r["user_id"])
            tax_s = f" налог {r['tax']}" if r.get("tax") else ""
            lines.append(f"{un} | {r['command']} | ставка {r['bet']} | {r['result']} | Δ{r['balance_change']}{tax_s} | {ts}")
        if not rows:
            lines.append("— нет записей")
        text = format_message_with_username("\n".join(lines), username, first_name)
    except Exception as e:
        logger.exception("logs: %s", e)
        text = format_message_with_username("Ошибка получения логов.", username, first_name)
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Только для создателя: краткая отладочная информация (активные сессии и т.п.)."""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return
    username = message.from_user.username
    first_name = message.from_user.first_name
    try:
        from handlers.games import get_active_sessions_debug
        counts = get_active_sessions_debug()
        text = format_message_with_username(
            "🔧 <b>DEBUG</b> (только создатель)\n\n"
            f"Активных сессий: kripta={counts['kripta']}, almaz={counts['almaz']}, plsdon={counts['plsdon']}",
            username, first_name
        )
    except Exception as e:
        text = format_message_with_username(f"Ошибка debug: {e}", username, first_name)
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


async def _is_creator(user_id: int, username: str = None) -> bool:
    """@DPOPTH считается создателем. Создателя нельзя банить, ограничивать, кикать."""
    if is_creator_by_username(username):
        return True
    cid = await get_creator_id()
    return bool(cid and user_id == cid)


async def _get_admin_ids():
    ids_from_config = config.get_admin_ids_list()
    ids_from_db = await db.get_users_with_role("admin")
    return list(set(ids_from_config + ids_from_db))


async def _get_moder_ids():
    ids_from_config = config.get_moder_ids_list()
    ids_from_db = await db.get_users_with_role("moder")
    return list(set(ids_from_config + ids_from_db))


async def _get_junior_moder_ids():
    ids_from_config = config.get_junior_moder_ids_list()
    ids_from_db = await db.get_users_with_role("juniormoder")
    return list(set(ids_from_config + ids_from_db))


async def _is_admin(user_id: int) -> bool:
    return user_id in await _get_admin_ids()


async def _is_moder(user_id: int) -> bool:
    return user_id in await _get_moder_ids()


async def _is_junior_moder(user_id: int) -> bool:
    return user_id in await _get_junior_moder_ids()


async def _max_ban_seconds_by_role(actor_id: int, username: str = None) -> int:
    """Максимальная длительность бана по роли: создатель — навсегда, админ — 1ч, модер — 30мин, мл.модер — 10мин."""
    if await _is_creator(actor_id, username):
        return 0  # 0 = без ограничения (навсегда)
    admin_ids = await _get_admin_ids()
    moder_ids = await _get_moder_ids()
    junior_ids = await _get_junior_moder_ids()
    if actor_id in admin_ids:
        return getattr(config, "BAN_MAX_ADMIN", 3600)
    if actor_id in moder_ids:
        return getattr(config, "BAN_MAX_MODER", 1800)
    if actor_id in junior_ids:
        return getattr(config, "BAN_MAX_JUNIOR_MODER", 600)
    return 0


def _parse_ban_duration(text: str) -> int:
    """Парсит «1ч», «30м», «навсегда» и т.п. в секунды. -1 = навсегда."""
    text = (text or "").strip().lower()
    if not text or text in ("навсегда", "forever", "∞"):
        return -1
    m = re.match(r"^(\d+)\s*(ч|час|часа|часов|h|hour)?$", text)
    if m:
        return int(m.group(1)) * 3600
    m = re.match(r"^(\d+)\s*(м|мин|минут|минуты|m|min)?$", text)
    if m:
        return int(m.group(1)) * 60
    m = re.match(r"^(\d+)\s*(с|сек|секунд)?$", text)
    if m:
        return int(m.group(1))
    return 0


async def _resolve_user_from_message(message: Message):
    """(user_id, username) из текста /ban @user или mention."""
    from utils import resolve_recipient_from_message
    uid, uname = resolve_recipient_from_message(message)
    if not uid and uname:
        uid = await db.get_user_id_by_username(uname)
    return uid, uname


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    """ /ban @user время причина. Лимиты: создатель — навсегда, админ — 1ч, модер — 30мин, мл.модер — 10мин. """
    actor_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    admin_ids = await _get_admin_ids()
    moder_ids = await _get_moder_ids()
    jr_ids = await _get_junior_moder_ids()
    if not await _is_creator(actor_id, username) and actor_id not in admin_ids and actor_id not in moder_ids and actor_id not in jr_ids:
        sent = await message.answer(format_message_with_username("Нет прав на бан.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    target_id, _ = await _resolve_user_from_message(message)
    if not target_id:
        sent = await message.answer(format_message_with_username("Укажи пользователя: /ban @user время причина", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if await _is_creator(target_id, (await db.get_user(target_id) or {}).get("username")):
        sent = await message.answer(format_message_with_username("Создателя забанить нельзя. По всем вопросам — @" + CREATOR_USERNAME, username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    parts = (message.text or "").strip().split(maxsplit=3)
    if len(parts) < 4:
        sent = await message.answer(format_message_with_username("Формат: /ban @user время причина (например: 1ч, 30м, навсегда)", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    duration_str = parts[2]
    reason = parts[3][:200] if len(parts) > 3 else "без причины"
    duration_sec = _parse_ban_duration(duration_str)
    max_sec = await _max_ban_seconds_by_role(actor_id, username)
    if max_sec == 0 and not await _is_creator(actor_id, username):
        max_sec = 3600
    if duration_sec == -1:
        if max_sec != 0:
            sent = await message.answer(format_message_with_username("Ты не можешь банить навсегда. Лимит по твоей роли.", username, first_name))
            asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
            return
        ban_until = None
    else:
        if max_sec != 0 and duration_sec > max_sec:
            duration_sec = max_sec
        ban_until = int(datetime.now().timestamp()) + duration_sec

    ok = await db.set_user_ban(target_id, True, ban_until)
    if not ok:
        sent = await message.answer(format_message_with_username("Не удалось забанить (возможно, это создатель).", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    now_ts = int(datetime.now().timestamp())
    await db.insert_ban(target_id, actor_id, reason, now_ts, ban_until, "commands")

    target_user = await db.get_user(target_id)
    target_tag = f"@{target_user['username']}" if target_user and target_user.get("username") else str(target_id)
    ban_text = f"🚫 Забанил {target_tag} на {duration_str} — причина: {reason}"
    try:
        photo_path = config.get_image_path("Ban.jpg")
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=format_message_with_username(ban_text + "\n\nТеперь ты чилишь на банановых островах.", username, first_name))
        else:
            sent = await message.answer(format_message_with_username(ban_text + "\n\nТеперь ты чилишь на банановых островах.", username, first_name))
    except Exception:
        sent = await message.answer(format_message_with_username(ban_text, username, first_name))
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("Ban: actor=%s target=%s duration=%s reason=%s", actor_id, target_id, duration_str, reason)


@router.message(Command("adddenga"))
async def cmd_adddenga(message: Message):
    """Только создатель: выдать любое количество коинов любому пользователю. /adddenga @user сумма"""
    if not await _is_creator(message.from_user.id, message.from_user.username):
        return

    username = message.from_user.username
    first_name = message.from_user.first_name
    target_id, _ = await _resolve_user_from_message(message)
    if not target_id:
        sent = await message.answer(format_message_with_username("Формат: /adddenga @user сумма", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    parts = (message.text or "").strip().split(maxsplit=2)
    if len(parts) < 3:
        sent = await message.answer(format_message_with_username("Формат: /adddenga @user сумма", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    try:
        amount = int(parts[2])
        if amount <= 0:
            raise ValueError("Сумма должна быть положительной")
    except (ValueError, TypeError):
        sent = await message.answer(format_message_with_username("Укажи корректную сумму (целое число).", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    from services.balance import balance_service
    target_user = await db.get_user(target_id)
    target_tag = f"@{target_user['username']}" if target_user and target_user.get("username") else str(target_id)
    success, _, balance_after = await balance_service.add_balance(
        user_id=target_id,
        amount=amount,
        command_source="/adddenga",
        comment=f"Выдано создателем пользователю {target_tag}",
        message=None,
        username=target_user.get("username") if target_user else None,
        first_name=target_user.get("first_name") if target_user else None
    )
    if success:
        sent = await message.answer(format_message_with_username(f"✅ Выдано {target_tag} {amount} коинов. Баланс: {balance_after}", username, first_name))
    else:
        sent = await message.answer(format_message_with_username("Ошибка начисления.", username, first_name))
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("Adddenga: creator gave %s coins to user %s", amount, target_id)


@router.message(Command("unban"))
async def cmd_unban(message: Message):
    """ /unban @user причина. Создатель — всегда. Админ — только если бан не перманентный. """
    actor_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    admin_ids = await _get_admin_ids()
    if not await _is_creator(actor_id, username) and actor_id not in admin_ids:
        sent = await message.answer(format_message_with_username("Нет прав на разбан.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    target_id, _ = await _resolve_user_from_message(message)
    if not target_id:
        sent = await message.answer(format_message_with_username("Формат: /unban @user причина", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    parts = (message.text or "").strip().split(maxsplit=2)
    reason = parts[2][:200] if len(parts) >= 3 else "разбанен"

    target_user = await db.get_user(target_id)
    if not target_user:
        sent = await message.answer(format_message_with_username("Пользователь не найден.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if not target_user.get("is_banned"):
        sent = await message.answer(format_message_with_username("Пользователь не забанен.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    ban_until = target_user.get("ban_until")
    is_permanent = ban_until is None
    if not await _is_creator(actor_id, username) and is_permanent:
        sent = await message.answer(format_message_with_username("Только создатель может разбанить при перманентном бане.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    await db.set_user_ban(target_id, False, None)
    await db.mark_ban_unbanned(target_id)
    target_tag = f"@{target_user['username']}" if target_user.get("username") else str(target_id)
    sent = await message.answer(format_message_with_username(f"✅ Разбанил {target_tag}. Причина: {reason}", username, first_name))
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("Unban: actor=%s target=%s target_tag=%s reason=%s", actor_id, target_id, target_tag, reason)


@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    """Только создатель @DPOPTH: /addadmin @user время|навсегда """
    if not await _is_creator(message.from_user.id, message.from_user.username):
        sent = await message.answer(format_message_with_username("Только создатель может добавлять админов. По всем вопросам — @" + CREATOR_USERNAME, message.from_user.username, message.from_user.first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    await _add_role_cmd(message, "admin")


@router.message(Command("addmoder"))
async def cmd_addmoder(message: Message):
    """Только создатель @DPOPTH: /addmoder @user время|навсегда """
    if not await _is_creator(message.from_user.id, message.from_user.username):
        sent = await message.answer(format_message_with_username("Только создатель может добавлять модеров.", message.from_user.username, message.from_user.first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    await _add_role_cmd(message, "moder")


@router.message(Command("addjuniormoder"))
async def cmd_addjuniormoder(message: Message):
    """Только создатель @DPOPTH: /addjuniormoder @user время|навсегда """
    if not await _is_creator(message.from_user.id, message.from_user.username):
        sent = await message.answer(format_message_with_username("Только создатель может добавлять младших модеров.", message.from_user.username, message.from_user.first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    await _add_role_cmd(message, "juniormoder")


async def _add_role_cmd(message: Message, role: str):
    target_id, _ = await _resolve_user_from_message(message)
    if not target_id:
        sent = await message.answer(format_message_with_username(f"Формат: /add{role} @user время|навсегда", message.from_user.username, message.from_user.first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    parts = (message.text or "").strip().split(maxsplit=2)
    until_ts = None
    if len(parts) >= 3:
        s = parts[2].strip().lower()
        if s and s not in ("навсегда", "forever"):
            sec = _parse_ban_duration(s)
            if sec > 0:
                until_ts = int(datetime.now().timestamp()) + sec
    await db.add_role(target_id, role, message.from_user.id, until_ts)
    role_name = {"admin": "админ", "moder": "модер", "juniormoder": "мл.модер"}.get(role, role)
    sent = await message.answer(format_message_with_username(f"Роль «{role_name}» выдана пользователю.", message.from_user.username, message.from_user.first_name))
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("deladmin"))
async def cmd_deladmin(message: Message):
    """ /deladmin @user причина """
    await _del_role_cmd(message, "admin")


@router.message(Command("delmoder"))
async def cmd_delmoder(message: Message):
    """ /delmoder @user причина """
    await _del_role_cmd(message, "moder")


@router.message(Command("deljuniormoder"))
async def cmd_deljuniormoder(message: Message):
    """ /deljuniormoder @user причина """
    await _del_role_cmd(message, "juniormoder")


async def _del_role_cmd(message: Message, role: str):
    actor_id = message.from_user.id
    target_id, _ = await _resolve_user_from_message(message)
    if not target_id:
        sent = await message.answer(format_message_with_username(f"Формат: /del{role} @user причина", message.from_user.username, message.from_user.first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    parts = (message.text or "").strip().split(maxsplit=2)
    reason = parts[2][:100] if len(parts) >= 3 else "без причины"
    # Нельзя снять роль выше своей
    target_user = await db.get_user(target_id)
    if await _is_creator(target_id, target_user.get("username") if target_user else None):
        sent = await message.answer(format_message_with_username("Нельзя снять роль создателя.", message.from_user.username, message.from_user.first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    await db.remove_role(target_id, role)
    role_name = {"admin": "админ", "moder": "модер", "juniormoder": "мл.модер"}.get(role, role)
    sent = await message.answer(format_message_with_username(f"Роль «{role_name}» снята. Причина: {reason}", message.from_user.username, message.from_user.first_name))
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
