"""
Игровые команды бота YandexPticaGPT v0.5
/slot, /konopla, /kripta, /plsdon
С применением бонусов Premium и зелий удачи к шансам выигрыша
Реальный async-механизм для /kripta с таймерами и ранним выходом
"""

import asyncio
import html
import logging
import random
import time
from datetime import datetime
from typing import Dict, Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

from config import config
from db import db
from utils import delete_message_after, delete_message_after_by_id, format_message_with_username, format_message_game_result_async, format_insufficient_balance, format_game_error, resolve_recipient_from_message
from games.rng import game_random
from games.constants import GAME_MAX_DURATION_SEC
from games.fracture_questions import FRACTURE_QUESTIONS_POOL
from middlewares import set_command_cooldown
from services.balance import balance_service
from services.effects import effects_service
from services.news import news_service
from services.events import events_service

# Создаем роутер для игровых команд
router = Router()

logger = logging.getLogger(__name__)

# Глобальное хранилище для активных сессий kripta (в памяти для быстрого доступа)
_active_kripta_sessions: Dict[int, Dict] = {}

# Глобальное хранилище для активных сообщений plsdon (для кнопки пожертвования)
_active_plsdon_messages: Dict[int, Dict] = {}

# Сессии /almaz: user_id -> {bet, current_win, message_id, chat_id, explosion_chance}
_active_almaz_sessions: Dict[int, Dict] = {}

# 40 игр «риск/забрать»: команда /reactor 100, /vault 50 и т.д.
RISK40_SLUGS = (
    "reactor", "vault", "dicepath", "overheat", "mindlock", "bombline", "liftx", "doza",
    "shum", "signal", "freeze", "tunnel", "escape", "code", "magnet", "candle",
    "pulse", "orbit", "wall", "watcher",
    "controlroom", "firesector", "mutation", "satellite", "mine", "clock", "lab", "bunker",
    "storm", "navigator", "icepath", "coinstack", "target", "fuse", "web", "logicgate",
    "depth", "field", "ritual", "trace",
)
_active_risk40_sessions: Dict[int, Dict] = {}  # user_id -> {slug, bet, mult, message_id, chat_id, started_at}
_active_fracture_sessions: Dict[int, Dict] = {}  # user_id -> {bet, choices[], message_id, chat_id, username, first_name}
_active_mirror_sessions: Dict[int, Dict] = {}  # Buckshot: обойма 8, жизни 2/2, ход в себя/в дилера


def get_active_sessions_debug() -> Dict[str, int]:
    """Для /debug: количество активных сессий по играм."""
    return {
        "kripta": len(_active_kripta_sessions),
        "almaz": len(_active_almaz_sessions),
        "plsdon": len(_active_plsdon_messages),
        "risk40": len(_active_risk40_sessions),
        "perekyp": len(_active_perekyp_sessions),
        "blackmarket": len(_active_blackmarket),
        "fracture": len(_active_fracture_sessions),
        "mirror": len(_active_mirror_sessions),
    }


# Честные игры дают больше MMR за победу и меньше за поражение; азартные — наоборот
GAMBLING_GAMES = {"slot", "konopla", "kripta", "almaz", "rulet", "frekaz", "perekyp", "random", "gamerandom", "blackmarket", "echo", "fracture", "mirror"} | set(RISK40_SLUGS)
MMR_WIN_HONEST, MMR_LOSS_HONEST = 15, -10
MMR_WIN_GAMBLING, MMR_LOSS_GAMBLING = 5, -15


async def _safe_callback_answer(callback: CallbackQuery, text: str = "", show_alert: bool = False):
    """Ответ на callback без падения по таймауту (query is too old)."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass


async def _update_mmr_and_achievements(
    user_id: int, game_type: str, result: str, balance_after: int,
    chat_id: Optional[int] = None, bot = None
):
    """Обновить MMR и проверить достижения после игры. При выигрыше может выпасть MMR-ивент (80% шанс, x1.2 множ)."""
    is_gambling = game_type in GAMBLING_GAMES
    if result == "win":
        delta = MMR_WIN_GAMBLING if is_gambling else MMR_WIN_HONEST
    else:
        delta = MMR_LOSS_GAMBLING if is_gambling else MMR_LOSS_HONEST
    new_mmr = await db.update_mmr(user_id, delta, game_type=game_type)
    # Достижения
    stats = await db.get_user_game_stats(user_id)
    if result == "win" and not await db.has_achievement(user_id, "first_win"):
        await db.unlock_achievement(user_id, "first_win")
    if stats["total"] >= 100 and not await db.has_achievement(user_id, "games_100"):
        await db.unlock_achievement(user_id, "games_100")
    if balance_after >= 1_000_000 and not await db.has_achievement(user_id, "millionaire"):
        await db.unlock_achievement(user_id, "millionaire")
    if balance_after >= 1_000_000_000 and not await db.has_achievement(user_id, "billionaire"):
        await db.unlock_achievement(user_id, "billionaire")
    # Серия побед/поражений (последние 10)
    rows = await db.fetchall(
        """SELECT result FROM games_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 10""",
        (user_id,)
    )
    if rows:
        results = [r[0] for r in rows]
        if len(results) >= 10 and all(r == "win" for r in results):
            await db.unlock_achievement(user_id, "wins_streak_10")
            await db.unlock_achievement(user_id, "wins_streak_10_cold")
        if len(results) >= 10 and all(r == "loss" for r in results):
            await db.unlock_achievement(user_id, "losses_streak_10")
            await db.unlock_achievement(user_id, "risky")
    # MMR-ивент: случайный бафф на 1 мин (80% шанс, x1.2 множ и т.д.)
    if result == "win" and chat_id and bot:
        try:
            out = await events_service.try_trigger_mmr_lucky_event(user_id, new_mmr, chat_id, bot)
            if out:
                text, img_name, path = out
                if path.exists():
                    await bot.send_photo(chat_id, FSInputFile(str(path)), caption=text or " ")
                else:
                    await bot.send_message(chat_id, text or "🍀 Ветер удачи на 1 минуту!")
        except Exception as e:
            logger.debug("MMR lucky event send: %s", e)


async def _maybe_send_event_message(user_id: int, chat_id: int, bot: Bot, balance: Optional[int] = None):
    """Если по условиям запускается персональный ивент — отправить сообщение с картинкой (теневой не показываем)."""
    try:
        out = await events_service.try_trigger_event(user_id, chat_id, bot, balance=balance)
        if not out:
            return
        text, img_name, path = out
        if path.exists():
            await bot.send_photo(chat_id, FSInputFile(str(path)), caption=text or " ")
        else:
            await bot.send_message(chat_id, text or "Что-то изменилось. Поиграй — почувствуешь.")
    except Exception as e:
        logger.debug("Event trigger send failed: %s", e)


def _apply_bet_penalty(bet: int, mult: float) -> float:
    """Чем больше ставка — тем меньше эффективный множитель (против фарма на крупных суммах)."""
    if bet <= 50_000:
        return mult
    if bet <= 200_000:
        return round(mult * 0.9, 2)
    if bet <= 500_000:
        return round(mult * 0.8, 2)
    if bet <= 1_000_000:
        return round(mult * 0.7, 2)
    return round(mult * 0.6, 2)


async def calculate_win_chance_async(base_chance: float, user_id: int, game_slug: Optional[str] = None) -> float:
    """
    Асинхронная версия вычисления шанса выигрыша.
    +10% ко всем играм (кроме /kripta), бонус Premium, зелья, новости и персональный ивент (игрок проценты не видит).
    """
    game_bonus = getattr(config, "GAME_WIN_CHANCE_BONUS", 0.0)
    final_chance = base_chance + game_bonus
    premium_bonus = await effects_service.get_win_chance_bonus(user_id)
    luck_multiplier = await effects_service.get_luck_multiplier(user_id)
    final_chance = (final_chance + premium_bonus) * luck_multiplier
    if game_slug:
        news_mod = await news_service.get_win_modifier(game_slug)
        final_chance += news_mod
    ev = await events_service.get_active_event(user_id)
    if ev:
        final_chance = events_service.apply_event_to_win_chance(final_chance, ev.get("event_type"))
    return min(max(final_chance, 0.0), 1.0)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    """Отмена текущей игры: Lucky Jet — возврат ставки; Алмазы — завершение без выигрыша."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    if user_id in _active_kripta_sessions:
        session_data = _active_kripta_sessions[user_id]
        bet = session_data.get("bet", 0)
        if session_data.get("task"):
            session_data["task"].cancel()
        await db.close_kripta_session(user_id)
        del _active_kripta_sessions[user_id]
        if bet > 0:
            await balance_service.add_balance(
                user_id=user_id, amount=bet,
                command_source="/cancel", comment="Возврат при отмене Lucky Jet",
                message=message, username=username, first_name=first_name
            )
        msg = format_message_with_username(
            "Игра Lucky Jet отменена. Ставка возвращена.",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if user_id in _active_almaz_sessions:
        _active_almaz_sessions.pop(user_id, None)
        msg = format_message_with_username(
            "Игра Алмазы отменена. Ставка уже использована.",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if user_id in _active_perekyp_sessions:
        _active_perekyp_sessions.pop(user_id, None)
        msg = format_message_with_username(
            "Перекуп отменён. Деньги не списывались.",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if user_id in _active_risk40_sessions:
        sess = _active_risk40_sessions.pop(user_id, None)
        if sess and sess.get("bet", 0) > 0:
            await balance_service.add_balance(
                user_id=user_id, amount=sess["bet"],
                command_source="/cancel", comment="Возврат при отмене игры",
                message=message, username=username, first_name=first_name
            )
        msg = format_message_with_username(
            "Игра отменена. Ставка возвращена.",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if user_id in _active_mirror_sessions:
        sess = _active_mirror_sessions.pop(user_id, None)
        if sess and sess.get("stake", 0) > 0:
            await balance_service.add_balance(
                user_id=user_id, amount=sess["stake"],
                command_source="/cancel", comment="Зеркало отменено — возврат ставки",
                message=message, username=username, first_name=first_name
            )
        msg = format_message_with_username(
            "Зеркало отменено. Ставка возвращена.",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if user_id in _active_fracture_sessions:
        sess = _active_fracture_sessions.pop(user_id, None)
        if sess and sess.get("timer_task"):
            try:
                sess["timer_task"].cancel()
            except Exception:
                pass
        if sess and sess.get("bet", 0) > 0:
            await balance_service.add_balance(
                user_id=user_id, amount=sess["bet"],
                command_source="/cancel", comment="Излом решения отменён — возврат ставки",
                message=message, username=username, first_name=first_name,
            )
        msg = format_message_with_username(
            "Излом решения отменён. Ставка возвращена.",
            username, first_name
        )
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    msg = format_message_with_username(
        "Нет активной игры для отмены.",
        username, first_name
    )
    sent = await message.answer(msg)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Проверка активной игры: Kripta, Almaz или нет."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    if user_id in _active_kripta_sessions:
        sess = _active_kripta_sessions[user_id]
        mult = sess.get("current_multiplier", 1.0)
        bet = sess.get("bet", 0)
        msg = format_message_with_username(
            f"Активная игра: <b>Lucky Jet</b> (/kripta)\nСтавка: {bet} коинов, множитель: x{mult:.1f}",
            username, first_name
        )
    elif user_id in _active_almaz_sessions:
        sess = _active_almaz_sessions[user_id]
        cw = sess.get("current_win", 0)
        msg = format_message_with_username(
            f"Активная игра: <b>Алмазы</b> (/almaz)\nТекущий выигрыш: {cw} коинов",
            username, first_name
        )
    elif user_id in _active_risk40_sessions:
        sess = _active_risk40_sessions[user_id]
        slug = sess.get("slug", "?")
        mult = sess.get("mult", 1.0)
        bet = sess.get("bet", 0)
        msg = format_message_with_username(
            f"Активная игра: <b>{slug}</b> (/{slug})\nСтавка: {bet}, множитель: x{mult:.2f}",
            username, first_name
        )
    elif user_id in _active_perekyp_sessions:
        sess = _active_perekyp_sessions[user_id]
        price = sess.get("listing", {}).get("price", 0)
        msg = format_message_with_username(
            f"Активная игра: <b>Перекуп</b> (/perekyp)\nТекущее объявление: {price} коинов",
            username, first_name
        )
    elif user_id in _active_fracture_sessions:
        sess = _active_fracture_sessions[user_id]
        step = len(sess.get("answers", [])) + 1
        lives = sess.get("lives", FRACTURE_LIVES)
        msg = format_message_with_username(
            f"Активная игра: <b>Излом решения</b> (/fracture)\nВопрос {step}/{FRACTURE_NUM_STEPS}, жизней: ❤️{lives}, на ответ {FRACTURE_QUESTION_TIMEOUT_SEC} сек.",
            username, first_name
        )
    elif user_id in _active_mirror_sessions:
        msg = format_message_with_username(
            "Активная игра: <b>Зеркало</b> (/mirror)\nВыбери: в себя или в дилера.",
            username, first_name
        )
    else:
        msg = format_message_with_username("Нет активной игры.", username, first_name)

    sent = await message.answer(msg)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


# ---------- 40 игр: уникальные описания и механика (bust_base, bust_per_step, mult_step) ----------
def _risk40_display_name(slug: str) -> str:
    """Отображаемое имя игры для сообщений."""
    names = {
        "reactor": "Reactor", "vault": "Vault", "dicepath": "Dice Path", "overheat": "Overheat",
        "mindlock": "Mind Lock", "bombline": "Bomb Line", "liftx": "Lift X", "doza": "Doza",
        "shum": "Shum", "signal": "Signal", "freeze": "Freeze", "tunnel": "Tunnel",
        "escape": "Escape", "code": "Code", "magnet": "Magnet", "candle": "Candle",
        "pulse": "Pulse", "orbit": "Orbit", "wall": "Wall", "watcher": "Watcher",
        "controlroom": "Control Room", "firesector": "Fire Sector", "mutation": "Mutation",
        "satellite": "Satellite", "mine": "Mine", "clock": "Clock", "lab": "Lab",
        "bunker": "Bunker", "storm": "Storm", "navigator": "Navigator", "icepath": "Ice Path",
        "coinstack": "Coin Stack", "target": "Target", "fuse": "Fuse", "web": "Web",
        "logicgate": "Logic Gate", "depth": "Depth", "field": "Field", "ritual": "Ritual",
        "trace": "Trace",
    }
    return names.get(slug, slug)


# 40 игр: уникальное описание + разный баланс риск/награда.
# Профили: safe (низкий bust, медленный рост) — medium — high — extreme (высокий bust, быстрый рост).
# actions: (action_id, label, bust_base, bust_per_step, mult_step)
RISK40_GAMES = {
    "reactor": {
        "description": "Реактор нестабилен. <b>Охладить</b> — риск ниже, множитель ползёт. <b>Греть</b> — множитель взлетает, шанс взрыва высокий. Забрать — в любой момент.",
        "take_btn": "Остановить и забрать",
        "actions": [
            ("cool", "Охладить", 0.04, 0.012, 1.07),
            ("heat", "Греть", 0.26, 0.058, 1.44),
        ],
    },
    "vault": {
        "description": "Сейф с двойным замком. <b>Крутить тихо</b> — множитель растёт стабильно. <b>Дёрнуть резко</b> — быстрый рост или блокировка. Забрать — унести выигрыш.",
        "take_btn": "Открыть и забрать",
        "actions": [
            ("soft", "Крутить тихо", 0.05, 0.018, 1.09),
            ("hard", "Дёрнуть резко", 0.18, 0.042, 1.30),
        ],
    },
    "dicepath": {
        "description": "Дорожка из костей. <b>Шаг осторожный</b> — меньше шанс сорваться. <b>Бросок на удачу</b> — большой скачок или падение. Забрать — зафиксировать результат.",
        "take_btn": "Сойти с дорожки",
        "actions": [
            ("safe", "Шаг осторожный", 0.06, 0.020, 1.10),
            ("rush", "Бросок на удачу", 0.22, 0.052, 1.36),
        ],
    },
    "overheat": {
        "description": "Индикатор нагрева ползёт вверх. <b>Сбросить температуру</b> — плавный рост. <b>Разогнать ещё</b> — множитель выше, перегрев ближе. Забрать — до красной зоны.",
        "take_btn": "Выключить и забрать",
        "actions": [
            ("cool", "Сбросить температуру", 0.07, 0.024, 1.12),
            ("boost", "Разогнать ещё", 0.28, 0.056, 1.48),
        ],
    },
    "mindlock": {
        "description": "Держишь концентрацию. <b>Дышать ровно</b> — стабильный рост. <b>Напрячься</b> — множитель резко выше, легче потерять фокус. Забрать — выйти из транса с выигрышем.",
        "take_btn": "Выйти из транса",
        "actions": [
            ("calm", "Дышать ровно", 0.03, 0.014, 1.06),
            ("focus", "Напрячься", 0.20, 0.050, 1.32),
        ],
    },
    "bombline": {
        "description": "Линия ячеек, в одной — бомба. <b>Проверить ячейку</b> — осторожно. <b>Шаг вперёд</b> — быстрее к цели, выше шанс взрыва. Забрать — уйти с текущим.",
        "take_btn": "Отступить с выигрышем",
        "actions": [
            ("check", "Проверить ячейку", 0.09, 0.026, 1.13),
            ("step", "Шаг вперёд", 0.28, 0.058, 1.42),
        ],
    },
    "liftx": {
        "description": "Лифт поднимается. <b>Один этаж</b> — безопаснее. <b>Пролететь два</b> — двойной рост или обвал. Забрать — выйти на текущем этаже.",
        "take_btn": "Выйти на этаже",
        "actions": [
            ("one", "Один этаж", 0.06, 0.020, 1.09),
            ("two", "Пролететь два", 0.21, 0.048, 1.30),
        ],
    },
    "doza": {
        "description": "Каждая кнопка — доза. <b>Микро-доза</b> — множитель растёт медленно. <b>Полная доза</b> — быстрый рост и высокий риск передоза. Забрать — вовремя.",
        "take_btn": "Забрать и выйти",
        "actions": [
            ("micro", "Микро-доза", 0.05, 0.018, 1.08),
            ("full", "Полная доза", 0.25, 0.056, 1.40),
        ],
    },
    "shum": {
        "description": "Двигаешься в шуме. <b>Тихо</b> — множитель тихо растёт. <b>Быстро</b> — множитель выше, риск быть замеченным. Забрать — выйти из шума.",
        "take_btn": "Выйти из шума",
        "actions": [
            ("slow", "Тихо", 0.05, 0.018, 1.08),
            ("fast", "Быстро", 0.19, 0.046, 1.28),
        ],
    },
    "signal": {
        "description": "Ловишь сигнал. <b>Держать волну</b> — стабильно. <b>Усилить приём</b> — множитель выше, связь нестабильнее. Забрать — зафиксировать сигнал.",
        "take_btn": "Забрать сигнал",
        "actions": [
            ("hold", "Держать волну", 0.04, 0.016, 1.07),
            ("boost", "Усилить приём", 0.22, 0.052, 1.34),
        ],
    },
    "freeze": {
        "description": "Множитель заморожен. <b>Разморозить по чуть-чуть</b> — рост плавный. <b>Резко разморозить</b> — большой скачок или трещина. Забрать — унести лёд.",
        "take_btn": "Забрать и выйти",
        "actions": [
            ("melt", "Разморозить по чуть-чуть", 0.06, 0.022, 1.10),
            ("crack", "Резко разморозить", 0.21, 0.050, 1.32),
        ],
    },
    "tunnel": {
        "description": "Копаешь тоннель. <b>Аккуратно</b> — меньше обвалов. <b>Пробивать</b> — быстрее, риск завала выше. Забрать — вынести добычу.",
        "take_btn": "Выйти с добычей",
        "actions": [
            ("careful", "Копать аккуратно", 0.08, 0.024, 1.11),
            ("blast", "Пробивать", 0.26, 0.054, 1.38),
        ],
    },
    "escape": {
        "description": "Убегаешь. <b>Тихий шаг</b> — множитель скромнее. <b>Спринт</b> — быстрый рост или споткнуться. Забрать — спасение с выигрышем.",
        "take_btn": "Остановиться и забрать",
        "actions": [
            ("sneak", "Тихий шаг", 0.05, 0.018, 1.08),
            ("sprint", "Спринт", 0.23, 0.052, 1.35),
        ],
    },
    "code": {
        "description": "Подбираешь код. <b>По цифре</b> — осторожно. <b>Угадать комбо</b> — резкий рост или блокировка. Забрать — открыть сейф с текущим.",
        "take_btn": "Открыть и забрать",
        "actions": [
            ("digit", "По цифре", 0.08, 0.024, 1.12),
            ("combo", "Угадать комбо", 0.28, 0.058, 1.44),
        ],
    },
    "magnet": {
        "description": "Поле притягивает бонусы и угрозы. <b>Слабое поле</b> — меньше риск. <b>Максимум</b> — множитель высокий, опасности ближе. Забрать — отключить поле.",
        "take_btn": "Отключить поле",
        "actions": [
            ("low", "Слабое поле", 0.05, 0.018, 1.08),
            ("max", "Максимум", 0.24, 0.052, 1.36),
        ],
    },
    "candle": {
        "description": "Пока свеча горит — множитель растёт. <b>Ждать</b> — пламя стабильнее. <b>Подлить воск</b> — множитель выше, дуть может сильнее. Затушить и забрать — в любой момент.",
        "take_btn": "Затушить и забрать",
        "actions": [
            ("wait", "Ждать", 0.06, 0.022, 1.10),
            ("wax", "Подлить воск", 0.22, 0.050, 1.32),
        ],
    },
    "pulse": {
        "description": "Ритм задаёт темп. <b>В такт</b> — стабильно. <b>Ускориться</b> — множитель выше, легче пропустить удар. Забрать — зафиксировать пульс.",
        "take_btn": "Забрать в такт",
        "actions": [
            ("sync", "В такт", 0.05, 0.018, 1.08),
            ("rush", "Ускориться", 0.24, 0.054, 1.36),
        ],
    },
    "orbit": {
        "description": "Вращаешься по орбите. <b>Стабильный виток</b> — множитель плавный. <b>Ускорить виток</b> — множитель выше, перегрузка. Сойти с орбиты — забрать.",
        "take_btn": "Сойти с орбиты",
        "actions": [
            ("stable", "Стабильный виток", 0.04, 0.014, 1.07),
            ("speed", "Ускорить виток", 0.20, 0.048, 1.30),
        ],
    },
    "wall": {
        "description": "Стена перед тобой. <b>Пробивать точечно</b> — меньше риск обвала. <b>Таран</b> — быстрый пролом или всё рушится. Отступить — забрать текущее.",
        "take_btn": "Отступить",
        "actions": [
            ("pick", "Пробивать точечно", 0.08, 0.026, 1.12),
            ("ram", "Таран", 0.27, 0.056, 1.42),
        ],
    },
    "watcher": {
        "description": "За тобой следят. <b>Не выделяться</b> — множитель тихо растёт. <b>Играть на публику</b> — множитель выше, внимание растёт. Забрать — выйти из поля зрения.",
        "take_btn": "Выйти и забрать",
        "actions": [
            ("hide", "Не выделяться", 0.06, 0.020, 1.09),
            ("show", "Играть на публику", 0.21, 0.050, 1.31),
        ],
    },
    "controlroom": {
        "description": "Панель управления. <b>Стабилизировать</b> — риск аварии ниже. <b>Дать нагрузку</b> — множитель выше, индикаторы в красном. Забрать — зафиксировать режим.",
        "take_btn": "Забрать и выйти",
        "actions": [
            ("stable", "Стабилизировать", 0.05, 0.018, 1.08),
            ("load", "Дать нагрузку", 0.22, 0.050, 1.32),
        ],
    },
    "firesector": {
        "description": "Сектор в огне. <b>Тушить</b> — множитель растёт медленно. <b>Идти сквозь</b> — множитель выше, шанс обжечься. Забрать — до полного пожара.",
        "take_btn": "Выйти с выигрышем",
        "actions": [
            ("extinguish", "Тушить", 0.07, 0.024, 1.11),
            ("through", "Идти сквозь", 0.28, 0.058, 1.44),
        ],
    },
    "mutation": {
        "description": "Каждый ход — мутация. <b>Контролируемая</b> — множитель предсказуемее. <b>Агрессивная</b> — множитель резко выше, шанс провала. Забрать — зафиксировать результат.",
        "take_btn": "Забрать результат",
        "actions": [
            ("control", "Контролируемая", 0.06, 0.022, 1.10),
            ("aggressive", "Агрессивная", 0.26, 0.058, 1.42),
        ],
    },
    "satellite": {
        "description": "Спутник теряет связь. <b>Удерживать канал</b> — стабильно. <b>Усилить передачу</b> — множитель выше, связь нестабильнее. Забрать — сохранить данные.",
        "take_btn": "Забрать данные",
        "actions": [
            ("hold", "Удерживать канал", 0.05, 0.018, 1.08),
            ("boost", "Усилить передачу", 0.20, 0.046, 1.28),
        ],
    },
    "mine": {
        "description": "Бьёшь киркой. <b>Аккуратный удар</b> — меньше обвалов. <b>Размах</b> — быстрее добыча или завал. Забрать — унести руду.",
        "take_btn": "Выйти с рудой",
        "actions": [
            ("tap", "Аккуратный удар", 0.09, 0.026, 1.12),
            ("swing", "Размах", 0.30, 0.060, 1.46),
        ],
    },
    "clock": {
        "description": "Время тикает. <b>Ждать тик</b> — риск обнуления ниже. <b>Ускорить</b> — множитель выше, время летит. Забрать — до нуля.",
        "take_btn": "Забрать до нуля",
        "actions": [
            ("tick", "Ждать тик", 0.05, 0.018, 1.08),
            ("rush", "Ускорить", 0.22, 0.050, 1.32),
        ],
    },
    "lab": {
        "description": "Эксперимент в пробирке. <b>Медленный нагрев</b> — реакция предсказуемее. <b>Резко смешать</b> — множитель выше, взрыв возможнее. Забрать — зафиксировать результат.",
        "take_btn": "Забрать результат",
        "actions": [
            ("slow", "Медленный нагрев", 0.06, 0.022, 1.10),
            ("mix", "Резко смешать", 0.24, 0.052, 1.34),
        ],
    },
    "bunker": {
        "description": "Спускаешься в бункер. <b>Один уровень</b> — безопаснее. <b>Два уровня</b> — быстрее вниз, риск выше. Выйти — забрать на любом этаже.",
        "take_btn": "Выйти наверх",
        "actions": [
            ("one", "Один уровень", 0.06, 0.020, 1.09),
            ("two", "Два уровня", 0.20, 0.046, 1.28),
        ],
    },
    "storm": {
        "description": "Шторм набирает силу. <b>Держаться</b> — множитель растёт медленно. <b>Идти в ветер</b> — множитель выше, шанс падения. Забрать — пока не снесло.",
        "take_btn": "Укрыться с выигрышем",
        "actions": [
            ("hold", "Держаться", 0.08, 0.026, 1.12),
            ("walk", "Идти в ветер", 0.26, 0.056, 1.38),
        ],
    },
    "navigator": {
        "description": "Выбираешь путь. <b>Проверенная тропа</b> — множитель плавный. <b>Короткий путь</b> — быстрее к цели, опаснее. Остановиться — забрать множитель.",
        "take_btn": "Остановиться",
        "actions": [
            ("safe", "Проверенная тропа", 0.05, 0.018, 1.08),
            ("short", "Короткий путь", 0.19, 0.046, 1.28),
        ],
    },
    "icepath": {
        "description": "Лёд под ногами. <b>Шаг осторожный</b> — меньше трещин. <b>Скользить</b> — быстрее, риск провала выше. Забрать — дойти до берега.",
        "take_btn": "Сойти на берег",
        "actions": [
            ("careful", "Шаг осторожный", 0.07, 0.024, 1.11),
            ("slide", "Скользить", 0.23, 0.052, 1.33),
        ],
    },
    "coinstack": {
        "description": "Складываешь монеты. <b>Аккуратно положить</b> — башня стабильнее. <b>Бросить сверху</b> — быстрый рост или обвал. Забрать — зафиксировать высоту.",
        "take_btn": "Забрать башню",
        "actions": [
            ("place", "Аккуратно положить", 0.07, 0.024, 1.11),
            ("drop", "Бросить сверху", 0.24, 0.052, 1.34),
        ],
    },
    "target": {
        "description": "Стреляешь по мишени. <b>Прицельный выстрел</b> — множитель растёт стабильно. <b>Быстрый выстрел</b> — множитель выше, риск промаха. Забрать — сохранить счёт.",
        "take_btn": "Забрать счёт",
        "actions": [
            ("aim", "Прицельный выстрел", 0.06, 0.022, 1.10),
            ("quick", "Быстрый выстрел", 0.22, 0.050, 1.32),
        ],
    },
    "fuse": {
        "description": "Фитиль горит. <b>Ждать</b> — множитель растёт медленно. <b>Раздуть</b> — множитель выше, взрыв ближе. Забрать — до взрыва.",
        "take_btn": "Затушить и забрать",
        "actions": [
            ("wait", "Ждать", 0.07, 0.024, 1.11),
            ("blow", "Раздуть", 0.28, 0.058, 1.42),
        ],
    },
    "web": {
        "description": "Паутина вокруг. <b>Выбираться медленно</b> — множитель тихо растёт. <b>Рвать</b> — быстрее на свободу или запутаться. Забрать — выйти с добычей.",
        "take_btn": "Выбраться",
        "actions": [
            ("slow", "Выбираться медленно", 0.06, 0.020, 1.09),
            ("tear", "Рвать", 0.21, 0.050, 1.30),
        ],
    },
    "logicgate": {
        "description": "Логические ворота. <b>Проверенный вход</b> — множитель плавный. <b>Угадать комбинацию</b> — множитель выше, сброс при ошибке. Забрать — зафиксировать выход.",
        "take_btn": "Забрать выход",
        "actions": [
            ("safe", "Проверенный вход", 0.05, 0.018, 1.08),
            ("guess", "Угадать комбинацию", 0.22, 0.050, 1.32),
        ],
    },
    "depth": {
        "description": "Погружаешься глубже. <b>Один уровень</b> — давление растёт медленно. <b>Нырнуть глубже</b> — множитель выше, декомпрессия опаснее. Выйти — забрать на любой глубине.",
        "take_btn": "Всплыть с выигрышем",
        "actions": [
            ("one", "Один уровень", 0.07, 0.024, 1.10),
            ("deep", "Нырнуть глубже", 0.24, 0.052, 1.34),
        ],
    },
    "field": {
        "description": "Поле притягивает бонусы. <b>Держать слабо</b> — множитель понемногу. <b>Максимум поля</b> — множитель высокий, опасности ближе. Забрать — отключить поле.",
        "take_btn": "Отключить поле",
        "actions": [
            ("low", "Держать слабо", 0.05, 0.018, 1.08),
            ("max", "Максимум поля", 0.20, 0.046, 1.28),
        ],
    },
    "ritual": {
        "description": "Ритуал усиливается. <b>Шаг по кругу</b> — стабильно. <b>Усилить призыв</b> — множитель выше, обратная волна опаснее. Забрать — завершить в плюсе.",
        "take_btn": "Завершить ритуал",
        "actions": [
            ("step", "Шаг по кругу", 0.06, 0.022, 1.09),
            ("invoke", "Усилить призыв", 0.23, 0.052, 1.33),
        ],
    },
    "trace": {
        "description": "Оставляешь следы. <b>Тихо</b> — множитель растёт медленно. <b>Быстро</b> — множитель выше, след заметнее. Забрать — скрыться с выигрышем.",
        "take_btn": "Скрыться с выигрышем",
        "actions": [
            ("quiet", "Тихо", 0.05, 0.018, 1.08),
            ("fast", "Быстро", 0.20, 0.046, 1.28),
        ],
    },
}

# Парсинг callback_data для risk40: risk40_take_SLUG|USER_ID или risk40_act_SLUG|ACTION|USER_ID
def _parse_risk40_callback(data: str, prefix: str):
    """Возвращает (slug, user_id) или (None, None) при ошибке."""
    if not data.startswith(prefix) or "|" not in data:
        return None, None
    try:
        rest = data[len(prefix):]
        slug, uid_str = rest.split("|", 1)
        return slug.strip(), int(uid_str.strip())
    except (ValueError, IndexError):
        return None, None


def _parse_risk40_act_callback(data: str, prefix: str):
    """Для risk40_act_SLUG|ACTION|USER_ID возвращает (slug, action_id, user_id) или (None, None, None)."""
    if not data.startswith(prefix) or data.count("|") < 2:
        return None, None, None
    try:
        rest = data[len(prefix):]
        parts = rest.split("|", 2)
        return parts[0].strip(), parts[1].strip(), int(parts[2].strip())
    except (ValueError, IndexError):
        return None, None, None


def _risk40_build_keyboard(slug: str, user_id: int, mult: float):
    """Клавиатура: Забрать + две тематические кнопки по RISK40_GAMES[slug]."""
    game = RISK40_GAMES.get(slug)
    if not game:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💰 Забрать x{mult:.2f}", callback_data=f"risk40_take_{slug}|{user_id}")],
        ])
    take_btn = game["take_btn"]
    row_take = [InlineKeyboardButton(text=f"💰 {take_btn} x{mult:.2f}", callback_data=f"risk40_take_{slug}|{user_id}")]
    row_actions = [
        InlineKeyboardButton(text=label, callback_data=f"risk40_act_{slug}|{act_id}|{user_id}")
        for act_id, label, _, _, _ in game["actions"]
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row_take, row_actions])


async def _run_risk40(message: Message, slug: str):
    """Общий запуск одной из 40 игр: ставка, стартовый экран, кнопки Забрать / Ещё."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    if user_id in _active_risk40_sessions:
        sent = await message.answer(format_message_with_username(
            f"У тебя уже есть активная игра. Заверши её или /cancel.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if await news_service.is_game_closed(slug):
        sent = await message.answer(format_message_with_username(
            f"Игра «{_risk40_display_name(slug)}» временно на починке — загляни в /news.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
    parts = (message.text or "").strip().split()
    try:
        bet = int(parts[1]) if len(parts) > 1 else config.RISK40_BET_MIN
    except (ValueError, IndexError):
        bet = config.RISK40_BET_MIN
    bet = max(config.RISK40_BET_MIN, min(config.RISK40_BET_MAX, bet))

    balance = await db.get_balance(user_id)
    if balance < bet:
        sent = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=bet,
        command_source=f"/{slug}", comment=f"Ставка в игре {slug}",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return
    await set_command_cooldown(user_id, f"/{slug}")

    display = _risk40_display_name(slug)
    game = RISK40_GAMES.get(slug)
    desc = game["description"] if game else "Забери выигрыш или сделай ход."
    caption = format_message_with_username(
        f"🎮 <b>{display}</b>\n\nСтавка: {bet} коинов\nМножитель: <b>x1.00</b>\n\n{desc}",
        username, first_name
    )
    # Сессию создаём до отправки сообщения, чтобы кнопки не давали «Игра уже завершена»
    _active_risk40_sessions[user_id] = {
        "slug": slug, "bet": bet, "mult": 1.0, "step": 0,
        "username": username, "first_name": first_name,
        "message_id": None, "chat_id": None, "started_at": time.time(),
    }
    keyboard = _risk40_build_keyboard(slug, user_id, 1.0)
    photo_path = config.get_game_image_path(slug, "start")
    try:
        if photo_path.exists():
            sent_msg = await message.answer_photo(
                FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard
            )
        else:
            sent_msg = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.warning("risk40 start photo %s: %s", slug, e)
        sent_msg = await message.answer(caption, reply_markup=keyboard)

    _active_risk40_sessions[user_id]["message_id"] = sent_msg.message_id
    _active_risk40_sessions[user_id]["chat_id"] = sent_msg.chat.id
    asyncio.create_task(_risk40_timeout_task(message.bot, user_id, GAME_MAX_DURATION_SEC))
    logger.info("User %s started /%s bet=%s", user_id, slug, bet)


async def _risk40_timeout_task(bot: Bot, user_id: int, timeout_sec: int):
    """По таймауту — забрать по текущему множителю."""
    await asyncio.sleep(timeout_sec)
    sess = _active_risk40_sessions.pop(user_id, None)
    if not sess:
        return
    slug, bet, mult = sess["slug"], sess["bet"], sess["mult"]
    chat_id, message_id = sess["chat_id"], sess["message_id"]
    win_amount = int(bet * mult)
    try:
        if win_amount > 0:
            await balance_service.add_game_win(
                user_id=user_id, gross_amount=win_amount,
                command_source=f"/{slug}", comment="Авто-забрать по таймауту",
                bot=bot, chat_id=chat_id, username=None, first_name=None,
            )
            await db.log_game_session(user_id, slug, bet, "win", win_amount - bet, mult)
            await db.log_admin_game(user_id, None, f"/{slug}", bet, "win", win_amount - bet, None)
        user = await db.get_user(user_id)
        un = user.get("username") if user else None
        caption = format_message_with_username(
            f"⏱ Время вышло. Забрал <b>{win_amount}</b> коинов (x{mult:.2f})." if win_amount > 0 else "⏱ Время вышло. Игра завершена.",
            un, None
        )
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption, reply_markup=None)
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, message_id, config.GAME_RESULT_DELETE_TIMEOUT))
    except Exception as e:
        logger.exception("risk40 timeout: %s", e)


@router.callback_query(F.data.startswith("risk40_take_"))
async def cb_risk40_take(callback: CallbackQuery):
    """Забрать выигрыш в одной из 40 игр."""
    slug, target_id = _parse_risk40_callback(callback.data, "risk40_take_")
    if slug is None or target_id is None:
        await _safe_callback_answer(callback, "Ошибка", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await _safe_callback_answer(callback, "Не жми на чужое!", show_alert=True)
        return
    if target_id not in _active_risk40_sessions:
        await _safe_callback_answer(callback, "Игра уже завершена.", show_alert=True)
        return
    sess = _active_risk40_sessions.pop(target_id)
    if sess.get("slug") != slug:
        _active_risk40_sessions[target_id] = sess
        await _safe_callback_answer(callback, "Ошибка игры.", show_alert=True)
        return
    await _safe_callback_answer(callback, "")
    bet, mult = sess["bet"], sess["mult"]
    win_amount = int(bet * mult)
    username = callback.from_user.username
    first_name = callback.from_user.first_name
    _, _, _, tax = await balance_service.add_game_win(
        user_id=target_id, gross_amount=win_amount,
        command_source=f"/{slug}", comment=f"Выигрыш {slug} x{mult:.2f}",
        bot=callback.bot, chat_id=callback.message.chat.id,
        username=username, first_name=first_name,
    )
    await db.log_game_session(target_id, slug, bet, "win", win_amount - bet, mult)
    await db.log_admin_game(target_id, username, f"/{slug}", bet, "win", win_amount - bet, tax or 0)
    balance_after = await db.get_balance(target_id)
    await _update_mmr_and_achievements(target_id, slug, "win", balance_after)
    await db.add_cup_win(target_id, slug)
    if await db.get_risk40_distinct_count(target_id) >= 40:
        await db.unlock_achievement(target_id, "all_40_risk")
    caption = await format_message_game_result_async(
        f"вы выиграли. 🎮 Забрал <b>{win_amount}</b> коинов (x{mult:.2f}). Баланс: <b>{balance_after}</b>",
        target_id
    )
    photo_path = config.get_game_image_path(slug, "win")
    try:
        if photo_path.exists():
            media = InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
            await callback.bot.edit_message_media(chat_id=callback.message.chat.id, message_id=callback.message.message_id, media=media, reply_markup=None)
        else:
            await callback.bot.edit_message_caption(chat_id=callback.message.chat.id, message_id=callback.message.message_id, caption=caption, reply_markup=None)
    except Exception:
        try:
            if photo_path.exists():
                await callback.bot.send_photo(callback.message.chat.id, FSInputFile(str(photo_path)), caption=caption)
            else:
                await callback.bot.send_message(callback.message.chat.id, caption)
        except Exception:
            pass
    await _safe_callback_answer(callback, "Забрал!")
    asyncio.create_task(delete_message_after_by_id(callback.bot, callback.message.chat.id, callback.message.message_id, config.GAME_RESULT_DELETE_TIMEOUT))


@router.callback_query(F.data.startswith("risk40_act_"))
async def cb_risk40_act(callback: CallbackQuery):
    """Одна из двух тематических кнопок: своя механика (bust_base, bust_per, mult_step) на действие."""
    slug, action_id, target_id = _parse_risk40_act_callback(callback.data, "risk40_act_")
    if slug is None or action_id is None or target_id is None:
        await _safe_callback_answer(callback, "Ошибка", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await _safe_callback_answer(callback, "Не жми на чужое!", show_alert=True)
        return
    if target_id not in _active_risk40_sessions:
        await _safe_callback_answer(callback, "Игра уже завершена.", show_alert=True)
        return
    sess = _active_risk40_sessions[target_id]
    if sess.get("slug") != slug:
        await _safe_callback_answer(callback, "Ошибка игры.", show_alert=True)
        return
    game = RISK40_GAMES.get(slug)
    if not game:
        await _safe_callback_answer(callback, "Ошибка игры.", show_alert=True)
        return
    action_mech = next((a for a in game["actions"] if a[0] == action_id), None)
    if not action_mech:
        await _safe_callback_answer(callback, "Ошибка действия.", show_alert=True)
        return
    await _safe_callback_answer(callback, "")
    _act_id, label, bust_base, bust_per, mult_step = action_mech
    step = sess.get("step", 0) + 1
    bust_chance = min(0.95, bust_base + step * bust_per)
    news_mod = await news_service.get_win_modifier(slug)
    bust_chance = max(0.02, min(0.95, bust_chance - news_mod))
    if game_random.random() < bust_chance:
        bet = sess["bet"]
        del _active_risk40_sessions[target_id]
        await db.log_game_session(target_id, slug, bet, "loss", -bet, sess["mult"])
        await db.log_admin_game(target_id, (await db.get_user(target_id) or {}).get("username", ""), f"/{slug}", bet, "loss", -bet, 0)
        balance_after = await db.get_balance(target_id)
        await _update_mmr_and_achievements(target_id, slug, "loss", balance_after)
        photo_path = config.get_game_image_path(slug, "lose")
        caption = await format_message_game_result_async(
            f"вы проиграли. 💥 Потеряли ставку <b>{bet}</b> коинов. Баланс: <b>{balance_after}</b>",
            target_id
        )
        try:
            if photo_path.exists():
                media = InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
                await callback.bot.edit_message_media(chat_id=callback.message.chat.id, message_id=callback.message.message_id, media=media, reply_markup=None)
            else:
                await callback.bot.edit_message_caption(chat_id=callback.message.chat.id, message_id=callback.message.message_id, caption=caption, reply_markup=None)
            asyncio.create_task(delete_message_after_by_id(callback.bot, callback.message.chat.id, callback.message.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
        except Exception:
            bust_msg = None
            if photo_path.exists():
                bust_msg = await callback.bot.send_photo(callback.message.chat.id, FSInputFile(str(photo_path)), caption=caption)
            else:
                bust_msg = await callback.bot.send_message(callback.message.chat.id, caption)
            if bust_msg:
                asyncio.create_task(delete_message_after_by_id(callback.bot, callback.message.chat.id, bust_msg.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
        await _safe_callback_answer(callback, "Обвал…")
    else:
        sess["step"] = step
        sess["mult"] = round(sess["mult"] * mult_step, 2)
        mult = sess["mult"]
        display = _risk40_display_name(slug)
        desc = game["description"]
        caption = format_message_with_username(
            f"🎮 <b>{display}</b>\n\nСтавка: {sess['bet']} коинов\nМножитель: <b>x{mult:.2f}</b>\n\n{desc}",
            callback.from_user.username, callback.from_user.first_name
        )
        keyboard = _risk40_build_keyboard(slug, target_id, mult)
        try:
            await callback.bot.edit_message_caption(
                chat_id=callback.message.chat.id, message_id=callback.message.message_id,
                caption=caption, reply_markup=keyboard
            )
        except Exception as e:
            logger.warning("risk40 act edit: %s", e)
        await _safe_callback_answer(callback, f"x{mult:.2f}!")


def _create_risk40_handler(slug: str):
    async def handler(message: Message):
        await _run_risk40(message, slug)
    return handler


for _slug in RISK40_SLUGS:
    router.message.register(_create_risk40_handler(_slug), Command(_slug))


# ---------- /rulet (русская рулетка: 2–8 игроков, каждые 20 сек выбывает один, последний забирает банк) ----------
_active_rulet_sessions: Dict[int, Dict] = {}  # chat_id -> {creator_id, bet, participants, message_id, chat_id, bank, task, bot}


async def _rulet_elimination_loop(chat_id: int):
    """Каждые 20 сек исключаем случайного игрока; когда остаётся 1 — он забирает банк."""
    min_players = getattr(config, "RULET_MIN_PLAYERS", 2)
    interval = getattr(config, "RULET_ELIMINATION_INTERVAL", 20)
    while True:
        await asyncio.sleep(interval)
        sess = _active_rulet_sessions.get(chat_id)
        if not sess or len(sess["participants"]) <= 1:
            if sess and sess.get("task"):
                sess["task"].cancel()
            break
        out_id = game_random.choice(sess["participants"])
        sess["participants"] = [p for p in sess["participants"] if p != out_id]
        bot = sess["bot"]
        out_msg_id = None
        try:
            user = await db.get_user(out_id)
            un = (user.get("username") or "user") if user else "user"
            out_caption = format_message_with_username("💥 Выбыл из рулетки. Остальные держатся.", un, None)
            photo_path = config.get_image_path("rulet_out.jpg")
            if photo_path.exists():
                out_msg = await bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=out_caption)
            else:
                out_msg = await bot.send_message(chat_id, out_caption)
            out_msg_id = out_msg.message_id
        except Exception as e:
            logger.warning("rulet out message: %s", e)
        if out_msg_id is not None:
            asyncio.create_task(delete_message_after_by_id(bot, chat_id, out_msg_id, 15))
        if len(sess["participants"]) == 1:
            winner_id = sess["participants"][0]
            bank = sess["bank"]
            del _active_rulet_sessions[chat_id]
            if sess.get("task"):
                sess["task"].cancel()
            try:
                main_mid = sess.get("message_id")
                try:
                    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=main_mid, reply_markup=None)
                    await bot.edit_message_caption(chat_id=chat_id, message_id=main_mid, caption="🔫 Русская рулетка завершена. Победитель забирает банк.")
                except Exception:
                    pass
                asyncio.create_task(delete_message_after_by_id(bot, chat_id, main_mid, config.GAME_RESULT_DELETE_TIMEOUT))
                await balance_service.add_game_win(
                    user_id=winner_id, gross_amount=bank,
                    command_source="/rulet", comment="Победа в русской рулетке",
                    bot=bot, chat_id=chat_id, username=None, first_name=None,
                )
                user = await db.get_user(winner_id)
                un = (user.get("username") or "user") if user else "user"
                win_caption = format_message_with_username(
                    f"🎉 Дружок, ты последний на ногах — забираешь банк <b>{bank}</b> коинов.", un, None
                )
                photo_path = config.get_image_path("rulet_win.jpg")
                if photo_path.exists():
                    win_msg = await bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=win_caption)
                else:
                    win_msg = await bot.send_message(chat_id, win_caption)
                asyncio.create_task(delete_message_after_by_id(bot, chat_id, win_msg.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
            except Exception as e:
                logger.exception("rulet winner pay: %s", e)
            break


@router.message(Command("rulet"))
async def cmd_rulet(message: Message):
    """Русская рулетка: /rulet сумма. Минимум 2, максимум 8 игроков. Кнопка «Вступить». Каждые 20 сек выбывает один, последний забирает банк."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    chat_id = message.chat.id

    if chat_id in _active_rulet_sessions:
        sent = await message.answer(format_message_with_username(
            "В этом чате уже идёт русская рулетка. Вступи или дождись окончания.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    parts = (message.text or "").strip().split()
    try:
        bet = int(parts[1]) if len(parts) > 1 else 100
    except (ValueError, IndexError):
        bet = 100
    bet = max(getattr(config, "RULET_BET_MIN", 10), min(getattr(config, "RULET_BET_MAX", 10000), bet))

    balance = await db.get_balance(user_id)
    if balance < bet:
        sent = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=bet,
        command_source="/rulet", comment="Старт русской рулетки",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    caption = format_message_with_username(
        f"@{(username or first_name or 'user')} начал русскую рулетку на <b>{bet}</b> коинов.\n\nВступить — кнопка ниже.",
        username, first_name
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вступить", callback_data=f"rulet_join_{chat_id}"),
         InlineKeyboardButton(text="Отмена", callback_data=f"rulet_cancel_{chat_id}")]
    ])
    photo_path = config.get_image_path("rulet.jpg")
    try:
        if photo_path.exists():
            sent_msg = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent_msg = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.warning("rulet start photo: %s", e)
        sent_msg = await message.answer(caption, reply_markup=keyboard)

    _active_rulet_sessions[chat_id] = {
        "creator_id": user_id,
        "bet": bet,
        "participants": [user_id],
        "message_id": sent_msg.message_id,
        "chat_id": chat_id,
        "bank": bet,
        "task": None,
        "bot": message.bot,
    }


@router.callback_query(F.data.startswith("rulet_join_"))
async def cb_rulet_join(callback: CallbackQuery):
    """Вступление в русскую рулетку."""
    try:
        chat_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    sess = _active_rulet_sessions.get(chat_id)
    if not sess:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id in sess["participants"]:
        await callback.answer("Ты уже в игре.", show_alert=True)
        return
    max_p = getattr(config, "RULET_MAX_PLAYERS", 8)
    if len(sess["participants"]) >= max_p:
        await callback.answer("Мест нет.", show_alert=True)
        return
    bet = sess["bet"]
    balance = await db.get_balance(user_id)
    if balance < bet:
        await callback.answer("Недостаточно коинов.", show_alert=True)
        return
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=bet,
        command_source="/rulet", comment="Вступление в рулетку",
        bot=callback.bot, chat_id=chat_id,
        username=callback.from_user.username, first_name=callback.from_user.first_name,
        allow_negative=False
    )
    if not success:
        await callback.answer("Не удалось списать ставку.", show_alert=True)
        return
    sess["participants"].append(user_id)
    sess["bank"] += bet
    min_p = getattr(config, "RULET_MIN_PLAYERS", 2)
    if len(sess["participants"]) == min_p and sess.get("task") is None:
        sess["task"] = asyncio.create_task(_rulet_elimination_loop(chat_id))
    try:
        new_caption = f"Участников: <b>{len(sess['participants'])}</b>. Банк: <b>{sess['bank']}</b> коинов.\nВступить — кнопка ниже."
        await callback.bot.edit_message_caption(
            chat_id=chat_id, message_id=sess["message_id"],
            caption=new_caption, reply_markup=callback.message.reply_markup
        )
    except Exception:
        pass
    await callback.answer("Ты в игре!")


@router.callback_query(F.data.startswith("rulet_cancel_"))
async def cb_rulet_cancel(callback: CallbackQuery):
    """Отмена рулетки создателем: возврат всем, удаление сообщения."""
    try:
        chat_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    sess = _active_rulet_sessions.get(chat_id)
    if not sess:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    if callback.from_user.id != sess["creator_id"]:
        await callback.answer("Отменить может только создатель.", show_alert=True)
        return
    if sess.get("task"):
        await callback.answer("Игра уже идёт — отмена невозможна.", show_alert=True)
        return
    bot = sess["bot"]
    main_mid = sess["message_id"]
    for uid in sess["participants"]:
        try:
            await balance_service.add_game_win(
                user_id=uid, gross_amount=sess["bet"],
                command_source="/rulet", comment="Возврат: отмена рулетки",
                bot=bot, chat_id=chat_id, username=None, first_name=None,
            )
        except Exception as e:
            logger.warning("rulet cancel refund %s: %s", uid, e)
    del _active_rulet_sessions[chat_id]
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=main_mid, reply_markup=None)
        await bot.edit_message_caption(chat_id=chat_id, message_id=main_mid, caption="🔫 Рулетка отменена. Ставки возвращены.")
    except Exception:
        pass
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, main_mid, config.GAME_RESULT_DELETE_TIMEOUT))
    await callback.answer("Рулетка отменена, коины возвращены.")


