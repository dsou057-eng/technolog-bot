"""
10 быстрых мини-игр: орёл/решка, угадай число 1–10, чёт/нечет и т.д.
Один раунд, фиксированный множитель, ставка 10–500 коинов.
"""
import random
import logging
from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import config
from db import db
from utils import format_message_with_username, format_insufficient_balance, delete_message_after
from services.balance import balance_service

router = Router()
logger = logging.getLogger(__name__)

MINIGAME_STAKE_MIN = 10
MINIGAME_STAKE_MAX = 500

# slug: (name, description, mult on win, win_chance 0-1 or None for custom)
MINIGAMES = {
    "coin": ("Орёл/решка", "Угадай сторону монеты. x2.", 2.0, 0.5),
    "guess": ("Угадай 1–10", "Загадано число от 1 до 10. Угадал — x5.", 5.0, 0.1),
    "dice": ("Кость 1–6", "Угадай число на кубике. x6.", 6.0, 1/6),
    "even": ("Чёт/нечет", "Чётное или нечётное (1–10). x2.", 2.0, 0.5),
    "highlow": ("Выше/ниже 5", "Число 1–10: выше 5 или нет. x2.", 2.0, 0.5),
    "redblack": ("Красное/чёрное", "Классика. x2.", 2.0, 0.5),
    "lucky7": ("Семёрка", "Выпало 7 из 1–10? x10.", 10.0, 0.1),
    "double": ("Дубль", "Две одинаковые цифры (11,22..99). x9.", 9.0, 1/9),
    "triple": ("Тройка", "Три одинаковые (111,222..). x20.", 20.0, 1/10),
    "spin": ("Колесо 1–8", "Угадай сектор. x8.", 8.0, 0.125),
}


def _parse_stake(text: str, default: int = 50) -> int:
    parts = (text or "").strip().split()
    if len(parts) < 2:
        return default
    try:
        n = int(parts[1])
        return max(MINIGAME_STAKE_MIN, min(MINIGAME_STAKE_MAX, n))
    except ValueError:
        return default


async def _run_minigame(user_id: int, username: str, first_name: str, slug: str, stake: int, message: Message):
    name, desc, mult, win_chance = MINIGAMES[slug]
    won = random.random() < win_chance
    win_amount = int(stake * mult) if won else 0

    balance = await db.get_balance(user_id)
    if balance < stake:
        sent = await message.answer(format_insufficient_balance(username, first_name))
        await delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT)
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=stake,
        command_source=f"/{slug}", comment=f"Мини-игра {name}",
        message=message, username=username, first_name=first_name,
        allow_negative=False,
    )
    if not success:
        return

    if won:
        await balance_service.add_game_win(
            user_id=user_id, gross_amount=win_amount,
            command_source=f"/{slug}", comment=f"Мини-игра {name}",
            bot=message.bot, chat_id=message.chat.id, username=username, first_name=first_name,
        )
    balance_after = await db.get_balance(user_id)
    await db.log_game_session(user_id, slug, stake, "win" if won else "loss", (win_amount - stake) if won else -stake, mult if won else 0)

    result = "✅ Победа" if won else "❌ Проигрыш"
    text = format_message_with_username(
        f"🎲 <b>{name}</b>\n\n{result}. "
        + (f"+{win_amount} коинов (x{mult}). " if won else f"Минус {stake} коинов. ")
        + f"Баланс: <b>{balance_after}</b>",
        username, first_name
    )
    sent = await message.answer(text)
    await delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT)


@router.message(Command(*MINIGAMES.keys()))
async def cmd_any_minigame(message: Message):
    """Любая мини-игра по команде /coin, /guess, /dice и т.д."""
    cmd = (message.text or "").strip().split()[0].lstrip("/").lower()
    if cmd not in MINIGAMES:
        return
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    stake = _parse_stake(message.text or "")
    await _run_minigame(user_id, username, first_name, cmd, stake, message)


@router.message(Command("minigames"))
async def cmd_minigames_list(message: Message):
    """Список мини-игр и ставки."""
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    lines = [
        "🎲 <b>Мини-игры</b> — быстрый раунд, ставка 10–500 коинов.",
        "",
        "Команды:",
    ]
    for slug, (name, desc, mult, _) in MINIGAMES.items():
        lines.append(f"• /{slug} — {name}. {desc}")
    lines.append("")
    lines.append("Пример: /coin 50 — орёл/решка на 50 коинов.")
    text = format_message_with_username("\n".join(lines), username, first_name)
    sent = await message.answer(text)
    await delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT)
