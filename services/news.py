"""
Сервис игровых новостей Tehnolog Games.
Каждые 2 часа — одна случайная новость (хорошая/плохая/нейтральная) для одной игры.
Влияние только в логике: +шанс, −шанс или игра закрыта на 2 часа.
"""

import asyncio
import logging
import random
import time
from typing import Optional, Dict, Any, List

from config import config
from db import db

logger = logging.getLogger(__name__)

# Игры, к которым могут привязываться новости (все игровые команды)
NEWS_GAME_SLUGS: List[str] = [
    "slot", "konopla", "kripta", "almaz", "plsdon", "chisla",
    "reactor", "vault", "dicepath", "overheat", "mindlock", "bombline", "liftx", "doza",
    "shum", "signal", "freeze", "tunnel", "escape", "code", "magnet", "candle",
    "pulse", "orbit", "wall", "watcher",
    "controlroom", "firesector", "mutation", "satellite", "mine", "clock", "lab", "bunker",
    "storm", "navigator", "icepath", "coinstack", "target", "fuse", "web", "logicgate",
    "depth", "field", "ritual", "trace",
]

NEWS_TYPES = ("good", "bad", "neutral")
NEWS_GOOD_DELTA = 0.05   # +5% к шансу выигрыша
NEWS_BAD_DELTA = -0.05   # −5%
NEWS_DURATION_HOURS = 2
NEWS_INTERVAL_HOURS = 2

FLAVOR_GOOD = [
    "Механика сломалась в нашу пользу — пока чинят, везёт.",
    "Стажёр перепутал настройки — шансы поднялись.",
    "Технолог в ударе, сегодня фортуна на твоей стороне.",
    "Кто-то подкрутил удачу — пользуйся.",
]
FLAVOR_BAD = [
    "Экономят на деталях — шансы урезали.",
    "Крутит стажёр — пока так.",
    "Технолог чинит — временно везёт меньше.",
    "Поломка в механике — удача не на нашей стороне.",
]
FLAVOR_NEUTRAL = [
    "Идёт починка — игра временно закрыта.",
    "Что-то пошло не так — на пару часов стоп.",
    "Плановые работы — заходи позже.",
]


class NewsService:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    async def get_current_news(self) -> Optional[Dict[str, Any]]:
        """Текущая активная новость (не истекшая)."""
        return await db.get_current_news()

    async def get_win_modifier(self, game_slug: str) -> float:
        """
        Модификатор шанса выигрыша для игры: +NEWS_GOOD_DELTA, −NEWS_BAD_DELTA или 0.
        Игрок проценты не видит.
        """
        news = await db.get_current_news()
        if not news or news["game_slug"] != game_slug:
            return 0.0
        if news["news_type"] == "good":
            return getattr(config, "NEWS_GOOD_DELTA", NEWS_GOOD_DELTA)
        if news["news_type"] == "bad":
            return getattr(config, "NEWS_BAD_DELTA", NEWS_BAD_DELTA)
        return 0.0

    async def is_game_closed(self, game_slug: str) -> bool:
        """Игра закрыта на 2 часа из-за нейтральной новости."""
        news = await db.get_current_news()
        if not news or news["game_slug"] != game_slug:
            return False
        return news["news_type"] == "neutral"

    async def start_scheduler(self):
        """Запуск фоновой задачи: каждые 2 часа генерировать одну новость."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._news_loop())
        logger.info("Сервис новостей: планировщик запущен (интервал %s ч)", NEWS_INTERVAL_HOURS)

    async def stop_scheduler(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Сервис новостей: планировщик остановлен")

    async def _news_loop(self):
        await asyncio.sleep(30)  # первая новость через 30 сек после старта
        while True:
            try:
                await self._generate_one_news()
                await asyncio.sleep(NEWS_INTERVAL_HOURS * 3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Ошибка в цикле новостей: %s", e, exc_info=True)

    async def _generate_one_news(self):
        """Сгенерировать одну новость: анализ активности — в непопулярные игры буст, в популярные «сломалась»."""
        counts = await db.get_all_play_counts_24h()
        with_counts = [(s, counts.get(s, 0)) for s in NEWS_GAME_SLUGS]
        with_counts.sort(key=lambda x: x[1])
        n = len(with_counts)
        if n == 0:
            game_slug = random.choice(NEWS_GAME_SLUGS)
            news_type = random.choice(NEWS_TYPES)
        else:
            low = [s for s, _ in with_counts[: max(1, n // 2)]]
            high = [s for s, _ in with_counts[-max(1, (n + 3) // 4):]]
            r = random.random()
            if r < 0.4 and low:
                game_slug = random.choice(low)
                news_type = "good"
            elif r < 0.65 and high:
                game_slug = random.choice(high)
                news_type = "neutral"
            else:
                game_slug = random.choice(NEWS_GAME_SLUGS)
                news_type = random.choice(NEWS_TYPES)
        now = int(time.time())
        expires_at = now + NEWS_DURATION_HOURS * 3600
        if news_type == "good":
            flavor = random.choice(FLAVOR_GOOD)
        elif news_type == "bad":
            flavor = random.choice(FLAVOR_BAD)
        else:
            flavor = random.choice(FLAVOR_NEUTRAL)
        await db.insert_game_news(news_type, game_slug, expires_at, flavor)
        logger.info("Новость: type=%s, game=%s, expires_at=%s (анализ активности)", news_type, game_slug, expires_at)


news_service = NewsService()
