"""
Автономность бота: сам сбрасывает сезоны и при необходимости вайпит балансы.
Проверка по расписанию (каждые N часов): если сезон истёк — завершение, награды топ-3, новый сезон, опционально обрезка балансов.
"""

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from config import config
from db import db

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

_rewards = [10000, 5000, 2500]
_task: Optional[asyncio.Task] = None
_bot: Optional["Bot"] = None


async def _do_end_season_and_wipe():
    """Проверить: сезон истёк? Завершить, награды топ-3, вайп если настроен."""
    if not getattr(config, "AUTO_END_SEASON_ENABLED", True):
        return
    season = await db.get_current_season()
    if not season:
        return
    now = int(__import__("datetime").datetime.now().timestamp())
    if season["ends_at"] > now:
        return
    logger.info("Автономность: сезон истёк, завершаем сезон и создаём новый")
    top = await db.get_top_by_mmr(3)
    for i, t in enumerate(top):
        uid = t.get("user_id")
        if uid and i < len(_rewards) and _rewards[i] > 0:
            await db.update_balance(uid, _rewards[i], "income", "autonomy_season", "Награда за топ сезона")
            await db.update_total_coins(uid, _rewards[i])
            if _bot:
                try:
                    await _bot.send_message(
                        uid,
                        f"🏆 Сезон завершён автоматически. Ты в топ-3: место {i+1}. Награда: {_rewards[i]} коинов."
                    )
                except Exception as e:
                    logger.debug("autonomy: не удалось отправить награду uid=%s: %s", uid, e)
    new_season = await db.end_current_season_and_start_new()
    name = new_season["name"] if new_season else "—"
    logger.info("Автономность: новый сезон %s", name)
    cap = getattr(config, "AUTO_WIPE_BALANCE_CAP", None)
    if cap is not None and int(cap) >= 0:
        n = await db.cap_all_balances(int(cap))
        logger.info("Автономность: вайп балансов (cap=%s), затронуто пользователей: %s", cap, n)


async def _loop():
    interval = getattr(config, "AUTO_SEASON_CHECK_INTERVAL_HOURS", 24.0) * 3600
    await asyncio.sleep(60)
    while True:
        try:
            await _do_end_season_and_wipe()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Ошибка автономности (сброс сезона): %s", e, exc_info=True)
        await asyncio.sleep(interval)


def start_autonomy(bot: Optional["Bot"] = None):
    global _task, _bot
    _bot = bot
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("Сервис автономности запущен (проверка сезона каждые %s ч)", getattr(config, "AUTO_SEASON_CHECK_INTERVAL_HOURS", 24))


async def stop_autonomy():
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    logger.info("Сервис автономности остановлен")
