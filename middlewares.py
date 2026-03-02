"""
Middleware для бота YandexPticaGPT v0.5
Антифлуд, cooldown, проверка налога, логирование, реклама
"""

import time
import random
import logging
import asyncio
from typing import Callable, Dict, Any, Awaitable
from datetime import datetime, timedelta

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, Update
from aiogram.exceptions import TelegramBadRequest

from config import config
from db import db
from utils import format_message_with_username, format_message_vip_async, is_creator_by_username, delete_message_after

# Настройка логирования
logger = logging.getLogger(__name__)

# Создатель по username — нельзя банить
CREATOR_USERNAME = getattr(config, "CREATOR_USERNAME", "DPOPTH")


async def set_command_cooldown(user_id: int, command: str):
    """
    Вспомогательная функция для установки cooldown команды
    Вызывается в handlers после успешной обработки команды
    
    Args:
        user_id: ID пользователя
        command: Название команды
    """
    await db.set_cooldown(user_id, command)


def _is_creator(event: TelegramObject) -> bool:
    """Проверка: пользователь — создатель @DPOPTH (по ID или username). Создателя нельзя банить, ограничивать, кикать."""
    uid = None
    uname = None
    if isinstance(event, Message) and event.from_user:
        uid = event.from_user.id
        uname = event.from_user.username
    elif isinstance(event, CallbackQuery) and event.from_user:
        uid = event.from_user.id
        uname = event.from_user.username
    if getattr(config, "CREATOR_ID", None) and uid == config.CREATOR_ID:
        return True
    if is_creator_by_username(uname):
        return True
    return False