# ---------- /frekaz (ставка 1000–100000, макс 5 игроков, через 2 мин победитель по шансам пропорционально ставкам) ----------
_active_frekaz_sessions: Dict[int, Dict] = {}  # chat_id -> {creator_id, bet, participants: [{user_id, bet}], message_id, bank, task, bot}


async def _frekaz_finish(chat_id: int):
    """Через 2 минуты — один победитель по весу ставок. Сообщения удаляются после результата."""
    sess = _active_frekaz_sessions.get(chat_id)
    if not sess or len(sess["participants"]) < 2:
        if sess:
            try:
                await sess["bot"].edit_message_reply_markup(chat_id=chat_id, message_id=sess["message_id"], reply_markup=None)
                asyncio.create_task(delete_message_after_by_id(sess["bot"], chat_id, sess["message_id"], config.GAME_RESULT_DELETE_TIMEOUT))
            except Exception:
                pass
            del _active_frekaz_sessions[chat_id]
        return
    main_mid = sess["message_id"]
    bot = sess["bot"]
    total_stake = sum(p["bet"] for p in sess["participants"])
    weights = [p["bet"] for p in sess["participants"]]
    winner_idx = game_random.choices(range(len(sess["participants"])), weights=weights, k=1)[0]
    winner = sess["participants"][winner_idx]
    winner_id = winner["user_id"]
    bank = sess["bank"]
    del _active_frekaz_sessions[chat_id]
    try:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=main_mid, reply_markup=None)
            await bot.edit_message_caption(chat_id=chat_id, message_id=main_mid, caption="🎲 Фреказ завершён. Победитель определён по ставкам.")
        except Exception:
            pass
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, main_mid, config.GAME_RESULT_DELETE_TIMEOUT))
        await balance_service.add_game_win(
            user_id=winner_id, gross_amount=bank,
            command_source="/frekaz", comment="Победа во фреказе",
            bot=bot, chat_id=chat_id, username=None, first_name=None,
        )
        user = await db.get_user(winner_id)
        un = (user.get("username") or "user") if user else "user"
        win_caption = format_message_with_username(
            f"🎉 Ставка сработала — забираешь банк <b>{bank}</b> коинов. Остальным — в следующий раз.", un, None
        )
        photo_path = config.get_image_path("frekaz_win.jpg")
        if photo_path.exists():
            win_msg = await bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=win_caption)
        else:
            win_msg = await bot.send_message(chat_id, win_caption)
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, win_msg.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
    except Exception as e:
        logger.exception("frekaz finish: %s", e)


