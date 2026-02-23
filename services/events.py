"""
Персональные ивенты под стиль игрока (как рекомендации TikTok).
Типы: gambling, meme, antigreed, save, shadow (скрытый).
Триггер: каждые 2–4 ч по кулдауну + условия (серия побед/лузов, баланс, агрессия).
"""

import logging
import random
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from db import db
from config import config

logger = logging.getLogger(__name__)

# Кулдаун между ивентами: 2–4 часа (секунды)
EVENT_COOLDOWN_MIN = 7200   # 2 ч
EVENT_COOLDOWN_MAX = 14400  # 4 ч
# Длительность ивента (секунды)
EVENT_DURATION = 1800  # 30 мин
# Шанс триггера теневым ивентом (игрок не видит сообщение)
SHADOW_CHANCE = 0.06

# Игроку показываем только атмосферу и короткий намёк на эффект (без формул и закулисья)
# MMR-ивенты: короткий бафф за выигрыш (шанс срабатывает при росте MMR)
MMR_LUCKY_DURATION = 60  # 1 минута
MMR_LUCKY_CHANCE = 0.12  # 12% шанс выпасть после выигрыша

EVENT_TEXTS = {
    "gambling": (
        "🔥 <b>Ты сегодня в ударе.</b>\n\n"
        "Удача благосклонна к смелым ставкам — но проигрыш может ударить сильнее. Играй осознанно."
    ),
    "lucky_80": (
        "🍀 <b>Ветер удачи!</b>\n\n"
        "Твои шансы на победу в любой игре повышены до ~80% на 1 минуту. Успей сыграть!"
    ),
    "lucky_mult": (
        "📈 <b>Множитель удачи.</b>\n\n"
        "Выигрыши в любой игре дают x1.2 к множителю в течение 1 минуты. Лови момент!"
    ),
    "lucky_taxfree": (
        "🛡️ <b>Налоговая каникула.</b>\n\n"
        "Следующий выигрыш без налога Технолога. Действует 1 минуту."
    ),
    "meme": (
        "🎲 <b>Что-то пошло не по плану.</b>\n\n"
        "Исходы могут быть неожиданными: не только выигрыш или проигрыш, но и нестандартные результаты. Лови момент."
    ),
    "antigreed": (
        "⚖️ <b>Баланс восстановлен.</b>\n\n"
        "Крупные куши реже, зато мелкие победы чаще. Идеально для спокойной игры."
    ),
    "save": (
        "🆘 <b>Второй шанс.</b>\n\n"
        "Удача даёт возможность отыграться. Используй её — или не используй, как знаешь."
    ),
    "shadow": "",  # Сообщение не показываем
}

EVENT_IMAGES = {
    "gambling": "event_hot.jpg",
    "lucky_80": "event_hot.jpg",
    "lucky_mult": "event_hot.jpg",
    "lucky_taxfree": "event_hot.jpg",
    "meme": "event_meme.jpg",
    "antigreed": "event_hot.jpg",  # можно заменить на event_antigreed.jpg
    "save": "event_save.jpg",
    "shadow": "event_shadow.jpg",
}