class AntifloodMiddleware(BaseMiddleware):
    """
    Антиспам: после 10 быстрых сообщений → предупреждение.
    Затем каждое сообщение уменьшает счётчик 5→4→3→2→1→БАН (1 час).
    Сброс счётчика через 30 сек без сообщений.
    """
    
    def __init__(self):
        super().__init__()
        self.max_messages = getattr(config, "ANTISPAM_MAX_MESSAGES", 10)
        self.messages_to_ban = getattr(config, "ANTISPAM_MESSAGES_TO_BAN", 5)
        self.window_seconds = getattr(config, "ANTISPAM_WINDOW_SECONDS", 60)
        self.reset_seconds = getattr(config, "ANTISPAM_RESET_SECONDS", 30)
        self.ban_duration = getattr(config, "ANTISPAM_BAN_DURATION", 3600)
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        if _is_creator(event):
            antispam_data = await db.get_antispam(user_id)
            if antispam_data and antispam_data.get("is_muted"):
                await db.update_antispam(
                    user_id, antispam_data["message_count"], antispam_data["window_start"],
                    is_muted=False, mute_until=None,
                    messages_left_to_ban=None, last_message_at=int(time.time())
                )
            return await handler(event, data)
        
        antispam_data = await db.get_antispam(user_id)
        now = int(time.time())
        last_message_at = antispam_data.get("last_message_at") if antispam_data else None
        
        # Сброс: если прошло 30 сек без сообщений — сбрасываем окно и счётчик до бана
        if last_message_at and (now - last_message_at) >= self.reset_seconds:
            antispam_data = None  # сбрасываем логику
            message_count = 1
            window_start = now
            messages_left_to_ban = None
            is_muted = False
            mute_until = None
        elif antispam_data:
            window_start = antispam_data["window_start"]
            message_count = antispam_data["message_count"]
            is_muted = antispam_data["is_muted"]
            mute_until = antispam_data["mute_until"]
            messages_left_to_ban = antispam_data.get("messages_left_to_ban")
            
            if is_muted and mute_until and mute_until > now:
                logger.warning(f"Пользователь {user_id} заблокирован антиспамом до {datetime.fromtimestamp(mute_until)}")
                bot = getattr(event, "bot", None) or (data.get("bot") if isinstance(data, dict) else None)
                if bot:
                    try:
                        until_str = datetime.fromtimestamp(mute_until).strftime("%H:%M")
                        chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
                        await bot.send_message(chat_id, f"Ты временно заблокирован за спам до {until_str}. Отдыхай 🍌")
                    except Exception as e:
                        logger.debug("Antiflood mute message: %s", e)
                return
            
            if is_muted and mute_until and mute_until <= now:
                is_muted = False
                mute_until = None
            
            # Окно: если вышли за window_seconds — начинаем окно заново (но messages_left_to_ban не сбрасываем по времени, только по 30 сек без сообщений)
            if (now - window_start) >= self.window_seconds:
                window_start = now
                message_count = 1
                if messages_left_to_ban is None:
                    messages_left_to_ban = None  # окно сбросилось — счётчик до бана только если уже был
            else:
                message_count += 1
            
            # Уже в режиме отсчёта до бана
            if messages_left_to_ban is not None:
                messages_left_to_ban -= 1
                if messages_left_to_ban <= 0:
                    # БАН (мют)
                    mute_until = now + self.ban_duration
                    await db.update_antispam(
                        user_id, message_count, window_start,
                        is_muted=True, mute_until=mute_until,
                        messages_left_to_ban=0, last_message_at=now
                    )
                    await self._send_ban_message(event, user_id)
                    logger.warning(f"Пользователь {user_id} забанен антиспамом на 1 час")
                    try:
                        bot = getattr(event, "bot", None) or (data.get("bot") if isinstance(data, dict) else None)
                        if bot:
                            from utils import notify_creator
                            un = event.from_user.username if event.from_user else ""
                            asyncio.create_task(notify_creator(bot, f"Антиспам: user_id={user_id} (@{un}) заблокирован на 1 ч (частый спам команд)."))
                    except Exception as e:
                        logger.debug("notify_creator antispam: %s", e)
                    return
                else:
                    await db.update_antispam(
                        user_id, message_count, window_start,
                        is_muted=False, mute_until=None,
                        messages_left_to_ban=messages_left_to_ban, last_message_at=now
                    )
                    await self._send_warning_message(event, user_id, messages_left_to_ban)
                    return
            
            # Первый раз достигли 10 сообщений — предупреждение «до бана осталось: 5 сообщений»
            if message_count >= self.max_messages:
                messages_left_to_ban = self.messages_to_ban
                await db.update_antispam(
                    user_id, message_count, window_start,
                    is_muted=False, mute_until=None,
                    messages_left_to_ban=messages_left_to_ban, last_message_at=now
                )
                await self._send_warning_message(event, user_id, messages_left_to_ban)
                return
            
            await db.update_antispam(
                user_id, message_count, window_start,
                is_muted, mute_until,
                messages_left_to_ban=None, last_message_at=now
            )
        else:
            message_count = 1
            window_start = now
            await db.update_antispam(
                user_id, message_count, window_start,
                is_muted=False, mute_until=None,
                messages_left_to_ban=None, last_message_at=now
            )
        
        return await handler(event, data)
    
    async def _send_warning_message(self, event: TelegramObject, user_id: int, left: int):
        text = "полегче — ещё раз и улетишь 🍌"
        try:
            msg = await format_message_vip_async(text, user_id)
        except Exception:
            un = event.from_user.username if event.from_user else None
            fn = event.from_user.first_name if event.from_user else None
            msg = format_message_with_username(text, un, fn)
        chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
        try:
            await event.bot.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Antispam warning send: {e}")
    
    async def _send_ban_message(self, event: TelegramObject, user_id: int):
        text = "ты улетел на Банановые острова 🍌 Отдыхай 1 час."
        try:
            msg = await format_message_vip_async(text, user_id)
        except Exception:
            un = event.from_user.username if event.from_user else None
            fn = event.from_user.first_name if event.from_user else None
            msg = format_message_with_username(text, un, fn)
        chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
        try:
            from aiogram.types import FSInputFile
            photo_path = config.get_image_path("Ban.jpg")
            if photo_path.exists():
                await event.bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=msg)
            else:
                await event.bot.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Antispam ban photo send: {e}")


# Хранилище для анти-бота: last_ts и список (ts, action_key) по user_id
_abuse_last_ts: Dict[int, float] = {}
_abuse_actions: Dict[int, list] = {}  # list of (ts, action_key)
_abuse_lock = asyncio.Lock()


