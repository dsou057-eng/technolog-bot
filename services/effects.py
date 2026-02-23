"""
Сервис для работы с эффектами пользователей
Premium, зелья удачи, баффы от /kachalka
Автоматическая проверка истечения, учет в играх и cooldown
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from config import config
from db import db

logger = logging.getLogger(__name__)


class EffectsService:
    """
    Сервис для работы с эффектами пользователей
    Управляет временными эффектами, проверяет их активность, применяет бонусы
    """
    
    def __init__(self):
        """Инициализация сервиса эффектов"""
        self._cleanup_task = None
    
    async def start_cleanup_task(self):
        """
        Запуск фоновой задачи для очистки истекших эффектов
        Вызывается при старте бота
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_effects())
            logger.info("Задача очистки истекших эффектов запущена")
    
    async def stop_cleanup_task(self):
        """Остановка фоновой задачи очистки"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Задача очистки истекших эффектов остановлена")
    
    async def _cleanup_expired_effects(self):
        """
        Фоновая задача для периодической очистки истекших эффектов
        Запускается каждые 60 секунд
        """
        while True:
            try:
                await asyncio.sleep(60)  # Проверка каждую минуту
                await db.remove_expired_effects()
                logger.debug("Очистка истекших эффектов выполнена")
            except asyncio.CancelledError:
                logger.info("Задача очистки эффектов отменена")
                break
            except Exception as e:
                logger.error(f"Ошибка при очистке истекших эффектов: {e}", exc_info=True)
    
    async def add_effect(
        self,
        user_id: int,
        effect_type: str,
        duration_seconds: int,
        multiplier: float = 1.0,
        metadata: str = None
    ) -> int:
        """
        Добавление эффекта пользователю
        
        Args:
            user_id: ID пользователя
            effect_type: Тип эффекта (premium, potion_x1.5, potion_x2, potion_x5, potion_x10, kachalka)
            duration_seconds: Длительность эффекта в секундах
            multiplier: Множитель эффекта (для зелий удачи)
            metadata: Дополнительные данные в JSON формате
            
        Returns:
            ID созданного эффекта
        """
        effect_id = await db.add_effect(
            user_id=user_id,
            effect_type=effect_type,
            duration_seconds=duration_seconds,
            multiplier=multiplier,
            metadata=metadata
        )
        
        logger.info(
            f"Добавлен эффект: user_id={user_id}, effect_type={effect_type}, "
            f"duration={duration_seconds}с, multiplier={multiplier}"
        )
        
        return effect_id
    
    async def get_active_effects(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Получение всех активных эффектов пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Список словарей с данными эффектов
        """
        effects = await db.get_active_effects(user_id)
        
        # Форматируем эффекты для удобства использования
        formatted_effects = []
        now = int(datetime.now().timestamp())
        
        for effect in effects:
            expires_at = effect["expires_at"]
            time_left = expires_at - now
            
            formatted_effect = {
                **effect,
                "time_left_seconds": time_left,
                "is_active": time_left > 0
            }
            formatted_effects.append(formatted_effect)
        
        return formatted_effects
    
    async def has_effect(self, user_id: int, effect_type: str) -> bool:
        """
        Проверка наличия активного эффекта определенного типа
        
        Args:
            user_id: ID пользователя
            effect_type: Тип эффекта
            
        Returns:
            True если эффект активен, False иначе
        """
        return await db.has_effect(user_id, effect_type)
    
    async def get_effect_multiplier(self, user_id: int, effect_type_prefix: str = "potion") -> float:
        """
        Получение максимального множителя эффекта определенного типа
        Например, для potion_x1.5, potion_x2 и т.д. возвращает максимальный активный
        
        Args:
            user_id: ID пользователя
            effect_type_prefix: Префикс типа эффекта (например, "potion")
            
        Returns:
            Максимальный множитель активных эффектов или 1.0
        """
        effects = await self.get_active_effects(user_id)
        
        max_multiplier = 1.0
        for effect in effects:
            if effect["effect_type"].startswith(effect_type_prefix):
                max_multiplier = max(max_multiplier, effect["multiplier"])
        
        return max_multiplier
    
    async def get_luck_multiplier(self, user_id: int) -> float:
        """
        Получение множителя удачи для игр: зелья + бонус перерождений (+0.5x за каждое).
        """
        potion_multiplier = await self.get_effect_multiplier(user_id, "potion")
        base = potion_multiplier if potion_multiplier > 1.0 else 1.0
        rebirth_count = await db.get_rebirth_count(user_id)
        rebirth_bonus = 1.0 + rebirth_count * 0.5
        return base * rebirth_bonus
    
    async def get_price_discount(self, user_id: int) -> float:
        """
        Получение скидки на цены (для Premium)
        Premium считается по timestamp (users.premium_until).
        """
        is_premium = await db.is_premium(user_id)
        if is_premium:
            return config.PREMIUM_PRICE_DISCOUNT  # 0.5%
        return 0.0
    
    async def apply_price_discount(self, user_id: int, price: int) -> int:
        """
        Применение скидки к цене (для Premium)
        
        Args:
            user_id: ID пользователя
            price: Исходная цена
            
        Returns:
            Цена со скидкой
        """
        discount = await self.get_price_discount(user_id)
        if discount > 0:
            discounted_price = int(price * (1 - discount))
            return discounted_price
        return price
    
    async def get_win_chance_bonus(self, user_id: int) -> float:
        """
        Получение бонуса к шансам выигрыша (для Premium).
        Premium считается по timestamp (users.premium_until).
        """
        is_premium = await db.is_premium(user_id)
        if is_premium:
            return config.PREMIUM_WIN_CHANCE_BONUS  # +1.4%
        return 0.0
    
    async def format_effects_list(self, user_id: int) -> str:
        """
        Форматирование списка активных эффектов для команды /effect.
        Включает Premium по timestamp (users.premium_until), зелья, kachalka и т.д.
        """
        effects = await self.get_active_effects(user_id)
        is_premium = await db.is_premium(user_id)
        user = await db.get_user(user_id)
        premium_until = user.get("premium_until") if user else None

        parts = []
        if is_premium and premium_until:
            now = int(datetime.now().timestamp())
            time_left = premium_until - now
            time_str = self._format_time_left(time_left)
            parts.append(f"• 👑 Premium\n  ⏱ Осталось: {time_str}\n")

        for effect in effects:
            if effect["effect_type"] == "premium":
                continue  # уже вывели из users.premium_until
            effect_type = effect["effect_type"]
            time_left = effect["time_left_seconds"]
            effect_name = self._format_effect_name(effect_type)
            time_str = self._format_time_left(time_left)
            multiplier_info = ""
            if effect["multiplier"] > 1.0:
                multiplier_info = f" (x{effect['multiplier']})"
            parts.append(f"• {effect_name}{multiplier_info}\n  ⏱ Осталось: {time_str}\n")

        if not parts:
            return "У тебя нет активных эффектов 😢"
        return "✨ <b>АКТИВНЫЕ ЭФФЕКТЫ:</b>\n\n" + "\n".join(parts)
    
    def _format_effect_name(self, effect_type: str) -> str:
        """
        Форматирование названия эффекта для отображения
        
        Args:
            effect_type: Тип эффекта из БД
            
        Returns:
            Отформатированное название
        """
        effect_names = {
            "premium": "👑 Premium",
            "potion_x1.5": "🍀 Зелье удачи x1.5",
            "potion_x2": "🍀 Зелье удачи x2",
            "potion_x5": "🍀 Зелье удачи x5",
            "potion_x10": "🍀 Зелье удачи x10",
            "kachalka": "💪 Бафф от качалки"
        }
        
        return effect_names.get(effect_type, f"❓ {effect_type}")
    
    def _format_time_left(self, seconds: int) -> str:
        """
        Форматирование оставшегося времени
        
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


# Глобальный экземпляр сервиса
effects_service = EffectsService()