class EventsService:
    """Сервис персональных ивентов под стиль игрока."""

    async def get_active_event(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Активный ивент пользователя (если не истёк)."""
        return await db.get_active_event(user_id)

    async def set_event(self, user_id: int, event_type: str, duration_seconds: int = EVENT_DURATION) -> None:
        """Установить ивент пользователю."""
        await db.set_user_event(user_id, event_type, duration_seconds)
        logger.info("Event set: user_id=%s type=%s duration=%s", user_id, event_type, duration_seconds)

    async def try_trigger_event(
        self, user_id: int, chat_id: int, bot, balance: Optional[int] = None
    ) -> Optional[Tuple[str, str, Path]]:
        """
        Проверить условия и при необходимости запустить ивент.
        Возвращает (text, image_filename, path) для отправки сообщения или None.
        Теневой ивент устанавливается без сообщения.
        """
        now = int(time.time())
        # Уже есть активный ивент — не перезаписываем
        active = await self.get_active_event(user_id)
        if active:
            return None

        last_ended = await db.get_last_event_ended_at(user_id)
        if last_ended:
            cooldown = random.randint(EVENT_COOLDOWN_MIN, EVENT_COOLDOWN_MAX)
            if now - last_ended < cooldown:
                return None

        balance = balance if balance is not None else await db.get_balance(user_id)
        sessions = await db.get_last_game_sessions(user_id, 20)
        if not sessions:
            # Мало данных — только теневая лотерея
            if random.random() < SHADOW_CHANCE:
                await self.set_event(user_id, "shadow")
            return None

        results = [s["result"] for s in sessions]
        bets = [s["bet"] for s in sessions if s.get("bet")]
        wins = sum(1 for r in results if r == "win")
        losses = sum(1 for r in results if r == "loss")
        avg_bet = sum(bets) / len(bets) if bets else 0
        # Условно «агрессия»: ставки выше 15% баланса в среднем (при текущем балансе)
        balance_for_pct = max(balance, 500)
        aggressive = avg_bet > balance_for_pct * 0.12 and len(bets) >= 5

        # Серия поражений (3+ подряд)
        loss_streak = 0
        for r in results:
            if r == "loss":
                loss_streak += 1
            else:
                break
        # Серия побед (3+ подряд)
        win_streak = 0
        for r in results:
            if r == "win":
                win_streak += 1
            else:
                break

        # Приоритет: спас (в минусе и серия лузов) > анти-жадный (рост баланса) > азартный > мем > тень
        if loss_streak >= 3 and balance < 2000:
            event_type = "save"
        elif win_streak >= 3 and balance > 5000 and wins >= losses:
            event_type = "antigreed"
        elif aggressive and losses >= 2:
            event_type = "gambling"
        elif len(set(bets)) > 5 and (wins - losses) in (-2, -1, 0, 1, 2):
            event_type = "meme"  # хаотичный разброс ставок и 50/50
        elif random.random() < SHADOW_CHANCE:
            event_type = "shadow"
        else:
            return None

        await self.set_event(user_id, event_type)

        if event_type == "shadow":
            return None

        text = EVENT_TEXTS.get(event_type, "")
        img_name = EVENT_IMAGES.get(event_type, "event_hot.jpg")
        path = config.get_image_path(img_name)
        return (text, img_name, path)

    async def try_trigger_mmr_lucky_event(
        self, user_id: int, new_mmr: int, chat_id: int, bot
    ) -> Optional[Tuple[str, str, Path]]:
        """
        После выигрыша с некоторой вероятностью выдать короткий бафф (80% шанс, x1.2 множ и т.д.).
        Чем выше MMR/лига — тем чуть выше шанс. Возвращает (text, img_name, path) для отправки или None.
        """
        if random.random() > MMR_LUCKY_CHANCE:
            return None
        active = await self.get_active_event(user_id)
        if active:
            return None
        choices = ["lucky_80", "lucky_mult"]
        if new_mmr >= 500:
            choices.append("lucky_taxfree")
        event_type = random.choice(choices)
        await self.set_event(user_id, event_type, MMR_LUCKY_DURATION)
        text = EVENT_TEXTS.get(event_type, "")
        img_name = EVENT_IMAGES.get(event_type, "event_hot.jpg")
        path = config.get_image_path(img_name)
        return (text, img_name, path)

    def apply_event_to_win_chance(self, base_chance: float, event_type: Optional[str]) -> float:
        """Модификатор шанса выигрыша от активного ивента."""
        if not event_type:
            return base_chance
        if event_type == "lucky_80":
            return min(1.0, 0.80)  # ~80% в любой игре
        if event_type == "gambling":
            return min(1.0, base_chance + 0.08)
        if event_type == "save":
            return min(1.0, base_chance + 0.06)
        if event_type == "antigreed":
            return max(0.0, base_chance - 0.04)
        if event_type == "meme":
            return base_chance
        if event_type == "shadow":
            return base_chance + 0.02
        return base_chance

    def apply_event_to_multiplier(self, mult: float, event_type: Optional[str], is_win: bool) -> float:
        """Модификатор множителя от ивента."""
        if not event_type:
            return mult
        if event_type == "lucky_mult" and is_win:
            return mult * 1.2
        if event_type == "gambling" and is_win:
            return mult * 1.15
        if event_type == "antigreed" and is_win:
            return mult * 0.85
        if event_type == "save" and is_win:
            return mult * 1.1
        return mult

    def apply_event_loss_penalty(self, penalty_factor: float, event_type: Optional[str]) -> float:
        """Азартный ивент: при проигрыше — больнее."""
        if event_type == "gambling":
            return penalty_factor * 1.2
        return penalty_factor


events_service = EventsService()