class AntiAbuseMiddleware(BaseMiddleware):
    """
    Анти-бот: задержка между командами, лимит кнопок, проверка паттернов.
    При авто-клике/эксплойте — временный бан, лог, уведомление админу.
    """
    
    def __init__(self):
        super().__init__()
        self.min_delay = getattr(config, "MIN_DELAY_BETWEEN_ACTIONS", 1.0)
        self.max_per_sec = getattr(config, "MAX_ACTIONS_PER_SECOND", 6)
        self.max_same_callback = getattr(config, "MAX_SAME_CALLBACK_IN_WINDOW", 15)
        self.window_sec = getattr(config, "ANTIBOT_WINDOW_SECONDS", 60)
        self.auto_ban_duration = getattr(config, "AUTO_BAN_DURATION", 3600)
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        user_id = event.from_user.id if event.from_user else None
        if not user_id or _is_creator(event):
            return await handler(event, data)
        
        now = time.time()
        action_key = None
        if isinstance(event, Message):
            action_key = (event.text or "").strip().split(maxsplit=1)[0] if (event.text or "").strip() else "msg"
        elif isinstance(event, CallbackQuery):
            action_key = (event.data or "")[:100]
        
        async with _abuse_lock:
            last_ts = _abuse_last_ts.get(user_id, 0)
            if now - last_ts < self.min_delay:
                _abuse_last_ts[user_id] = now
                await self._send_slow_down(event, user_id)
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:
                        pass
                return None
            
            actions = _abuse_actions.get(user_id, [])
            actions.append((now, action_key))
            cutoff = now - self.window_sec
            actions = [(t, k) for t, k in actions if t > cutoff]
            _abuse_actions[user_id] = actions
            _abuse_last_ts[user_id] = now
            
            # Действий в последнюю секунду
            in_last_sec = sum(1 for t, _ in actions if t > now - 1)
            if in_last_sec >= self.max_per_sec:
                await self._apply_autoban(event, user_id, f"эксплойт/автоклик: {in_last_sec} действий/сек")
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:
                        pass
                return None
            
            # Одинаковых действий в окне
            same_count = sum(1 for _, k in actions if k == action_key)
            if same_count > self.max_same_callback:
                await self._send_slow_down(event, user_id)
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:
                        pass
                return None
        
        return await handler(event, data)
    
    async def _send_slow_down(self, event: TelegramObject, user_id: int):
        text = "полегче — ещё раз и улетишь 🍌"
        try:
            from utils import format_message_vip_async
            msg = await format_message_vip_async(text, user_id)
        except Exception:
            un = event.from_user.username if event.from_user else None
            fn = event.from_user.first_name if event.from_user else None
            msg = format_message_with_username(text, un, fn)
        chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
        try:
            await event.bot.send_message(chat_id, msg)
        except Exception as e:
            logger.debug("AntiAbuse slow_down send: %s", e)
    
    async def _apply_autoban(self, event: TelegramObject, user_id: int, reason: str):
        now_ts = int(time.time())
        mute_until = now_ts + self.auto_ban_duration
        antispam_data = await db.get_antispam(user_id)
        if antispam_data:
            await db.update_antispam(
                user_id,
                antispam_data.get("message_count", 0),
                antispam_data.get("window_start", now_ts),
                is_muted=True,
                mute_until=mute_until,
                messages_left_to_ban=0,
                last_message_at=now_ts
            )
        else:
            await db.update_antispam(user_id, 0, now_ts, is_muted=True, mute_until=mute_until, messages_left_to_ban=0, last_message_at=now_ts)
        logger.warning("Auto-ban анти-абуз: user_id=%s reason=%s mute_until=%s", user_id, reason, mute_until)
        try:
            from utils import notify_creator
            bot = getattr(event, "bot", None)
            if bot:
                un = event.from_user.username if event.from_user else ""
                asyncio.create_task(notify_creator(bot, f"Авто-бан (анти-абуз): user_id={user_id} @{un}\nПричина: {reason}\nМут до {mute_until}"))
        except Exception as e:
            logger.debug("notify_creator autoban: %s", e)
        text = "ты улетел на Банановые острова 🍌 Отдыхай 1 час."
        try:
            from utils import format_message_vip_async
            msg = await format_message_vip_async(text, user_id)
        except Exception:
            un = event.from_user.username if event.from_user else None
            fn = event.from_user.first_name if event.from_user else None
            msg = format_message_with_username(text, un, fn)
        chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
        try:
            from aiogram.types import FSInputFile
            photo_path = config.get_image_path("Ban.jpg")
            if photo_path.exists():
                await event.bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=msg)
            else:
                await event.bot.send_message(chat_id, msg)
        except Exception as e:
            logger.error("AntiAbuse autoban message: %s", e)


