"""
Утилиты для бота Tehnolog Games
Автоудаление сообщений, форматирование текста, мем-фишка «дружок»
"""

import asyncio
import random
from typing import Optional, Tuple

from aiogram import Bot
from aiogram.types import Message

from config import config

# Сообщение при недостатке баланса для ставки (игры)
INSUFFICIENT_BALANCE_PHRASE = "с тебя нечего взять — ты нищет 😭"


def format_insufficient_balance(username: str = None, first_name: str = None) -> str:
    """Форматирует сообщение «@user, дружок, с тебя нечего взять — ты нищет 😭» для ответа при ставке > баланс."""
    return format_message_with_username(INSUFFICIENT_BALANCE_PHRASE, username, first_name)


# Сообщение при сбое в игре (деньги не списывать, писать в лог)
GAME_ERROR_PHRASE = "произошёл сбой, Технолог уже чинит ⚙️"


def format_game_error(username: str = None, first_name: str = None) -> str:
    """Форматирует сообщение «@user, дружок, произошёл сбой, Технолог уже чинит ⚙️» при ошибке в игре."""
    return format_message_with_username(GAME_ERROR_PHRASE, username, first_name)


# Мем-фишка: каждое сообщение бота начинается или заканчивается фразой «@user, дружок, ...»
TEHNOLOG_PHRASES = [
    "дружок, подожди секунду",
    "дружок, ты проиграл",
    "дружок, удача сегодня на твоей стороне",
    "дружок, держись",
    "дружок, так держать",
    "дружок, ничего — бывает",
    "дружок, красавчик",
    "дружок, погнали дальше",
    "дружок, не сдавайся",
    "дружок, вот это да",
    "дружок, молодец",
    "дружок, в следующий раз повезёт",
    "дружок, коины ждут",
    "дружок, рискуй с умом",
    "дружок, удача любит смелых",
]


async def delete_message_after(message: Message, seconds: int = None):
    """
    Автоматическое удаление сообщения через указанное время.
    """
    if seconds is None:
        seconds = config.MESSAGE_DELETE_TIMEOUT
    if seconds <= 0:
        return
    try:
        await asyncio.sleep(seconds)
        await message.delete()
    except Exception:
        pass


async def delete_message_after_by_id(bot: Bot, chat_id: int, message_id: int, seconds: int = None):
    """Удаление сообщения по chat_id и message_id через указанное время."""
    if seconds is None:
        seconds = config.MESSAGE_DELETE_TIMEOUT
    if seconds <= 0:
        return
    try:
        await asyncio.sleep(seconds)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def resolve_recipient_from_message(message: Message) -> Tuple[Optional[int], Optional[str]]:
    """
    Извлекает получателя из сообщения: по entity text_mention (user_id из Telegram)
    или по тексту @username.
    Returns:
        (user_id, username) — user_id может быть None, тогда ищем по username в БД.
    """
    text = (message.text or "").strip()
    if not text or not message.entities:
        return None, None
    # Проверяем text_mention — Telegram передаёт user_id при упоминании по имени
    for ent in message.entities:
        if ent.type == "text_mention" and getattr(ent, "user", None):
            u = ent.user
            return u.id, (u.username or u.first_name or str(u.id))
        if ent.type == "mention":
            # mention — это @username в тексте
            part = text[ent.offset : ent.offset + ent.length]
            username_clean = part.lstrip("@").strip().lower()
            return None, username_clean or None
    # Парсим первый аргумент после команды как @user
    parts = text.split(maxsplit=2)
    if len(parts) >= 2:
        raw = parts[1].strip().lstrip("@").strip().lower()
        if raw:
            return None, raw
    return None, None


def format_username(username: Optional[str], first_name: Optional[str] = None) -> str:
    """
    Форматирование username для сообщений
    Всегда возвращает строку начинающуюся с @
    
    Args:
        username: Username пользователя
        first_name: Имя пользователя (fallback)
        
    Returns:
        Отформатированный username
    """
    if username:
        return f"@{username}"
    elif first_name:
        return f"@{first_name}"
    else:
        return "@Пользователь"


def _tehnolog_wrap(content: str, user_tag: str) -> str:
    """Обёртка: сообщение начинается или заканчивается фразой «@user, дружок, ...» (случайно)."""
    phrase = random.choice(TEHNOLOG_PHRASES)
    block = f"{user_tag}, {phrase}."
    if random.random() < 0.5:
        return f"{block}\n\n{content}"
    return f"{content}\n\n{block}"


def format_message_with_username(text: str, username: Optional[str], 
                                first_name: Optional[str] = None) -> str:
    """
    Форматирование сообщения: «@user, текст». Упоминание в тему, без случайных фраз.
    """
    user_tag = format_username(username, first_name)
    return f"{user_tag}, {text}"


async def format_message_vip_async(text: str, user_id: int) -> str:
    """
    Форматирование сообщения с обращением к пользователю (vip_address или как в профиле).
    Упоминание в тему, без случайных фраз.
    """
    from db import db
    profile = await db.get_profile(user_id)
    bot_address = profile.get("bot_address") or profile.get("vip_address") if profile else None
    user = await db.get_user(user_id)
    username = user.get("username") if user else None
    user_tag = f"@{username}" if username else f"ID{user_id}"
    if bot_address:
        return f"{user_tag}, {bot_address}\n\n{text}"
    return f"{user_tag}, {text}"


async def format_message_game_result_async(text: str, user_id: int) -> str:
    """
    Для итогов игр: @user, царь батюшка, извольте молвить — вы выиграли / вы проиграли и т.д.
    Использует обращение из профиля (bot_address / vip_address).
    """
    from db import db
    profile = await db.get_profile(user_id)
    bot_address = profile.get("bot_address") or profile.get("vip_address") if profile else None
    user = await db.get_user(user_id)
    username = user.get("username") if user else None
    user_tag = f"@{username}" if username else f"ID{user_id}"
    if bot_address:
        return f"{user_tag}, {bot_address}, извольте молвить — {text}"
    return f"{user_tag}, извольте молвить — {text}"


# Единственный источник создателя: @DPOPTH. Кэш для разрешения по username.
_creator_id_cache: Optional[int] = None


async def get_creator_id() -> Optional[int]:
    """
    ID создателя: из config.CREATOR_ID или по username DPOPTH из БД.
    Создателя нельзя банить, ограничивать, кикать. Во всех проверках ролей @DPOPTH = creator.
    """
    global _creator_id_cache
    if getattr(config, "CREATOR_ID", None):
        return config.CREATOR_ID
    if _creator_id_cache is not None:
        return _creator_id_cache
    from db import db
    _creator_id_cache = await db.get_user_id_by_username("DPOPTH")
    return _creator_id_cache


async def notify_creator(bot: Bot, text: str) -> None:
    """Отправить уведомление создателю (по user_id). Логи игроку не показываются."""
    if not bot:
        return
    try:
        creator_id = await get_creator_id()
        if creator_id:
            await bot.send_message(chat_id=creator_id, text=f"🔔 <b>Уведомление</b>\n\n{text}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("notify_creator: %s", e)


def is_creator_by_username(username: Optional[str]) -> bool:
    """Проверка по username: @DPOPTH считается создателем."""
    if not username:
        return False
    return str(username).strip().upper() == "DPOPTH"