@router.message(Command("frekaz"))
async def cmd_frekaz(message: Message):
    """Фреказ: /frekaz сумма. Ставка 1000–100000, макс 5 игроков. Через 2 мин победитель определяется по шансам (пропорционально ставкам), забирает весь банк."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    chat_id = message.chat.id

    if chat_id in _active_frekaz_sessions:
        sent = await message.answer(format_message_with_username(
            "В этом чате уже идёт фреказ. Вступи или дождись окончания.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    parts = (message.text or "").strip().split()
    try:
        bet = int(parts[1]) if len(parts) > 1 else config.FREKAZ_BET_MIN
    except (ValueError, IndexError):
        bet = config.FREKAZ_BET_MIN
    bet = max(config.FREKAZ_BET_MIN, min(config.FREKAZ_BET_MAX, bet))

    balance = await db.get_balance(user_id)
    if balance < bet:
        sent = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=bet,
        command_source="/frekaz", comment="Старт фреказа",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    caption = format_message_with_username(
        f"@{(username or first_name or 'user')} начал фреказ на <b>{bet}</b> коинов.\n\nВступить — кнопка ниже. Через 2 минуты победитель забирает банк.",
        username, first_name
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вступить", callback_data=f"frekaz_join_{chat_id}"),
         InlineKeyboardButton(text="Отмена", callback_data=f"frekaz_cancel_{chat_id}")]
    ])
    photo_path = config.get_image_path("frekaz.jpg")
    try:
        if photo_path.exists():
            sent_msg = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent_msg = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        sent_msg = await message.answer(caption, reply_markup=keyboard)

    _active_frekaz_sessions[chat_id] = {
        "creator_id": user_id,
        "bet": bet,
        "participants": [{"user_id": user_id, "bet": bet}],
        "message_id": sent_msg.message_id,
        "bank": bet,
        "bot": message.bot,
    }
    task = asyncio.create_task(_frekaz_delayed_finish(chat_id))
    _active_frekaz_sessions[chat_id]["task"] = task


async def _frekaz_delayed_finish(chat_id: int):
    await asyncio.sleep(getattr(config, "FREKAZ_DURATION", 120))
    await _frekaz_finish(chat_id)


@router.callback_query(F.data.startswith("frekaz_join_"))
async def cb_frekaz_join(callback: CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    sess = _active_frekaz_sessions.get(chat_id)
    if not sess:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    user_id = callback.from_user.id
    if any(p["user_id"] == user_id for p in sess["participants"]):
        await callback.answer("Ты уже в игре.", show_alert=True)
        return
    max_p = getattr(config, "FREKAZ_MAX_PLAYERS", 5)
    if len(sess["participants"]) >= max_p:
        await callback.answer("Мест нет.", show_alert=True)
        return
    bet = sess["bet"]
    balance = await db.get_balance(user_id)
    if balance < bet:
        await callback.answer("Недостаточно коинов.", show_alert=True)
        return
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=bet,
        command_source="/frekaz", comment="Вступление во фреказ",
        bot=callback.bot, chat_id=chat_id,
        username=callback.from_user.username, first_name=callback.from_user.first_name,
        allow_negative=False
    )
    if not success:
        await callback.answer("Не удалось списать ставку.", show_alert=True)
        return
    sess["participants"].append({"user_id": user_id, "bet": bet})
    sess["bank"] += bet
    try:
        new_caption = f"Участников: <b>{len(sess['participants'])}</b>. Банк: <b>{sess['bank']}</b> коинов. Через 2 мин — победитель."
        await callback.bot.edit_message_caption(
            chat_id=chat_id, message_id=sess["message_id"],
            caption=new_caption, reply_markup=callback.message.reply_markup
        )
    except Exception:
        pass
    await callback.answer("Ты в игре!")


@router.callback_query(F.data.startswith("frekaz_cancel_"))
async def cb_frekaz_cancel(callback: CallbackQuery):
    """Отмена фреказа создателем: возврат всем, удаление сообщения."""
    try:
        chat_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    sess = _active_frekaz_sessions.get(chat_id)
    if not sess:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    if callback.from_user.id != sess["creator_id"]:
        await callback.answer("Отменить может только создатель.", show_alert=True)
        return
    bot = sess["bot"]
    main_mid = sess["message_id"]
    for p in sess["participants"]:
        try:
            await balance_service.add_game_win(
                user_id=p["user_id"], gross_amount=p["bet"],
                command_source="/frekaz", comment="Возврат: отмена фреказа",
                bot=bot, chat_id=chat_id, username=None, first_name=None,
            )
        except Exception as e:
            logger.warning("frekaz cancel refund %s: %s", p["user_id"], e)
    if sess.get("task"):
        sess["task"].cancel()
    del _active_frekaz_sessions[chat_id]
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=main_mid, reply_markup=None)
        await bot.edit_message_caption(chat_id=chat_id, message_id=main_mid, caption="🎲 Фреказ отменён. Ставки возвращены.")
    except Exception:
        pass
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, main_mid, config.GAME_RESULT_DELETE_TIMEOUT))
    await callback.answer("Фреказ отменён, коины возвращены.")


# ---------- /perekyp (Перекуп: объявления, торг, перепродажа) ----------
_active_perekyp_sessions: Dict[int, Dict] = {}  # user_id -> {chat_id, message_id, listing, scroll_count, torg_failed}

# Спецпродавцы: редкие, фиксированное описание, 70% шанс окупа (не 100%)
PEREKYP_SPECIAL_DIRECTRISA = {
    "seller": "Жирная Директриса",
    "description": "Биг маки на развес",
    "short_desc": "Самые лучшие биг маки в мире.",
    "rating": 8,
    "reviews": 88,
    "special_win_chance": 0.7,
}
PEREKYP_SPECIAL_KAZAK = {
    "seller": "Казак",
    "description": "Обед по-казачьи",
    "short_desc": "Клянусь, там нет соплей.",
    "rating": 4,
    "reviews": 88,
    "special_win_chance": 0.7,
}
PEREKYP_SPECIAL_CHANCE = 0.02  # шанс выпасть каждому редкому продавцу (Жирная Директриса / Казак — редко)

# Товары для перекупа: техника Apple, ноутбуки, смартфоны, периферия, гаджеты
PEREKYP_ITEMS = [
    # Apple
    "iPhone 15 Pro Max 256GB",
    "iPhone 14 128GB",
    "iPhone 13 mini",
    "iPhone 12, б/у",
    "MacBook Pro 14 M3",
    "MacBook Air M2",
    "MacBook Pro 16 M1 Pro",
    "iMac 24\" M1",
    "iPad Pro 12.9 M2",
    "iPad Air 5",
    "Apple Watch Ultra 2",
    "Apple Watch SE",
    "AirPods Pro 2",
    "AirPods Max",
    "Mac mini M2",
    "Mac Studio M2 Max",
    # Смартфоны
    "Смартфон Samsung Galaxy S24",
    "Samsung Galaxy Z Flip 5",
    "Xiaomi 14 Pro",
    "Google Pixel 8 Pro",
    "OnePlus 12",
    "Смартфон в отличном состоянии",
    "Телефон с треснутым стеклом, работает",
    # Ноутбуки и ПК
    "Ноутбук ASUS ROG",
    "Ноутбук Lenovo ThinkPad X1",
    "Ноутбук HP Pavilion",
    "Ноутбук, б/у 2 года",
    "Игровой ноутбук MSI",
    "MacBook Pro 2019 Intel",
    "Мини-ПК Intel NUC",
    "Системный блок RTX 4070",
    # Мониторы и графика
    "Монитор 27 дюймов 144 Гц",
    "Монитор 32 4K",
    "Видеокарта RTX 4080",
    "Видеокарта после майнинга",
    "Видеокарта RX 7900 XTX",
    # Аудио и стрим
    "Наушники Sony WH-1000XM5",
    "Наушники премиум",
    "Микрофон Shure SM7B",
    "Микрофон студийный",
    "Веб-камера Logitech 4K",
    "Веб-камера 4K",
    "Звуковая карта Focusrite",
    # Периферия
    "Клавиатура Keychron механическая",
    "Клавиатура механическая",
    "Мышь Logitech G Pro X",
    "Коврик для мыши XL",
    "Геймпад DualSense",
    "Геймпад с вибрацией",
    "Колесо руля Thrustmaster",
    "Колесо руля для симуляторов",
    "VR-шлем Meta Quest 3",
    "VR-шлем Valve Index",
    # Мебель и быт
    "Кресло Herman Miller",
    "Кресло офисное",
    "Стол игровой с подсветкой",
    "Стол игровой",
    "Стойка для мониторов",
    "Роутер ASUS Wi‑Fi 6",
    "Роутер Wi‑Fi 6",
    "Powerbank 20000 mAh",
    "Зарядка MagSafe",
    "Док-станция USB-C",
    # Гаджеты
    "Умные часы",
    "Фитнес-браслет",
    "Электросамокат Xiaomi",
    "Электросамокат",
    "Планшет Samsung Tab S9",
    "Электронная книга Kindle",
    "Портативная колонка JBL",
    "Гриль электрический",
    "Кофемашина капсульная",
    "Робот-пылесос",
    "Умная колонка с Алисой",
    # Дополнительные товары
    "iPhone 15 128GB",
    "iPhone SE 3",
    "MacBook Pro 13 M2",
    "iPad 10",
    "Apple Watch Series 9",
    "AirPods 3",
    "Samsung Galaxy A54",
    "Samsung Galaxy Tab S8",
    "Xiaomi 13",
    "Poco F5",
    "Realme GT 3",
    "Nothing Phone 2",
    "Ноутбук Acer Nitro",
    "Ноутбук Dell XPS 15",
    "Ноутбук MSI Katana",
    "Игровой ПК Ryzen 7 + RTX 4060",
    "Монитор 24 дюйма IPS",
    "Видеокарта RTX 3060",
    "Видеокарта RX 6600",
    "Клавиатура Razer BlackWidow",
    "Мышь Razer DeathAdder",
    "Наушники SteelSeries Arctis",
    "Колонки Edifier",
    "Внешний SSD 1 ТБ",
    "Флешка 128 ГБ",
    "Умная лампа",
    "Умный термостат",
    "Электронная книга PocketBook",
    "Планшет Lenovo Tab",
    "Смарт-часы Amazfit",
    "Квадрокоптер DJI Mini",
    "Фотоаппарат зеркальный б/у",
    "Объектив 50 мм",
    "Микрофон Blue Yeti",
    "Светодиодная лента",
    "ИБП для ПК",
    "Кресло DXRacer",
    "Подставка для ноутбука",
    "Коврик для мыши с подсветкой",
    "USB-хаб 4 порта",
    "Держатель для смартфона в авто",
    "Чехол для MacBook",
    "Защитное стекло на телефон",
]

PEREKYP_SELLER_NAMES = [
    "Алексей_92", "Дмитрий_Продам", "Сергей_Торг", "Андрей_М", "Максим_Тчк",
    "Иван_Иванов", "Никита_Н", "Артём_Авито", "Михаил_Мск", "Павел_П",
    "Евгений_Екб", "Олег_Омск", "Роман_Рф", "Владимир_Вл", "Станислав_Спб",
    "Кирилл_К", "Тимофей_Т", "Глеб_Г", "Даниил_Д", "Марк_М",
    "Продам_Честно", "Торг_Уместен", "Отдам_Дёшево", "Мск_Доставка", "Спб_Самовывоз",
    "Техно_Перекуп", "Эпл_Бу", "Ноут_Сервис", "Гаджеты_Рф", "Авито_Топ",
    "Юрий_Ю", "Виктор_Вит", "Константин_Кост", "Денис_Ден", "Игорь_Иг",
    "Федор_Ф", "Вадим_Вадим", "Леонид_Лео", "Борис_Б", "Григорий_Гри",
    "Антон_Ан", "Семён_Сем", "Валерий_Вал", "Эдуард_Эд", "Ярослав_Яр",
    "Марина_М", "Ольга_Ол", "Елена_Ел", "Наталья_Нат", "Татьяна_Таня",
    "Александра_Саша", "Дарья_Дарья", "Полина_Пол", "София_Соф", "Виктория_Вика",
    "ТехноМир_Мск", "Гаджет_Спб", "Бу_Техника", "Честный_Продавец", "Без_Обмана",
    "Доставка_День_В_День", "Самовывоз_Круглосуточно", "Гарантия_Год", "Торг_При_Встрече",
    "Продаю_Срочно", "Цена_Ок", "Состояние_Идеал", "Проверка_При_Покупке", "Авито_Верифид",
]

PEREKYP_SHORT_DESCRIPTIONS = [
    "Почти новый, всё в комплекте.",
    "Б/у, есть мелкие царапины.",
    "Работает стабильно, отдам с гарантией.",
    "Срочно, торг уместен.",
    "После апгрейда, лишнее продаю.",
    "Редко пользовался, как новый.",
    "Честное описание, обман не прошу.",
    "Коробка есть, документы сохранены.",
    "Без сколов и потертостей.",
    "Проверял лично, всё ок.",
    "Отдам в день обращения.",
    "Цена с доставкой по городу.",
    "Самовывоз, могу отправить.",
    "Работает без нареканий.",
    "Комплект полный, зарядка в наличии.",
    "Есть чеки, гарантия истекла.",
    "Состояние на фото — не вру.",
    "Торг при встрече.",
    "Пыли нет, чистил недавно.",
    "Батарея держит как новую.",
    "Экран без битых пикселей.",
    "Продаю из-за перехода на другую модель.",
    "Цена ниже рыночной, торг уместен.",
    "Отвечаю быстро, могу отправить в другой город.",
    "Пользовался аккуратно, коробка и документы есть.",
    "Не бит, не крашен, всё родное.",
    "Работает как часы, претензий не будет.",
    "Отдам с гарантией 2 недели.",
    "Можно проверить при встрече.",
    "Цена фикс, без торга.",
    "Срочный переезд, отдам дёшево.",
    "Подарок не подошёл, продаю.",
    "Дубликат, один оставил себе.",
    "Снял с производства, раритет.",
    "Состояние 9 из 10.",
    "Есть мелкие потертости на корпусе.",
    "Батарея заменена на новую.",
    "Заряда хватает на 2 дня.",
    "Всё по честному, обман не в моих правилах.",
    "Могу прислать дополнительные фото.",
    "Отправлю в день оплаты.",
    "Самовывоз предпочтительнее.",
    "Живу рядом с метро.",
    "Работаю с 9 до 21, пишите.",
]


def _perekyp_generate_listing(base_sum: int) -> Dict:
    """Генерирует объявление: товар, цена, продавец, рейтинг, отзывы, короткое описание. Редко — спецпродавцы (всегда окуп)."""
    price_min = getattr(config, "PEREKYP_PRICE_MIN", 0.85)
    price_max = getattr(config, "PEREKYP_PRICE_MAX", 1.15)
    price = max(1, int(base_sum * game_random.uniform(price_min, price_max)))
    r = game_random.random()
    if r < PEREKYP_SPECIAL_CHANCE:
        s = PEREKYP_SPECIAL_DIRECTRISA
        return {
            "description": s["description"],
            "price": price,
            "seller": s["seller"],
            "rating": s["rating"],
            "reviews": s["reviews"],
            "short_desc": s["short_desc"],
            "special_win_chance": s.get("special_win_chance", 0.7),
        }
    if r < 2 * PEREKYP_SPECIAL_CHANCE:
        s = PEREKYP_SPECIAL_KAZAK
        return {
            "description": s["description"],
            "price": price,
            "seller": s["seller"],
            "rating": s["rating"],
            "reviews": s["reviews"],
            "short_desc": s["short_desc"],
            "special_win_chance": s.get("special_win_chance", 0.7),
        }
    item = game_random.choice(PEREKYP_ITEMS)
    seller = game_random.choice(PEREKYP_SELLER_NAMES)
    rating = game_random.randint(1, 5)
    reviews = game_random.randint(5, 400) if rating >= 4 else game_random.randint(1, 150)
    short_desc = game_random.choice(PEREKYP_SHORT_DESCRIPTIONS)
    return {
        "description": item,
        "price": price,
        "seller": seller,
        "rating": rating,
        "reviews": reviews,
        "short_desc": short_desc,
    }


def _perekyp_listing_caption(listing: Dict, username: str, first_name: str) -> str:
    """Форматирует карточку объявления: товар, продавец, рейтинг, отзывы, описание, цена."""
    r = listing["rating"]
    stars = ("⭐" * r) if r > 5 else ("⭐" * r + "☆" * (5 - r))
    lines = [
        "🛒 <b>Объявление</b>",
        "",
        f"📦 {listing['description']}",
        f"💰 Цена: <b>{listing['price']}</b> коинов",
        "",
        f"👤 Продавец: {listing['seller']}",
        f"{stars} {listing['rating']}/5 · отзывов: {listing['reviews']}",
        "",
        f"📝 {listing['short_desc']}",
    ]
    return format_message_with_username("\n".join(lines), username, first_name)


def _perekyp_keyboard(user_id: int, torg_failed: bool = False) -> InlineKeyboardMarkup:
    if torg_failed:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Купить", callback_data=f"perekyp_buy|{user_id}")],
            [InlineKeyboardButton(text="Выйти", callback_data=f"perekyp_exit|{user_id}")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Выйти", callback_data=f"perekyp_exit|{user_id}"),
            InlineKeyboardButton(text="Пролистать", callback_data=f"perekyp_scroll|{user_id}"),
        ],
        [
            InlineKeyboardButton(text="Купить", callback_data=f"perekyp_buy|{user_id}"),
            InlineKeyboardButton(text="Торг", callback_data=f"perekyp_torg|{user_id}"),
        ],
    ])


async def _perekyp_do_buy(
    bot: Bot, user_id: int, chat_id: int, message_id: int,
    price: int, was_torg: bool, username: str, first_name: str,
    listing: Optional[Dict] = None,
) -> None:
    """Списать цену; шанс выигрыша зависит от рейтинга продавца. Успех — перепродажа (x1.5–x5), иначе потеря. Сообщение удаляется."""
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=price,
        command_source="/perekyp", comment="Покупка по объявлению",
        bot=bot, chat_id=chat_id, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        try:
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption="Недостаточно средств.", reply_markup=None)
        except Exception:
            pass
        return
    base_chance = getattr(config, "PEREKYP_BUY_WIN_CHANCE", 0.38)
    rating = (listing or {}).get("rating", 3)
    special_chance = (listing or {}).get("special_win_chance")
    if special_chance is not None:
        win_chance = special_chance
    else:
        win_chance = min(0.85, base_chance * (0.6 + 0.1 * rating))
    won = game_random.random() < win_chance
    if won:
        mult_min = getattr(config, "PEREKYP_WIN_MULT_MIN", 1.3)
        mult_max = getattr(config, "PEREKYP_WIN_MULT_MAX", 3.2)
        mult = round(game_random.uniform(mult_min, mult_max), 2)
        mult = _apply_bet_penalty(price, mult)
        win_amount = int(price * mult)
        _, _, _, tax = await balance_service.add_game_win(
            user_id=user_id, gross_amount=win_amount,
            command_source="/perekyp", comment="Перепродажа",
            bot=bot, chat_id=chat_id, username=username, first_name=first_name,
        )
        await db.log_game_session(user_id, "perekyp", price, "win", win_amount - price, mult)
        await db.log_admin_game(user_id, username, "/perekyp", price, "win", win_amount - price, tax or 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "perekyp", "win", balance_after, chat_id=chat_id, bot=bot)
        caption = format_message_with_username(
            f"✅ Дружок, риск был оправдан — перепродал и в плюсе <b>+{win_amount}</b> коинов (x{mult:.2f}). Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        photo_path = config.get_image_path("perekupwin.jpg")
        logger.info("perekyp: user_id=%s price=%s rating=%s was_torg=%s mult=%s win=%s", user_id, price, rating, was_torg, mult, win_amount)
    else:
        await db.log_game_session(user_id, "perekyp", price, "loss", -price, 0)
        await db.log_admin_game(user_id, username, "/perekyp", price, "loss", -price, 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "perekyp", "loss", balance_after)
        caption = format_message_with_username(
            f"❌ Сегодня рынок против тебя — не удалось перепродать. Минус <b>{price}</b> коинов. Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        photo_path = config.get_image_path("perekuplose.jpg")
        logger.info("perekyp: user_id=%s price=%s rating=%s was_torg=%s result=loss", user_id, price, rating, was_torg)
    result_msg_id = message_id
    try:
        if photo_path.exists():
            media = InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
            await bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media, reply_markup=None)
        else:
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption, reply_markup=None)
    except Exception as e:
        logger.warning("perekyp edit result: %s", e)
        try:
            if photo_path.exists():
                sent_perekyp = await bot.send_photo(chat_id, FSInputFile(str(photo_path)), caption=caption)
            else:
                sent_perekyp = await bot.send_message(chat_id, caption)
            result_msg_id = sent_perekyp.message_id
        except Exception:
            sent_perekyp = await bot.send_message(chat_id, caption)
            result_msg_id = sent_perekyp.message_id if sent_perekyp else message_id
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, result_msg_id, config.GAME_RESULT_DELETE_TIMEOUT))


@router.message(Command("perekyp"))
async def cmd_perekyp(message: Message):
    """Перекуп: /perekyp сумма. Объявление с ценой около суммы. Выйти / Пролистать / Купить / Торг."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    if user_id in _active_perekyp_sessions:
        sent = await message.answer(format_message_with_username(
            "Заверши текущий перекуп (Выйти) или выбери объявление.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    parts = (message.text or "").strip().split()
    try:
        base_sum = int(parts[1]) if len(parts) > 1 else 1000
    except (ValueError, IndexError):
        base_sum = 1000
    bet_min = getattr(config, "PEREKYP_BET_MIN", 100)
    bet_max = getattr(config, "PEREKYP_BET_MAX", 100000)
    base_sum = max(bet_min, min(bet_max, base_sum))

    balance = await db.get_balance(user_id)
    listing = _perekyp_generate_listing(base_sum)
    if balance < listing["price"]:
        sent = await message.answer(format_message_with_username(
            f"Для этого объявления нужно минимум <b>{listing['price']}</b> коинов. Баланс: {balance}.",
            username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    caption = _perekyp_listing_caption(listing, username or "", first_name or "")
    keyboard = _perekyp_keyboard(user_id, torg_failed=False)
    photo_path = config.get_image_path("perekup.jpg")
    try:
        if photo_path.exists():
            sent_msg = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent_msg = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.warning("perekyp start photo: %s", e)
        sent_msg = await message.answer(caption, reply_markup=keyboard)

    _active_perekyp_sessions[user_id] = {
        "chat_id": message.chat.id,
        "message_id": sent_msg.message_id,
        "listing": listing,
        "base_sum": base_sum,
        "scroll_count": 0,
        "torg_failed": False,
        "bot": message.bot,
    }
    await set_command_cooldown(user_id, "/perekyp")


def _parse_perekyp_cb(data: str, prefix: str):
    """perekyp_exit|user_id -> (action, user_id). После префикса идёт только user_id (без второго |)."""
    if not data.startswith(prefix):
        return None, None
    try:
        rest = data[len(prefix):].strip()
        if not rest:
            return None, None
        uid = int(rest)
        action = prefix.replace("perekyp_", "").rstrip("|")
        return action, uid
    except (ValueError, IndexError):
        return None, None


@router.callback_query(F.data.startswith("perekyp_exit|"))
async def cb_perekyp_exit(callback: CallbackQuery):
    _, target_id = _parse_perekyp_cb(callback.data, "perekyp_exit|")
    if target_id is None or callback.from_user.id != target_id:
        await _safe_callback_answer(callback, "Не жми на чужое!" if (target_id and callback.from_user.id != target_id) else "Ошибка", show_alert=True)
        return
    sess = _active_perekyp_sessions.pop(target_id, None)
    if not sess:
        await _safe_callback_answer(callback, "Игра уже завершена.", show_alert=True)
        return
    await _safe_callback_answer(callback, "")
    try:
        await callback.bot.edit_message_caption(
            chat_id=sess["chat_id"], message_id=sess["message_id"],
            caption="Вышел из перекупа — деньги не списаны.", reply_markup=None
        )
    except Exception:
        pass
    asyncio.create_task(delete_message_after_by_id(callback.bot, sess["chat_id"], sess["message_id"], config.GAME_RESULT_DELETE_TIMEOUT))


@router.callback_query(F.data.startswith("perekyp_scroll|"))
async def cb_perekyp_scroll(callback: CallbackQuery):
    _, target_id = _parse_perekyp_cb(callback.data, "perekyp_scroll|")
    if target_id is None or callback.from_user.id != target_id:
        await _safe_callback_answer(callback, "Не жми на чужое!" if target_id else "Ошибка", show_alert=True)
        return
    sess = _active_perekyp_sessions.get(target_id)
    if not sess:
        await _safe_callback_answer(callback, "Игра уже завершена.", show_alert=True)
        return
    scroll_max = getattr(config, "PEREKYP_SCROLL_MAX", 15)
    if sess["scroll_count"] >= scroll_max:
        await _safe_callback_answer(callback, "Лимит пролистываний.", show_alert=True)
        return
    await _safe_callback_answer(callback, "")
    base_sum = sess.get("base_sum", 1000)
    balance = await db.get_balance(target_id)
    for _ in range(5):
        sess["listing"] = _perekyp_generate_listing(base_sum)
        if sess["listing"]["price"] <= balance:
            break
    sess["scroll_count"] += 1
    sess["torg_failed"] = False
    listing = sess["listing"]
    caption = _perekyp_listing_caption(listing, callback.from_user.username or "", callback.from_user.first_name or "")
    try:
        await callback.bot.edit_message_caption(
            chat_id=sess["chat_id"], message_id=sess["message_id"],
            caption=caption, reply_markup=_perekyp_keyboard(target_id, False)
        )
    except Exception as e:
        logger.warning("perekyp scroll edit: %s", e)


@router.callback_query(F.data.startswith("perekyp_buy|"))
async def cb_perekyp_buy(callback: CallbackQuery):
    _, target_id = _parse_perekyp_cb(callback.data, "perekyp_buy|")
    if target_id is None or callback.from_user.id != target_id:
        await _safe_callback_answer(callback, "Не жми на чужое!" if target_id else "Ошибка", show_alert=True)
        return
    sess = _active_perekyp_sessions.pop(target_id, None)
    if not sess:
        await _safe_callback_answer(callback, "Игра уже завершена.", show_alert=True)
        return
    price = sess["listing"]["price"]
    balance = await db.get_balance(target_id)
    if balance < price:
        _active_perekyp_sessions[target_id] = sess
        await _safe_callback_answer(callback, "Недостаточно коинов.", show_alert=True)
        return
    await _safe_callback_answer(callback, "Покупаем…")
    await _perekyp_do_buy(
        callback.bot, target_id, sess["chat_id"], sess["message_id"],
        price, was_torg=False,
        username=callback.from_user.username or "", first_name=callback.from_user.first_name or "",
        listing=sess.get("listing"),
    )


@router.callback_query(F.data.startswith("perekyp_torg|"))
async def cb_perekyp_torg(callback: CallbackQuery):
    _, target_id = _parse_perekyp_cb(callback.data, "perekyp_torg|")
    if target_id is None or callback.from_user.id != target_id:
        await _safe_callback_answer(callback, "Не жми на чужое!" if target_id else "Ошибка", show_alert=True)
        return
    sess = _active_perekyp_sessions.get(target_id)
    if not sess:
        await _safe_callback_answer(callback, "Игра уже завершена.", show_alert=True)
        return
    torg_chance = getattr(config, "PEREKYP_TORG_WIN_CHANCE", 0.78)
    torg_ok = game_random.random() < torg_chance
    discount = getattr(config, "PEREKYP_TORG_DISCOUNT", 0.85)
    chat_id = sess["chat_id"]
    message_id = sess["message_id"]
    bot = sess["bot"]
    username = callback.from_user.username or ""
    first_name = callback.from_user.first_name or ""

    if torg_ok:
        old_price = sess["listing"]["price"]
        new_price = max(1, int(old_price * discount))
        sess["listing"]["price"] = new_price
        torg_msg_id = None
        try:
            torg_photo = config.get_image_path("perekuptorg.jpg")
            if torg_photo.exists():
                torg_msg = await bot.send_photo(chat_id, FSInputFile(str(torg_photo)),
                    caption=format_message_with_username(f"🤝 Торг удался! Новая цена: <b>{new_price}</b> коинов. Покупаем…", username, first_name))
            else:
                torg_msg = await bot.send_message(chat_id, format_message_with_username(f"🤝 Торг удался! Новая цена: <b>{new_price}</b> коинов.", username, first_name))
            torg_msg_id = torg_msg.message_id
        except Exception:
            pass
        if torg_msg_id is not None:
            asyncio.create_task(delete_message_after_by_id(bot, chat_id, torg_msg_id, config.GAME_RESULT_DELETE_TIMEOUT))
        listing = sess.get("listing")
        _active_perekyp_sessions.pop(target_id, None)
        await _safe_callback_answer(callback, "Торг удался! Покупаем…")
        await _perekyp_do_buy(bot, target_id, chat_id, message_id, new_price, was_torg=True, username=username, first_name=first_name, listing=listing)
        return

    sess["torg_failed"] = True
    torg_msg_id = None
    try:
        torg_photo = config.get_image_path("perekuptorg.jpg")
        if torg_photo.exists():
            torg_msg = await bot.send_photo(chat_id, FSInputFile(str(torg_photo)),
                caption=format_message_with_username("😤 Продавец не сдался. Цена без изменений — купи или выйди.", username, first_name))
        else:
            torg_msg = await bot.send_message(chat_id, format_message_with_username("😤 Торг не удался. Цена без изменений. Купи или выйди.", username, first_name))
        torg_msg_id = torg_msg.message_id
    except Exception:
        pass
    if torg_msg_id is not None:
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, torg_msg_id, config.GAME_RESULT_DELETE_TIMEOUT))
    caption = _perekyp_listing_caption(sess["listing"], username, first_name)
    caption_extra = caption + "\n\n⚠️ Торг не удался — купи или выйди."
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption_extra, reply_markup=_perekyp_keyboard(target_id, True))
    except Exception as e:
        logger.warning("perekyp torg fail edit: %s", e)
    await _safe_callback_answer(callback, "Торг не удался.")


@router.message(Command("slot"))
async def cmd_slot(message: Message):
    """
    Команда /slot
    Ставка: 20 коинов
    Выигрыш: 150 коинов
    Базовый шанс: 5%
    При выигрыше показывается 5.jpg
    Применяются бонусы Premium и зелий удачи
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    if await news_service.is_game_closed("slot"):
        sent = await message.answer(format_message_with_username(
            "Слоты временно на починке — загляни в /news.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    bet = config.SLOT_BET
    win_amount = config.SLOT_WIN
    base_chance = config.SLOT_WIN_CHANCE

    free_spins = await db.get_free_spins(user_id)
    use_free = free_spins > 0
    if use_free:
        await db.use_free_spin(user_id)
        bet_charged = 0
    else:
        bet_charged = bet

    balance = await db.get_balance(user_id)
    use_free_daily = False
    if balance < bet_charged:
        if balance == 0 and not await db.get_free_game_used_today(user_id):
            bet_charged = 0
            use_free_daily = True
        else:
            sent_message = await message.answer(format_insufficient_balance(username, first_name))
            asyncio.create_task(delete_message_after(sent_message))
            return

    if bet_charged > 0:
        success, balance_before, balance_after, error = await balance_service.subtract_balance(
            user_id=user_id,
            amount=bet_charged,
            command_source="/slot",
            comment="Ставка в слотах",
            message=message,
            username=username,
            first_name=first_name,
            allow_negative=False
        )
        if not success:
            return
        balance_after_slot = balance_after
    else:
        balance_after_slot = balance

    try:
        final_chance = await calculate_win_chance_async(base_chance, user_id, "slot")
        roll = game_random.random()
        is_win = roll < final_chance

        await set_command_cooldown(user_id, "/slot")
        
        # Определяем результат
        if is_win:
            # Выигрыш - показываем 5.jpg (согласно README: "5.jpg — шанс 5% это и есть выигрыш")
            photo_path = config.get_image_path("5.jpg")
            win_to_add = min(win_amount, getattr(config, "FREE_GAME_WIN_CAP", 100)) if use_free_daily else win_amount
            slot_day = await db.get_global_event("slot_day")
            if slot_day:
                win_to_add = int(win_to_add * 1.1)
            _, _, _, tax = await balance_service.add_game_win(
                user_id=user_id,
                gross_amount=win_to_add,
                command_source="/slot",
                comment="Выигрыш в слотах",
                message=message,
                username=username,
                first_name=first_name,
            )
            await db.log_admin_game(user_id, username, "/slot", bet, "win", win_to_add - (bet_charged or 0), tax or 0)
            if use_free_daily:
                await db.set_free_game_used_today(user_id)
            
            await db.log_game_session(
                user_id=user_id,
                game_type="slot",
                bet=bet,
                result="win",
                amount_change=win_amount - bet,
                multiplier=1.0
            )
            balance_final = await db.get_balance(user_id)
            await _update_mmr_and_achievements(user_id, "slot", "win", balance_final)
            await db.add_cup_win(user_id, "slot")
            caption = format_message_with_username(
                f"🎰 <b>ВЫИГРЫШ!</b>\n\n"
                f"Выиграл: <b>{win_to_add}</b> коинов 💰\n"
                f"Твой баланс: <b>{balance_final}</b> коинов"
                + (" (фриспин)" if use_free else "")
                + (" (бесплатная игра)" if use_free_daily else ""),
                username, first_name
            )
            if photo_path.exists():
                photo = FSInputFile(str(photo_path))
                sent_message = await message.answer_photo(photo=photo, caption=caption)
            else:
                sent_message = await message.answer(caption)
                logger.warning(f"Фото 5.jpg не найдено для пользователя {user_id}")
            asyncio.create_task(delete_message_after(sent_message, config.GAME_RESULT_DELETE_TIMEOUT))
            logger.info(
                f"Пользователь {user_id} сыграл в /slot: "
                f"bet={bet}, win={is_win}, chance={final_chance:.4f} (base={base_chance:.4f})"
            )
        else:
            photo_num = game_random.randint(1, 4)
            photo_path = config.get_image_path(f"{photo_num}.jpg")
            caption = format_message_with_username(
                f"🎰 <b>ПРОИГРЫШ</b>\n\n"
                f"Ставка: {bet} коинов\n"
                f"Твой баланс: <b>{balance_after_slot}</b> коинов"
                + (" (фриспин)" if use_free else ""),
                username, first_name
            )
            
            await db.log_game_session(
                user_id=user_id,
                game_type="slot",
                bet=bet,
                result="loss",
                amount_change=-bet,
                multiplier=1.0
            )
            await db.log_admin_game(user_id, username, "/slot", bet, "loss", -bet, 0)
            await _update_mmr_and_achievements(user_id, "slot", "loss", balance_after_slot)
        
            # Отправляем фото
            if photo_path.exists():
                photo = FSInputFile(str(photo_path))
                sent_message = await message.answer_photo(photo=photo, caption=caption)
            else:
                sent_message = await message.answer(caption)
                logger.warning(f"Фото {photo_path.name} не найдено для пользователя {user_id}")
            asyncio.create_task(delete_message_after(sent_message, config.GAME_RESULT_DELETE_TIMEOUT))
            logger.info(
                f"Пользователь {user_id} сыграл в /slot: "
                f"bet={bet}, win={is_win}, chance={final_chance:.4f} (base={base_chance:.4f})"
            )
    except Exception as e:
        logger.exception("Ошибка в /slot для %s: %s", user_id, e)
        if bet_charged > 0:
            await balance_service.add_balance(user_id=user_id, amount=bet_charged, command_source="/slot", comment="Возврат при сбое", message=message, username=username, first_name=first_name)
        await message.answer(format_game_error(username, first_name))


@router.message(Command("konopla"))
async def cmd_konopla(message: Message):
    """
    Команда /konopla
    Ставка: 30 коинов
    Проигрыш: -70 коинов (93%)
    Выигрыш: +250 коинов (7%)
    Применяются бонусы Premium и зелий удачи
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    if await news_service.is_game_closed("konopla"):
        sent = await message.answer(format_message_with_username(
            "Канапля временно на починке — загляни в /news.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    bet = config.KONOPLA_BET
    loss_amount = config.KONOPLA_LOSS
    win_amount = config.KONOPLA_WIN
    base_chance = config.KONOPLA_WIN_CHANCE

    balance = await db.get_balance(user_id)
    if balance < bet:
        sent_message = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent_message))
        return

    bet_subtracted = False
    try:
        final_chance = await calculate_win_chance_async(base_chance, user_id, "konopla")
        roll = game_random.random()
        is_win = roll < final_chance

        success, balance_before, balance_after, error = await balance_service.subtract_balance(
            user_id=user_id,
            amount=bet,
            command_source="/konopla",
            comment="Ставка в конопле",
            message=message,
            username=username,
            first_name=first_name,
            allow_negative=False
        )
        if not success:
            return
        bet_subtracted = True

        await set_command_cooldown(user_id, "/konopla")

        if is_win:
            photo_path = config.get_image_path("konwin.jpg")
            _, _, _, tax = await balance_service.add_game_win(
                user_id=user_id,
                gross_amount=win_amount,
                command_source="/konopla",
                comment="Выигрыш в конопле",
                message=message,
                username=username,
                first_name=first_name,
            )
            await db.log_admin_game(user_id, username, "/konopla", bet, "win", win_amount - bet, tax or 0)
            balance_final = await db.get_balance(user_id)
            await db.log_game_session(
                user_id=user_id,
                game_type="konopla",
                bet=bet,
                result="win",
                amount_change=win_amount - bet,
                multiplier=1.0
            )
            await _update_mmr_and_achievements(user_id, "konopla", "win", balance_final)
            caption = format_message_with_username(
                f"🌿 <b>ВЫИГРЫШ!</b>\n\n"
                f"Выиграл: <b>{win_amount}</b> коинов 💰\n"
                f"Твой баланс: <b>{balance_final}</b> коинов",
                username, first_name
            )
        else:
            photo_path = config.get_image_path("kon.jpg")
            balance_after_bet = await db.get_balance(user_id)
            if balance_after_bet >= loss_amount:
                await balance_service.subtract_balance(
                    user_id=user_id,
                    amount=loss_amount,
                    command_source="/konopla",
                    comment="Проигрыш в конопле",
                    message=message,
                    username=username,
                    first_name=first_name,
                    allow_negative=False
                )
                final_balance = balance_after_bet - loss_amount
            else:
                final_balance = balance_after_bet
            await db.log_game_session(
                user_id=user_id,
                game_type="konopla",
                bet=bet,
                result="loss",
                amount_change=-(bet + (loss_amount if balance_after_bet >= loss_amount else 0)),
                multiplier=1.0
            )
            await db.log_admin_game(user_id, username, "/konopla", bet, "loss", -(bet + (loss_amount if balance_after_bet >= loss_amount else 0)), 0)
            await _update_mmr_and_achievements(user_id, "konopla", "loss", final_balance)
            caption = format_message_with_username(
                f"🌿 <b>ПРОИГРЫШ</b>\n\n"
                f"Ставка: {bet} коинов\n"
                f"Проигрыш: {loss_amount} коинов\n"
                f"Твой баланс: <b>{final_balance}</b> коинов",
                username, first_name
            )

        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await message.answer_photo(photo=photo, caption=caption)
        else:
            sent_message = await message.answer(caption)
            logger.warning(f"Фото {photo_path.name} не найдено для пользователя {user_id}")
        asyncio.create_task(delete_message_after(sent_message, config.GAME_RESULT_DELETE_TIMEOUT))
        logger.info(
            f"Пользователь {user_id} сыграл в /konopla: "
            f"bet={bet}, win={is_win}, chance={final_chance:.4f} (base={base_chance:.4f})"
        )
    except Exception as e:
        logger.exception("Ошибка в /konopla для %s: %s", user_id, e)
        if bet_subtracted:
            try:
                await balance_service.add_balance(user_id=user_id, amount=bet, command_source="/konopla", comment="Возврат при сбое", message=message, username=username, first_name=first_name)
            except Exception:
                pass
        await message.answer(format_game_error(username, first_name))


async def kripta_game_loop(bot: Bot, user_id: int, session_data: Dict):
    """
    Фоновая задача для обновления множителя в игре /kripta
    Обновляет сообщение каждые 10 секунд до обвала
    """
    try:
        multiplier_interval = config.KRIPTA_MULTIPLIER_INTERVAL
        current_multiplier = session_data["current_multiplier"]
        next_update_at = session_data["next_update_at"]
        crash_at = session_data["crash_at"]
        message_id = session_data["message_id"]
        chat_id = session_data["chat_id"]
        bet = session_data["bet"]
        
        while True:
            now = time.time()
            
            # Проверяем, не обвалилась ли игра
            if now >= crash_at:
                # Игра обвалилась
                await _handle_kripta_crash(bot, user_id, session_data, current_multiplier)
                break
            
            # Проверяем, нужно ли обновить множитель
            if now >= next_update_at:
                # Увеличиваем множитель
                current_multiplier += 1.0
                next_update_at = now + multiplier_interval
                
                # Обновляем в БД
                await db.update_kripta_multiplier(user_id, current_multiplier, int(next_update_at))
                
                # Обновляем в памяти
                if user_id in _active_kripta_sessions:
                    _active_kripta_sessions[user_id]["current_multiplier"] = current_multiplier
                    _active_kripta_sessions[user_id]["next_update_at"] = next_update_at
                
                # Обновляем сообщение
                try:
                    caption = (
                        f"🚀 <b>LUCKY JET</b>\n\n"
                        f"Множитель: <b>x{current_multiplier:.1f}</b>\n"
                        f"Ставка: {bet} коинов\n"
                        f"Потенциальный выигрыш: <b>{int(bet * current_multiplier)}</b> коинов\n\n"
                        f"⚠️ Игра может обвалиться в любой момент!"
                    )
                    
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text=f"Забрать x{current_multiplier:.1f}",
                            callback_data=f"kripta_take_{user_id}"
                        )
                    ]])
                    
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=caption,
                        reply_markup=keyboard
                    )
                except TelegramBadRequest as e:
                    # Сообщение уже было изменено или удалено
                    logger.warning(f"Не удалось обновить сообщение kripta для {user_id}: {e}")
                    break
                except Exception as e:
                    logger.error(f"Ошибка обновления сообщения kripta для {user_id}: {e}")
                    break
            
            # Ждем до следующего обновления или обвала
            sleep_time = min(next_update_at - now, crash_at - now, 1.0)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                break
                
    except asyncio.CancelledError:
        logger.info(f"Задача kripta для пользователя {user_id} отменена")
    except Exception as e:
        logger.error(f"Ошибка в задаче kripta для пользователя {user_id}: {e}", exc_info=True)
        # Закрываем сессию при ошибке
        await db.close_kripta_session(user_id)
        if user_id in _active_kripta_sessions:
            del _active_kripta_sessions[user_id]