class CooldownMiddleware(BaseMiddleware):
    """
    Middleware для проверки cooldown команд
    Учитывает Premium статус и временные эффекты (kachalka)
    """
    
    def __init__(self):
        """Инициализация middleware cooldown"""
        super().__init__()
        self.free_commands = config.FREE_COMMANDS
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Обработка события с проверкой cooldown
        
        Args:
            handler: Следующий обработчик в цепочке
            event: Событие (Message, CallbackQuery и т.д.)
            data: Данные события
        """
        # Проверяем только команды из сообщений
        if not isinstance(event, Message):
            return await handler(event, data)
        
        # Проверяем наличие команды
        if not event.text or not event.text.startswith("/"):
            return await handler(event, data)
        
        # Получаем команду
        command = event.text.split()[0].split("@")[0]  # Убираем username бота если есть
        
        # Команды без cooldown пропускаем
        if command in self.free_commands:
            return await handler(event, data)
        
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)
        
        # Получаем время последнего использования команды
        last_used = await db.get_cooldown(user_id, command)
        now = int(time.time())
        
        if last_used:
            # Вычисляем cooldown для пользователя
            cooldown_seconds = await self._get_cooldown_seconds(user_id)
            
            # Проверяем, прошло ли достаточно времени
            time_passed = now - last_used
            
            if time_passed < cooldown_seconds:
                # Cooldown еще активен
                remaining = cooldown_seconds - time_passed
                minutes = remaining // 60
                seconds = remaining % 60
                
                # Форматируем время
                if minutes > 0:
                    time_str = f"{minutes} мин {seconds} сек"
                else:
                    time_str = f"{seconds} сек"
                
                # Отправляем сообщение о cooldown (удаляется через MESSAGE_DELETE_TIMEOUT)
                username = event.from_user.username or event.from_user.first_name or "Пользователь"
                try:
                    sent = await event.answer(
                        f"@{username}, команда в cooldown. Приходи через {time_str} ⏳",
                        show_alert=False
                    )
                    if sent and hasattr(sent, "message_id"):
                        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
                except TelegramBadRequest:
                    pass  # Игнорируем ошибки отправки
                
                logger.info(f"Пользователь {user_id} попытался использовать {command} (cooldown {remaining} сек)")
                return  # Блокируем обработку
        
        data["_cooldown_command"] = command
        data["_cooldown_user_id"] = user_id

        # Комиссия 5 коинов для платных команд (проверка и списание в CommissionMiddleware)
        return await handler(event, data)
    
    async def _get_cooldown_seconds(self, user_id: int) -> int:
        """
        Получение времени cooldown для пользователя с учётом Premium и эффектов
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Время cooldown в секундах
        """
        # Базовый cooldown
        base_cooldown = config.DEFAULT_COOLDOWN
        
        # Проверяем Premium
        is_premium = await db.is_premium(user_id)
        if is_premium:
            base_cooldown = config.PREMIUM_COOLDOWN
        
        # Проверяем эффект kachalka (снижает cooldown до 30 сек)
        has_kachalka = await db.has_effect(user_id, "kachalka")
        if has_kachalka:
            base_cooldown = config.KACHALKA_COOLDOWN_REDUCTION
        
        return base_cooldown


class CommissionMiddleware(BaseMiddleware):
    """
    Комиссия 5 коинов: списывается ТОЛЬКО при УСПЕШНОМ выполнении платной команды.
    Здесь комиссию НЕ списываем (middleware выполняется до handler'а).
    Списывать комиссию должен только handler после успешного выполнения (через balance_service.charge_commission).
    При ошибке / отмене / нехватке баланса — 0 списаний.
    """
    def __init__(self):
        super().__init__()
        self.free_commands = config.FREE_COMMANDS

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        if not event.text or not event.text.startswith("/"):
            return await handler(event, data)

        command = event.text.split()[0].split("@")[0]
        if command in self.free_commands:
            return await handler(event, data)
        exempt = getattr(config, "COMMISSION_EXEMPT", []) or []
        if command in exempt:
            return await handler(event, data)
        return await handler(event, data)


class ReklamaBlockMiddleware(BaseMiddleware):
    """
    Блокирует все команды на 3 минуты при эффекте reklama_block (реклама).
    Premium пользователи не видят рекламу и не блокируются.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        if not event.text or not event.text.startswith("/"):
            return await handler(event, data)
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)
        if _is_creator(event):
            return await handler(event, data)
        has_block = await db.has_effect(user_id, "reklama_block")
        if has_block:
            username = event.from_user.username or event.from_user.first_name or "Пользователь"
            try:
                await event.answer(f"@{username}, смотри рекламу — команды заблокированы на 3 минуты 📺")
            except TelegramBadRequest:
                pass
            return
        return await handler(event, data)


