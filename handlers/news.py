"""
Игровые новости: /news — текущая новость, к какой игре, когда заканчивается эффект.
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Router
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command

from config import config
from services.news import news_service
from utils import delete_message_after, format_message_with_username

router = Router()
logger = logging.getLogger(__name__)

GAME_DISPLAY_NAMES = {
    "slot": "Слоты",
    "konopla": "Канапля",
    "kripta": "Lucky Jet",
    "almaz": "Алмазы",
    "plsdon": "Задонать",
    "chisla": "PvP Дуэль",
    "reactor": "Reactor",
    "vault": "Vault",
    "dicepath": "Dice Path",
    "overheat": "Overheat",
    "mindlock": "Mind Lock",
    "bombline": "Bomb Line",
    "liftx": "Lift X",
    "doza": "Doza",
    "shum": "Shum",
    "signal": "Signal",
    "freeze": "Freeze",
    "tunnel": "Tunnel",
    "escape": "Escape",
    "code": "Code",
    "magnet": "Magnet",
    "candle": "Candle",
    "pulse": "Pulse",
    "orbit": "Orbit",
    "wall": "Wall",
    "watcher": "Watcher",
    "controlroom": "Control Room",
    "firesector": "Fire Sector",
    "mutation": "Mutation",
    "satellite": "Satellite",
    "mine": "Mine",
    "clock": "Clock",
    "lab": "Lab",
    "bunker": "Bunker",
    "storm": "Storm",
    "navigator": "Navigator",
    "icepath": "Ice Path",
    "coinstack": "Coin Stack",
    "target": "Target",
    "fuse": "Fuse",
    "web": "Web",
    "logicgate": "Logic Gate",
    "depth": "Depth",
    "field": "Field",
    "ritual": "Ritual",
    "trace": "Trace",
}


def _format_time_left(expires_at: int) -> str:
    now = int(datetime.now().timestamp())
    sec = max(0, expires_at - now)
    h = sec // 3600
    m = (sec % 3600) // 60
    if h > 0:
        return f"{h} ч {m} мин"
    return f"{m} мин"


@router.message(Command("news"))
async def cmd_news(message: Message):
    """
    /news — текущая активная новость: к какой игре, тип, когда закончится.
    Если новостей нет: «Сейчас всё стабильно, дружок».
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    news = await news_service.get_current_news()
    if not news:
        text = format_message_with_username(
            "Сейчас всё стабильно, дружок.",
            username, first_name
        )
        sent = await message.answer(text)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    game_name = GAME_DISPLAY_NAMES.get(news["game_slug"], news["game_slug"])
    time_left = _format_time_left(news["expires_at"])
    flavor = news.get("flavor_text") or ""

    if news["news_type"] == "good":
        line = f"📈 Хорошая новость по игре <b>{game_name}</b>.\n{flavor}\n\nЭффект ещё: {time_left}"
        photo_name = "news_good.jpg"
    elif news["news_type"] == "bad":
        line = f"📉 Плохая новость по игре <b>{game_name}</b>.\n{flavor}\n\nЭффект ещё: {time_left}"
        photo_name = "news_bad.jpg"
    else:
        line = f"🔧 Игра <b>{game_name}</b> временно закрыта.\n{flavor}\n\nОткроется через: {time_left}"
        photo_name = "news_neutral.jpg"

    caption = format_message_with_username(line, username, first_name)
    photo_path = config.get_image_path(photo_name)
    try:
        if photo_path.exists():
            sent = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption)
        else:
            sent = await message.answer(caption)
    except Exception as e:
        logger.warning("news photo %s: %s", photo_name, e)
        sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