async def _handle_kripta_crash(bot: Bot, user_id: int, session_data: Dict, final_multiplier: float):
    """Обработка обвала игры kripta. При краше — всегда проигрыш (баланс только в моменте краша)."""
    try:
        message_id = session_data["message_id"]
        chat_id = session_data["chat_id"]
        bet = session_data["bet"]
        
        user = await db.get_user(user_id)
        username = user.get("username") if user else None
        first_name = None
        
        # При краше — всегда проигрыш: kriptalox.jpg + текст проигрыша и множителя
        photo_path = config.get_image_path("kriptalox.jpg")
        
        await db.log_game_session(
            user_id=user_id,
            game_type="kripta",
            bet=bet,
            result="loss",
            amount_change=-bet,
            multiplier=final_multiplier
        )
        await db.log_admin_game(user_id, username or "", "/kripta", bet, "loss", -bet, 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "kripta", "loss", balance_after)
        caption = format_message_with_username(
            f"🚀 <b>ПРОИГРЫШ</b>\n\n"
            f"Проиграл <b>{bet}</b> коинов на множителе <b>x{final_multiplier:.1f}</b>",
            username, first_name
        )
        
        # Обновляем сообщение: в aiogram 3 нужен InputMediaPhoto
        try:
            if photo_path.exists():
                media = InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
                await bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media)
            else:
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption)
            game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
            asyncio.create_task(delete_message_after_by_id(bot, chat_id, message_id, game_timeout))
        except Exception as e:
            logger.error(f"Ошибка обновления финального сообщения kripta для {user_id}: {e}")
            try:
                if photo_path.exists():
                    sent = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(str(photo_path)), caption=caption)
                else:
                    sent = await bot.send_message(chat_id=chat_id, text=caption)
                if sent:
                    game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
                    asyncio.create_task(delete_message_after_by_id(bot, chat_id, sent.message_id, game_timeout))
            except Exception as e2:
                logger.error(f"Не удалось отправить результат kripta: {e2}")
        
        # Закрываем сессию
        await db.close_kripta_session(user_id)
        if user_id in _active_kripta_sessions:
            if "task" in _active_kripta_sessions[user_id]:
                _active_kripta_sessions[user_id]["task"].cancel()
            del _active_kripta_sessions[user_id]
            
    except Exception as e:
        logger.error(f"Ошибка обработки обвала kripta для {user_id}: {e}", exc_info=True)


@router.message(Command("kripta"))
async def cmd_kripta(message: Message):
    """
    Команда /kripta сумма
    Lucky Jet - реальный async-механизм с растущим множителем
    Базовый шанс: 8%
    Применяются бонусы Premium и зелий удачи
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    if user_id in _active_kripta_sessions:
        response_text = format_message_with_username(
            "У тебя уже есть активная игра! Дождись окончания или забери выигрыш.",
            username, first_name
        )
        sent_message = await message.answer(response_text)
        asyncio.create_task(delete_message_after(sent_message))
        return

    if await news_service.is_game_closed("kripta"):
        sent = await message.answer(format_message_with_username(
            "Lucky Jet временно на починке — загляни в /news.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    # Парсим сумму ставки
    try:
        parts = message.text.split()
        if len(parts) < 2:
            response_text = format_message_with_username(
                "Использование: /kripta сумма\n"
                "Пример: /kripta 100",
                username, first_name
            )
            sent_message = await message.answer(response_text)
            asyncio.create_task(delete_message_after(sent_message))
            return
        
        bet = int(parts[1])
        if bet <= 0:
            raise ValueError("Ставка должна быть положительной")
    except (ValueError, IndexError):
        response_text = format_message_with_username(
            "Ошибка! Укажи корректную сумму ставки.\n"
            "Пример: /kripta 100",
            username, first_name
        )
        sent_message = await message.answer(response_text)
        asyncio.create_task(delete_message_after(sent_message))
        return
    
    balance = await db.get_balance(user_id)
    if balance < bet:
        sent_message = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent_message))
        return
    
    success, balance_before, balance_after, error = await balance_service.subtract_balance(
        user_id=user_id,
        amount=bet,
        command_source="/kripta",
        comment="Ставка в Lucky Jet",
        message=message,
        username=username,
        first_name=first_name,
        allow_negative=False
    )
    
    if not success:
        return
    
    # Устанавливаем cooldown
    await set_command_cooldown(user_id, "/kripta")
    
    # Генерируем момент обвала: x2 ~20%, x3 меньше, x4+ редко. Чем больше ставка — тем ниже шанс гипер-выигрыша
    now = int(time.time())
    multiplier_interval = config.KRIPTA_MULTIPLIER_INTERVAL
    max_intervals = min(100, config.KRIPTA_MAX_MULTIPLIER)
    if bet > 5000:
        weights = [90, 7, 2, 1] + [0.5] * 6 + [0.1] * (max_intervals - 10)
    else:
        weights = [80, 12, 4, 2] + [1] * 5 + [0.5] * 10 + [0.1] * (max_intervals - 19)
    weights = weights[:max_intervals]
    crash_interval = game_random.choices(range(1, len(weights) + 1), weights=weights, k=1)[0]
    crash_at = now + (crash_interval * multiplier_interval)
    crash_at = min(crash_at, now + GAME_MAX_DURATION_SEC)  # макс 3 минуты
    
    # При старте: Startkripta.jpg, иначе kripta.jpg, иначе 1.jpg
    photo_path = config.get_image_path("Startkripta.jpg")
    if not photo_path.exists():
        photo_path = config.get_image_path("kripta.jpg")
    if not photo_path.exists():
        photo_path = config.get_image_path("1.jpg")
    caption = format_message_with_username(
        f"🚀 <b>LUCKY JET</b>\n\n"
        f"Множитель: <b>x1.0</b>\n"
        f"Ставка: {bet} коинов\n"
        f"Потенциальный выигрыш: <b>{bet}</b> коинов\n\n"
        f"⚠️ Игра может обвалиться в любой момент!",
        username, first_name
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Забрать x1.0",
            callback_data=f"kripta_take_{user_id}"
        )
    ]])
    
    # Отправляем начальное сообщение
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await message.answer_photo(
                photo=photo,
                caption=caption,
                reply_markup=keyboard
            )
        else:
            sent_message = await message.answer(caption, reply_markup=keyboard)
            logger.warning(f"Фото kripta.jpg не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки /kripta для {user_id}: {e}")
        sent_message = await message.answer(caption, reply_markup=keyboard)
    
    # Создаем сессию в БД
    await db.create_kripta_session(
        user_id=user_id,
        bet=bet,
        message_id=sent_message.message_id,
        chat_id=sent_message.chat.id,
        crash_at=crash_at
    )
    
    # Создаем сессию в памяти
    session_data = {
        "user_id": user_id,
        "bet": bet,
        "current_multiplier": 1.0,
        "message_id": sent_message.message_id,
        "chat_id": sent_message.chat.id,
        "started_at": now,
        "next_update_at": now + multiplier_interval,
        "crash_at": crash_at,
        "is_active": True
    }
    
    # Запускаем фоновую задачу обновления множителя
    task = asyncio.create_task(
        kripta_game_loop(message.bot, user_id, session_data)
    )
    session_data["task"] = task
    _active_kripta_sessions[user_id] = session_data
    
    logger.info(
        f"Пользователь {user_id} начал игру /kripta: "
        f"bet={bet}, crash_at={crash_at} (через {crash_interval * multiplier_interval} сек)"
    )


@router.callback_query(F.data.startswith("kripta_take_"))
async def callback_kripta_take(callback: CallbackQuery):
    """Обработчик кнопки "Забрать" в игре /kripta"""
    # Проверяем, что callback от правильного пользователя
    callback_user_id = callback.from_user.id
    callback_data = callback.data
    
    # Извлекаем user_id из callback_data
    try:
        target_user_id = int(callback_data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка обработки запроса", show_alert=True)
        return
    
    # Проверяем, что пользователь забирает свою игру
    if callback_user_id != target_user_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    
    # Проверяем наличие активной сессии
    if target_user_id not in _active_kripta_sessions:
        await callback.answer("Игра уже завершена", show_alert=True)
        return
    
    session_data = _active_kripta_sessions[target_user_id]
    
    if not session_data.get("is_active", False):
        await callback.answer("Игра уже завершена", show_alert=True)
        return
    
    # Кнопка «Забрать» = пользователь забирает вовремя (README: «можно забрать вовремя»).
    # Проигрыш только при краше; при нажатии «Забрать» — всегда выигрыш по текущему множителю.
    current_multiplier = session_data["current_multiplier"]
    bet = session_data["bet"]
    win_amount = int(bet * current_multiplier)

    username = callback.from_user.username if callback.from_user else None
    first_name = callback.from_user.first_name if callback.from_user else None

    photo_path = config.get_image_path("kriptawin.jpg")

    _, _, _, tax = await balance_service.add_game_win(
        user_id=target_user_id,
        gross_amount=win_amount,
        command_source="/kripta",
        comment=f"Выигрыш в Lucky Jet (x{current_multiplier:.1f})",
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        username=username,
        first_name=first_name,
    )
    await db.log_admin_game(target_user_id, username, "/kripta", bet, "win", win_amount - bet, tax or 0)
    balance_after = await db.get_balance(target_user_id)
    await db.log_game_session(
        user_id=target_user_id,
        game_type="kripta",
        bet=bet,
        result="win",
        amount_change=win_amount - bet,
        multiplier=current_multiplier
    )
    await _update_mmr_and_achievements(target_user_id, "kripta", "win", balance_after)
    caption = format_message_with_username(
        f"🚀 <b>ВЫИГРЫШ!</b>\n\n"
        f"Выиграл <b>{win_amount}</b> коинов на множителе <b>x{current_multiplier:.1f}</b> 💰\n"
        f"Твой баланс: <b>{balance_after}</b> коинов",
        username, first_name
    )

    try:
        if photo_path.exists():
            media = InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
            await callback.bot.edit_message_media(
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                media=media,
                reply_markup=None
            )
        else:
            await callback.bot.edit_message_caption(
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                caption=caption,
                reply_markup=None
            )
        game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
        asyncio.create_task(delete_message_after_by_id(
            callback.bot, callback.message.chat.id, callback.message.message_id,
            game_timeout
        ))
    except Exception as e:
        logger.error(f"Ошибка обновления сообщения kripta при раннем выходе для {target_user_id}: {e}")
        try:
            if photo_path.exists():
                sent = await callback.bot.send_photo(
                    callback.message.chat.id,
                    photo=FSInputFile(str(photo_path)),
                    caption=caption
                )
            else:
                sent = await callback.bot.send_message(callback.message.chat.id, text=caption)
            if sent:
                game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
                asyncio.create_task(delete_message_after_by_id(
                    callback.bot, callback.message.chat.id, sent.message_id,
                    game_timeout
                ))
        except Exception:
            pass

    await db.close_kripta_session(target_user_id)
    if target_user_id in _active_kripta_sessions:
        if "task" in _active_kripta_sessions[target_user_id]:
            _active_kripta_sessions[target_user_id]["task"].cancel()
        del _active_kripta_sessions[target_user_id]

    await callback.answer("Выигрыш зачислен!", show_alert=False)
    logger.info(
        f"Пользователь {target_user_id} забрал выигрыш в /kripta: "
        f"multiplier=x{current_multiplier:.1f}, win_amount={win_amount}"
    )


@router.message(Command("plsdon"))
async def cmd_plsdon(message: Message):
    """
    Команда /plsdon
    КД: 5 минут
    50% — игнор (jail.jpg)
    45% — −10…−20 (otzhal.jpg)
    5% — +10…+40 (beg.jpg)
    Применяются бонусы Premium и зелий удачи
    
    ВАЖНО: При каждом использовании на 15 секунд появляется для всех
    картинка (jail.jpg) с подписью "ярому феменисту на еду @user"
    с кнопкой "Пожертвовать" (50 коинов)
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    if await news_service.is_game_closed("plsdon"):
        sent = await message.answer(format_message_with_username(
            "Задонать временно на починке — загляни в /news.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    # Проверяем cooldown
    last_used = await db.get_cooldown(user_id, "/plsdon")
    now = int(time.time())
    cooldown_seconds = config.PLSDON_COOLDOWN
    
    if last_used:
        time_passed = now - last_used
        if time_passed < cooldown_seconds:
            remaining = cooldown_seconds - time_passed
            minutes = remaining // 60
            seconds = remaining % 60
            
            time_str = f"{minutes} мин {seconds} сек" if minutes > 0 else f"{seconds} сек"
            response_text = format_message_with_username(
                f"Команда в cooldown! Приходи через {time_str} ⏳",
                username, first_name
            )
            sent_message = await message.answer(response_text)
            asyncio.create_task(delete_message_after(sent_message))
            return
    
    # Устанавливаем cooldown
    await set_command_cooldown(user_id, "/plsdon")
    
    # Отправляем глобальное сообщение с кнопкой пожертвования (на 15 секунд)
    photo_path = config.get_image_path("jail.jpg")
    global_caption = f"Ярому феминисту на еду @{username or first_name or 'Пользователь'}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Пожертвовать 50 коинов",
            callback_data=f"plsdon_donate_{user_id}"
        )
    ]])
    
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            global_message = await message.answer_photo(
                photo=photo,
                caption=global_caption,
                reply_markup=keyboard
            )
        else:
            global_message = await message.answer(
                global_caption,
                reply_markup=keyboard
            )
            logger.warning(f"Фото jail.jpg не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки глобального сообщения /plsdon для {user_id}: {e}")
        global_message = await message.answer(global_caption, reply_markup=keyboard)
    
    # Сохраняем сообщение для обработки кнопки
    _active_plsdon_messages[user_id] = {
        "message_id": global_message.message_id,
        "chat_id": global_message.chat.id,
        "target_user_id": user_id,
        "expires_at": now + config.PLSDON_DONATE_BUTTON_TIMEOUT
    }
    
    # Удаляем глобальное сообщение через 15 секунд
    asyncio.create_task(delete_message_after(global_message, config.PLSDON_DONATE_BUTTON_TIMEOUT))
    
    # Базовые шансы
    base_ignore_chance = config.PLSDON_IGNORE_CHANCE  # 50%
    base_loss_chance = config.PLSDON_LOSS_CHANCE  # 45%
    base_win_chance = config.PLSDON_WIN_CHANCE  # 5%
    
    # Вычисляем финальный шанс выигрыша с учетом бонусов
    final_win_chance = await calculate_win_chance_async(base_win_chance, user_id, "plsdon")

    # Пересчитываем шансы (игнор и проигрыш уменьшаются пропорционально)
    win_increase = final_win_chance - base_win_chance
    
    # Уменьшаем шансы игнора и проигрыша пропорционально
    final_ignore_chance = base_ignore_chance - (win_increase * (base_ignore_chance / (base_ignore_chance + base_loss_chance)))
    final_loss_chance = base_loss_chance - (win_increase * (base_loss_chance / (base_ignore_chance + base_loss_chance)))
    
    # Нормализуем (на всякий случай)
    total = final_ignore_chance + final_loss_chance + final_win_chance
    if total > 0:
        final_ignore_chance /= total
        final_loss_chance /= total
        final_win_chance /= total
    
    # Генерируем результат
    roll = game_random.random()
    
    if roll < final_ignore_chance:
        # Игнор
        photo_path = config.get_image_path("jail.jpg")
        caption = format_message_with_username(
            "🔇 <b>ИГНОР</b>\n\n"
            "Тебе не заплатят 😢",
            username, first_name
        )
        amount_change = 0
        result = "ignore"
    elif roll < final_ignore_chance + final_loss_chance:
        # Проигрыш
        loss_amount = game_random.randint(10, 20)
        photo_path = config.get_image_path("otzhal.jpg")
        
        # Проверяем баланс
        balance = await db.get_balance(user_id)
        if balance >= loss_amount:
            await balance_service.subtract_balance(
                user_id=user_id,
                amount=loss_amount,
                command_source="/plsdon",
                comment="Проигрыш в plsdon",
                message=message,
                username=username,
                first_name=first_name,
                allow_negative=False
            )
            amount_change = -loss_amount
        else:
            amount_change = 0
        
        caption = format_message_with_username(
            f"😢 <b>ПРОИГРЫШ</b>\n\n"
            f"У тебя отжали {loss_amount} коинов 😭",
            username, first_name
        )
        result = "loss"
    else:
        # Выигрыш
        win_amount = game_random.randint(10, 40)
        photo_path = config.get_image_path("beg.jpg")
        
        await balance_service.add_balance(
            user_id=user_id,
            amount=win_amount,
            command_source="/plsdon",
            comment="Выигрыш в plsdon",
            message=message,
            username=username,
            first_name=first_name
        )
        
        caption = format_message_with_username(
            f"💰 <b>ВЫИГРЫШ!</b>\n\n"
            f"Тебе дали {win_amount} коинов! 🎉",
            username, first_name
        )
        amount_change = win_amount
        result = "win"
    
    # Отправляем фото с результатом
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await message.answer_photo(photo=photo, caption=caption)
        else:
            sent_message = await message.answer(caption)
            logger.warning(f"Фото {photo_path.name} не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки /plsdon для {user_id}: {e}")
        sent_message = await message.answer(caption)
    
    # Автоудаление через 30 секунд
    asyncio.create_task(delete_message_after(sent_message))
    
    logger.info(
        f"Пользователь {user_id} использовал /plsdon: "
        f"result={result}, amount_change={amount_change}, "
        f"win_chance={final_win_chance:.4f} (base={base_win_chance:.4f})"
    )