class AdTriggerMiddleware(BaseMiddleware):
    """
    Каждые ~50 сообщений от non-Premium пользователя показываем рекламу:
    текст + видео, блок команд 1 мин, через минуту удаляем и пишем «спасибо что посмотрели».
    Premium — без рекламы.
    """
    def __init__(self):
        super().__init__()
        self._counters: Dict[int, int] = {}
        self._threshold = getattr(config, "AD_MESSAGES_THRESHOLD", 50)
        self._block_duration = getattr(config, "AD_BLOCK_DURATION", 60)
        self._channel_link = getattr(config, "AD_CHANNEL_LINK", "https://t.me/+wMpwWUp30fwwMjEy")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)
        user_id = event.from_user.id
        if _is_creator(event):
            return await handler(event, data)
        is_premium = await db.is_premium(user_id)
        if is_premium:
            return await handler(event, data)
        self._counters[user_id] = self._counters.get(user_id, 0) + 1
        count = self._counters[user_id]
        if count >= self._threshold:
            self._counters[user_id] = 0
            asyncio.create_task(self._show_ad(event, user_id))
        return await handler(event, data)

    async def _show_ad(self, event: Message, user_id: int):
        try:
            chat_id = event.chat.id
            text = f"Темки от Технолога 🔥\n{self._channel_link}"
            from aiogram.types import FSInputFile
            photo_path = config.get_image_path("ads.jpg")
            if photo_path.exists():
                msg = await event.bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=text)
            else:
                msg = await event.bot.send_message(chat_id, text)
            await db.add_effect(user_id, "reklama_block", self._block_duration)
            msg_id = msg.message_id
            await asyncio.sleep(self._block_duration)
            try:
                await event.bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
            thanks_text = "Спасибо за просмотр, дружок"
            thanks_path = config.get_image_path("ads_thanks.jpg")
            if thanks_path.exists():
                await event.bot.send_photo(chat_id, FSInputFile(str(thanks_path)), caption=thanks_text)
            else:
                await event.bot.send_message(chat_id, thanks_text)
            logger.info(f"Ad shown to user {user_id}, block cleared")
        except Exception as e:
            logger.error(f"AdTrigger error: {e}", exc_info=True)


class TaxMiddleware(BaseMiddleware):
    """
    Middleware для проверки налога Технолога
    Блокирует все команды кроме /refill каждые 4 часа
    """
    
    def __init__(self):
        """Инициализация middleware налога"""
        super().__init__()
        self.tax_interval_hours = config.TAX_INTERVAL_HOURS
        self.tax_interval_seconds = self.tax_interval_hours * 3600
        # Команды, которые работают даже при неуплаченном налоге (баланс, помощь, профиль — не блокируем)
        self.allowed_commands = list(getattr(config, "FREE_COMMANDS", [])) or [
            "/refill", "/help", "/helpgame", "/start", "/news", "/balance", "/top", "/admins", "/report", "/obnova", "/tutorial"
        ]
        if "/profile" not in self.allowed_commands:
            self.allowed_commands.append("/profile")
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Обработка события с проверкой налога
        
        Args:
            handler: Следующий обработчик в цепочке
            event: Событие (Message, CallbackQuery и т.д.)
            data: Данные события
        """
        # Проверяем только команды из сообщений
        if not isinstance(event, Message):
            return await handler(event, data)
        
        # Проверяем наличие команды
        if not event.text or not event.text.startswith("/"):
            return await handler(event, data)
        
        # Получаем команду
        command = event.text.split()[0].split("@")[0]
        
        # Команда /refill всегда разрешена
        if command in self.allowed_commands:
            return await handler(event, data)
        
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)
        
        # Получаем состояние налога
        tax_state = await db.get_tax_state(user_id)
        now = int(time.time())
        
        # Инициализация при первом использовании (старт 4-часового таймера, не блокировать команды)
        if tax_state["last_tax_time"] is None:
            await db.init_tax_timer(user_id)
            return await handler(event, data)
        
        # Проверяем, прошло ли 4 часа с последнего налога
        time_since_last_tax = now - tax_state["last_tax_time"]
        
        # Если налог был оплачен и прошло 4 часа, устанавливаем новый налог
        if tax_state["is_paid"] and time_since_last_tax >= self.tax_interval_seconds:
            await self._check_and_set_tax(user_id)
            # Обновляем состояние после установки нового налога
            tax_state = await db.get_tax_state(user_id)
        
        # Если налог не оплачен, блокируем команду
        if not tax_state["is_paid"]:
            # Получаем баланс пользователя
            balance = await db.get_balance(user_id)
            
            if balance == 0:
                # Баланс = 0, налог пропадает
                await db.pay_tax(user_id)
                logger.info(f"Пользователь {user_id} имеет баланс 0, налог отменен")
                return await handler(event, data)
            
            # Блокируем команду и отправляем сообщение о налоге
            username = event.from_user.username or event.from_user.first_name or "Пользователь"
            
            # Вычисляем сумму налога (берем из tax_state или вычисляем заново)
            tax_amount = tax_state["tax_due"] if tax_state["tax_due"] > 0 else int(balance * config.TAX_PERCENTAGE)
            
            # Отправляем сообщение о налоге
            from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
            
            try:
                # Отправляем фото zl.jpg
                photo_path = config.get_image_path("zl.jpg")
                if photo_path.exists():
                    photo = FSInputFile(str(photo_path))
                    await event.answer_photo(
                        photo=photo,
                        caption=f"@{username}, оплати, ярый феминист 💰\n\n"
                                f"Налог Технолога: {tax_amount} коинов (25% от баланса)\n"
                                f"Ну дай дай дай деняг",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(
                                text="Оплатить налог",
                                callback_data=f"pay_tax_{user_id}"
                            )
                        ]])
                    )
                else:
                    await event.answer(
                        f"@{username}, оплати, ярый феминист 💰\n\n"
                        f"Налог Технолога: {tax_amount} коинов (25% от баланса)\n"
                        f"Ну дай дай дай деняг"
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения о налоге: {e}")
                try:
                    await event.answer(
                        f"@{username}, оплати, ярый феминист 💰\n\n"
                        f"Налог Технолога: {tax_amount} коинов (25% от баланса)\n"
                        f"Ну дай дай дай деняг"
                    )
                except:
                    pass
            
            logger.info(f"Пользователь {user_id} заблокирован налогом (команда {command})")
            return  # Блокируем обработку команды
        
        # Налог оплачен или еще не требуется
        return await handler(event, data)
    
    async def _check_and_set_tax(self, user_id: int):
        """
        Проверка и установка налога для пользователя
        
        Args:
            user_id: ID пользователя
        """
        balance = await db.get_balance(user_id)
        
        if balance == 0:
            # Баланс = 0, налог не требуется
            await db.pay_tax(user_id)
            return
        
        # Вычисляем налог (1/4 баланса, раз в 4 часа)
        tax_amount = int(balance * config.TAX_PERCENTAGE)
        logger.info(f"Налог Технолог установлен: user_id={user_id}, сумма={tax_amount}, баланс={balance}")
        await db.set_tax_due(user_id, tax_amount)


class LoggingMiddleware(BaseMiddleware):
    """
    Middleware для логирования всех действий пользователей
    Записывает команды, callback'и и другие события
    """
    
    def __init__(self):
        """Инициализация middleware логирования"""
        super().__init__()
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Обработка события с логированием
        
        Args:
            handler: Следующий обработчик в цепочке
            event: Событие (Message, CallbackQuery и т.д.)
            data: Данные события
        """
        # Получаем информацию о пользователе
        user_id = None
        username = None
        
        if isinstance(event, Message):
            if event.from_user:
                user_id = event.from_user.id
                username = event.from_user.username or event.from_user.first_name
            action = f"Команда: {event.text}" if event.text else "Сообщение"
        elif isinstance(event, CallbackQuery):
            if event.from_user:
                user_id = event.from_user.id
                username = event.from_user.username or event.from_user.first_name
            action = f"Callback: {event.data}"
        else:
            action = f"Событие: {type(event).__name__}"
        
        # Логируем начало обработки
        if user_id:
            logger.info(f"[{user_id}] @{username} - {action}")
            try:
                # Обновляем время последней активности
                await db.update_user_last_active(user_id)
                if username:
                    await db.update_user_username(user_id, username)
            except Exception as e:
                logger.warning("LoggingMiddleware: обновление user/username: %s", e)
            
            # Premium 7d: при первом сообщении в чате раз в 24ч — «👑 @user зашёл в чат — целуйте экран»
            if isinstance(event, Message) and event.chat and event.chat.id:
                try:
                    is_premium = await db.is_premium(user_id)
                    if is_premium:
                        last_ts = await db.get_premium_chat_greeting(event.chat.id, user_id)
                        now_ts = int(time.time())
                        if last_ts is None or (now_ts - last_ts) >= 86400:
                            await db.set_premium_chat_greeting(event.chat.id, user_id)
                            un = event.from_user.username or event.from_user.first_name or "user"
                            greet = f"👑 @{un} зашёл в чат — целуйте экран"
                            await event.bot.send_message(event.chat.id, greet)
                except Exception as e:
                    logger.debug("Premium chat greeting: %s", e)
        else:
            logger.info(f"Системное событие - {action}")
        
        try:
            # Выполняем обработчик
            result = await handler(event, data)
            
            # Логируем успешное выполнение
            if user_id:
                logger.debug(f"[{user_id}] Обработка завершена успешно")
            
            return result
            
        except Exception as e:
            # Логируем ошибку
            logger.error(
                f"[{user_id if user_id else 'Unknown'}] Ошибка при обработке {action}: {e}",
                exc_info=True
            )
            raise