@router.callback_query(F.data.startswith("plsdon_donate_"))
async def callback_plsdon_donate(callback: CallbackQuery):
    """Обработчик кнопки "Пожертвовать" в /plsdon"""
    callback_user_id = callback.from_user.id
    callback_data = callback.data
    
    # Извлекаем user_id получателя
    try:
        target_user_id = int(callback_data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка обработки запроса", show_alert=True)
        return
    
    # Проверяем, что сообщение еще активно
    if target_user_id not in _active_plsdon_messages:
        await callback.answer("Время пожертвования истекло", show_alert=True)
        return
    
    message_data = _active_plsdon_messages[target_user_id]
    now = int(time.time())
    
    if now > message_data["expires_at"]:
        await callback.answer("Время пожертвования истекло", show_alert=True)
        del _active_plsdon_messages[target_user_id]
        return
    
    # Проверяем баланс донора
    donor_balance = await db.get_balance(callback_user_id)
    donate_cost = config.PLSDON_DONATE_COST
    
    if donor_balance < donate_cost:
        await callback.answer(
            f"Недостаточно средств! Нужно {donate_cost} коинов",
            show_alert=True
        )
        return
    
    # Списываем у донора
    donor_username = callback.from_user.username
    donor_first_name = callback.from_user.first_name
    
    success, _, _, error = await balance_service.subtract_balance(
        user_id=callback_user_id,
        amount=donate_cost,
        command_source="/plsdon_donate",
        comment=f"Пожертвование пользователю {target_user_id}",
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        username=donor_username,
        first_name=donor_first_name,
        allow_negative=False
    )
    
    if not success:
        await callback.answer(error, show_alert=True)
        return
    
    # Начисляем получателю
    target_user = await db.get_user(target_user_id)
    target_username = target_user.get("username") if target_user else None
    target_first_name = None  # Не используется в add_balance для получателя
    
    await balance_service.add_balance(
        user_id=target_user_id,
        amount=donate_cost,
        command_source="/plsdon_donate",
        comment=f"Пожертвование от пользователя {callback_user_id}",
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        username=target_username,
        first_name=target_first_name
    )
    
    # Отправляем сообщение о пожертвовании
    donation_text = (
        f"@{donor_username or donor_first_name or 'Пользователь'} "
        f"пожертвовал нищете @{target_username or target_first_name or 'Пользователь'} "
        f"{donate_cost} коинов за это "
        f"@{target_username or target_first_name or 'Пользователь'} "
        f"расцеловал ботинки @{donor_username or donor_first_name or 'Пользователь'}"
    )
    
    try:
        don_msg = await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            text=donation_text
        )
        asyncio.create_task(delete_message_after(don_msg, config.MESSAGE_DELETE_TIMEOUT))
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения о пожертвовании: {e}")
    
    await callback.answer(f"Пожертвовано {donate_cost} коинов!", show_alert=False)
    
    logger.info(
        f"Пользователь {callback_user_id} пожертвовал {donate_cost} коинов "
        f"пользователю {target_user_id} в /plsdon"
    )


# ---------- /almaz (добыча алмазов, аналог сапёра/риска) ----------
ALMAZ_EXPLOSION_BASE = 0.5
ALMAZ_EXPLOSION_INCREASE = 0.05
ALMAZ_WIN_PER_DIG = 0.5