class BanMiddleware(BaseMiddleware):
    """
    Блокировка забаненных пользователей: запрет игр и команд.
    Сообщение: «ты чилишь на банановых островах» + Ban.jpg.
    Создателя забанить нельзя (проверка в _is_creator).
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)
        
        if _is_creator(event):
            return await handler(event, data)
        
        user = await db.get_user(user_id)
        if not user:
            return await handler(event, data)
        
        is_banned = user.get("is_banned", False)
        ban_until = user.get("ban_until")
        now = int(time.time())
        
        if is_banned and ban_until and ban_until > now:
            from aiogram.types import FSInputFile
            text = "🚫 Теперь ты чилишь на банановых островах."
            try:
                msg = await format_message_vip_async(text, user_id)
            except Exception:
                un = event.from_user.username if event.from_user else None
                fn = event.from_user.first_name if event.from_user else None
                msg = format_message_with_username(text, un, fn)
            chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
            try:
                photo_path = config.get_image_path("Ban.jpg")
                if photo_path.exists():
                    await event.bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=msg)
                else:
                    await event.bot.send_message(chat_id, msg)
            except Exception as e:
                logger.error(f"BanMiddleware send: {e}")
            return
        
        return await handler(event, data)


class UpdateUserDataMiddleware(BaseMiddleware):
    """
    Middleware для обновления данных пользователя в БД
    Создает пользователя если его нет, обновляет username и активность
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """
        Обработка события с обновлением данных пользователя
        
        Args:
            handler: Следующий обработчик в цепочке
            event: Событие (Message, CallbackQuery и т.д.)
            data: Данные события
        """
        # Получаем информацию о пользователе
        user_id = None
        username = None
        
        if isinstance(event, (Message, CallbackQuery)):
            if hasattr(event, "from_user") and event.from_user:
                user_id = event.from_user.id
                username = event.from_user.username
        
        # Если есть пользователь, обновляем данные
        if user_id:
            # @DPOPTH считается создателем: при первом сообщении от него сохраняем CREATOR_ID
            if is_creator_by_username(username) and not getattr(config, "CREATOR_ID", None):
                setattr(config, "CREATOR_ID", user_id)
                logger.info(f"Создатель @DPOPTH привязан к user_id={user_id}")
            # Проверяем существование пользователя
            user = await db.get_user(user_id)
            if not user:
                # Создаем нового пользователя
                await db.create_user(user_id, username)
                logger.info(f"Создан новый пользователь: {user_id} (@{username})")
            else:
                # Обновляем username если изменился
                if username and username != user.get("username"):
                    await db.update_user_username(user_id, username)
                
                # Обновляем время последней активности
                await db.update_user_last_active(user_id)
        
        # Пропускаем событие дальше
        return await handler(event, data)


# Экспорт всех middleware для удобного импорта
__all__ = [
    "AntifloodMiddleware",
    "BanMiddleware",
    "CooldownMiddleware",
    "CommissionMiddleware",
    "TaxMiddleware",
    "LoggingMiddleware",
    "UpdateUserDataMiddleware",
    "ReklamaBlockMiddleware",
    "AdTriggerMiddleware",
    "set_command_cooldown"
]