@router.message(Command("almaz"))
async def cmd_almaz(message: Message):
    """Игра /almaz сумма: добыча алмазов. Кнопки: Добыть алмаз, Забрать выигрыш, Завершить."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    if user_id in _active_almaz_sessions:
        sent = await message.answer(format_message_with_username("У тебя уже есть активная игра /almaz. Забери выигрыш или заверши.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    if await news_service.is_game_closed("almaz"):
        sent = await message.answer(format_message_with_username(
            "Алмазы временно на починке — загляни в /news.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)

    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        sent = await message.answer(format_message_with_username("Формат: /almaz сумма", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    try:
        bet = int(parts[1])
        if bet <= 0:
            raise ValueError("сумма > 0")
    except (ValueError, IndexError):
        sent = await message.answer(format_message_with_username("Укажи корректную сумму.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    balance = await db.get_balance(user_id)
    if balance < bet:
        sent = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=bet,
        command_source="/almaz", comment="Ставка в алмазах",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    await set_command_cooldown(user_id, "/almaz")

    caption = format_message_with_username(
        f"💎 <b>АЛМАЗЫ</b>\n\nСтавка: {bet} коинов\nТекущий выигрыш: 0\n\n"
        f"⛏ Добывай алмазы или забирай выигрыш. Каждая добыча — риск взрыва!",
        username, first_name
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛏ Копать дальше", callback_data=f"almaz_dig_{user_id}")],
        [InlineKeyboardButton(text="💰 Забрать", callback_data=f"almaz_take_{user_id}")],
        [InlineKeyboardButton(text="❌ Завершить", callback_data=f"almaz_end_{user_id}")]
    ])
    photo_path = config.get_image_path("almaz.jpg")
    try:
        if photo_path.exists():
            sent_msg = await message.answer_photo(FSInputFile(str(photo_path)), caption=caption, reply_markup=keyboard)
        else:
            sent_msg = await message.answer(caption, reply_markup=keyboard)
    except Exception as e:
        logger.warning("almaz start photo: %s", e)
        sent_msg = await message.answer(caption, reply_markup=keyboard)

    bet_risk = min(0.12, bet / 10000 * 0.2)
    _active_almaz_sessions[user_id] = {
        "bet": bet,
        "current_win": 0,
        "message_id": sent_msg.message_id,
        "chat_id": sent_msg.chat.id,
        "explosion_chance": ALMAZ_EXPLOSION_BASE + bet_risk,
        "started_at": time.time(),
    }
    asyncio.create_task(_almaz_timeout_task(message.bot, user_id, GAME_MAX_DURATION_SEC))
    asyncio.create_task(delete_message_after(sent_msg, config.MESSAGE_DELETE_TIMEOUT))
    logger.info("User %s started /almaz bet=%s", user_id, bet)


async def _almaz_timeout_task(bot: Bot, user_id: int, timeout_sec: int):
    """По истечении 3 минут — авто-забрать текущий выигрыш или завершить."""
    await asyncio.sleep(timeout_sec)
    sess = _active_almaz_sessions.pop(user_id, None)
    if not sess:
        return
    chat_id = sess["chat_id"]
    message_id = sess["message_id"]
    bet = sess["bet"]
    current_win = sess["current_win"]
    try:
        if current_win > 0:
            await balance_service.add_game_win(
                user_id=user_id,
                gross_amount=current_win,
                command_source="/almaz",
                comment="Авто-забрать по таймауту",
                bot=bot,
                chat_id=chat_id,
                username=None,
                first_name=None,
            )
            await db.log_game_session(user_id, "almaz", bet, "win", current_win - bet, 1.0)
            balance_after = await db.get_balance(user_id)
            await _update_mmr_and_achievements(user_id, "almaz", "win", balance_after)
        user = await db.get_user(user_id)
        un = user.get("username") if user else None
        caption = format_message_with_username(
            f"⏱ Время вышло. Забрал выигрыш: <b>{current_win}</b> коинов." if current_win > 0 else "⏱ Время вышло. Игра завершена.",
            un, None
        )
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption, reply_markup=None)
    except Exception as e:
        logger.exception("almaz timeout task: %s", e)
        if current_win > 0:
            await balance_service.add_game_win(user_id=user_id, gross_amount=current_win, command_source="/almaz", comment="Авто-забрать по таймауту", bot=bot, chat_id=chat_id, username=None, first_name=None)


@router.callback_query(F.data.startswith("almaz_dig_"))
async def cb_almaz_dig(callback: CallbackQuery):
    """Добыть алмаз: 50/50 взрыв (потеря всего) или алмаз (выигрыш растёт)."""
    try:
        target_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    if target_id not in _active_almaz_sessions:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return

    sess = _active_almaz_sessions[target_id]
    bet = sess["bet"]
    current_win = sess["current_win"]
    explosion_chance = sess["explosion_chance"]

    if game_random.random() < explosion_chance:
        del _active_almaz_sessions[target_id]
        await db.log_game_session(
            user_id=target_id,
            game_type="almaz",
            bet=bet,
            result="loss",
            amount_change=-bet,
            multiplier=1.0
        )
        await db.log_admin_game(target_id, (await db.get_user(target_id) or {}).get("username", ""), "/almaz", bet, "loss", -bet, 0)
        balance_after = await db.get_balance(target_id)
        await _update_mmr_and_achievements(target_id, "almaz", "loss", balance_after)
        photo_path = config.get_image_path("almazlox.jpg")
        user = await db.get_user(target_id)
        un = user.get("username") if user else None
        caption = format_message_with_username(
            f"💥 <b>ВЗРЫВ!</b>\n\nПотерял весь выигрыш. Ставка {bet} коинов списана.",
            un, None
        )
        try:
            if photo_path.exists():
                await callback.bot.edit_message_media(
                    chat_id=callback.message.chat.id, message_id=callback.message.message_id,
                    media=InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
                )
            else:
                await callback.bot.edit_message_caption(chat_id=callback.message.chat.id, message_id=callback.message.message_id, caption=caption)
        except Exception:
            await callback.bot.send_photo(callback.message.chat.id, FSInputFile(str(photo_path)), caption=caption) if photo_path.exists() else await callback.bot.send_message(callback.message.chat.id, caption)
        game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
        asyncio.create_task(delete_message_after_by_id(callback.bot, callback.message.chat.id, callback.message.message_id, game_timeout))
        await callback.answer("Взрыв! Проигрыш.", show_alert=True)
        return

    add_win = max(1, int(bet * ALMAZ_WIN_PER_DIG))
    sess["current_win"] = current_win + add_win
    sess["explosion_chance"] = min(0.95, explosion_chance + ALMAZ_EXPLOSION_INCREASE)
    username = callback.from_user.username
    first_name = callback.from_user.first_name
    caption = format_message_with_username(
        f"💎 <b>АЛМАЗ!</b>\n\nСтавка: {bet}\nТекущий выигрыш: <b>{sess['current_win']}</b>\n"
        f"Шанс взрыва растёт. Забери выигрыш или копай дальше.",
        username, first_name
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛏ Копать дальше", callback_data=f"almaz_dig_{target_id}")],
        [InlineKeyboardButton(text="💰 Забрать", callback_data=f"almaz_take_{target_id}")],
        [InlineKeyboardButton(text="❌ Завершить", callback_data=f"almaz_end_{target_id}")]
    ])
    try:
        await callback.bot.edit_message_caption(
            chat_id=callback.message.chat.id, message_id=callback.message.message_id,
            caption=caption, reply_markup=keyboard
        )
    except Exception as e:
        logger.warning("almaz dig edit: %s", e)
    await callback.answer(f"+{add_win} коинов! Выигрыш: {sess['current_win']}", show_alert=False)


@router.callback_query(F.data.startswith("almaz_take_"))
async def cb_almaz_take(callback: CallbackQuery):
    """Забрать выигрыш."""
    try:
        target_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    if target_id not in _active_almaz_sessions:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return

    sess = _active_almaz_sessions.pop(target_id)
    win_amount = sess["current_win"]
    bet = sess["bet"]
    if win_amount > 0:
        _, _, _, tax = await balance_service.add_game_win(
            user_id=target_id,
            gross_amount=win_amount,
            command_source="/almaz",
            comment="Выигрыш в алмазах",
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        await db.log_admin_game(target_id, callback.from_user.username, "/almaz", bet, "win", win_amount - bet, tax or 0)
    else:
        await db.log_admin_game(target_id, callback.from_user.username, "/almaz", bet, "loss", -bet, 0)
    balance_after = await db.get_balance(target_id)
    await _update_mmr_and_achievements(target_id, "almaz", "win", balance_after)
    photo_path = config.get_image_path("almazwin.jpg")
    user = await db.get_user(target_id)
    un = user.get("username") if user else None
    caption = format_message_with_username(
        f"💰 <b>ПОБЕДА!</b>\n\nЗабрал выигрыш: <b>{win_amount}</b> коинов.",
        un, None
    )
    try:
        if photo_path.exists():
            await callback.bot.edit_message_media(
                chat_id=callback.message.chat.id, message_id=callback.message.message_id,
                media=InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=caption)
            )
        else:
            await callback.bot.edit_message_caption(chat_id=callback.message.chat.id, message_id=callback.message.message_id, caption=caption)
    except Exception:
        if photo_path.exists():
            await callback.bot.send_photo(callback.message.chat.id, FSInputFile(str(photo_path)), caption=caption)
        else:
            await callback.bot.send_message(callback.message.chat.id, caption)
    game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
    asyncio.create_task(delete_message_after_by_id(callback.bot, callback.message.chat.id, callback.message.message_id, game_timeout))
    await callback.answer(f"Выигрыш {win_amount} зачислен!", show_alert=False)


@router.callback_query(F.data.startswith("almaz_end_"))
async def cb_almaz_end(callback: CallbackQuery):
    """Завершить без вывода (ставка уже списана)."""
    try:
        target_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    if target_id in _active_almaz_sessions:
        _active_almaz_sessions.pop(target_id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Игра завершена.", show_alert=False)


# ==================== 5 УНИКАЛЬНЫХ ИГР: /random, /gamerandom, /blackmarket, /pressure, /echo ====================

# Игры для /random: только одиночные (без rulet, frekaz, chisla — нужны другие люди). Ставка не ниже 100.
RANDOM_GAME_OPTIONS = [
    {"id": "slot", "name": "Слоты", "min_bet": 100, "max_bet": 500},
    {"id": "konopla", "name": "Конопля", "min_bet": 100, "max_bet": 500},
    {"id": "kripta", "name": "Lucky Jet", "min_bet": 100, "max_bet": 50000},
    {"id": "almaz", "name": "Алмазы", "min_bet": 100, "max_bet": 10000},
    {"id": "perekyp", "name": "Перекуп", "min_bet": 100, "max_bet": 50000},
]
# + один из risk40 (min_bet 100 для единообразия)
RANDOM_LOADING_DURATION = 5  # секунд загрузки «разлом матрицы»
_blackmarket_red_choices: Dict[int, int] = {}  # user_id -> count последних красных выборов (для подставы)


async def _resolve_random_game_round(
    user_id: int, game_id: str, bet: int, luck_bonus: float,
    bot: Bot, chat_id: int, username: str, first_name: str
) -> tuple:
    """Один раунд выбранной игры. Возвращает (won: bool, win_amount: int, text: str). Без защиты от нищеты."""
    if game_id == "slot":
        base = config.SLOT_WIN_CHANCE
        chance = min(0.95, base + luck_bonus)
        won = game_random.random() < chance
        win_amount = int(config.SLOT_WIN * (bet / max(1, config.SLOT_BET))) if won else 0
        text = "Слоты" if won else "Слоты"
        return (won, win_amount, text)
    if game_id == "konopla":
        base = config.KONOPLA_WIN_CHANCE
        chance = min(0.95, base + luck_bonus)
        won = game_random.random() < chance
        win_amount = int(config.KONOPLA_WIN * (bet / max(1, config.KONOPLA_BET))) if won else 0
        return (won, win_amount, "Конопля")
    if game_id == "kripta":
        r = game_random.random()
        if r < 0.65:
            return (False, 0, "Lucky Jet")
        if r < 0.85:
            mult = 2.0
        elif r < 0.95:
            mult = 3.0
        else:
            mult = round(game_random.uniform(4.0, 8.0), 2)
        chance_survive = 0.5 + luck_bonus
        if game_random.random() < chance_survive:
            win_amount = int(bet * mult)
            return (True, win_amount, f"Lucky Jet x{mult:.1f}")
        return (False, 0, "Lucky Jet")
    if game_id == "almaz":
        explosion = 0.25 - luck_bonus * 0.1
        explosion = max(0.08, min(0.5, explosion))
        if game_random.random() < explosion:
            return (False, 0, "Алмазы")
        win_amount = int(bet * game_random.uniform(1.3, 2.0))
        return (True, win_amount, "Алмазы")
    if game_id == "perekyp":
        base = getattr(config, "PEREKYP_BUY_WIN_CHANCE", 0.45)
        chance = min(0.95, base + luck_bonus)
        won = game_random.random() < chance
        if won:
            mult = round(game_random.uniform(1.5, 5.0), 2)
            win_amount = int(bet * mult)
            return (True, win_amount, f"Перекуп x{mult:.2f}")
        return (False, 0, "Перекуп")
    # risk40 (slug)
    game = RISK40_GAMES.get(game_id)
    if game:
        action = game_random.choice(game["actions"])
        bust_base, bust_per, mult_step = action[2], action[3], action[4]
        bust = min(0.95, bust_base + bust_per - luck_bonus * 0.05)
        bust = max(0.05, bust)
        if game_random.random() < bust:
            return (False, 0, _risk40_display_name(game_id))
        win_amount = int(bet * mult_step)
        return (True, win_amount, f"{_risk40_display_name(game_id)} x{mult_step:.2f}")
    return (False, 0, "Игра")


@router.message(Command("random"))
async def cmd_random(message: Message):
    """Разлом матрицы: случайная игра из всех одиночных (без мультиплеера), случайная ставка ≥100. Загрузка ~5 сек, затем картинка выбранной игры и результат."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    chat_id = message.chat.id
    bot = message.bot

    balance = await db.get_balance(user_id)
    if balance < 100:
        sent = await message.answer(format_message_with_username(
            "Для разлома матрицы нужен минимум 100 коинов.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    await _maybe_send_event_message(user_id, chat_id, bot, balance=balance)

    games = list(RANDOM_GAME_OPTIONS) + [{"id": game_random.choice(RISK40_SLUGS), "name": "риск/забрать", "min_bet": 100, "max_bet": 5000}]
    chosen = game_random.choice(games)
    game_id, name, min_bet, max_bet = chosen["id"], chosen["name"], chosen["min_bet"], chosen["max_bet"]
    if game_random.random() < 0.02:
        other = [g for g in games if g["id"] != game_id]
        if other:
            chosen = game_random.choice(other)
            game_id, name, min_bet, max_bet = chosen["id"], chosen["name"], chosen["min_bet"], chosen["max_bet"]

    stake = max(min_bet, min(max_bet, int(balance * game_random.uniform(0.02, 0.08))))
    if stake < min_bet:
        stake = min_bet
    if balance < stake:
        stake = min(balance, max_bet)
    if stake < 100:
        sent = await message.answer(format_message_with_username(
            "Минимум 100 коинов для разлома.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=stake,
        command_source="/random", comment="Разлом матрицы",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    # Плавная загрузка: «Идёт разлом матрицы…»
    load_cap = format_message_with_username(
        "⏳ Идёт разлом матрицы…\n\nВселенная выбирает игру и твою ставку.",
        username, first_name
    )
    loading = await message.answer(load_cap)
    await asyncio.sleep(RANDOM_LOADING_DURATION)

    # Замена на «Разлом произошёл. Вселенная решила: игра — X. Ставка Y.»
    break_cap = format_message_with_username(
        f"🌀 Разлом произошёл.\n\nВселенная решила: игра — <b>{name}</b>. Ставка: <b>{stake}</b> коинов.",
        username, first_name
    )
    try:
        await loading.edit_text(break_cap)
    except Exception:
        await bot.send_message(chat_id, break_cap)

    archetype = _last_echo_archetype.get(user_id, "chaotic")
    luck_bonus = game_random.uniform(0.03, 0.07)
    if archetype == "cautious":
        luck_bonus += 0.02
    elif archetype == "overconfident":
        luck_bonus -= 0.02
    won, win_amount, game_label = await _resolve_random_game_round(
        user_id, game_id, stake, luck_bonus, bot, chat_id, username, first_name
    )

    if won and win_amount > 0:
        await balance_service.add_game_win(
            user_id=user_id, gross_amount=win_amount,
            command_source="/random", comment=f"Разлом: {name}",
            bot=bot, chat_id=chat_id, username=username, first_name=first_name,
        )
        await db.log_game_session(user_id, "random", stake, "win", win_amount - stake, win_amount / max(stake, 1))
        await db.log_admin_game(user_id, username, "/random", stake, "win", win_amount - stake, None)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "random", "win", balance_after)
        echo_hint = (_last_echo_analysis.get(user_id, {}).get("signature", "") + "\n\n") if user_id in _last_echo_analysis else ""
        caption = format_message_with_username(
            f"🎲 <b>{name}</b>\n\n{echo_hint}✅ Победил. +<b>{win_amount}</b> коинов. Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        photo = config.get_game_image_path(game_id, "win")
        if not photo.exists():
            photo = config.get_image_path("random_win.jpg")
    else:
        await db.log_game_session(user_id, "random", stake, "loss", -stake, 0)
        await db.log_admin_game(user_id, username, "/random", stake, "loss", -stake, 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "random", "loss", balance_after)
        echo_hint = (_last_echo_analysis.get(user_id, {}).get("signature", "") + "\n\n") if user_id in _last_echo_analysis else ""
        caption = format_message_with_username(
            f"🎲 <b>{name}</b>\n\n{echo_hint}❌ Проигрыш. Минус <b>{stake}</b> коинов. Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        photo = config.get_game_image_path(game_id, "lose")
        if not photo.exists():
            photo = config.get_image_path("random_lose.jpg")

    if photo.exists():
        result_msg = await message.answer_photo(FSInputFile(str(photo)), caption=caption)
    else:
        result_msg = await message.answer(caption)
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, loading.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
    asyncio.create_task(delete_message_after(result_msg, config.GAME_RESULT_DELETE_TIMEOUT))


# ---------- /gamerandom — Сбой матрицы ----------
GAMERANDOM_LOADING_SEC = 4  # плавная загрузка «матрица думает»


@router.message(Command("gamerandom"))
async def cmd_gamerandom(message: Message):
    """Сбой матрицы: загрузка «вселенная/матрица думает», выбор типа игры (азартная = сразу результат, пошаговая = один симулированный ход), затем исход."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    chat_id = message.chat.id
    bot = message.bot

    balance = await db.get_balance(user_id)
    if balance < 50:
        sent = await message.answer(format_message_with_username(
            "Матрица требует минимум 50 коинов.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    pct = game_random.uniform(0.02, 0.05)
    stake = max(50, min(int(balance * pct), 5000))
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=stake,
        command_source="/gamerandom", comment="Сбой матрицы",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    # Плавная загрузка: «Матрица думает» / «Вселенная думает»
    load_text = game_random.choice([
        "⏳ Матрица думает…\n\nВыбирает тип игры: азартная — сразу результат, пошаговая — один ход и исход.",
        "⏳ Вселенная думает…\n\nОпределяет, будет ли игра мгновенной или с одним выбором.",
    ])
    load_cap = format_message_with_username(load_text + f"\n\n💰 Ставка: <b>{stake}</b> коинов.", username, first_name)
    photo_load = config.get_image_path("gamerandom_load.jpg")
    if photo_load.exists():
        loading = await message.answer_photo(FSInputFile(str(photo_load)), caption=load_cap)
    else:
        loading = await message.answer(load_cap)
    await asyncio.sleep(GAMERANDOM_LOADING_SEC)

    # Тип: азартная (мгновенный результат) или пошаговая (один «ход» — флейвор, результат тот же)
    game_type = game_random.choice(["азартная", "пошаговая"])
    type_cap = format_message_with_username(
        f"🌀 Матрица выбрала: <b>{game_type} игра</b>.\n\n"
        + ("Мгновенный результат." if game_type == "азартная" else "Один симулированный ход — исход ниже.") + f"\n\nСтавка: <b>{stake}</b> коинов.",
        username, first_name
    )
    try:
        if loading.photo:
            await loading.edit_caption(caption=type_cap)
        else:
            await loading.edit_text(type_cap)
    except Exception:
        await bot.send_message(chat_id, type_cap)

    archetype = _last_echo_archetype.get(user_id, "chaotic")
    archetype_mod = 0.05 if archetype == "cautious" else (-0.05 if archetype == "overconfident" else 0)
    event_roll = game_random.random()
    bug_event = event_roll < 0.04
    extra_chance = 0.10 if bug_event else 0
    win_chance = 0.45 + extra_chance + archetype_mod
    mult = round(game_random.uniform(2.0, 4.0), 2)
    win_amount = int(stake * mult) if game_random.random() < win_chance else 0
    won = win_amount > 0

    event_text = "🔧 Баг матрицы дал лишний шанс…\n\n" if bug_event else ""
    echo_hint = ""
    if user_id in _last_echo_analysis:
        sig = _last_echo_analysis[user_id].get("signature", "")
        if sig:
            echo_hint = f"📌 {sig}\n\n"
    result_cap = format_message_with_username(
        f"⚠️ Сбой матрицы. Тип: <b>{game_type}</b>.\n\n{echo_hint}{event_text}"
        + (f"✅ +<b>{win_amount}</b> коинов." if won else f"❌ Минус <b>{stake}</b> коинов."),
        username, first_name
    )
    try:
        if loading.photo:
            await loading.edit_caption(caption=result_cap)
        else:
            await loading.edit_text(result_cap)
    except Exception:
        await bot.send_message(chat_id, result_cap)

    if won:
        await balance_service.add_game_win(
            user_id=user_id, gross_amount=win_amount,
            command_source="/gamerandom", comment="Сбой матрицы",
            bot=bot, chat_id=chat_id, username=username, first_name=first_name,
        )
        await db.log_game_session(user_id, "gamerandom", stake, "win", win_amount - stake, win_amount / max(stake, 1))
        await db.log_admin_game(user_id, username, "/gamerandom", stake, "win", win_amount - stake, None)
    else:
        await db.log_game_session(user_id, "gamerandom", stake, "loss", -stake, 0)
        await db.log_admin_game(user_id, username, "/gamerandom", stake, "loss", -stake, 0)
    balance_after = await db.get_balance(user_id)
    await _update_mmr_and_achievements(user_id, "gamerandom", "win" if won else "loss", balance_after)
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, loading.message_id, config.GAME_RESULT_DELETE_TIMEOUT))


# ---------- /blackmarket — Чёрный рынок ----------
_active_blackmarket: Dict[int, Dict] = {}  # user_id -> {stake, message_id, chat_id, bot}

BLACKMARKET_DEALS = [
    {"id": "red", "label": "🔴 Высокий риск / высокий профит", "win_chance": 0.35, "mult": 2.5, "podstva_chance": 0.25},
    {"id": "yellow", "label": "🟡 Средний риск", "win_chance": 0.55, "mult": 1.5, "podstva_chance": 0.10},
    {"id": "green", "label": "🟢 Почти безопасная", "win_chance": 0.82, "mult": 1.2, "podstva_chance": 0.02},
]


@router.message(Command("blackmarket"))
async def cmd_blackmarket(message: Message):
    """Чёрный рынок: три сделки (красная/жёлтая/зелёная). Риск и профит от жадности. Частый выбор красного — выше шанс подставы."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    chat_id = message.chat.id

    parts = (message.text or "").strip().split()
    try:
        stake = int(parts[1]) if len(parts) > 1 else 500
    except (ValueError, IndexError):
        stake = 500
    stake = max(100, min(50000, stake))
    balance = await db.get_balance(user_id)
    if balance < stake:
        sent = await message.answer(format_insufficient_balance(username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return

    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=stake,
        command_source="/blackmarket", comment="Вход на чёрный рынок",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    red_count = _blackmarket_red_choices.get(user_id, 0)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=d["label"], callback_data=f"bm|{user_id}|{d['id']}")]
        for d in BLACKMARKET_DEALS
    ])
    caption = format_message_with_username(
        f"🕳 <b>Чёрный рынок</b>\n\nСтавка: <b>{stake}</b> коинов. Выбери сделку: 🟢 безопасная, 🟡 риск, 🔴 жесть. Рынок запоминает жадных.",
        username, first_name
    )
    photo_bm = config.get_image_path("blackmarket_start.jpg")
    if photo_bm.exists():
        sent = await message.answer_photo(FSInputFile(str(photo_bm)), caption=caption, reply_markup=keyboard)
    else:
        sent = await message.answer(caption, reply_markup=keyboard)
    _active_blackmarket[user_id] = {"stake": stake, "message_id": sent.message_id, "chat_id": chat_id, "bot": message.bot}


@router.callback_query(F.data.startswith("bm|"))
async def cb_blackmarket(callback: CallbackQuery):
    data = callback.data.split("|")
    if len(data) != 3:
        await callback.answer("Ошибка", show_alert=True)
        return
    _, uid_str, deal_id = data
    try:
        target_id = int(uid_str)
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    sess = _active_blackmarket.pop(target_id, None)
    if not sess:
        await callback.answer("Сделка уже закрыта.", show_alert=True)
        return

    stake = sess["stake"]
    deal = next((d for d in BLACKMARKET_DEALS if d["id"] == deal_id), None)
    if not deal:
        await callback.answer("Ошибка", show_alert=True)
        return

    # Анти-имба: если часто красный — выше шанс подставы
    red_count = _blackmarket_red_choices.get(target_id, 0)
    if deal_id == "red":
        _blackmarket_red_choices[target_id] = min(10, red_count + 1)
    else:
        _blackmarket_red_choices[target_id] = max(0, red_count - 1)

    podstva_extra = red_count * 0.04 if deal_id == "red" else 0
    podstva_chance = min(0.6, deal["podstva_chance"] + podstva_extra)
    win_chance = deal["win_chance"]
    r = game_random.random()
    if r < podstva_chance:
        penalty = int(stake * 1.5)
        extra = penalty - stake
        if extra > 0:
            await balance_service.subtract_balance(
                user_id=target_id, amount=extra,
                command_source="/blackmarket", comment="Подстава — доп. штраф",
                bot=callback.bot, chat_id=callback.message.chat.id,
                username=callback.from_user.username, first_name=callback.from_user.first_name,
                allow_negative=False
            )
        await db.log_game_session(target_id, "blackmarket", stake, "loss", -penalty, 0)
        await db.log_admin_game(target_id, callback.from_user.username or "", "/blackmarket", stake, "loss", -penalty, 0)
        balance_after = await db.get_balance(target_id)
        await _update_mmr_and_achievements(target_id, "blackmarket", "loss", balance_after)
        caption = format_message_with_username(
            f"🕳 <b>Скам.</b> Подстава — фейковый продавец. Потерял <b>{penalty}</b> коинов (ставка + штраф). Баланс: <b>{balance_after}</b>",
            callback.from_user.username or "", callback.from_user.first_name or ""
        )
        photo_res = config.get_image_path("blackmarket_scam.jpg")
    elif r < podstva_chance + (1 - podstva_chance) * (1 - win_chance):
        await db.log_game_session(target_id, "blackmarket", stake, "loss", -stake, 0)
        await db.log_admin_game(target_id, callback.from_user.username or "", "/blackmarket", stake, "loss", -stake, 0)
        balance_after = await db.get_balance(target_id)
        await _update_mmr_and_achievements(target_id, "blackmarket", "loss", balance_after)
        caption = format_message_with_username(
            f"🕳 Сделка провалилась — контрагент оказался фейковым продавцом. Минус <b>{stake}</b> коинов. Баланс: <b>{balance_after}</b>",
            callback.from_user.username or "", callback.from_user.first_name or ""
        )
        photo_res = config.get_image_path("blackmarket_scam.jpg")
    else:
        win_amount = int(stake * deal["mult"])
        await balance_service.add_game_win(
            user_id=target_id, gross_amount=win_amount,
            command_source="/blackmarket", comment="Сделка на чёрном рынке",
            bot=callback.bot, chat_id=callback.message.chat.id, username=callback.from_user.username, first_name=callback.from_user.first_name,
        )
        await db.log_game_session(target_id, "blackmarket", stake, "win", win_amount - stake, deal["mult"])
        await db.log_admin_game(target_id, callback.from_user.username or "", "/blackmarket", stake, "win", win_amount - stake, None)
        balance_after = await db.get_balance(target_id)
        await _update_mmr_and_achievements(target_id, "blackmarket", "win", balance_after)
        caption = format_message_with_username(
            f"🕳 <b>Удачный прокрут.</b> Сделка прошла. +<b>{win_amount}</b> коинов (x{deal['mult']}). Баланс: <b>{balance_after}</b>",
            callback.from_user.username or "", callback.from_user.first_name or ""
        )
        photo_res = config.get_image_path("blackmarket_win.jpg")

    bot = callback.bot
    chat_id = callback.message.chat.id
    msg_id_to_delete = callback.message.message_id
    try:
        if photo_res.exists():
            media = InputMediaPhoto(media=FSInputFile(str(photo_res)), caption=caption)
            await bot.edit_message_media(chat_id=chat_id, message_id=callback.message.message_id, media=media, reply_markup=None)
        else:
            await bot.edit_message_caption(chat_id=chat_id, message_id=callback.message.message_id, caption=caption, reply_markup=None)
    except Exception:
        if photo_res.exists():
            sent = await bot.send_photo(chat_id, FSInputFile(str(photo_res)), caption=caption)
        else:
            sent = await bot.send_message(chat_id, caption)
        msg_id_to_delete = sent.message_id
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, msg_id_to_delete, config.GAME_RESULT_DELETE_TIMEOUT))
    await callback.answer("Готово.")


# ---------- /topgame — Топ игр + тренды (как TikTok) ----------
TOP_GAME_COMMENTS = {
    "/gamerandom": "Хаос зашёл.",
    "/blackmarket": "Опасен, но популярен. Рынок помнит жадных.",
    "/echo": "Визитка бота. Игра, которая помнит тебя.",
    "/random": "Судьба технолога — мем и риск.",
    "/perekyp": "Перекуп держится в топе.",
    "/slot": "Классика. Часто крутят.",
    "/konopla": "Один раунд — крупный выигрыш или проигрыш.",
    "/kripta": "Забрать вовремя — искусство.",
    "/almaz": "Алмазы. Копать или забирать.",
    "/rulet": "Русская рулетка. Выбывание по одной.",
    "/frekaz": "Фреказ. Победитель по весу ставок.",
    "/fracture": "Излом решения. Цепочка выборов.",
    "/mirror": "Зеркало. Игра против своей копии.",
}


def _trend_label(total_24h: int, total_prev_24h: int) -> tuple:
    """Возвращает (эмодзи, короткий текст) для тренда: В тренде / Стабильно / Умирает."""
    if total_prev_24h == 0:
        return ("🔥", "В тренде") if total_24h > 0 else ("😐", "Стабильно")
    ratio = total_24h / total_prev_24h if total_prev_24h else 0
    if ratio >= 1.25:
        return ("🔥", "В тренде")
    if ratio <= 0.65:
        return ("🧊", "Падает")
    return ("😐", "Стабильно")


@router.message(Command("topgame"))
async def cmd_topgame(message: Message):
    """Топ игр + анализ трендов за 24ч: В тренде / Стабильно / Падает. Совет смотреть /news."""
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    rows = await db.get_top_games_stats_with_trend(8)
    if not rows:
        sent = await message.answer(format_message_with_username(
            "Пока нет статистики по играм. Поиграй в разные игры — и топ появится.", username, first_name))
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    lines = [
        "📊 <b>Топ игр</b>\n",
        "По количеству запусков за последние сутки. Тренд показывает, растёт интерес к игре или падает.\n",
        "🔥 в тренде  ·  😐 стабильно  ·  🧊 падает\n",
    ]
    for i, r in enumerate(rows, 1):
        cmd = r["command"] if isinstance(r["command"], str) and r["command"].startswith("/") else f"/{r['command']}"
        total = r["total"]
        total_24h = r.get("total_24h", 0) or 0
        total_prev_24h = r.get("total_prev_24h", 0) or 0
        wins = r["wins"]
        losses = r["losses"]
        emo, trend_text = _trend_label(total_24h, total_prev_24h)
        comm = TOP_GAME_COMMENTS.get(cmd, "В топе по активности.")
        win_pct = int(100 * wins / total) if total else 0
        lines.append(f"{i}. {emo} <b>{cmd}</b> — {trend_text}\n   Всего игр: {total}, побед: {win_pct}%. {comm}")
    lines.append("\n💡 Подсказка: в /news иногда меняются условия в играх — заглядывай перед ставкой.")
    caption = format_message_with_username("\n".join(lines), username, first_name)
    sent = await message.answer(caption)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


# ---------- /echo — Эхо решений (архетипы, углублённый анализ, подстройка бота) ----------
_last_echo_archetype: Dict[int, str] = {}  # user_id -> archetype_id для /random и /gamerandom
_last_echo_analysis: Dict[int, Dict] = {}  # user_id -> полный анализ для подстройки сообщений

ECHO_ARCHETYPES = {
    "strategist": {"label": "🧠 Стратег", "desc": "Ты просчитываешь ходы. Средние ставки, стабильный результат. Расчёт в плюсе.", "hint": "Эхо помнит: ты играешь расчётливо."},
    "gambling": {"label": "🎰 Азартный", "desc": "Ты часто рискуешь и редко отступаешь. Крупные ставки, ва-банк. Эхо это помнит.", "hint": "Эхо помнит: ты идёшь ва-банк."},
    "cautious": {"label": "🐀 Осторожный", "desc": "Маленькие ставки, много побед. Эхо видит тебя надёжным.", "hint": "Эхо помнит: ты не спешишь рисковать."},
    "chaotic": {"label": "🧨 Хаотичный", "desc": "Непредсказуемая манера. То осторожно, то ва-банк. Эхо не уверено в тебе.", "hint": "Эхо помнит: твой стиль меняется."},
    "overconfident": {"label": "👑 Самоуверенный", "desc": "После серии побед ты заходишь слишком далеко. Эхо предупреждает.", "hint": "Эхо помнит: после побед ты задираешь ставки."},
}

# Человекочитаемые названия игр для подписи (остальные — slug как есть)
ECHO_GAME_NAMES = {
    "slot": "слот", "konopla": "конопля", "kripta": "Lucky Jet", "almaz": "алмазы",
    "rulet": "рулетка", "frekaz": "фреказ", "perekyp": "перекуп", "random": "судьба", "gamerandom": "сбой матрицы",
    "blackmarket": "чёрный рынок", "echo": "эхо", "fracture": "излом", "mirror": "зеркало",
    "reactor": "реактор", "vault": "хранилище", "dicepath": "кубик",
}


def _echo_archetype(sessions: list) -> tuple:
    """По последним 10–20 играм: архетип (id, label, desc), avg_bet, win_rate. Возвращает 5 элементов."""
    if not sessions or len(sessions) < 2:
        a = ECHO_ARCHETYPES["chaotic"]
        return "chaotic", a["label"], a["desc"], 0, 0.0
    wins = sum(1 for s in sessions if s.get("result") == "win")
    losses = len(sessions) - wins
    total_bet = sum(s.get("bet", 0) for s in sessions)
    avg_bet = total_bet / len(sessions)
    win_rate = wins / len(sessions)
    big_losses = sum(1 for s in sessions if s.get("result") == "loss" and abs(s.get("amount_change", 0)) > avg_bet * 1.5)
    recent_wins = sum(1 for s in sessions[:5] if s.get("result") == "win")
    if big_losses >= 4 and losses > wins:
        a = ECHO_ARCHETYPES["gambling"]
        return "gambling", a["label"], a["desc"], int(avg_bet), win_rate
    if wins >= 6 and avg_bet <= 200:
        a = ECHO_ARCHETYPES["cautious"]
        return "cautious", a["label"], a["desc"], int(avg_bet), win_rate
    if recent_wins >= 4 and avg_bet > 300:
        a = ECHO_ARCHETYPES["overconfident"]
        return "overconfident", a["label"], a["desc"], int(avg_bet), win_rate
    if 0.45 <= win_rate <= 0.55 and avg_bet >= 100:
        a = ECHO_ARCHETYPES["strategist"]
        return "strategist", a["label"], a["desc"], int(avg_bet), win_rate
    a = ECHO_ARCHETYPES["chaotic"]
    return "chaotic", a["label"], a["desc"], int(avg_bet), win_rate


def _echo_player_analysis(sessions: list) -> Dict:
    """
    Углублённый анализ игрока по последним играм: архетип, разнообразие, доминирующая игра,
    риск (0–1), подпись стиля. Используется в /echo и для подстройки текстов бота.
    """
    arch_id, label, desc, avg_bet, win_rate = _echo_archetype(sessions)
    unique_types = list({s.get("game_type") for s in sessions if s.get("game_type")})
    variety = len(unique_types)
    from collections import Counter
    types_counts = Counter(s.get("game_type") for s in sessions if s.get("game_type"))
    dominant = types_counts.most_common(1)[0][0] if types_counts else None
    dominant_name = ECHO_GAME_NAMES.get(dominant, dominant) if dominant else None
    # Риск: высокие ставки и проигрыши = высокий риск
    bets = [s.get("bet", 0) for s in sessions if s.get("bet", 0) > 0]
    bet_var = (max(bets) - min(bets)) / max(bets, default=1) if bets else 0
    risk_score = min(1.0, (avg_bet / 500.0) * 0.5 + (1 - win_rate) * 0.3 + bet_var * 0.2) if sessions else 0.5
    # Подпись стиля (одна строка)
    if variety <= 2 and dominant_name:
        signature = f"Чаще всего: {dominant_name}. Меняй игру — больше прогресс по MMR."
    elif variety >= 5:
        signature = f"Разнообразная манера — {variety} разных игр. Эхо это ценит."
    elif arch_id == "cautious":
        signature = "Маленькие ставки, стабильный результат. Эхо видит тебя надёжным."
    elif arch_id == "gambling":
        signature = "Крупные ставки, ва-банк. Эхо предупреждает: разнообразие даёт больше MMR."
    else:
        signature = ECHO_ARCHETYPES.get(arch_id, {}).get("hint", "Эхо следит за твоим стилем.")
    return {
        "archetype_id": arch_id,
        "archetype_label": label,
        "archetype_desc": desc,
        "avg_bet": int(avg_bet),
        "win_rate": win_rate,
        "variety": variety,
        "dominant_game": dominant,
        "dominant_game_name": dominant_name,
        "risk_score": round(risk_score, 2),
        "signature": signature,
        "games_analyzed": len(sessions),
    }


ECHO_FORTUNE_LUCKY = [
    "Тебе сегодня будет вести.",
    "Сегодня удача на твоей стороне.",
    "День подходит для риска.",
]
ECHO_FORTUNE_UNLUCKY = [
    "Тебе сегодня не особо повезёт.",
    "Лучше играть осторожнее.",
    "Сегодня день не твой.",
]


@router.message(Command("echo"))
async def cmd_echo(message: Message):
    """Рек как в TikTok: система «думает кто ты», первый раз за сутки — 50 коинов, потом только описание архетипа и прогноз на день (везёт / не везёт). Игра не проводится."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    chat_id = message.chat.id
    bot = message.bot

    today = int(datetime.now().strftime("%Y%m%d"))
    last_reward = await db.get_echo_last_reward_date(user_id)
    give_reward = last_reward != today
    if give_reward:
        await db.set_echo_reward_date(user_id, today)
        await balance_service.add_balance(
            user_id=user_id, amount=50,
            command_source="/echo", comment="Эхо: награда за первый запуск за сутки",
            bot=bot, chat_id=chat_id, username=username, first_name=first_name,
        )

    # «Система думает кто ты» — одна фотка echo.jpg
    think_cap = format_message_with_username(
        "🔮 Система думает, кто ты…",
        username, first_name
    )
    photo_echo = config.get_image_path("echo.jpg")
    if photo_echo.exists():
        sent_think = await message.answer_photo(FSInputFile(str(photo_echo)), caption=think_cap)
    else:
        sent_think = await message.answer(think_cap)
    await asyncio.sleep(2)

    last_sessions = await db.get_last_game_sessions(user_id, 20)
    analysis = _echo_player_analysis(last_sessions)
    _last_echo_archetype[user_id] = analysis["archetype_id"]
    _last_echo_analysis[user_id] = analysis

    archetype_id = analysis["archetype_id"]
    label = analysis["archetype_label"]
    desc = analysis["archetype_desc"]
    avg_bet = analysis["avg_bet"]
    win_rate = analysis["win_rate"]
    variety = analysis["variety"]
    dominant_name = analysis.get("dominant_game_name")
    signature = analysis.get("signature", "")

    # Прогноз: везёт или нет (с учётом архетипа и risk_score)
    if archetype_id == "cautious":
        fortune = game_random.choice(ECHO_FORTUNE_LUCKY + ECHO_FORTUNE_LUCKY + ECHO_FORTUNE_UNLUCKY)
    elif archetype_id == "gambling":
        fortune = game_random.choice(ECHO_FORTUNE_UNLUCKY + ECHO_FORTUNE_UNLUCKY + ECHO_FORTUNE_LUCKY)
    elif analysis.get("risk_score", 0.5) > 0.6:
        fortune = game_random.choice(ECHO_FORTUNE_UNLUCKY + ECHO_FORTUNE_LUCKY)
    else:
        fortune = game_random.choice(ECHO_FORTUNE_LUCKY + ECHO_FORTUNE_UNLUCKY)

    variety_line = f"🎮 Разнообразие: <b>{variety}</b> разных игр за последние {analysis['games_analyzed']}."
    if dominant_name:
        variety_line += f" Чаще всего: <b>{dominant_name}</b>."
    result_cap = format_message_with_username(
        f"🔮 <b>Кто ты</b>\n\n{label}.\n{desc}\n\n"
        f"📊 Средняя ставка ~{avg_bet} коинов, доля побед ~{int(win_rate*100)}%.\n"
        f"{variety_line}\n\n"
        f"📌 {signature}\n\n"
        f"🔮 {fortune}"
        + (f"\n\n✅ За первый запуск сегодня: +50 коинов." if give_reward else "\n\nПовторный запуск сегодня — только описание, без награды."),
        username, first_name
    )
    try:
        if sent_think.photo:
            await sent_think.edit_caption(caption=result_cap)
        else:
            await sent_think.edit_text(result_cap)
    except Exception:
        await bot.send_message(chat_id, result_cap)
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, sent_think.message_id, config.GAME_RESULT_DELETE_TIMEOUT))


# ---------- /fracture — Излом решения (10 вопросов 7–9 класс, таймер 30 сек, 3 жизни) ----------
FRACTURE_QUESTION_TIMEOUT_SEC = 30
FRACTURE_LIVES = 3
FRACTURE_LOADING_TEXTS = [
    "Подготовка теста…",
    "Генерация вопросов…",
    "Перелистывание заданий…",
    "Почти готово…",
]
FRACTURE_NUM_STEPS = 10


def _build_fracture_questions() -> list:
    """Случайные 10 вопросов без повторов. Варианты ответов перемешаны (анти-абуз: ИИ не может полагаться на фикс. индекс)."""
    pool = list(FRACTURE_QUESTIONS_POOL)
    game_random.shuffle(pool)
    result = []
    for (q_text, options, correct_idx) in pool[:FRACTURE_NUM_STEPS]:
        opts = list(options)
        game_random.shuffle(opts)
        new_correct = opts.index(options[correct_idx])
        result.append((q_text, opts, new_correct))
    return result


async def _fracture_timeout_task(user_id: int, step_at_start: int):
    """Таймер 30 сек на вопрос: если игрок не ответил — минус жизнь или проигрыш."""
    await asyncio.sleep(FRACTURE_QUESTION_TIMEOUT_SEC)
    sess = _active_fracture_sessions.get(user_id)
    if not sess or len(sess.get("answers", [])) != step_at_start:
        return
    sess = _active_fracture_sessions.pop(user_id, None)
    if not sess:
        return
    bet, questions, chat_id, username, first_name, bot = (
        sess["bet"], sess["questions"], sess["chat_id"],
        sess.get("username", ""), sess.get("first_name", ""), sess.get("bot"),
    )
    lives = sess.get("lives", FRACTURE_LIVES) - 1
    message_id = sess.get("message_id")
    if bot is None:
        return
    wrong_idx = 0
    new_answers = sess.get("answers", []) + [wrong_idx]
    if lives <= 0:
        await db.log_game_session(user_id, "fracture", bet, "loss", -bet, 0)
        await db.log_admin_game(user_id, username, "/fracture", bet, "loss", -bet, 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "fracture", "loss", balance_after)
        caption = format_message_with_username(
            f"🧩 <b>Излом решения</b>\n\n⏱ Время вышло. Жизней не осталось — проигрыш.\n\n❌ Минус <b>{bet}</b> коинов. Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=caption, reply_markup=None, parse_mode="HTML")
        except Exception:
            try:
                sent = await bot.send_message(chat_id, caption, parse_mode="HTML")
                asyncio.create_task(delete_message_after_by_id(bot, chat_id, sent.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
            except Exception:
                pass
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, message_id, config.GAME_RESULT_DELETE_TIMEOUT))
        return
    next_step = len(new_answers)
    if next_step >= FRACTURE_NUM_STEPS:
        correct = sum(1 for i, idx in enumerate(new_answers) if i < len(questions) and questions[i][2] == idx)
        win_chance = 0.22 + 0.05 * correct
        try:
            win_chance = await calculate_win_chance_async(win_chance, user_id, "fracture")
        except Exception:
            pass
        mult_min, mult_max = 1.15 + correct * 0.08, 1.6 + correct * 0.12
        mult_min, mult_max = min(1.8, mult_min), min(2.5, mult_max)
        won = game_random.random() < win_chance
        style_comment = f"Правильных: <b>{correct}</b> из {FRACTURE_NUM_STEPS}. Часть — по таймауту."
        if won:
            mult = round(game_random.uniform(mult_min, mult_max), 2)
            try:
                ev = await events_service.get_active_event(user_id)
                ev_type = ev.get("event_type") if ev else None
                mult = events_service.apply_event_to_multiplier(mult, ev_type, is_win=True)
            except Exception:
                pass
            mult = _apply_bet_penalty(bet, mult)
            win_amount = max(1, int(bet * mult))
            success, balance_before, balance_after, tax = await balance_service.add_game_win(user_id=user_id, gross_amount=win_amount, command_source="/fracture", comment="Излом (финал по таймауту)", bot=bot, chat_id=chat_id, username=username, first_name=first_name)
            if not success:
                await balance_service.add_balance(user_id, bet, command_source="/fracture", comment="Возврат ставки (излом, таймаут)", bot=bot, chat_id=chat_id, username=username, first_name=first_name)
                balance_after = await db.get_balance(user_id)
                net_added = bet
            else:
                net_added = balance_after - balance_before
            await db.log_game_session(user_id, "fracture", bet, "win", net_added - bet, mult)
            await db.log_admin_game(user_id, username, "/fracture", bet, "win", net_added - bet, tax or 0)
            await _update_mmr_and_achievements(user_id, "fracture", "win", balance_after, chat_id=chat_id, bot=bot)
            await db.add_cup_win(user_id, "fracture")
            caption = format_message_with_username(f"🧩 <b>Излом решения</b>\n\n{style_comment}\n\n✅ Зачислено <b>+{net_added}</b> коинов (x{mult:.2f}). Баланс: <b>{balance_after}</b>", username, first_name)
        else:
            await db.log_game_session(user_id, "fracture", bet, "loss", -bet, 0)
            await db.log_admin_game(user_id, username, "/fracture", bet, "loss", -bet, 0)
            balance_after = await db.get_balance(user_id)
            await _update_mmr_and_achievements(user_id, "fracture", "loss", balance_after)
            caption = format_message_with_username(f"🧩 <b>Излом решения</b>\n\n{style_comment}\n\n❌ Минус <b>{bet}</b> коинов. Баланс: <b>{balance_after}</b>", username, first_name)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=caption, reply_markup=None, parse_mode="HTML")
        except Exception:
            try:
                sent = await bot.send_message(chat_id, caption, parse_mode="HTML")
                asyncio.create_task(delete_message_after_by_id(bot, chat_id, sent.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
            except Exception:
                pass
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, message_id, config.GAME_RESULT_DELETE_TIMEOUT))
        return
    q_data = questions[next_step]
    q_text, options, _ = q_data
    q_safe = html.escape(q_text)
    cap = format_message_with_username(
        f"🧩 <b>Излом решения</b>\n\n⏱ Время вышло. Жизней: ❤️×{lives}\n\n💰 Ставка: <b>{bet}</b> коинов.\n\n<b>Вопрос {next_step + 1}/{FRACTURE_NUM_STEPS}</b>\n{q_safe}",
        username, first_name
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=opt, callback_data=f"fracture:{next_step}:{i}")] for i, opt in enumerate(options)
    ])
    new_sess = {
        "bet": bet, "questions": questions, "answers": new_answers,
        "message_id": message_id, "chat_id": chat_id, "username": username, "first_name": first_name, "bot": bot,
        "lives": lives,
    }
    _active_fracture_sessions[user_id] = new_sess
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=cap, reply_markup=kb, parse_mode="HTML")
    except Exception:
        sent = await bot.send_message(chat_id, cap, reply_markup=kb)
        new_sess["message_id"] = sent.message_id
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, message_id, 5))
    new_sess["timer_task"] = asyncio.create_task(_fracture_timeout_task(user_id, next_step))


@router.message(Command("fracture"))
async def cmd_fracture(message: Message):
    """Излом решения: ставка, 10 вопросов, 3 жизни, 1 мин на ответ. Таймаут или много ошибок — проигрыш."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    chat_id = message.chat.id
    if user_id in _active_fracture_sessions:
        await message.answer(
            format_message_with_username(
                "У тебя уже запущен тест «Излом решения». Ответь на вопросы в том сообщении.",
                username, first_name
            )
        )
        return
    balance = await db.get_balance(user_id)
    parts = (message.text or "").strip().split()
    if len(parts) >= 2:
        try:
            stake = int(parts[1])
        except ValueError:
            stake = max(100, min(int(balance * 0.03), 3000))
    else:
        stake = max(100, min(int(balance * game_random.uniform(0.02, 0.05)), 3000))
    if stake < 100:
        await message.answer(format_message_with_username("Минимум 100 коинов для излома.", username, first_name))
        return
    if balance < stake:
        await message.answer(format_message_with_username("Недостаточно коинов.", username, first_name))
        return
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=stake,
        command_source="/fracture", comment="Излом решения",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return
    await _maybe_send_event_message(user_id, chat_id, message.bot, balance=balance)

    questions = _build_fracture_questions()
    loading_cap = format_message_with_username(
        f"🧩 <b>Излом решения</b>\n\n{FRACTURE_LOADING_TEXTS[0]}\n\n💰 Ставка: <b>{stake}</b> коинов.",
        username, first_name
    )
    sent = await message.answer(loading_cap)
    for i, txt in enumerate(FRACTURE_LOADING_TEXTS[1:], 1):
        await asyncio.sleep(0.8)
        cap = format_message_with_username(
            f"🧩 <b>Излом решения</b>\n\n{txt}\n\n💰 Ставка: <b>{stake}</b> коинов.",
            username, first_name
        )
        try:
            await sent.edit_text(cap)
        except Exception:
            pass
    await asyncio.sleep(0.5)

    q0 = questions[0]
    question_text, options, _ = q0
    question_safe = html.escape(question_text)
    caption = format_message_with_username(
        f"🧩 <b>Излом решения</b>\n\n❤️ Жизней: {FRACTURE_LIVES}  ·  ⏱ На ответ: {FRACTURE_QUESTION_TIMEOUT_SEC} сек\n\n💰 Ставка: <b>{stake}</b> коинов.\n\n<b>Вопрос 1/{FRACTURE_NUM_STEPS}</b>\n{question_safe}",
        username, first_name
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=opt, callback_data=f"fracture:0:{i}")] for i, opt in enumerate(options)
    ])
    photo_start = config.get_game_image_path("fracture", "start")
    if photo_start.exists():
        loading_msg_id = sent.message_id
        try:
            sent = await message.answer_photo(FSInputFile(str(photo_start)), caption=caption, reply_markup=kb, parse_mode="HTML")
            asyncio.create_task(delete_message_after_by_id(message.bot, chat_id, loading_msg_id, 3))
        except Exception:
            sent = await message.answer(caption, reply_markup=kb, parse_mode="HTML")
    else:
        try:
            await sent.edit_text(caption, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            sent = await message.answer(caption, reply_markup=kb, parse_mode="HTML")
    timer_task = asyncio.create_task(_fracture_timeout_task(user_id, 0))
    _active_fracture_sessions[user_id] = {
        "bet": stake, "questions": questions, "answers": [], "message_id": sent.message_id, "chat_id": chat_id,
        "username": username, "first_name": first_name, "bot": message.bot,
        "lives": FRACTURE_LIVES, "timer_task": timer_task,
    }


@router.callback_query(F.data.startswith("fracture:"))
async def cb_fracture(callback: CallbackQuery):
    """Обработка ответов: отмена таймера, учёт жизней, следующий вопрос или финал."""
    user_id = callback.from_user.id
    if user_id not in _active_fracture_sessions:
        await _safe_callback_answer(callback, "Тест уже завершён. Запусти /fracture заново.")
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await _safe_callback_answer(callback, "Ошибка данных.")
        return
    try:
        step = int(parts[1])
        choice_idx = int(parts[2])
    except (ValueError, IndexError):
        await _safe_callback_answer(callback, "Ошибка данных.")
        return
    sess = _active_fracture_sessions[user_id]
    if sess.get("timer_task"):
        try:
            sess["timer_task"].cancel()
        except Exception:
            pass
    answers = sess["answers"]
    questions = sess["questions"]
    if step != len(answers):
        await _safe_callback_answer(callback, "Сначала выбери ответ на текущий вопрос.")
        return
    if choice_idx < 0 or choice_idx >= 4:
        await _safe_callback_answer(callback, "Неверный вариант.")
        return
    is_correct = questions[step][2] == choice_idx
    lives = sess.get("lives", FRACTURE_LIVES)
    if not is_correct:
        lives -= 1
    answers.append(choice_idx)
    sess["lives"] = lives
    bet = sess["bet"]
    chat_id = sess["chat_id"]
    username = sess.get("username", "")
    first_name = sess.get("first_name", "")
    bot = sess.get("bot") or callback.bot
    msg_id = sess.get("message_id")

    if lives <= 0 and not is_correct:
        _active_fracture_sessions.pop(user_id, None)
        await db.log_game_session(user_id, "fracture", bet, "loss", -bet, 0)
        await db.log_admin_game(user_id, username, "/fracture", bet, "loss", -bet, 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "fracture", "loss", balance_after)
        caption = format_message_with_username(
            f"🧩 <b>Излом решения</b>\n\n❌ Неверный ответ. Жизней не осталось — проигрыш.\n\nМинус <b>{bet}</b> коинов. Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=callback.message.message_id, text=caption, reply_markup=None, parse_mode="HTML")
        except Exception:
            try:
                sent = await bot.send_message(chat_id, caption, parse_mode="HTML")
                asyncio.create_task(delete_message_after_by_id(bot, chat_id, sent.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
            except Exception:
                pass
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, callback.message.message_id, config.GAME_RESULT_DELETE_TIMEOUT))
        await _safe_callback_answer(callback, "Проигрыш.")
        return

    if len(answers) < FRACTURE_NUM_STEPS:
        n = len(answers) + 1
        q_data = questions[len(answers)]
        question_text, options, _ = q_data
        question_safe = html.escape(question_text)
        caption = format_message_with_username(
            f"🧩 <b>Излом решения</b>\n\n{'✅ Верно!' if is_correct else '❌ Неверно.'}  ❤️ Жизней: {lives}  ·  ⏱ {FRACTURE_QUESTION_TIMEOUT_SEC} сек на ответ\n\n💰 Ставка: <b>{bet}</b> коинов.\n\n<b>Вопрос {n}/{FRACTURE_NUM_STEPS}</b>\n{question_safe}",
            username, first_name
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=opt, callback_data=f"fracture:{len(answers)}:{i}")] for i, opt in enumerate(options)
        ])
        try:
            await callback.message.edit_text(caption, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                new_msg = await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode="HTML")
                sess["message_id"] = new_msg.message_id
                asyncio.create_task(delete_message_after_by_id(bot, chat_id, callback.message.message_id, 5))
            except TelegramBadRequest:
                pass
        sess["timer_task"] = asyncio.create_task(_fracture_timeout_task(user_id, len(answers)))
        await _safe_callback_answer(callback, "Верно!" if is_correct else "Неверно…")
        return

    _active_fracture_sessions.pop(user_id, None)
    correct = sum(1 for i, idx in enumerate(answers) if questions[i][2] == idx)
    win_chance = 0.22 + 0.05 * correct
    win_chance = await calculate_win_chance_async(win_chance, user_id, "fracture")
    mult_min, mult_max = 1.15 + correct * 0.08, 1.6 + correct * 0.12
    mult_min, mult_max = min(1.8, mult_min), min(2.5, mult_max)
    won = game_random.random() < win_chance
    style_comment = f"Правильных ответов: <b>{correct}</b> из {FRACTURE_NUM_STEPS}."
    if won:
        mult = round(game_random.uniform(mult_min, mult_max), 2)
        ev = await events_service.get_active_event(user_id)
        ev_type = ev.get("event_type") if ev else None
        mult = events_service.apply_event_to_multiplier(mult, ev_type, is_win=True)
        mult = _apply_bet_penalty(bet, mult)
        win_amount = max(1, int(bet * mult))
        success, balance_before, balance_after, tax = await balance_service.add_game_win(
            user_id=user_id, gross_amount=win_amount,
            command_source="/fracture", comment="Излом решения",
            bot=bot, chat_id=chat_id, username=username, first_name=first_name,
        )
        if not success:
            await balance_service.add_balance(user_id, bet, command_source="/fracture", comment="Возврат ставки (излом)", bot=bot, chat_id=chat_id, username=username, first_name=first_name)
            balance_after = await db.get_balance(user_id)
            net_added = bet
        else:
            net_added = balance_after - balance_before
        await db.log_game_session(user_id, "fracture", bet, "win", net_added - bet, mult)
        await db.log_admin_game(user_id, username, "/fracture", bet, "win", net_added - bet, tax or 0)
        await _update_mmr_and_achievements(user_id, "fracture", "win", balance_after, chat_id=chat_id, bot=bot)
        await db.add_cup_win(user_id, "fracture")
        caption = format_message_with_username(
            f"🧩 <b>Излом решения</b>\n\n{style_comment}\n\n✅ Зачислено <b>+{net_added}</b> коинов (x{mult:.2f}). Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        photo = config.get_game_image_path("fracture", "win")
    else:
        await db.log_game_session(user_id, "fracture", bet, "loss", -bet, 0)
        await db.log_admin_game(user_id, username, "/fracture", bet, "loss", -bet, 0)
        balance_after = await db.get_balance(user_id)
        await _update_mmr_and_achievements(user_id, "fracture", "loss", balance_after)
        caption = format_message_with_username(
            f"🧩 <b>Излом решения</b>\n\n{style_comment}\n\n❌ Минус <b>{bet}</b> коинов. Баланс: <b>{balance_after}</b>",
            username, first_name
        )
        photo = config.get_game_image_path("fracture", "lose")
    result_msg_id = callback.message.message_id
    try:
        if photo.exists():
            media = InputMediaPhoto(media=FSInputFile(str(photo)), caption=caption)
            await bot.edit_message_media(chat_id=chat_id, message_id=callback.message.message_id, media=media, reply_markup=None)
        else:
            await callback.message.edit_text(caption, reply_markup=None)
    except TelegramBadRequest:
        if photo.exists():
            sent = await bot.send_photo(chat_id, FSInputFile(str(photo)), caption=caption)
        else:
            sent = await bot.send_message(chat_id, caption)
        result_msg_id = sent.message_id
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, callback.message.message_id, 5))
    asyncio.create_task(delete_message_after_by_id(bot, chat_id, result_msg_id, config.GAME_RESULT_DELETE_TIMEOUT))
    await callback.answer("Результат готов.")


# ---------- /mirror — Buckshot Roulette 1 в 1: 8 патронов, 2 жизни, ходы игрок/дилер, перезарядка ----------
# _active_mirror_sessions объявлен в начале файла
MIRROR_LOADING_TEXTS = ["Револьвер заряжается…", "Патроны в барабане…", "Зеркало готово к дуэли…"]

MIRROR_LIVES = 2
MIRROR_MAGAZINE_SIZE = 8


def _mirror_new_magazine() -> list:
    """Обойма из 8 патронов: 4 боевых, 4 холостых в случайном порядке."""
    arr = [True] * 4 + [False] * 4
    game_random.shuffle(arr)
    return arr


def _mirror_caption(sess: dict, extra: str = "") -> str:
    """Текст состояния: жизни, чей ход, доп. строка."""
    pl = sess.get("player_lives", 2)
    dl = sess.get("dealer_lives", 2)
    turn = sess.get("turn", "player")
    stake = sess.get("stake", 0)
    un = sess.get("first_name", "") or sess.get("username", "")
    base = (
        f"🪞 <b>Зеркало</b> (Buckshot)\n\n"
        f"Ты: {'❤️' * pl}{'🖤' * (2 - pl)}  |  Дилер: {'❤️' * dl}{'🖤' * (2 - dl)}\n\n"
        f"💰 Ставка: <b>{stake}</b> коинов.\n\n"
    )
    if extra:
        base += extra + "\n\n"
    if turn == "player":
        base += "Твой ход. Выбери: в себя или в дилера."
    return format_message_with_username(base, sess.get("username", ""), sess.get("first_name", "") or un)


@router.message(Command("mirror"))
async def cmd_mirror(message: Message):
    """Зеркало — Buckshot Roulette 1 в 1: 8 патронов, по 2 жизни, ходы по очереди, перезарядка при пустой обойме."""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    chat_id = message.chat.id
    balance = await db.get_balance(user_id)
    if balance < 100:
        await message.answer(format_message_with_username(
            "Для «Зеркала» нужен минимум 100 коинов. Набери баланс и возвращайся.", username, first_name))
        return
    stake = max(100, min(int(balance * game_random.uniform(0.02, 0.05)), 2000))
    if balance < stake:
        await message.answer(format_message_with_username("Недостаточно коинов.", username, first_name))
        return
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=user_id, amount=stake,
        command_source="/mirror", comment="Зеркало",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return
    await _maybe_send_event_message(user_id, chat_id, message.bot, balance=balance)

    loading_cap = format_message_with_username(
        f"🪞 <b>Зеркало</b>\n\n{MIRROR_LOADING_TEXTS[0]}\n\n💰 Ставка: <b>{stake}</b> коинов.",
        username, first_name
    )
    sent = await message.answer(loading_cap)
    for txt in MIRROR_LOADING_TEXTS[1:]:
        await asyncio.sleep(1)
        cap = format_message_with_username(
            f"🪞 <b>Зеркало</b>\n\n{txt}\n\n💰 Ставка: <b>{stake}</b> коинов.",
            username, first_name
        )
        try:
            await sent.edit_text(cap)
        except Exception:
            pass
    await asyncio.sleep(0.6)

    magazine = _mirror_new_magazine()
    sess = {
        "stake": stake,
        "magazine": magazine,
        "index": 0,
        "player_lives": MIRROR_LIVES,
        "dealer_lives": MIRROR_LIVES,
        "turn": "player",
        "message_id": None,
        "chat_id": chat_id,
        "bot": message.bot,
        "username": username,
        "first_name": first_name,
    }
    caption = _mirror_caption(sess)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔫 В себя", callback_data=f"mirror|{user_id}|self")],
        [InlineKeyboardButton(text="🎯 В дилера", callback_data=f"mirror|{user_id}|opp")],
    ])
    photo_start = config.get_game_image_path("mirror", "start")
    if photo_start.exists():
        loading_msg_id = sent.message_id
        try:
            sent = await message.answer_photo(FSInputFile(str(photo_start)), caption=caption, reply_markup=kb)
            asyncio.create_task(delete_message_after_by_id(message.bot, chat_id, loading_msg_id, 2))
        except Exception:
            try:
                await sent.edit_text(caption, reply_markup=kb)
            except TelegramBadRequest:
                sent = await message.answer(caption, reply_markup=kb)
    else:
        try:
            await sent.edit_text(caption, reply_markup=kb)
        except TelegramBadRequest:
            sent = await message.answer(caption, reply_markup=kb)
    sess["message_id"] = sent.message_id
    _active_mirror_sessions[user_id] = sess


def _mirror_advance_and_reload(sess: dict) -> None:
    """Увеличить индекс; при пустой обойме — перезарядка (новая 8)."""
    sess["index"] = sess.get("index", 0) + 1
    if sess["index"] >= len(sess.get("magazine", [])):
        sess["magazine"] = _mirror_new_magazine()
        sess["index"] = 0


@router.callback_query(F.data.startswith("mirror|"))
async def cb_mirror(callback: CallbackQuery):
    data = callback.data.split("|")
    if len(data) != 3:
        await _safe_callback_answer(callback, "Ошибка", show_alert=True)
        return
    try:
        uid = int(data[1])
    except ValueError:
        await _safe_callback_answer(callback, "Ошибка", show_alert=True)
        return
    if callback.from_user.id != uid:
        await _safe_callback_answer(callback, "Не твоя дуэль.", show_alert=True)
        return
    action = data[2]
    if action not in ("self", "opp"):
        await _safe_callback_answer(callback, "Неизвестное действие.", show_alert=True)
        return
    sess = _active_mirror_sessions.get(uid)
    if not sess:
        await _safe_callback_answer(callback, "Раунд уже завершён.", show_alert=True)
        return
    # Ответ сразу, чтобы Telegram не закрыл callback по таймауту
    await _safe_callback_answer(callback, "")
    bot = sess.get("bot") or callback.bot
    chat_id = sess["chat_id"]
    username = sess.get("username", "")
    first_name = sess.get("first_name", "")
    stake = sess["stake"]
    magazine = sess["magazine"]
    idx = sess["index"]
    if idx >= len(magazine):
        _mirror_advance_and_reload(sess)
        magazine = sess["magazine"]
        idx = sess["index"]
    bullet = magazine[idx]
    msg_id = sess.get("message_id")
    game_over = None  # "win" | "loss" | None

    if action == "self":
        if bullet:
            sess["player_lives"] = sess.get("player_lives", 2) - 1
            if sess["player_lives"] <= 0:
                game_over = "loss"
        # холостой в себя — дополнительный ход игрока (ничего не меняем по turn)
        _mirror_advance_and_reload(sess)
    else:
        if bullet:
            sess["dealer_lives"] = sess.get("dealer_lives", 2) - 1
            if sess["dealer_lives"] <= 0:
                game_over = "win"
            _mirror_advance_and_reload(sess)
        else:
            _mirror_advance_and_reload(sess)
            sess["turn"] = "dealer"
            mag = sess["magazine"]
            i = sess["index"]
            if i < len(mag):
                dealer_bullet = mag[i]
                sess["_last_dealer_live"] = dealer_bullet
                if dealer_bullet:
                    sess["player_lives"] = sess.get("player_lives", 2) - 1
                    if sess["player_lives"] <= 0:
                        game_over = "loss"
                else:
                    sess["turn"] = "player"
                _mirror_advance_and_reload(sess)
            else:
                sess["turn"] = "player"
                sess["_last_dealer_live"] = False

    if game_over:
        _active_mirror_sessions.pop(uid, None)
        if game_over == "win":
            win_amount = stake * 2
            await balance_service.add_game_win(
                user_id=uid, gross_amount=win_amount,
                command_source="/mirror", comment="Зеркало — победа (Buckshot)",
                bot=bot, chat_id=chat_id, username=username, first_name=first_name,
            )
            await db.log_game_session(uid, "mirror", stake, "win", win_amount - stake, 2.0)
            await db.log_admin_game(uid, username, "/mirror", stake, "win", win_amount - stake, None)
            balance_after = await db.get_balance(uid)
            await _update_mmr_and_achievements(uid, "mirror", "win", balance_after)
            caption = await format_message_game_result_async(
                f"вы выиграли. 🪞 <b>Зеркало</b> — дилер повержен. ✅ +<b>{win_amount}</b> коинов (x2). Баланс: <b>{balance_after}</b>",
                uid
            )
            photo = config.get_game_image_path("mirror", "win")
        else:
            await db.log_game_session(uid, "mirror", stake, "loss", -stake, 0)
            await db.log_admin_game(uid, username, "/mirror", stake, "loss", -stake, 0)
            balance_after = await db.get_balance(uid)
            await _update_mmr_and_achievements(uid, "mirror", "loss", balance_after)
            caption = await format_message_game_result_async(
                f"вы проиграли. 🪞 <b>Зеркало</b> — дилер выиграл. ❌ Минус <b>{stake}</b> коинов. Баланс: <b>{balance_after}</b>",
                uid
            )
            photo = config.get_game_image_path("mirror", "lose")
        try:
            if photo.exists():
                media = InputMediaPhoto(media=FSInputFile(str(photo)), caption=caption)
                await bot.edit_message_media(chat_id=chat_id, message_id=msg_id, media=media, reply_markup=None)
            else:
                await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=None)
        except Exception:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=None)
            except Exception:
                await bot.send_message(chat_id, caption)
        asyncio.create_task(delete_message_after_by_id(bot, chat_id, msg_id, config.GAME_RESULT_DELETE_TIMEOUT))
        return

    # Продолжаем: обновить сообщение с жизнями и кнопками (если ход игрока)
    extra = ""
    if action == "self":
        extra = "Выстрел в себя — " + ("боевой. Минус жизнь." if bullet else "холостой. Ещё ход.")
    else:
        if bullet:
            extra = "Выстрел в дилера — боевой. Дилер теряет жизнь."
        else:
            dl = sess.get("_last_dealer_live", False)
            extra = "Выстрел в дилера — холостой. Дилер стреляет в тебя — " + ("боевой. Минус жизнь." if dl else "холостой. Твой ход.")
    sess.pop("_last_dealer_live", None)
    caption = _mirror_caption(sess, extra)
    kb = None
    if sess.get("turn") == "player":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔫 В себя", callback_data=f"mirror|{uid}|self")],
            [InlineKeyboardButton(text="🎯 В дилера", callback_data=f"mirror|{uid}|opp")],
        ])
    try:
        if kb:
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption, reply_markup=None, parse_mode="HTML")
    except Exception:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


# Тексты для /helpgame <название>: полные правила, как играть, без формул и процентов
GAME_HELP_TEXTS = {
    "slot": (
        "🎰 <b>Слоты</b>\n\n"
        "<b>Что это.</b> Классическая игра на один раунд: крутишь — либо выигрыш, либо проигрыш. Никаких сложных правил.\n\n"
        "<b>Как играть.</b> Напиши /slot. Ставка списывается сразу (фиксированная). Один раунд — результат приходит сразу: "
        "выиграл — коины падают на баланс, проиграл — ставка сгорает. Фриспины и бесплатная попытка при нулевом балансе дают крутить без ставки.\n\n"
        "<b>На что обратить внимание.</b> Удачу усиливают Premium и зелья из маркета. Игра быстрая — идеально для разгона или проверки настроения."
    ),
    "konopla": (
        "🌿 <b>Конопля</b>\n\n"
        "<b>Что это.</b> Одна ставка — один раунд. Исход только два: либо крупный выигрыш, либо проигрыш ставки. Без промежуточных шагов.\n\n"
        "<b>Как играть.</b> Команда /konopla. Ставка фиксированная. Нажимать ничего не нужно — результат приходит сразу после команды. "
        "Выиграл — получишь награду на баланс; проиграл — ставка сгорает.\n\n"
        "<b>На что обратить внимание.</b> Исход непредсказуем. Premium и зелья удачи немного повышают твои шансы. Играй осознанно."
    ),
    "kripta": (
        "🚀 <b>Lucky Jet (Крипта)</b>\n\n"
        "<b>Что это.</b> Множитель растёт со временем. Твоя задача — успеть нажать «Забрать» до обвала и забрать ставку, умноженную на текущий множитель.\n\n"
        "<b>Как играть.</b> Напиши /kripta и сумму ставки (например: /kripta 100). Ставка списывается в начале. Появляется экран с растущим множителем и кнопкой «Забрать». "
        "Нажал «Забрать» — получаешь ставка × множитель на баланс. Не успел — множитель обвалился, ставка сгорает. Игра живёт не больше трёх минут; по таймауту забирается текущий множитель.\n\n"
        "<b>На что обратить внимание.</b> Чем выше множитель — тем выше риск обвала. Не жадничай: часто выгоднее забрать раньше. Можно отменить игру через /cancel — ставка вернётся."
    ),
    "almaz": (
        "💎 <b>Алмазы</b>\n\n"
        "<b>Что это.</b> Ты «копаешь» в шахте. Каждый шаг «Копать дальше» увеличивает возможный выигрыш, но и риск взрыва. В любой момент можно остановиться и забрать накопленное.\n\n"
        "<b>Как играть.</b> Команда /almaz и сумма ставки. Ставка списывается в начале. Три кнопки: "
        "«Копать дальше» — риск: либо прибавляется выигрыш, либо взрыв и потеря всего; "
        "«Забрать» — зафиксировать текущий выигрыш и получить его на баланс; "
        "«Завершить» — выйти без выигрыша. На игру даётся три минуты; по истечении результат подводится автоматически.\n\n"
        "<b>На что обратить внимание.</b> Чем чаще копаешь подряд, тем выше риск взрыва. Умей вовремя забирать. /cancel завершает игру по текущему результату."
    ),
    "chisla": (
        "🔢 <b>Числа (PvP-дуэль)</b>\n\n"
        "<b>Что это.</b> Ты и соперник делаете ставку. Каждому выдаётся по шесть карт с разными множителями. Выбираешь одну — у кого множитель выше, тот забирает весь банк.\n\n"
        "<b>Как играть.</b> Напиши /chisla @username сумма (например: /chisla @friend 500). Соперник должен принять вызов. У обоих блокируется ставка до конца игры. "
        "Каждому показывается по шесть кнопок (карт). Выбираешь одну — открывается твой множитель. Победитель — у кого множитель выше; при равенстве победитель определяется случайно. "
        "На выбор даётся время; кто не нажал — за него выбирает игра. После этого победитель получает весь банк, проигравший теряет ставку.\n\n"
        "<b>На что обратить внимание.</b> Баланс проверяется у обоих. Если соперник не подтвердил вызов — ставка возвращается. Максимальный множитель на картах ограничен."
    ),
    "plsdon": (
        "🎁 <b>Задонать мне</b>\n\n"
        "<b>Что это.</b> Бот предлагает «задонатить» ему коины — отправить сумму в общий котёл или по логике бота. Чисто добровольно.\n\n"
        "<b>Как играть.</b> Команда /plsdon. Появляется сообщение с кнопкой пожертвования. Можешь нажать и отправить сумму — коины спишутся с твоего баланса; "
        "или просто закрыть. Между использованиями есть кулдаун.\n\n"
        "<b>На что обратить внимание.</b> Это не игра на выигрыш — ты отдаёшь коины осознанно. Подробности и точную сумму покажет сама команда при запуске."
    ),
    # 20 игр — правила для /helpgame (игры могут быть добавлены позже)
    "reactor": (
        "🧨 <b>Reactor</b>\n\n"
        "Ты управляешь реактором. Каждое действие либо охлаждает его, либо ускоряет перегрев. "
        "Можно выйти в любой момент и забрать награду. Чем дольше рискуешь — тем выше выигрыш."
    ),
    "vault": (
        "🪙 <b>Vault</b>\n\n"
        "Перед тобой сейф с замком. Каждая попытка — шаг ближе к открытию… или блокировке. "
        "Можно остановиться и забрать текущий выигрыш."
    ),
    "dicepath": (
        "🎲 <b>Dice Path</b>\n\n"
        "Ты бросаешь кости и продвигаешься по дорожке. Каждый шаг повышает награду, "
        "но увеличивает риск всё потерять."
    ),
    "overheat": (
        "🔥 <b>Overheat</b>\n\n"
        "Индикатор нагрева растёт. Каждое нажатие — шанс сорвать куш или спалить всё. Можно выйти заранее."
    ),
    "mindlock": (
        "🧠 <b>Mind Lock</b>\n\n"
        "Нужно запоминать последовательность действий. Ошибка — проигрыш. Чем дальше — тем выше награда."
    ),
    "bombline": (
        "💣 <b>Bomb Line</b>\n\n"
        "Перед тобой линия из ячеек. Одна из них — бомба. Можно идти дальше или остановиться."
    ),
    "liftx": (
        "🪜 <b>Lift X</b>\n\n"
        "Поднимаешься по этажам. Каждый этаж — выше риск. Можно выйти на любом."
    ),
    "doza": (
        "🧪 <b>Doza</b>\n\n"
        "Каждое «ещё» усиливает эффект. Но если переборщишь — проиграешь всё."
    ),
    "shum": (
        "🌫 <b>Shum</b>\n\n"
        "Ты движешься в шуме. Есть скрытый предел. Никто не знает, когда он сработает."
    ),
    "signal": (
        "📡 <b>Signal</b>\n\n"
        "Ты ловишь сигнал. Чем дольше держишь — тем выше награда. Сигнал может оборваться в любой момент."
    ),
    "freeze": (
        "🧊 <b>Freeze</b>\n\n"
        "Замораживаешь множитель. Можно рискнуть разморозкой ради большего выигрыша."
    ),
    "tunnel": (
        "🕳 <b>Tunnel</b>\n\n"
        "Ты копаешь тоннель. Каждый метр — шанс на находку или обвал."
    ),
    "escape": (
        "🏃 <b>Escape</b>\n\n"
        "Ты убегаешь. Каждый шаг — либо спасение, либо провал."
    ),
    "code": (
        "🔐 <b>Code</b>\n\n"
        "Нужно угадать комбинацию из вариантов. Чем меньше попыток — тем выше награда."
    ),
    "magnet": (
        "🧲 <b>Magnet</b>\n\n"
        "Притягиваешь бонусы, но и опасности тоже."
    ),
    "candle": (
        "🕯 <b>Candle</b>\n\n"
        "Пока свеча горит — награда растёт. Когда погаснет — всё сгорает."
    ),
    "pulse": (
        "⚡ <b>Pulse</b>\n\n"
        "Ритм ускоряется. Пропустил момент — проиграл."
    ),
    "orbit": (
        "🪐 <b>Orbit</b>\n\n"
        "Ты вращаешься по орбите. Можно выйти на любом круге."
    ),
    "wall": (
        "🧱 <b>Wall</b>\n\n"
        "Каждая стена — выбор: рискнуть или отойти."
    ),
    "watcher": (
        "👁 <b>Watcher</b>\n\n"
        "За тобой кто-то следит. Чем дольше играешь — тем выше награда… и риск."
    ),
    # Игры 21–40 — правила для /helpgame
    "controlroom": (
        "🕹 <b>Control Room</b>\n\n"
        "Ты управляешь панелью с индикаторами. Каждое действие стабилизирует систему или приближает аварию. "
        "Можно выйти в любой момент."
    ),
    "firesector": (
        "🧯 <b>Fire Sector</b>\n\n"
        "Сектора загораются по очереди. Туши или игнорируй — риск растёт."
    ),
    "mutation": (
        "🧬 <b>Mutation</b>\n\n"
        "Каждый ход мутирует множитель. Иногда в плюс, иногда в минус."
    ),
    "satellite": (
        "🛰 <b>Satellite</b>\n\n"
        "Спутник теряет сигнал. Каждое действие удерживает связь."
    ),
    "mine": (
        "🪓 <b>Mine</b>\n\n"
        "Каждый удар киркой может принести награду… или обвал."
    ),
    "clock": (
        "🕰 <b>Clock</b>\n\n"
        "Время работает против тебя. Можно забрать выигрыш до обнуления."
    ),
    "lab": (
        "🧪 <b>Lab</b>\n\n"
        "Эксперимент идёт. Чем дольше — тем нестабильнее результат."
    ),
    "bunker": (
        "🧱 <b>Bunker</b>\n\n"
        "Ты углубляешься в бункер. Чем глубже — тем ценнее находки."
    ),
    "storm": (
        "🌪 <b>Storm</b>\n\n"
        "Шторм усиливается. Каждое действие — риск."
    ),
    "navigator": (
        "🧭 <b>Navigator</b>\n\n"
        "Ты выбираешь направление. Не все пути безопасны."
    ),
    "icepath": (
        "🧊 <b>Ice Path</b>\n\n"
        "Лёд трескается с каждым шагом. Можно остановиться."
    ),
    "coinstack": (
        "🪙 <b>Coin Stack</b>\n\n"
        "Ты складываешь монеты. Башня может рухнуть."
    ),
    "target": (
        "🎯 <b>Target</b>\n\n"
        "Каждый выстрел — шанс увеличить награду."
    ),
    "fuse": (
        "🧨 <b>Fuse</b>\n\n"
        "Фитиль горит. Когда догорит — всё закончится."
    ),
    "web": (
        "🕷 <b>Web</b>\n\n"
        "Ты запутываешься всё сильнее. Можно выбраться раньше."
    ),
    "logicgate": (
        "🧠 <b>Logic Gate</b>\n\n"
        "Нужно делать правильные выборы подряд."
    ),
    "depth": (
        "🪜 <b>Depth</b>\n\n"
        "Каждый уровень глубже и опаснее."
    ),
    "field": (
        "🧲 <b>Field</b>\n\n"
        "Поле притягивает бонусы и угрозы."
    ),
    "ritual": (
        "🕯 <b>Ritual</b>\n\n"
        "Каждый шаг усиливает ритуал… или ломает его."
    ),
    "trace": (
        "👣 <b>Trace</b>\n\n"
        "Ты оставляешь следы. Чем больше шагов — тем выше риск быть пойманным."
    ),
    "rulet": (
        "🔫 <b>Русская рулетка</b>\n\n"
        "<b>Что это.</b> Мультиплеер: от 2 до 8 игроков вносят одинаковую ставку. Каждые 20 секунд один случайно выбывает. Последний оставшийся забирает весь банк.\n\n"
        "<b>Как играть.</b> Напиши /rulet и сумму ставки. В чате создаётся игра. Игроки нажимают «Вступить» и вносят ту же сумму. "
        "Когда набралось минимум 2 человека, старт. Каждые 20 секунд бот объявляет выбывшего. Игра идёт в чате — все видят, кто вышел. "
        "Последний оставшийся получает весь банк на баланс.\n\n"
        "<b>На что обратить внимание.</b> Нужны другие игроки в чате. Ставка блокируется до конца раунда. Таймер и порядок выбывания задаёт бот."
    ),
    "frekaz": (
        "🎲 <b>Фреказ</b>\n\n"
        "<b>Что это.</b> Мультиплеер на банк: до 5 игроков вносят ставку (от 1000 до 100000 коинов). Через 2 минуты определяется один победитель — он забирает весь банк. "
        "Чем твоя ставка выше относительно других, тем выше шанс выиграть (точные проценты игрок не видит).\n\n"
        "<b>Как играть.</b> Команда /frekaz и сумма. Другие игроки вступают с той же суммой. Ждёшь 2 минуты. Бот объявляет победителя — он получает весь банк на баланс, остальные теряют ставку.\n\n"
        "<b>На что обратить внимание.</b> Нужны соперники в чате. Ставка высокая — играй осознанно."
    ),
    "perekyp": (
        "🛒 <b>Перекуп</b>\n\n"
        "<b>Что это.</b> Ты «листаешь» объявления о продаже техники (iPhone, MacBook и др.). У каждого объявления — продавец, рейтинг, отзывы и цена. "
        "Можешь торговаться, пролистывать или купить. После покупки исход: перепродал с прибылью (множитель к ставке) или не сбыл — потеря ставки. Рейтинг продавца влияет на надёжность сделки.\n\n"
        "<b>Как играть.</b> Команда /perekyp и сумма. Появляется карточка: товар, цена, продавец, рейтинг. Кнопки: Выйти (без списания), Пролистать (другое объявление), Торг (попытка сбить цену), Купить. "
        "После «Купить» ставка списывается и определяется исход: выигрыш с множителем или проигрыш.\n\n"
        "<b>На что обратить внимание.</b> Высокий рейтинг продавца — надёжнее, но не гарантия. Торг увеличивает шанс удачной перепродажи. Есть лимит пролистываний."
    ),
    "random": (
        "🎲 <b>Судьба технолога</b>\n\n"
        "<b>Что это.</b> Ты не выбираешь игру сам — бот за тебя выбирает одну из игр (слоты, конопля, Lucky Jet, алмазы, перекуп или одна из 40 игр «риск/забрать»), "
        "сам назначает ставку (в пределах твоего баланса) и сразу запускает раунд без дополнительного подтверждения. Дополнительно даётся бонус к удаче. "
        "Редко «Технолог подменяет игру» в последний момент — тогда выбранная игра меняется.\n\n"
        "<b>Как играть.</b> Напиши /random. Нужен минимум 20 коинов. Бот списывает ставку, выбирает игру и выдаёт результат одним сообщением: выигрыш или проигрыш. "
        "Никаких кнопок и ожидания — всё происходит сразу.\n\n"
        "<b>На что обратить внимание.</b> Защита от нищеты при проигрыше не действует. Идеально, когда не можешь выбрать сам — пусть судьба решит."
    ),
    "gamerandom": (
        "⚠️ <b>Сбой матрицы</b>\n\n"
        "<b>Что это.</b> Одна игра, собранная из «кусков» других: случайно комбинируются механика риска, тип ставки, время и вид награды. "
        "Ты не знаешь заранее, во что попадёшь — только после загрузки видишь параметры. Исход определяется по этим параметрам. Очень редко сбой даёт «лишний шанс» — выше шанс выигрыша.\n\n"
        "<b>Как играть.</b> Команда /gamerandom. Нужен минимум 50 коинов. Ставка берётся 2–5% баланса. Появляется сообщение о загрузке параметров (риск, ставка, время, награда). "
        "Через пару секунд приходит результат: выигрыш с множителем или проигрыш.\n\n"
        "<b>На что обратить внимание.</b> Хаотичная и непредсказуемая игра. Подходит тем, кто любит полный рандом."
    ),
    "blackmarket": (
        "🕳 <b>Чёрный рынок</b>\n\n"
        "<b>Что это.</b> Тебе предлагают три сделки: 🔴 высокий риск и высокий профит, 🟡 средняя, 🟢 почти безопасная. "
        "У каждой сделки — скрытый шанс успеха, провала или подставы (дополнительный штраф). Рынок «запоминает»: если часто брать красную сделку, шанс подставы растёт.\n\n"
        "<b>Как играть.</b> Команда /blackmarket или /blackmarket 1000. Ставка от 100 до 50000 коинов. Появляются три кнопки — три сделки. Выбираешь одну. "
        "Результат: успех (выигрыш с множителем), провал (потеря ставки) или подстава (потеря больше ставки). Всё приходит одним сообщением.\n\n"
        "<b>На что обратить внимание.</b> Красная сделка — самая рискованная и самая выгодная, но рынок наказывает за жадность. Зелёная — стабильнее, но награда скромнее."
    ),
    "echo": (
        "🔮 <b>Эхо решений</b>\n\n"
        "<b>Что это.</b> Бот смотрит на твои последние 10–20 игр (ставки, победы, поражения) и присваивает тебе архетип: Стратег, Азартный, Осторожный, Хаотичный или Самоуверенный. "
        "Потом запускается один раунд, где шансы и множители зависят от архетипа. Возможны победа, частичный возврат ставки или «наказание за стиль» — дополнительный минус. "
        "Ставка 2–5% баланса, списывается после того, как ты увидишь свой архетип.\n\n"
        "<b>Как играть.</b> Напиши /echo. Нужен минимум 100 коинов. Бот покажет, как тебя видит (архетип и короткое описание), потом спишет ставку и выдаст результат: выигрыш, проигрыш, частичный возврат или наказание.\n\n"
        "<b>На что обратить внимание.</b> Визитка бота — игра, которая «помнит» твой стиль. Чем разнообразнее и осознаннее ты играешь, тем интереснее архетип."
    ),
    "topgame": (
        "📊 <b>Топ игр</b>\n\n"
        "<b>Что это.</b> Список игр по количеству запусков за последние сутки и тренд по каждой: растёт интерес, стабильно или падает. "
        "Удобно решить, во что зайти, и посмотреть, что сейчас в тренде. Без формул — только названия команд, число игр, доля побед и короткий комментарий.\n\n"
        "<b>Как пользоваться.</b> Напиши /topgame. Получишь сообщение с топом игр. Эмодзи у каждой строки: 🔥 в тренде, 😐 стабильно, 🧊 падает. "
        "Внизу подсказка: в /news иногда меняются условия в играх — заглядывай перед ставкой.\n\n"
        "<b>На что обратить внимание.</b> Топ обновляется по реальной активности. Используй его вместе с /news для выбора игры."
    ),
    "fracture": (
        "🧩 <b>Излом решения</b>\n\n"
        "<b>Что это.</b> Тест из 10 случайных вопросов по математике уровня 7–9 класса: алгебра, геометрия, степени, уравнения, тригонометрия. "
        "У тебя 3 жизни и <b>30 секунд</b> на каждый ответ. Таймаут или неверный ответ — минус жизнь. Ноль жизней — проигрыш. "
        "Чем больше правильных ответов — тем выше шанс выигрыша и множитель. Вопросы рассчитаны на быстрый счёт в уме.\n\n"
        "<b>Как играть.</b> /fracture или /fracture 500 (ставка от 100 коинов). После загрузки — вопросы с четырьмя вариантами. Выбери ответ до истечения 30 секунд. "
        "Верно — следующий вопрос. Неверно — минус жизнь. После 10 вопросов или при нуле жизней — результат.\n\n"
        "<b>На что обратить внимание.</b> Вопросы не повторяются в одной игре. Сообщения с результатом удаляются через 20 секунд."
    ),
    "mirror": (
        "🪞 <b>Зеркало (Buckshot Roulette)</b>\n\n"
        "<b>Что это.</b> Механика Buckshot Roulette 1 в 1: обойма 8 патронов (4 боевых, 4 холостых), у тебя и у дилера по 2 жизни. "
        "Ходы по очереди: ты выбираешь «в себя» или «в дилера»; при холостом в себя — дополнительный ход, при холостом в дилера — стреляет дилер в тебя.\n\n"
        "<b>Как играть.</b> /mirror, минимум 100 коинов. После загрузки — две кнопки. «В себя»: боевой — минус жизнь; холостой — ещё ход. "
        "«В дилера»: боевой — дилер теряет жизнь; холостой — ход дилера (один выстрел в тебя). Когда обойма пуста — перезарядка (новая восьмёрка). "
        "Победа: дилер 0 жизней (x2 ставки). Поражение: твои жизни 0.\n\n"
        "<b>На что обратить внимание.</b> /cancel — возврат ставки."
    ),
}


@router.message(Command("helpgame"))
async def cmd_helpgame(message: Message):
    """Справка по игре: /helpgame slot | konopla | kripta | almaz | chisla | plsdon."""
    username = message.from_user.username
    first_name = message.from_user.first_name
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    name = (parts[1].strip().lower() if len(parts) > 1 else "").lstrip("/")
    if not name or name not in GAME_HELP_TEXTS:
        lines = [
            "📖 <b>Справка по играм</b>\n",
            "Напиши /helpgame и <b>название</b> игры — получишь полные правила (без формул и процентов).\n",
            "Пример: /helpgame fracture или /helpgame echo\n",
            "▸ <b>Основные:</b> slot, konopla, kripta, almaz, chisla, plsdon",
            "▸ <b>Мультиплеер:</b> rulet, frekaz, perekyp",
            "▸ <b>Особые:</b> random, gamerandom, blackmarket, echo, topgame, fracture, mirror",
            "▸ <b>Риск/забрать (40 игр):</b> reactor, vault, dicepath, overheat, mindlock, bombline, liftx, doza, shum, signal, freeze, tunnel, escape, code, magnet, candle, pulse, orbit, wall, watcher, controlroom, firesector, mutation, satellite, mine, clock, lab, bunker, storm, navigator, icepath, coinstack, target, fuse, web, logicgate, depth, field, ritual, trace",
        ]
        msg = format_message_with_username("\n".join(lines), username, first_name)
        sent = await message.answer(msg)
        asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
        return
    msg = format_message_with_username(GAME_HELP_TEXTS[name], username, first_name)
    sent = await message.answer(msg)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("infoslot"))
async def cmd_infoslot(message: Message):
    """Информация об игре /slot — без раскрытия процентов."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    response_text = format_message_with_username(
        f"🎰 <b>ИНФОРМАЦИЯ О СЛОТАХ</b>\n\n"
        f"Ставка: {config.SLOT_BET} коинов\n"
        f"Выигрыш: {config.SLOT_WIN} коинов\n\n"
        f"💡 Premium и зелья удачи повышают шансы",
        username, first_name
    )
    
    sent_message = await message.answer(response_text)
    asyncio.create_task(delete_message_after(sent_message))


@router.message(Command("infokonopla"))
async def cmd_infokonopla(message: Message):
    """Информация об игре /konopla — без раскрытия процентов."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    response_text = format_message_with_username(
        f"🌿 <b>ИНФОРМАЦИЯ О КОНОПЛЕ</b>\n\n"
        f"Ставка: {config.KONOPLA_BET} коинов\n"
        f"При проигрыше: -{config.KONOPLA_LOSS} коинов\n"
        f"При выигрыше: +{config.KONOPLA_WIN} коинов\n\n"
        f"💡 Premium и зелья удачи повышают шансы",
        username, first_name
    )
    
    sent_message = await message.answer(response_text)
    asyncio.create_task(delete_message_after(sent_message))


@router.message(Command("infolucky"))
async def cmd_infolucky(message: Message):
    """Информация об игре /kripta (Lucky Jet)"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    response_text = format_message_with_username(
        f"🚀 <b>ИНФОРМАЦИЯ О LUCKY JET</b>\n\n"
        f"Ставка: любая сумма\n"
        f"Множитель: растет каждые 10 сек, максимум x{config.KRIPTA_MAX_MULTIPLIER}\n\n"
        f"При краше — проигрыш. Выигрыш только если забрал вовремя кнопкой «Забрать»!",
        username, first_name
    )
    
    sent_message = await message.answer(response_text)
    asyncio.create_task(delete_message_after(sent_message))


# ---------- /chisla PvP ----------
CHISLA_TTL = GAME_MAX_DURATION_SEC  # 3 минуты макс
CHISLA_CARDS = ["🂡", "🂢", "🂣", "🂤", "🂥", "🂦"]

# Ожидающие вызовы: (p1_id, p2_id) -> {amount, chat_id, message_id} — для TTL возврата
_chisla_pending: Dict[tuple, dict] = {}


def _chisla_multiplier() -> float:
    """Множитель для кнопки: x1.0–x2.0 часто, x3 редко, x4 очень редко, x5 экстремально редко."""
    r = game_random.random()
    if r < 0.55:
        return round(game_random.uniform(1.0, 2.0), 1)
    if r < 0.88:
        return round(game_random.uniform(2.0, 3.0), 1)
    if r < 0.97:
        return round(game_random.uniform(3.0, 4.0), 1)
    if r < 0.995:
        return round(game_random.uniform(4.0, 4.9), 1)
    return 5.0


@router.message(Command("chisla"))
async def cmd_chisla(message: Message):
    """PvP: /chisla @user сумма — вызов на дуэль множителей."""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    user1 = await db.get_user(user_id)
    if not user1:
        await db.create_user(user_id, username)
        user1 = await db.get_user(user_id)

    recipient_id, recipient_username = resolve_recipient_from_message(message)
    if not recipient_id and recipient_username:
        recipient_id = await db.get_user_id_by_username(recipient_username)
    if not recipient_id:
        sent = await message.answer(format_message_with_username("Укажи пользователя: /chisla @user сумма", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    if recipient_id == user_id:
        sent = await message.answer(format_message_with_username("Нельзя играть с самим собой.", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        sent = await message.answer(format_message_with_username("Формат: /chisla @user сумма", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return
    try:
        amount = int(parts[2])
        if amount <= 0:
            raise ValueError("сумма > 0")
    except (ValueError, IndexError):
        sent = await message.answer(format_message_with_username("Укажи корректную сумму.", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    user2 = await db.get_user(recipient_id)
    if not user2:
        sent = await message.answer(format_message_with_username("Пользователь не найден.", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    if user1.get("is_banned") or user2.get("is_banned"):
        sent = await message.answer(format_message_with_username("Один из игроков в бане.", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    bal1 = await db.get_balance(user_id)
    bal2 = await db.get_balance(recipient_id)
    if bal1 < amount or bal2 < amount:
        sent = await message.answer(format_message_with_username("Недостаточно средств у одного из игроков.", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    # Проверка: нет ли уже активного вызова между этими игроками
    existing = await db.get_chisla_session_by_players(user_id, recipient_id)
    if existing:
        sent = await message.answer(format_message_with_username("У вас уже есть активный вызов. Дождитесь ответа.", username, first_name))
        asyncio.create_task(delete_message_after(sent))
        return

    # Резервируем у игрока1 (списываем)
    success, _, _, err = await balance_service.subtract_balance(
        user_id=user_id, amount=amount,
        command_source="/chisla", comment="Резерв на дуэль chisla",
        message=message, username=username, first_name=first_name,
        allow_negative=False
    )
    if not success:
        return

    u1_tag = f"@{username}" if username else str(user_id)
    u2_tag = f"@{recipient_username}" if recipient_username else str(recipient_id)
    text = f"🎲 {u1_tag} хочет сразиться с {u2_tag} на {amount} коинов.\nПринять вызов?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"chisla_accept_{user_id}_{recipient_id}_{amount}")],
        [InlineKeyboardButton(text="❌ Отказаться", callback_data=f"chisla_decline_{user_id}_{recipient_id}_{amount}")]
    ])
    sent_msg = await message.answer(text, reply_markup=keyboard)
    key = (user_id, recipient_id)
    _chisla_pending[key] = {"amount": amount, "chat_id": sent_msg.chat.id, "message_id": sent_msg.message_id}
    asyncio.create_task(_chisla_challenge_ttl(message.bot, key))
    logger.info(f"Chisla challenge: {user_id} vs {recipient_id} amount={amount}")


async def _chisla_challenge_ttl(bot: Bot, key: tuple):
    """Если второй игрок не ответил за 5 минут — возврат игроку 1."""
    await asyncio.sleep(CHISLA_TTL)
    if key not in _chisla_pending:
        return
    data = _chisla_pending.pop(key, None)
    if not data:
        return
    p1_id, p2_id = key
    amount = data["amount"]
    chat_id = data["chat_id"]
    message_id = data["message_id"]
    await balance_service.add_balance(p1_id, amount, "/chisla", "Возврат — вызов не принят", bot=bot, chat_id=chat_id, username=None, first_name=None)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass
    logger.info(f"Chisla challenge expired: {p1_id} vs {p2_id}, refunded {amount}")


@router.callback_query(F.data.startswith("chisla_accept_"))
async def cb_chisla_accept(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 5:
        await callback.answer("Ошибка", show_alert=True)
        return
    player1_id = int(parts[2])
    player2_id = int(parts[3])
    amount = int(parts[4])
    if callback.from_user.id != player2_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    key = (player1_id, player2_id)
    _chisla_pending.pop(key, None)
    # Проверка: ещё не принято и игрок2 в наличии
    bal2 = await db.get_balance(player2_id)
    if bal2 < amount:
        await callback.answer("Недостаточно средств.", show_alert=True)
        return
    success, _, _, _ = await balance_service.subtract_balance(
        user_id=player2_id, amount=amount,
        command_source="/chisla_accept", comment="Резерв на дуэль chisla",
        bot=callback.bot, chat_id=callback.message.chat.id,
        username=callback.from_user.username, first_name=callback.from_user.first_name,
        allow_negative=False
    )
    if not success:
        await callback.answer("Не удалось списать ставку.", show_alert=True)
        return
    session_id = f"{player1_id}_{player2_id}_{int(time.time())}"
    ok = await db.create_chisla_session(
        session_id, player1_id, player2_id, amount,
        callback.message.message_id, callback.message.chat.id, CHISLA_TTL
    )
    if not ok:
        await balance_service.add_balance(player2_id, amount, "/chisla_accept", "Возврат", bot=callback.bot, chat_id=callback.message.chat.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
        await callback.answer("Ошибка создания сессии.", show_alert=True)
        return
    await db.update_chisla_accepted(session_id)
    rules = "Выбери одну карту. Больший множитель побеждает. У вас 5 минут."
    photo_path = config.get_image_path("chisla.jpg")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=CHISLA_CARDS[i], callback_data=f"chisla_btn_{session_id}_{i}") for i in range(6)]
    ])
    try:
        if photo_path.exists():
            media = InputMediaPhoto(media=FSInputFile(str(photo_path)), caption=rules)
            await callback.bot.edit_message_media(chat_id=callback.message.chat.id, message_id=callback.message.message_id, media=media, reply_markup=keyboard)
        else:
            await callback.bot.edit_message_caption(chat_id=callback.message.chat.id, message_id=callback.message.message_id, caption=rules, reply_markup=keyboard)
    except Exception:
        await callback.bot.edit_message_caption(chat_id=callback.message.chat.id, message_id=callback.message.message_id, caption=rules, reply_markup=keyboard)
    await callback.answer("Вызов принят! Выбери карту.", show_alert=False)
    asyncio.create_task(_chisla_ttl_task(callback.bot, session_id, callback.message.chat.id, callback.message.message_id))


@router.callback_query(F.data.startswith("chisla_decline_"))
async def cb_chisla_decline(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 5:
        await callback.answer("Ошибка", show_alert=True)
        return
    player1_id = int(parts[2])
    player2_id = int(parts[3])
    amount = int(parts[4])
    if callback.from_user.id != player2_id:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    key = (player1_id, player2_id)
    _chisla_pending.pop(key, None)
    await balance_service.add_balance(player1_id, amount, "/chisla_decline", "Возврат отказа", bot=callback.bot, chat_id=callback.message.chat.id, username=callback.from_user.username, first_name=callback.from_user.first_name)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Вызов отклонён.", show_alert=False)


async def _chisla_ttl_task(bot: Bot, session_id: str, chat_id: int, message_id: int):
    """Через 5 минут автовыбор и завершение игры."""
    await asyncio.sleep(CHISLA_TTL)
    sess = await db.get_chisla_session(session_id)
    if not sess or sess["status"] != "active":
        return
    if sess.get("player1_choice") is None:
        await db.update_chisla_choice(session_id, sess["player1_id"], game_random.randint(0, 5), _chisla_multiplier())
        sess = await db.get_chisla_session(session_id)
    if sess and sess.get("player2_choice") is None:
        await db.update_chisla_choice(session_id, sess["player2_id"], game_random.randint(0, 5), _chisla_multiplier())
    await _chisla_finish(bot, session_id, chat_id, message_id)


async def _chisla_finish(bot: Bot, session_id: str, chat_id: int, message_id: int):
    sess = await db.get_chisla_session(session_id)
    if not sess or sess["status"] == "finished":
        return
    await db.finish_chisla_session(session_id)
    p1_id, p2_id = sess["player1_id"], sess["player2_id"]
    amount = sess["amount"]
    mult1 = sess.get("player1_mult")
    mult2 = sess.get("player2_mult")
    if mult1 is None:
        mult1 = _chisla_multiplier()
        await db.update_chisla_choice(session_id, p1_id, sess.get("player1_choice") or 0, mult1)
    if mult2 is None:
        mult2 = _chisla_multiplier()
        await db.update_chisla_choice(session_id, p2_id, sess.get("player2_choice") or 0, mult2)
    sess = await db.get_chisla_session(session_id)
    mult1, mult2 = sess["player1_mult"], sess["player2_mult"]
    if mult1 > mult2:
        winner_id, loser_id, win_mult = p1_id, p2_id, mult1
    elif mult2 > mult1:
        winner_id, loser_id, win_mult = p2_id, p1_id, mult2
    else:
        winner_id = game_random.choice([p1_id, p2_id])
        loser_id = p2_id if winner_id == p1_id else p1_id
        win_mult = mult1
    u_win = await db.get_user(winner_id)
    u_lose = await db.get_user(loser_id)
    logger.info(
        "chisla finish: winner_id=%s loser_id=%s amount=%s mult1=%.1f mult2=%.1f pot=%s",
        winner_id, loser_id, amount, mult1, mult2, amount * 2
    )
    pot = amount * 2
    _, _, _, tax = await balance_service.add_game_win(
        user_id=winner_id,
        gross_amount=pot,
        command_source="/chisla",
        comment="Победа в дуэли",
        bot=bot,
        chat_id=chat_id,
        username=u_win.get("username") if u_win else None,
        first_name=None,
    )
    await db.log_admin_game(winner_id, u_win.get("username") if u_win else "", "/chisla", amount, "win", amount, tax or 0)
    await db.log_admin_game(loser_id, u_lose.get("username") if u_lose else "", "/chisla", amount, "loss", -amount, 0)
    balance_win = await db.get_balance(winner_id)
    balance_lose = await db.get_balance(loser_id)
    await db.log_game_session(winner_id, "chisla", amount, "win", amount, float(win_mult))
    await db.log_game_session(loser_id, "chisla", amount, "loss", -amount, float(mult2 if winner_id == p1_id else mult1))
    await _update_mmr_and_achievements(winner_id, "chisla", "win", balance_win)
    await _update_mmr_and_achievements(loser_id, "chisla", "loss", balance_lose)
    win_tag = f"@{u_win['username']}" if u_win and u_win.get("username") else str(winner_id)
    lose_tag = f"@{u_lose['username']}" if u_lose and u_lose.get("username") else str(loser_id)
    win_caption = f"🏆 Победа! Твой множитель: x{win_mult}\nТы забрал {amount * 2} коинов"
    lose_caption = f"💀 Проигрыш. Твой множитель: x{(mult2 if winner_id == p1_id else mult1)}"
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        pass
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass
    win_photo = config.get_image_path("winchisla.jpg")
    lox_photo = config.get_image_path("loxchislo.jpg")
    try:
        win_msg = None
        lose_msg = None
        if win_photo.exists():
            win_msg = await bot.send_photo(chat_id, FSInputFile(str(win_photo)), caption=f"{win_tag}, {win_caption}")
        else:
            win_msg = await bot.send_message(chat_id, f"{win_tag}, {win_caption}")
        if lox_photo.exists():
            lose_msg = await bot.send_photo(chat_id, FSInputFile(str(lox_photo)), caption=f"{lose_tag}, {lose_caption}")
        else:
            lose_msg = await bot.send_message(chat_id, f"{lose_tag}, {lose_caption}")
        game_timeout = getattr(config, "GAME_RESULT_DELETE_TIMEOUT", 20)
        if win_msg:
            asyncio.create_task(delete_message_after_by_id(bot, chat_id, win_msg.message_id, game_timeout))
        if lose_msg:
            asyncio.create_task(delete_message_after_by_id(bot, chat_id, lose_msg.message_id, game_timeout))
    except Exception as e:
        logger.error(f"Chisla finish send: {e}")
    await db.delete_chisla_session(session_id)


@router.callback_query(F.data.startswith("chisla_btn_"))
async def cb_chisla_btn(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка", show_alert=True)
        return
    session_id = "_".join(parts[2:-1])
    btn_idx = int(parts[-1])
    sess = await db.get_chisla_session(session_id)
    if not sess or sess["status"] != "active":
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    player_id = callback.from_user.id
    if player_id != sess["player1_id"] and player_id != sess["player2_id"]:
        await callback.answer("Не жми на чужое!", show_alert=True)
        return
    if (sess["player1_id"] == player_id and sess.get("player1_choice") is not None) or (sess["player2_id"] == player_id and sess.get("player2_choice") is not None):
        await callback.answer("Ты уже выбрал карту.", show_alert=True)
        return
    mult = _chisla_multiplier()
    await db.update_chisla_choice(session_id, player_id, btn_idx, mult)
    sess = await db.get_chisla_session(session_id)
    if sess.get("player1_choice") is not None and sess.get("player2_choice") is not None:
        await callback.answer(f"Твой множитель: x{mult}", show_alert=False)
        await _chisla_finish(callback.bot, session_id, callback.message.chat.id, callback.message.message_id)
    else:
        await callback.answer(f"Твой множитель: x{mult}. Ждём второго игрока.", show_alert=False)
