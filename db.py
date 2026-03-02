"""
Модуль работы с базой данных SQLite
Асинхронная работа с БД для бота YandexPticaGPT v0.5
Устойчивость к перезапускам, полное сохранение данных
"""

import aiosqlite
import asyncio
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any
from pathlib import Path
import logging

from config import config

# Настройка логирования для модуля БД
logger = logging.getLogger(__name__)


class Database:
    """
    Класс для работы с асинхронной SQLite базой данных
    Обеспечивает подключение, создание таблиц и методы для работы с данными
    """
    
    def __init__(self, db_path: Path = None):
        """
        Инициализация подключения к БД
        
        Args:
            db_path: Путь к файлу БД (по умолчанию из config)
        """
        self.db_path = db_path or config.DB_PATH
        self.connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()  # Блокировка для thread-safe операций
    
    async def connect(self):
        """
        Установка соединения с БД
        Вызывается при старте бота
        """
        try:
            self.connection = await aiosqlite.connect(
                str(self.db_path),
                timeout=config.DB_TIMEOUT,
                check_same_thread=config.DB_CHECK_SAME_THREAD
            )
            # Включаем WAL режим для лучшей производительности и устойчивости
            await self.connection.execute("PRAGMA journal_mode=WAL")
            await self.connection.execute("PRAGMA foreign_keys=ON")
            await self.connection.commit()
            path_abs = Path(self.db_path).resolve()
            logger.info("Подключение к БД установлено: %s", path_abs)
        except Exception as e:
            logger.error(f"Ошибка подключения к БД: {e}")
            raise
    
    async def close(self):
        """
        Закрытие соединения с БД
        Вызывается при остановке бота
        """
        if self.connection:
            await self.connection.close()
            logger.info("Соединение с БД закрыто")
    
    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        """
        Выполнение SQL запроса с параметрами
        
        Args:
            query: SQL запрос
            params: Параметры запроса
            
        Returns:
            Курсор с результатами
        """
        async with self._lock:
            try:
                cursor = await self.connection.execute(query, params)
                await self.connection.commit()
                return cursor
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" in msg:
                    logger.debug("Колонка уже существует (миграция): %s", query[:80])
                else:
                    logger.error(f"Ошибка выполнения запроса: {query[:100]}... | {e}")
                await self.connection.rollback()
                raise
    
    async def fetchone(self, query: str, params: tuple = ()) -> Optional[Tuple]:
        """
        Получение одной записи из БД
        
        Args:
            query: SQL запрос
            params: Параметры запроса
            
        Returns:
            Кортеж с данными или None
        """
        cursor = await self.execute(query, params)
        return await cursor.fetchone()
    
    async def fetchall(self, query: str, params: tuple = ()) -> List[Tuple]:
        """
        Получение всех записей из БД
        
        Args:
            query: SQL запрос
            params: Параметры запроса
            
        Returns:
            Список кортежей с данными
        """
        cursor = await self.execute(query, params)
        return await cursor.fetchall()
    
    async def create_tables(self):
        """
        Создание всех необходимых таблиц в БД
        Вызывается при первом запуске или миграциях
        """
        try:
            # Таблица пользователей (основная информация)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    balance INTEGER DEFAULT 0 NOT NULL,
                    level INTEGER DEFAULT 1 NOT NULL,
                    premium_until INTEGER DEFAULT NULL,
                    status TEXT DEFAULT NULL,
                    created_at INTEGER NOT NULL,
                    last_active INTEGER NOT NULL,
                    is_banned INTEGER DEFAULT 0 NOT NULL,
                    ban_until INTEGER DEFAULT NULL
                )
            """)
            
            # Таблица профилей (дополнительная информация о пользователях)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    avatar_path TEXT DEFAULT NULL,
                    vip_address TEXT DEFAULT NULL,
                    about_info TEXT DEFAULT NULL,
                    selected_status TEXT DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица cooldown'ов команд
            await self.execute("""
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    last_used INTEGER NOT NULL,
                    PRIMARY KEY (user_id, command),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица временных эффектов (premium, зелья, баффы)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS effects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    effect_type TEXT NOT NULL,
                    multiplier REAL DEFAULT 1.0,
                    started_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    metadata TEXT DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица инвентаря пользователей
            await self.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    quality_level INTEGER DEFAULT 0,
                    quantity INTEGER DEFAULT 1 NOT NULL,
                    multiplier REAL DEFAULT 1.0,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица реферальных кодов
            await self.execute("""
                CREATE TABLE IF NOT EXISTS refcodes (
                    code TEXT PRIMARY KEY,
                    reward_type TEXT NOT NULL,
                    reward_value TEXT NOT NULL,
                    activated_by INTEGER DEFAULT NULL,
                    activated_at INTEGER DEFAULT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (activated_by) REFERENCES users(user_id) ON DELETE SET NULL
                )
            """)
            
            # Таблица транзакций (история всех операций с балансом)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    transaction_type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    balance_before INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    command_source TEXT DEFAULT NULL,
                    comment TEXT DEFAULT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица истории уровней (для отслеживания прогресса)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS levels (
                    user_id INTEGER PRIMARY KEY,
                    level INTEGER DEFAULT 1 NOT NULL,
                    total_coins_earned INTEGER DEFAULT 0 NOT NULL,
                    level_up_cost INTEGER DEFAULT 500 NOT NULL,
                    last_level_up INTEGER DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица подарков (дарение игрушек между пользователями)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS gifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    quality_level INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (sender_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (receiver_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица истории игр (для статистики и отладки)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS games_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    bet INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    amount_change INTEGER NOT NULL,
                    multiplier REAL DEFAULT 1.0,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица состояния налога Технолога
            await self.execute("""
                CREATE TABLE IF NOT EXISTS tax_states (
                    user_id INTEGER PRIMARY KEY,
                    last_tax_time INTEGER DEFAULT NULL,
                    tax_due INTEGER DEFAULT 0 NOT NULL,
                    is_paid INTEGER DEFAULT 1 NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица фриспинов (для команды /slot)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS free_spins (
                    user_id INTEGER NOT NULL,
                    spins_count INTEGER DEFAULT 0 NOT NULL,
                    expires_at INTEGER DEFAULT NULL,
                    PRIMARY KEY (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица активных сессий kripta (Lucky Jet)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS kripta_sessions (
                    user_id INTEGER PRIMARY KEY,
                    bet INTEGER NOT NULL,
                    current_multiplier REAL DEFAULT 1.0,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    started_at INTEGER NOT NULL,
                    next_update_at INTEGER NOT NULL,
                    crash_at INTEGER DEFAULT NULL,
                    is_active INTEGER DEFAULT 1 NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица статусов (справочник доступных статусов)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS statuses (
                    status_name TEXT PRIMARY KEY,
                    price INTEGER NOT NULL,
                    description TEXT DEFAULT NULL,
                    emoji TEXT DEFAULT NULL
                )
            """)
            
            # Таблица антиспама (для отслеживания активности пользователей)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS antispam (
                    user_id INTEGER PRIMARY KEY,
                    message_count INTEGER DEFAULT 0 NOT NULL,
                    window_start INTEGER NOT NULL,
                    is_muted INTEGER DEFAULT 0 NOT NULL,
                    mute_until INTEGER DEFAULT NULL,
                    messages_left_to_ban INTEGER DEFAULT NULL,
                    last_message_at INTEGER DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Миграция: добавить колонки antispam если их нет (старые БД)
            for col_name, col_def in [("messages_left_to_ban", "INTEGER DEFAULT NULL"), ("last_message_at", "INTEGER DEFAULT NULL")]:
                try:
                    await self.execute(f"ALTER TABLE antispam ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass
            # Миграция: обращение бота к игроку (дружок, боец, легенда и т.п.)
            try:
                await self.execute("ALTER TABLE profiles ADD COLUMN bot_address TEXT DEFAULT NULL")
            except Exception:
                pass
            # Миграция: MMR для лиг
            try:
                await self.execute("ALTER TABLE users ADD COLUMN mmr INTEGER DEFAULT 0 NOT NULL")
            except Exception:
                pass
            # Миграции birzh_state/user_birzh выполняются после CREATE TABLE этих таблиц (см. ниже)

            # Таблица: 1 бесплатная игра в сутки при балансе 0 (дата последнего использования)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS free_game_daily (
                    user_id INTEGER PRIMARY KEY,
                    last_used_date TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Достижения: справочник и выданные пользователям
            await self.execute("""
                CREATE TABLE IF NOT EXISTS achievement_definitions (
                    achievement_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    prefix TEXT DEFAULT NULL
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_achievements (
                    user_id INTEGER NOT NULL,
                    achievement_key TEXT NOT NULL,
                    unlocked_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, achievement_key),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (achievement_key) REFERENCES achievement_definitions(achievement_key)
                )
            """)
            # Заполняем справочник достижений при первом запуске
            await self._init_achievements()

            # Сезоны лиг: id, название, начало, конец
            await self.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    ends_at INTEGER NOT NULL
                )
            """)
            row = await self.fetchone("SELECT 1 FROM seasons LIMIT 1")
            if not row:
                now = int(datetime.now().timestamp())
                end = now + 90 * 86400
                await self.execute("INSERT INTO seasons (name, started_at, ends_at) VALUES (?, ?, ?)", ("Сезон 1", now, end))

            # Кубки по играм: за сезон считаем победы (слот, излом и т.д.)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS cup_wins (
                    season_id INTEGER NOT NULL,
                    game_slug TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    wins INTEGER DEFAULT 0 NOT NULL,
                    PRIMARY KEY (season_id, game_slug, user_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Дневные задания биржи: дата, тип (buy_jd_100, sell_kris_100...), выполнен, награда получена
            await self.execute("""
                CREATE TABLE IF NOT EXISTS birzh_daily_quests (
                    user_id INTEGER NOT NULL,
                    quest_date TEXT NOT NULL,
                    quest_type TEXT NOT NULL,
                    completed INTEGER DEFAULT 0 NOT NULL,
                    reward_claimed INTEGER DEFAULT 0 NOT NULL,
                    PRIMARY KEY (user_id, quest_date, quest_type),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Снимок портфеля биржи на начало дня (для достижения «+10% за день»)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS birzh_daily_snapshot (
                    user_id INTEGER NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    morning_value REAL NOT NULL,
                    PRIMARY KEY (user_id, snapshot_date),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Глобальные события (день слота, день биржи): тип, окончание (timestamp)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS global_events (
                    event_type TEXT PRIMARY KEY,
                    ends_at INTEGER NOT NULL
                )
            """)

            # Боевой пропуск от технолога (как Brawl Pass)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS bp_seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    ends_at INTEGER NOT NULL
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS bp_levels (
                    season_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    xp_required INTEGER NOT NULL,
                    reward_free_type TEXT NOT NULL,
                    reward_free_value INTEGER NOT NULL,
                    reward_premium_type TEXT NOT NULL,
                    reward_premium_value INTEGER NOT NULL,
                    PRIMARY KEY (season_id, level),
                    FOREIGN KEY (season_id) REFERENCES bp_seasons(id) ON DELETE CASCADE
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS bp_quests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    season_id INTEGER NOT NULL,
                    quest_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    xp_reward INTEGER NOT NULL,
                    quest_type TEXT NOT NULL,
                    target_value INTEGER NOT NULL,
                    UNIQUE(season_id, quest_key),
                    FOREIGN KEY (season_id) REFERENCES bp_seasons(id) ON DELETE CASCADE
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_bp_progress (
                    user_id INTEGER NOT NULL,
                    season_id INTEGER NOT NULL,
                    level INTEGER DEFAULT 1 NOT NULL,
                    xp INTEGER DEFAULT 0 NOT NULL,
                    PRIMARY KEY (user_id, season_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_bp_claimed (
                    user_id INTEGER NOT NULL,
                    season_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    is_premium INTEGER NOT NULL,
                    PRIMARY KEY (user_id, season_id, level, is_premium),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_bp_quest_progress (
                    user_id INTEGER NOT NULL,
                    season_id INTEGER NOT NULL,
                    quest_key TEXT NOT NULL,
                    progress INTEGER DEFAULT 0 NOT NULL,
                    reset_date TEXT,
                    PRIMARY KEY (user_id, season_id, quest_key),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            row_bp = await self.fetchone("SELECT 1 FROM bp_seasons LIMIT 1")
            if not row_bp:
                now = int(datetime.now().timestamp())
                end_bp = now + 90 * 86400
                await self.execute("INSERT INTO bp_seasons (name, started_at, ends_at) VALUES (?, ?, ?)", ("Боевой пропуск 1", now, end_bp))
                await self._init_bp_levels_and_quests()

            # Логи для админа: игры (user_id, username, command, bet, result, balance_change, tax, created_at)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS admin_game_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    command TEXT NOT NULL,
                    bet INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    balance_change INTEGER NOT NULL,
                    tax INTEGER DEFAULT 0 NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
                )
            """)
            await self.execute("CREATE INDEX IF NOT EXISTS idx_admin_game_logs_created ON admin_game_logs(created_at DESC)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_admin_game_logs_result ON admin_game_logs(result)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_admin_game_logs_command ON admin_game_logs(command)")

            # Персональные ивенты под стиль игрока (азартный, мем, анти-жадный, спас, тень)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_events (
                    user_id INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    ends_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_event_history (
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    ends_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            # Перерождения: user_id, rebirth_count (каждое +0.5x к удаче), стоимость следующего x2
            await self.execute("""
                CREATE TABLE IF NOT EXISTS rebirths (
                    user_id INTEGER PRIMARY KEY,
                    rebirth_count INTEGER DEFAULT 0 NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # /birzh: глобальная цена шарага-коина (1–100), обновляется при действиях
            await self.execute("""
                CREATE TABLE IF NOT EXISTS birzh_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    price INTEGER DEFAULT 50 NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_birzh (
                    user_id INTEGER PRIMARY KEY,
                    sharaga_balance INTEGER DEFAULT 0 NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            row = await self.fetchone("SELECT 1 FROM birzh_state WHERE id = 1")
            if not row:
                await self.execute(
                    "INSERT INTO birzh_state (id, price, updated_at) VALUES (1, 50, ?)",
                    (int(datetime.now().timestamp()),)
                )
            # Миграция: доп. колонки биржи (после создания таблиц)
            try:
                await self.execute("ALTER TABLE birzh_state ADD COLUMN technolog_rub REAL DEFAULT 1.0")
            except Exception:
                pass
            for col, default in [("kris_price", "1250"), ("jd_price", "7500"), ("lisaya_price", "60000")]:
                try:
                    await self.execute(f"ALTER TABLE birzh_state ADD COLUMN {col} INTEGER DEFAULT {default}")
                except Exception:
                    pass
            for col in ["kris_balance", "jd_balance", "lisaya_balance"]:
                try:
                    await self.execute(f"ALTER TABLE user_birzh ADD COLUMN {col} INTEGER DEFAULT 0")
                except Exception:
                    pass

            # /echo: дата последней выдачи 50 коинов (раз в сутки)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS echo_reward_dates (
                    user_id INTEGER PRIMARY KEY,
                    reward_date INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Таблица одноразового промокода /freedurev (1 раз на ВСЕГО бота — первый активировавший)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS freedurev_activated (
                    user_id INTEGER PRIMARY KEY,
                    activated_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            # Глобальная одна запись: кто первый активировал /freedurev (id=1 единственная строка)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS freedurev_global (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    user_id INTEGER NOT NULL,
                    activated_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
                )
            """)
            # Миграция: если в freedurev_activated есть записи, а в freedurev_global нет — переносим первого
            row_global = await self.fetchone("SELECT 1 FROM freedurev_global WHERE id = 1")
            if not row_global:
                row_first = await self.fetchone(
                    "SELECT user_id, activated_at FROM freedurev_activated ORDER BY activated_at ASC LIMIT 1"
                )
                if row_first:
                    await self.execute(
                        "INSERT OR IGNORE INTO freedurev_global (id, user_id, activated_at) VALUES (1, ?, ?)",
                        (row_first[0], row_first[1])
                    )
            
            # Таблица PvP-игры /chisla
            await self.execute("""
                CREATE TABLE IF NOT EXISTS chisla_sessions (
                    session_id TEXT PRIMARY KEY,
                    player1_id INTEGER NOT NULL,
                    player2_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message_id INTEGER DEFAULT NULL,
                    chat_id INTEGER DEFAULT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    player1_choice INTEGER DEFAULT NULL,
                    player2_choice INTEGER DEFAULT NULL,
                    player1_mult REAL DEFAULT NULL,
                    player2_mult REAL DEFAULT NULL,
                    FOREIGN KEY (player1_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (player2_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Таблица приветствия Premium 7d при первом сообщении в чате раз в 24ч
            await self.execute("""
                CREATE TABLE IF NOT EXISTS premium_chat_greeting (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_greeting_at INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, user_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)
            
            # Роли (динамические): админ, модер, младший модер (создатель только из config)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    until_ts INTEGER DEFAULT NULL,
                    granted_by INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, role),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Таблица банов (история и данные)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    banned_by INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER DEFAULT NULL,
                    ban_type TEXT DEFAULT 'commands',
                    unbanned_at INTEGER DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # Игровые новости: одна активная запись (good/bad/neutral), привязка к игре, срок действия
            await self.execute("""
                CREATE TABLE IF NOT EXISTS game_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_type TEXT NOT NULL,
                    game_slug TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    flavor_text TEXT DEFAULT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            await self.execute("CREATE INDEX IF NOT EXISTS idx_game_news_expires ON game_news(expires_at)")

            # Создание индексов для оптимизации запросов
            await self.execute("CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance DESC)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_users_level ON users(level DESC)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_effects_user_expires ON effects(user_id, expires_at)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_cooldowns_user_command ON cooldowns(user_id, command)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_created ON transactions(user_id, created_at DESC)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_inventory_user_type ON inventory(user_id, item_type)")
            await self.execute("CREATE INDEX IF NOT EXISTS idx_kripta_sessions_active ON kripta_sessions(user_id, is_active)")
            
            # Инициализация справочника статусов (если пусто)
            await self._init_statuses()
            
            # Инициализация реферальных кодов (если пусто)
            await self._init_refcodes()
            
            logger.info("Все таблицы БД созданы/проверены успешно")
            
        except Exception as e:
            logger.error(f"Ошибка создания таблиц: {e}")
            raise
    
    async def _init_statuses(self):
        """Инициализация справочника статусов при первом запуске"""
        count = await self.fetchone("SELECT COUNT(*) FROM statuses")
        if count and count[0] == 0:
            statuses_data = [
                ("Богач🤡🫵", 5000, "Статус богача", "🤡🫵"),
                ("Хомяк🐹", 5000, "Статус хомяка", "🐹"),
                ("Легенда☠️", 5000, "Статус легенды", "☠️"),
                ("Потужномэн💫", 5000, "Статус потужномэна", "💫"),
                ("Главный пубертат страны💓", 5000, "Главный пубертат", "💓"),
                ("Технолог🪑", 5000, "Статус технолога", "🪑")
            ]
            for status_name, price, description, emoji in statuses_data:
                await self.execute(
                    "INSERT OR IGNORE INTO statuses (status_name, price, description, emoji) VALUES (?, ?, ?, ?)",
                    (status_name, price, description, emoji)
                )
            logger.info("Справочник статусов инициализирован")
    
    async def _init_refcodes(self):
        """Инициализация реферальных кодов при первом запуске"""
        codes_data = [
            ("Makrosa220", "coins", "200", 1),
            ("MTV2026NLO", "premium", "300", 1),  # 5 минут = 300 секунд
            ("MACKRAT", "random_potion", "1", 1),
            ("OYMYGOD", "coins", "50", 1),
            ("YANDEXPTICA", "coins", "500", 1),
            ("GODKUZATOP", "coins_spins", "30:5", 1),  # 30 коинов + 5 фриспинов
            ("PADLOPLAY", "reset_refill", "1", 1),
            ("VECNA", "steal_balance", "0.166", 1),  # 1/6 баланса
            ("DRISTIN", "fake_reset", "1", 1)
        ]
        
        for code, reward_type, reward_value, is_active in codes_data:
            exists = await self.fetchone(
                "SELECT code FROM refcodes WHERE code = ?",
                (code,)
            )
            if not exists:
                await self.execute(
                    """INSERT INTO refcodes (code, reward_type, reward_value, is_active, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (code, reward_type, reward_value, is_active, int(datetime.now().timestamp()))
                )
        logger.info("Реферальные коды инициализированы")

    async def _init_achievements(self):
        """Инициализация справочника достижений при первом запуске."""
        count = await self.fetchone("SELECT COUNT(*) FROM achievement_definitions")
        if count and count[0] > 0:
            return
        definitions = [
            ("first_win", "Первый выигрыш", "🥉"),
            ("wins_streak_10", "10 побед подряд", "🥈"),
            ("wins_streak_10_cold", "Холодный разум (10 побед подряд)", "🧠"),
            ("games_100", "100 игр", "🥇"),
            ("millionaire", "Миллионер (1 000 000)", "💰"),
            ("billionaire", "Миллиардер (1 000 000 000)", "💎"),
            ("losses_streak_10", "10 проигрышей подряд", "🔥"),
            ("risky", "Рискованный (10 проигрышей подряд)", "🔥"),
            ("rebirth_first", "Первое перерождение", "🔄"),
            ("all_40_risk", "Все 40 risk-игр", "🎮"),
            ("birzh_10pct_day", "Биржа +10% за день", "📈"),
        ]
        for key, title, prefix in definitions:
            await self.execute(
                "INSERT OR IGNORE INTO achievement_definitions (achievement_key, title, prefix) VALUES (?, ?, ?)",
                (key, title, prefix)
            )
        logger.info("Справочник достижений инициализирован")
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ ====================
    
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Получение информации о пользователе
        
        Args:
            user_id: ID пользователя Telegram
            
        Returns:
            Словарь с данными пользователя или None
        """
        row = await self.fetchone(
            """SELECT user_id, username, balance, level, premium_until, status, 
                      created_at, last_active, is_banned, ban_until
               FROM users WHERE user_id = ?""",
            (user_id,)
        )
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "balance": row[2],
                "level": row[3],
                "premium_until": row[4],
                "status": row[5],
                "created_at": row[6],
                "last_active": row[7],
                "is_banned": bool(row[8]),
                "ban_until": row[9]
            }
        return None

    async def get_user_id_by_username(self, username: str) -> Optional[int]:
        """Получение user_id по username (без @). Сравнение без учёта регистра и пробелов."""
        if not username:
            return None
        username_clean = (str(username).strip().lstrip("@").strip().lower())
        if not username_clean:
            return None
        row = await self.fetchone(
            "SELECT user_id FROM users WHERE LOWER(TRIM(REPLACE(COALESCE(username,''), '@', ''))) = ?",
            (username_clean,)
        )
        return row[0] if row else None
    
    async def create_user(self, user_id: int, username: str = None) -> bool:
        """
        Создание нового пользователя в БД
        
        Args:
            user_id: ID пользователя Telegram
            username: Имя пользователя (опционально)
            
        Returns:
            True если создан, False если уже существует
        """
        try:
            now = int(datetime.now().timestamp())
            await self.execute(
                """INSERT OR IGNORE INTO users 
                   (user_id, username, balance, level, created_at, last_active)
                   VALUES (?, ?, 0, 1, ?, ?)""",
                (user_id, username, now, now)
            )
            # Создаем запись в profiles
            await self.execute(
                "INSERT OR IGNORE INTO profiles (user_id) VALUES (?)",
                (user_id,)
            )
            # Создаем запись в levels
            await self.execute(
                "INSERT OR IGNORE INTO levels (user_id, level, total_coins_earned, level_up_cost) VALUES (?, 1, 0, ?)",
                (user_id, config.LEVEL_UP_BASE_COST)
            )
            # Создаем запись в tax_states
            await self.execute(
                "INSERT OR IGNORE INTO tax_states (user_id, is_paid) VALUES (?, 1)",
                (user_id,)
            )
            logger.info(f"Создан новый пользователь: {user_id}")
            return True
        except Exception as e:
            logger.error(f"Ошибка создания пользователя {user_id}: {e}")
            return False
    
    async def update_user_username(self, user_id: int, username: str):
        """Обновление username пользователя"""
        await self.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id)
        )
    
    async def update_user_last_active(self, user_id: int):
        """Обновление времени последней активности"""
        now = int(datetime.now().timestamp())
        await self.execute(
            "UPDATE users SET last_active = ? WHERE user_id = ?",
            (now, user_id)
        )

    async def set_user_ban(self, user_id: int, is_banned: bool, ban_until: int = None) -> bool:
        """Установка бана пользователю. Создателя (CREATOR_ID) забанить нельзя."""
        if config.CREATOR_ID and user_id == config.CREATOR_ID:
            logger.warning(f"Попытка забанить создателя (user_id={user_id}) — отклонено")
            return False
        await self.execute(
            "UPDATE users SET is_banned = ?, ban_until = ? WHERE user_id = ?",
            (1 if is_banned else 0, ban_until, user_id)
        )
        return True

    async def insert_ban(self, user_id: int, banned_by: int, reason: str,
                        start_time: int, end_time: int = None, ban_type: str = "commands"):
        """Запись бана в таблицу bans."""
        await self.execute(
            """INSERT INTO bans (user_id, banned_by, reason, start_time, end_time, ban_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, banned_by, reason[:500], start_time, end_time, ban_type)
        )

    async def mark_ban_unbanned(self, user_id: int):
        """Отметить последний активный бан как разбаненный."""
        row = await self.fetchone(
            "SELECT id FROM bans WHERE user_id = ? AND unbanned_at IS NULL ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if row:
            now = int(datetime.now().timestamp())
            await self.execute("UPDATE bans SET unbanned_at = ? WHERE id = ?", (now, row[0]))

    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С БАЛАНСОМ ====================
    
    async def set_balance_direct(self, user_id: int, new_balance: int) -> bool:
        """Прямая установка баланса (для /skinna0, перерождения)."""
        await self.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (new_balance, user_id)
        )
        return True

    async def get_balance(self, user_id: int) -> int:
        """
        Получение текущего баланса пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Баланс пользователя (0 если пользователь не найден)
        """
        row = await self.fetchone(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        )
        return row[0] if row else 0
    
    async def update_balance(self, user_id: int, amount: int, 
                            transaction_type: str, command_source: str = None,
                            comment: str = None, allow_negative: bool = False) -> Tuple[int, int]:
        """
        Изменение баланса пользователя с записью транзакции
        
        Args:
            user_id: ID пользователя
            amount: Изменение баланса (положительное для начисления, отрицательное для списания)
            transaction_type: Тип транзакции (income/expense)
            command_source: Команда-источник транзакции
            comment: Комментарий к транзакции
            allow_negative: Разрешить отрицательный баланс (по умолчанию False)
            
        Returns:
            Кортеж (баланс_до, баланс_после)
        """
        balance_before = await self.get_balance(user_id)
        balance_after = balance_before + amount
        
        # Защита от отрицательного баланса (если не разрешено)
        if not allow_negative and balance_after < 0:
            logger.warning(
                f"Попытка установить отрицательный баланс для user_id={user_id}: "
                f"balance_before={balance_before}, amount={amount}, balance_after={balance_after}"
            )
            # Не изменяем баланс, но записываем транзакцию как неудачную
            balance_after = balance_before
        
        # Обновляем баланс
        await self.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (balance_after, user_id)
        )
        
        # Записываем транзакцию
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT INTO transactions 
               (user_id, transaction_type, amount, balance_before, balance_after, 
                command_source, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, transaction_type, amount, balance_before, balance_after,
             command_source, comment, now)
        )
        
        return balance_before, balance_after
    
    async def get_top_users(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Получение топа пользователей по балансу
        
        Args:
            limit: Количество пользователей в топе
            
        Returns:
            Список словарей с данными пользователей
        """
        rows = await self.fetchall(
            """SELECT user_id, username, balance, level, status, premium_until
               FROM users 
               WHERE is_banned = 0
               ORDER BY balance DESC, level DESC
               LIMIT ?""",
            (limit,)
        )
        return [
            {
                "user_id": row[0],
                "username": row[1],
                "balance": row[2],
                "level": row[3],
                "status": row[4],
                "premium_until": row[5]
            }
            for row in rows
        ]
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С COOLDOWN'АМИ ====================
    
    async def get_cooldown(self, user_id: int, command: str) -> Optional[int]:
        """
        Получение времени последнего использования команды
        
        Args:
            user_id: ID пользователя
            command: Название команды
            
        Returns:
            Timestamp последнего использования или None
        """
        row = await self.fetchone(
            "SELECT last_used FROM cooldowns WHERE user_id = ? AND command = ?",
            (user_id, command)
        )
        return row[0] if row else None
    
    async def set_cooldown(self, user_id: int, command: str):
        """
        Установка времени последнего использования команды
        
        Args:
            user_id: ID пользователя
            command: Название команды
        """
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT OR REPLACE INTO cooldowns (user_id, command, last_used)
               VALUES (?, ?, ?)""",
            (user_id, command, now)
        )
    
    async def reset_cooldown(self, user_id: int, command: str):
        """Сброс cooldown для команды (для реф-кода #PADLOPLAY)"""
        await self.execute(
            "DELETE FROM cooldowns WHERE user_id = ? AND command = ?",
            (user_id, command)
        )
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ЭФФЕКТАМИ ====================
    
    async def add_effect(self, user_id: int, effect_type: str, duration_seconds: int,
                         multiplier: float = 1.0, metadata: str = None) -> int:
        """
        Добавление временного эффекта пользователю
        
        Args:
            user_id: ID пользователя
            effect_type: Тип эффекта (premium, potion_x1.5, kachalka и т.д.)
            duration_seconds: Длительность эффекта в секундах
            multiplier: Множитель эффекта
            metadata: Дополнительные данные в JSON формате
            
        Returns:
            ID созданного эффекта
        """
        now = int(datetime.now().timestamp())
        expires_at = now + duration_seconds
        
        cursor = await self.execute(
            """INSERT INTO effects (user_id, effect_type, multiplier, started_at, expires_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, effect_type, multiplier, now, expires_at, metadata)
        )
        return cursor.lastrowid
    
    async def get_active_effects(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Получение всех активных эффектов пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Список словарей с данными эффектов
        """
        now = int(datetime.now().timestamp())
        rows = await self.fetchall(
            """SELECT id, effect_type, multiplier, started_at, expires_at, metadata
               FROM effects
               WHERE user_id = ? AND expires_at > ?
               ORDER BY expires_at ASC""",
            (user_id, now)
        )
        return [
            {
                "id": row[0],
                "effect_type": row[1],
                "multiplier": row[2],
                "started_at": row[3],
                "expires_at": row[4],
                "metadata": row[5]
            }
            for row in rows
        ]
    
    async def remove_expired_effects(self):
        """Удаление истекших эффектов (вызывается периодически)"""
        now = int(datetime.now().timestamp())
        await self.execute(
            "DELETE FROM effects WHERE expires_at <= ?",
            (now,)
        )
    
    async def has_effect(self, user_id: int, effect_type: str) -> bool:
        """Проверка наличия активного эффекта определенного типа"""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT 1 FROM effects WHERE user_id = ? AND effect_type = ? AND expires_at > ?",
            (user_id, effect_type, now)
        )
        return row is not None
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ИНВЕНТАРЕМ ====================
    
    async def add_item_to_inventory(self, user_id: int, item_type: str, item_name: str,
                                   quality_level: int = 0, quantity: int = 1,
                                   multiplier: float = 1.0) -> int:
        """
        Добавление предмета в инвентарь пользователя
        
        Args:
            user_id: ID пользователя
            item_type: Тип предмета (potion, toy и т.д.)
            item_name: Название предмета
            quality_level: Уровень качества (0-4 для игрушек)
            quantity: Количество
            multiplier: Множитель (для зелий)
            
        Returns:
            ID созданной записи
        """
        now = int(datetime.now().timestamp())
        cursor = await self.execute(
            """INSERT INTO inventory 
               (user_id, item_type, item_name, quality_level, quantity, multiplier, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, item_type, item_name, quality_level, quantity, multiplier, now)
        )
        return cursor.lastrowid
    
    async def get_user_inventory(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Получение инвентаря пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Список словарей с предметами
        """
        rows = await self.fetchall(
            """SELECT id, item_type, item_name, quality_level, quantity, multiplier, created_at
               FROM inventory
               WHERE user_id = ?
               ORDER BY item_type, item_name, quality_level DESC""",
            (user_id,)
        )
        return [
            {
                "id": row[0],
                "item_type": row[1],
                "item_name": row[2],
                "quality_level": row[3],
                "quantity": row[4],
                "multiplier": row[5],
                "created_at": row[6]
            }
            for row in rows
        ]
    
    async def remove_item_from_inventory(self, item_id: int, user_id: int) -> bool:
        """
        Удаление предмета из инвентаря
        
        Args:
            item_id: ID предмета
            user_id: ID пользователя (для проверки владельца)
            
        Returns:
            True если удалено, False если не найдено
        """
        cursor = await self.execute(
            "DELETE FROM inventory WHERE id = ? AND user_id = ?",
            (item_id, user_id)
        )
        return cursor.rowcount > 0
    
    async def update_item_quality(self, item_id: int, user_id: int, new_quality: int, new_multiplier: float):
        """Обновление качества предмета (для крафта)"""
        await self.execute(
            "UPDATE inventory SET quality_level = ?, multiplier = ? WHERE id = ? AND user_id = ?",
            (new_quality, new_multiplier, item_id, user_id)
        )
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С РЕФ-КОДАМИ ====================
    
    async def get_refcode(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Получение информации о реф-коде
        
        Args:
            code: Код без символа #
            
        Returns:
            Словарь с данными кода или None
        """
        row = await self.fetchone(
            """SELECT code, reward_type, reward_value, activated_by, activated_at, is_active
               FROM refcodes WHERE code = ?""",
            (code.upper(),)
        )
        if row:
            return {
                "code": row[0],
                "reward_type": row[1],
                "reward_value": row[2],
                "activated_by": row[3],
                "activated_at": row[4],
                "is_active": bool(row[5])
            }
        return None
    
    async def activate_refcode(self, code: str, user_id: int) -> bool:
        """
        Активация реф-кода пользователем
        
        Args:
            code: Код без символа #
            user_id: ID пользователя
            
        Returns:
            True если активирован, False если уже был активирован
        """
        refcode = await self.get_refcode(code)
        if not refcode or refcode["activated_by"] is not None:
            return False
        
        now = int(datetime.now().timestamp())
        await self.execute(
            "UPDATE refcodes SET activated_by = ?, activated_at = ? WHERE code = ?",
            (user_id, now, code.upper())
        )
        return True
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С PREMIUM ====================
    
    async def set_premium(self, user_id: int, duration_seconds: int):
        """
        Установка Premium статуса пользователю
        
        Args:
            user_id: ID пользователя
            duration_seconds: Длительность Premium в секундах
        """
        now = int(datetime.now().timestamp())
        current_premium = await self.fetchone(
            "SELECT premium_until FROM users WHERE user_id = ?",
            (user_id,)
        )
        
        if current_premium and current_premium[0] and current_premium[0] > now:
            # Продлеваем существующий Premium
            new_premium_until = current_premium[0] + duration_seconds
            # Вычисляем реальную длительность для эффекта (от текущего момента)
            effect_duration = new_premium_until - now
        else:
            # Создаем новый Premium
            new_premium_until = now + duration_seconds
            effect_duration = duration_seconds
        
        await self.execute(
            "UPDATE users SET premium_until = ? WHERE user_id = ?",
            (new_premium_until, user_id)
        )
        
        # Удаляем старые эффекты Premium перед добавлением нового
        await self.execute(
            "DELETE FROM effects WHERE user_id = ? AND effect_type = 'premium'",
            (user_id,)
        )
        
        # Добавляем новый эффект Premium
        await self.add_effect(user_id, "premium", effect_duration, multiplier=1.0)
    
    async def is_premium(self, user_id: int) -> bool:
        """Проверка наличия активного Premium"""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT premium_until FROM users WHERE user_id = ? AND premium_until > ?",
            (user_id, now)
        )
        return row is not None
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С УРОВНЯМИ ====================
    
    async def get_user_level(self, user_id: int) -> Dict[str, Any]:
        """
        Получение информации об уровне пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Словарь с данными уровня
        """
        row = await self.fetchone(
            """SELECT level, total_coins_earned, level_up_cost, last_level_up
               FROM levels WHERE user_id = ?""",
            (user_id,)
        )
        if row:
            return {
                "level": row[0],
                "total_coins_earned": row[1],
                "level_up_cost": row[2],
                "last_level_up": row[3]
            }
        # Если записи нет, создаем
        await self.execute(
            "INSERT OR IGNORE INTO levels (user_id, level, total_coins_earned, level_up_cost) VALUES (?, 1, 0, ?)",
            (user_id, config.LEVEL_UP_BASE_COST)
        )
        return {
            "level": 1,
            "total_coins_earned": 0,
            "level_up_cost": config.LEVEL_UP_BASE_COST,
            "last_level_up": None
        }
    
    async def level_up(self, user_id: int) -> Tuple[int, int]:
        """
        Повышение уровня пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Кортеж (старый_уровень, новый_уровень)
        """
        level_info = await self.get_user_level(user_id)
        old_level = level_info["level"]
        new_level = old_level + 1
        
        # Обновляем уровень в таблице levels
        now = int(datetime.now().timestamp())
        new_cost = int(config.LEVEL_UP_BASE_COST * (config.LEVEL_UP_COST_MULTIPLIER ** (new_level - 1)))
        
        await self.execute(
            """UPDATE levels SET level = ?, last_level_up = ?, level_up_cost = ?
               WHERE user_id = ?""",
            (new_level, now, new_cost, user_id)
        )
        
        # Обновляем уровень в таблице users
        await self.execute(
            "UPDATE users SET level = ? WHERE user_id = ?",
            (new_level, user_id)
        )
        
        return old_level, new_level
    
    async def update_total_coins(self, user_id: int, amount: int):
        """Обновление общего количества заработанных коинов (для автоматического повышения уровня)"""
        await self.execute(
            "UPDATE levels SET total_coins_earned = total_coins_earned + ? WHERE user_id = ?",
            (amount, user_id)
        )
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ПРОФИЛЯМИ ====================
    
    async def get_profile(self, user_id: int) -> Dict[str, Any]:
        """Получение профиля пользователя (включая bot_address — как бот обращается к игроку)."""
        try:
            row = await self.fetchone(
                """SELECT avatar_path, vip_address, about_info, selected_status, bot_address
                   FROM profiles WHERE user_id = ?""",
                (user_id,)
            )
        except Exception:
            row = await self.fetchone(
                """SELECT avatar_path, vip_address, about_info, selected_status
                   FROM profiles WHERE user_id = ?""",
                (user_id,)
            )
            row = (row[0], row[1], row[2], row[3], None) if row else None
        if row:
            return {
                "avatar_path": row[0],
                "vip_address": row[1],
                "about_info": row[2],
                "selected_status": row[3],
                "bot_address": row[4] if len(row) > 4 else None
            }
        return {
            "avatar_path": None,
            "vip_address": None,
            "about_info": None,
            "selected_status": None,
            "bot_address": None
        }
    
    async def update_profile(self, user_id: int, avatar_path: str = None,
                           vip_address: str = None, about_info: str = None,
                           selected_status: str = None, bot_address: str = None):
        """Обновление профиля пользователя"""
        updates = []
        params = []
        
        if avatar_path is not None:
            updates.append("avatar_path = ?")
            params.append(avatar_path)
        if vip_address is not None:
            updates.append("vip_address = ?")
            params.append(vip_address)
        if about_info is not None:
            updates.append("about_info = ?")
            params.append(about_info)
        if selected_status is not None:
            updates.append("selected_status = ?")
            params.append(selected_status)
        if bot_address is not None:
            updates.append("bot_address = ?")
            params.append(bot_address)
        
        if updates:
            params.append(user_id)
            await self.execute(
                f"UPDATE profiles SET {', '.join(updates)} WHERE user_id = ?",
                tuple(params)
            )
    
    async def get_user_game_stats(self, user_id: int) -> Dict[str, int]:
        """Статистика игр: всего игр, побед, поражений (из games_sessions)."""
        row = await self.fetchone(
            """SELECT 
                   COUNT(*) as total,
                   SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END)
               FROM games_sessions WHERE user_id = ?""",
            (user_id,)
        )
        if row and row[0] is not None:
            return {"total": row[0] or 0, "wins": row[1] or 0, "losses": row[2] or 0}
        return {"total": 0, "wins": 0, "losses": 0}

    async def get_last_game_sessions(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Последние N игр пользователя для /echo (архетип, стиль)."""
        rows = await self.fetchall(
            """SELECT game_type, bet, result, amount_change, multiplier
               FROM games_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit)
        )
        if not rows:
            return []
        return [
            {"game_type": r[0], "bet": r[1], "result": r[2], "amount_change": r[3] or 0, "multiplier": r[4] or 1.0}
            for r in rows
        ]

    async def get_top_games_stats(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ игр по количеству запусков для /topgame: command, total, wins, losses."""
        rows = await self.fetchall(
            """SELECT command,
                      COUNT(*) as total,
                      SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses
               FROM admin_game_logs
               WHERE command IS NOT NULL AND command != ''
               GROUP BY command
               ORDER BY total DESC
               LIMIT ?""",
            (limit,)
        )
        if not rows:
            return []
        return [
            {"command": r[0], "total": r[1] or 0, "wins": r[2] or 0, "losses": r[3] or 0}
            for r in rows
        ]

    async def get_top_games_stats_with_trend(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ игр + статистика за последние 24ч и предыдущие 24ч для тренда (В тренде / Стабильно / Умирает)."""
        now = int(datetime.now().timestamp())
        last_24 = now - 86400
        prev_24 = now - 172800
        rows = await self.fetchall(
            """SELECT command,
                      COUNT(*) as total,
                      SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                      SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as total_24h,
                      SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) as total_prev_24h
               FROM admin_game_logs
               WHERE command IS NOT NULL AND command != ''
               GROUP BY command
               ORDER BY total DESC
               LIMIT ?""",
            (last_24, prev_24, last_24, limit)
        )
        if not rows:
            return []
        return [
            {
                "command": r[0],
                "total": r[1] or 0,
                "wins": r[2] or 0,
                "losses": r[3] or 0,
                "total_24h": r[4] or 0,
                "total_prev_24h": r[5] or 0,
            }
            for r in rows
        ]

    async def get_active_event(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Активный ивент пользователя (если не истёк)."""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT event_type, ends_at FROM user_events WHERE user_id = ? AND ends_at > ?",
            (user_id, now)
        )
        if not row:
            return None
        return {"event_type": row[0], "ends_at": row[1]}

    async def set_user_event(self, user_id: int, event_type: str, duration_seconds: int) -> None:
        """Установить ивент пользователю."""
        now = int(datetime.now().timestamp())
        ends_at = now + duration_seconds
        await self.execute(
            """INSERT OR REPLACE INTO user_events (user_id, event_type, ends_at) VALUES (?, ?, ?)""",
            (user_id, event_type, ends_at)
        )
        await self.execute(
            """INSERT INTO user_event_history (user_id, event_type, started_at, ends_at) VALUES (?, ?, ?, ?)""",
            (user_id, event_type, now, ends_at)
        )

    async def get_last_event_ended_at(self, user_id: int) -> Optional[int]:
        """Время окончания последнего ивента (для кулдауна 2–4 ч)."""
        row = await self.fetchone(
            """SELECT MAX(ends_at) FROM user_event_history WHERE user_id = ?""",
            (user_id,)
        )
        return int(row[0]) if row and row[0] else None

    async def get_echo_last_reward_date(self, user_id: int) -> Optional[int]:
        """Дата последней выдачи 50 коинов за /echo (YYYYMMDD)."""
        row = await self.fetchone("SELECT reward_date FROM echo_reward_dates WHERE user_id = ?", (user_id,))
        return int(row[0]) if row and row[0] else None

    async def set_echo_reward_date(self, user_id: int, reward_date: int) -> None:
        """Записать дату выдачи награды /echo (YYYYMMDD)."""
        await self.execute(
            """INSERT OR REPLACE INTO echo_reward_dates (user_id, reward_date) VALUES (?, ?)""",
            (user_id, reward_date)
        )

    # ==================== MMR И ЛИГИ ====================

    LEAGUE_RANGES = [
        (0, 99, "🟤 Новичок"),
        (100, 499, "🟢 Игрок"),
        (500, 999, "🔵 Профи"),
        (1000, 1999, "🟣 Эксперт"),
        (2000, 10**9, "🟡 Легенда"),
    ]

    async def get_user_mmr(self, user_id: int) -> int:
        """Получить MMR пользователя (0 если колонки нет или пользователя нет)."""
        try:
            row = await self.fetchone("SELECT mmr FROM users WHERE user_id = ?", (user_id,))
            return int(row[0]) if row else 0
        except Exception:
            return 0

    MMR_MIN_GAMES_FOR_LEGEND = 60
    MMR_SAME_GAME_PENALTY_AFTER = 10

    async def get_last_game_types(self, user_id: int, limit: int = 12) -> List[str]:
        """Последние game_type по games_sessions (для анти-абуза одной игры)."""
        try:
            rows = await self.fetchall(
                "SELECT game_type FROM games_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            )
            return [r[0] for r in (rows or []) if r and r[0]]
        except Exception:
            return []

    async def get_total_games_count(self, user_id: int) -> int:
        """Общее количество сыгранных игр (games_sessions)."""
        try:
            row = await self.fetchone("SELECT COUNT(*) FROM games_sessions WHERE user_id = ?", (user_id,))
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0

    async def update_mmr(self, user_id: int, delta: int, game_type: Optional[str] = None) -> int:
        """Изменить MMR на delta. Анти-абуз: если последние 10+ игр одной и той же — снижаем прирост и усиливаем потери. До лиги Легенда — минимум 60 игр."""
        current = await self.get_user_mmr(user_id)
        if game_type:
            last_types = await self.get_last_game_types(user_id, limit=12)
            same_count = sum(1 for t in last_types if t == game_type)
            if same_count >= self.MMR_SAME_GAME_PENALTY_AFTER:
                if delta > 0:
                    delta = max(1, int(delta * 0.25))
                else:
                    delta = int(delta * 1.3)
        new_mmr = max(0, current + delta)
        total_games = await self.get_total_games_count(user_id)
        if new_mmr >= 2000 and total_games < self.MMR_MIN_GAMES_FOR_LEGEND:
            new_mmr = min(new_mmr, 1999)
        try:
            await self.execute("UPDATE users SET mmr = ? WHERE user_id = ?", (new_mmr, user_id))
        except Exception:
            pass
        return new_mmr

    def get_league_by_mmr(self, mmr: int) -> str:
        """Название лиги по MMR."""
        for low, high, name in self.LEAGUE_RANGES:
            if low <= mmr <= high:
                return name
        return self.LEAGUE_RANGES[0][2]

    def get_mmr_to_next_league(self, mmr: int) -> Optional[int]:
        """MMR до следующей лиги (None если уже Легенда)."""
        for i, (low, high, _) in enumerate(self.LEAGUE_RANGES):
            if low <= mmr <= high and i + 1 < len(self.LEAGUE_RANGES):
                next_low = self.LEAGUE_RANGES[i + 1][0]
                return max(0, next_low - mmr)
        return None

    def get_league_info(self, mmr: int) -> Dict[str, Any]:
        """Полная сводка по лиге для профиля: название, диапазон, прогресс внутри лиги (0–1), MMR до следующей."""
        for i, (low, high, name) in enumerate(self.LEAGUE_RANGES):
            if low <= mmr <= high:
                span = high - low + 1
                mmr_in = mmr - low
                progress = mmr_in / span if span else 0
                to_next = None
                if i + 1 < len(self.LEAGUE_RANGES):
                    next_low = self.LEAGUE_RANGES[i + 1][0]
                    to_next = max(0, next_low - mmr)
                return {
                    "name": name,
                    "low": low,
                    "high": high,
                    "mmr_in_league": mmr_in,
                    "span": span,
                    "progress": min(1.0, progress),
                    "to_next_league": to_next,
                }
        low, high, name = self.LEAGUE_RANGES[0]
        return {"name": name, "low": low, "high": high, "mmr_in_league": 0, "span": high - low + 1, "progress": 0.0, "to_next_league": max(0, 100 - mmr)}

    # ==================== БИРЖА /birzh ====================

    BIRZH_PRICE_MIN, BIRZH_PRICE_MAX = 1, 100
    BIRZH_TICK_SEC = 30
    BIRZH_TECHNOLOG_RUB_MIN, BIRZH_TECHNOLOG_RUB_MAX = 0.1, 3.0
    BIRZH_COINS = {
        "sharaga": (1, 100, "sharaga_balance"),
        "kris": (500, 2000, "kris_balance"),
        "jd": (5000, 10000, "jd_balance"),
        "lisaya": (20000, 100000, "lisaya_balance"),
    }

    async def get_birzh_price(self) -> Tuple[int, int, float]:
        """Текущая цена шарага, updated_at, технолог-коин ₽. Обратная совместимость."""
        data = await self.get_birzh_all_prices()
        return data["sharaga"], data["updated_at"], data["technolog_rub"]

    async def get_birzh_all_prices(self) -> Dict[str, Any]:
        """Все курсы биржи: sharaga, kris, jd, lisaya (коины за 100), technolog_rub (₽). Обновляет при тике."""
        try:
            row = await self.fetchone(
                "SELECT price, updated_at, technolog_rub, kris_price, jd_price, lisaya_price FROM birzh_state WHERE id = 1"
            )
        except Exception:
            row = await self.fetchone("SELECT price, updated_at FROM birzh_state WHERE id = 1")
            row = (row[0], row[1], 1.0, 1250, 7500, 60000) if row else None
        if not row:
            return {"sharaga": 50, "kris": 1250, "jd": 7500, "lisaya": 60000, "technolog_rub": 1.0, "updated_at": 0}
        price, updated_at = row[0], row[1]
        technolog_rub = row[2] if len(row) > 2 and row[2] is not None else 1.0
        kris = row[3] if len(row) > 3 and row[3] is not None else 1250
        jd = row[4] if len(row) > 4 and row[4] is not None else 7500
        lisaya = row[5] if len(row) > 5 and row[5] is not None else 60000
        now = int(datetime.now().timestamp())
        if now - updated_at >= self.BIRZH_TICK_SEC:
            delta = random.randint(-5, 5)
            if delta == 0:
                delta = random.choice([-1, 1])
            birzh_day = await self.get_global_event("birzh_day")
            if birzh_day:
                delta = max(-2, min(2, delta))
            price = max(self.BIRZH_PRICE_MIN, min(self.BIRZH_PRICE_MAX, price + delta))
            technolog_rub = round(random.uniform(self.BIRZH_TECHNOLOG_RUB_MIN, self.BIRZH_TECHNOLOG_RUB_MAX), 1)
            low, high = self.BIRZH_COINS["kris"][0], self.BIRZH_COINS["kris"][1]
            kris = max(low, min(high, kris + random.randint(-50, 50)))
            low, high = self.BIRZH_COINS["jd"][0], self.BIRZH_COINS["jd"][1]
            jd = max(low, min(high, jd + random.randint(-200, 200)))
            low, high = self.BIRZH_COINS["lisaya"][0], self.BIRZH_COINS["lisaya"][1]
            lisaya = max(low, min(high, lisaya + random.randint(-1000, 1000)))
            try:
                await self.execute(
                    """UPDATE birzh_state SET price = ?, updated_at = ?, technolog_rub = ?, kris_price = ?, jd_price = ?, lisaya_price = ? WHERE id = 1""",
                    (price, now, technolog_rub, kris, jd, lisaya)
                )
            except Exception:
                await self.execute("UPDATE birzh_state SET price = ?, updated_at = ? WHERE id = 1", (price, now))
        return {"sharaga": price, "kris": kris, "jd": jd, "lisaya": lisaya, "technolog_rub": technolog_rub, "updated_at": updated_at}

    async def get_user_sharaga(self, user_id: int) -> int:
        """Баланс шарага-коинов у пользователя."""
        row = await self.fetchone("SELECT sharaga_balance FROM user_birzh WHERE user_id = ?", (user_id,))
        return row[0] if row else 0

    async def get_user_birzh_all(self, user_id: int) -> Dict[str, int]:
        """Все балансы биржевых коинов: sharaga, kris, jd, lisaya."""
        try:
            row = await self.fetchone(
                "SELECT sharaga_balance, COALESCE(kris_balance,0), COALESCE(jd_balance,0), COALESCE(lisaya_balance,0) FROM user_birzh WHERE user_id = ?",
                (user_id,)
            )
        except Exception:
            row = await self.fetchone("SELECT sharaga_balance FROM user_birzh WHERE user_id = ?", (user_id,))
            return {"sharaga": row[0] if row else 0, "kris": 0, "jd": 0, "lisaya": 0}
        if not row:
            return {"sharaga": 0, "kris": 0, "jd": 0, "lisaya": 0}
        return {"sharaga": row[0], "kris": row[1] if len(row) > 1 else 0, "jd": row[2] if len(row) > 2 else 0, "lisaya": row[3] if len(row) > 3 else 0}

    async def birzh_buy_100(self, user_id: int, price_koins: int, coin_type: str = "sharaga") -> bool:
        """Купить 100 единиц коина за price_koins. coin_type: sharaga, kris, jd, lisaya."""
        balance = await self.get_balance(user_id)
        if balance < price_koins:
            return False
        await self.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price_koins, user_id))
        col = self.BIRZH_COINS.get(coin_type, (0, 0, "sharaga_balance"))[2]
        await self.execute(
            """INSERT INTO user_birzh (user_id, sharaga_balance) VALUES (?, 0)
               ON CONFLICT(user_id) DO NOTHING""",
            (user_id,)
        )
        await self.execute(
            f"UPDATE user_birzh SET {col} = COALESCE({col}, 0) + 100 WHERE user_id = ?",
            (user_id,)
        )
        return True

    async def birzh_sell_100(self, user_id: int, price_koins: int, coin_type: str = "sharaga") -> bool:
        """Продать 100 единиц коина за price_koins."""
        balances = await self.get_user_birzh_all(user_id)
        if balances.get(coin_type, 0) < 100:
            return False
        col = self.BIRZH_COINS.get(coin_type, (0, 0, "sharaga_balance"))[2]
        await self.execute(f"UPDATE user_birzh SET {col} = COALESCE({col}, 0) - 100 WHERE user_id = ?", (user_id,))
        await self.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price_koins, user_id))
        return True

    # ==================== БЕСПЛАТНАЯ ИГРА ПРИ БАЛАНСЕ 0 ====================

    async def get_free_game_used_today(self, user_id: int) -> bool:
        """Использовал ли пользователь бесплатную игру сегодня (при балансе 0)."""
        from datetime import date
        today = date.today().isoformat()
        row = await self.fetchone(
            "SELECT 1 FROM free_game_daily WHERE user_id = ? AND last_used_date = ?",
            (user_id, today)
        )
        return row is not None

    async def set_free_game_used_today(self, user_id: int) -> None:
        """Отметить использование бесплатной игры сегодня."""
        from datetime import date
        today = date.today().isoformat()
        await self.execute(
            """INSERT OR REPLACE INTO free_game_daily (user_id, last_used_date) VALUES (?, ?)""",
            (user_id, today)
        )

    # ==================== ДОСТИЖЕНИЯ ====================

    async def get_user_achievements(self, user_id: int) -> List[Dict[str, Any]]:
        """Список достижений пользователя (ключ, название, префикс, дата)."""
        rows = await self.fetchall(
            """SELECT ua.achievement_key, ad.title, ad.prefix, ua.unlocked_at
               FROM user_achievements ua
               JOIN achievement_definitions ad ON ad.achievement_key = ua.achievement_key
               WHERE ua.user_id = ?
               ORDER BY ua.unlocked_at ASC""",
            (user_id,)
        )
        return [
            {"key": r[0], "title": r[1], "prefix": r[2] or "", "unlocked_at": r[3]}
            for r in (rows or [])
        ]

    async def has_achievement(self, user_id: int, achievement_key: str) -> bool:
        """Есть ли у пользователя достижение."""
        row = await self.fetchone(
            "SELECT 1 FROM user_achievements WHERE user_id = ? AND achievement_key = ?",
            (user_id, achievement_key)
        )
        return row is not None

    async def unlock_achievement(self, user_id: int, achievement_key: str) -> bool:
        """Выдать достижение (если ещё не выдано). Возвращает True если только что выдано."""
        if await self.has_achievement(user_id, achievement_key):
            return False
        now = int(datetime.now().timestamp())
        await self.execute(
            "INSERT OR IGNORE INTO user_achievements (user_id, achievement_key, unlocked_at) VALUES (?, ?, ?)",
            (user_id, achievement_key, now)
        )
        return True

    # ==================== СЕЗОНЫ И КУБКИ ====================

    async def get_current_season(self) -> Optional[Dict[str, Any]]:
        """Текущий сезон (где ends_at > now)."""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT id, name, started_at, ends_at FROM seasons WHERE ends_at > ? ORDER BY ends_at ASC LIMIT 1",
            (now,)
        )
        if not row:
            return None
        return {"id": row[0], "name": row[1], "started_at": row[2], "ends_at": row[3]}

    async def end_current_season_and_start_new(self) -> Optional[Dict[str, Any]]:
        """Завершить текущий сезон (сброс MMR), создать новый. Возвращает новый сезон."""
        now = int(datetime.now().timestamp())
        cur = await self.get_current_season()
        if not cur:
            return None
        await self.execute("UPDATE users SET mmr = 0")
        new_end = now + 90 * 86400
        await self.execute(
            "INSERT INTO seasons (name, started_at, ends_at) VALUES (?, ?, ?)",
            (f"Сезон {(cur['id'] or 0) + 1}", now, new_end)
        )
        row = await self.fetchone("SELECT id, name, started_at, ends_at FROM seasons ORDER BY id DESC LIMIT 1")
        return {"id": row[0], "name": row[1], "started_at": row[2], "ends_at": row[3]} if row else None

    async def cap_all_balances(self, max_balance: int) -> int:
        """Вайп: обрезать все балансы выше max_balance. Возвращает кол-во затронутых пользователей."""
        if max_balance < 0:
            return 0
        cur = await self.execute("UPDATE users SET balance = ? WHERE balance > ?", (max_balance, max_balance))
        return cur.rowcount if cur and hasattr(cur, "rowcount") else 0

    async def get_top_by_mmr(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ игроков по MMR (для наград за сезон)."""
        rows = await self.fetchall(
            """SELECT u.user_id, u.username, u.mmr FROM users u
               WHERE u.is_banned = 0 AND u.mmr > 0 ORDER BY u.mmr DESC LIMIT ?""",
            (limit,)
        )
        return [{"user_id": r[0], "username": r[1] or "", "mmr": r[2]} for r in (rows or [])]

    async def add_cup_win(self, user_id: int, game_slug: str) -> None:
        """Учесть победу в кубке по игре за текущий сезон."""
        season = await self.get_current_season()
        if not season:
            return
        row = await self.fetchone(
            "SELECT wins FROM cup_wins WHERE season_id = ? AND game_slug = ? AND user_id = ?",
            (season["id"], game_slug, user_id)
        )
        if row:
            await self.execute(
                "UPDATE cup_wins SET wins = wins + 1 WHERE season_id = ? AND game_slug = ? AND user_id = ?",
                (season["id"], game_slug, user_id)
            )
        else:
            await self.execute(
                "INSERT INTO cup_wins (season_id, game_slug, user_id, wins) VALUES (?, ?, ?, 1)",
                (season["id"], game_slug, user_id)
            )

    async def get_cup_leaderboard(self, game_slug: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Лидерборд кубка по игре за текущий сезон."""
        season = await self.get_current_season()
        if not season:
            return []
        rows = await self.fetchall(
            """SELECT cw.user_id, u.username, cw.wins FROM cup_wins cw
               JOIN users u ON u.user_id = cw.user_id
               WHERE cw.season_id = ? AND cw.game_slug = ? AND u.is_banned = 0
               ORDER BY cw.wins DESC LIMIT ?""",
            (season["id"], game_slug, limit)
        )
        return [{"user_id": r[0], "username": r[1] or str(r[0]), "wins": r[2]} for r in (rows or [])]

    # ==================== ТИП ИГРОКА (НОВИЧОК / ПРО) ====================

    async def get_user_tier(self, user_id: int) -> str:
        """Тип игрока: newcomer (< 30 игр и низкий lvl), regular, pro (много игр, высокий MMR/уровень)."""
        total = await self.get_total_games_count(user_id)
        mmr = await self.get_user_mmr(user_id)
        user = await self.get_user(user_id)
        level = (user or {}).get("level", 1) or 1
        if total < 30 and level < 5:
            return "newcomer"
        if total >= 200 or mmr >= 1500 or level >= 25:
            return "pro"
        return "regular"

    # ==================== БОЕВОЙ ПРОПУСК ====================

    async def _init_bp_levels_and_quests(self) -> None:
        """Заполнить уровни и квесты для первого сезона БП."""
        row = await self.fetchone("SELECT id FROM bp_seasons ORDER BY id DESC LIMIT 1")
        if not row:
            return
        sid = row[0]
        for lvl in range(1, 51):
            xp_need = 100 + lvl * 30
            free_coins = 50 + lvl * 10
            prem_coins = 100 + lvl * 25
            await self.execute(
                """INSERT OR REPLACE INTO bp_levels (season_id, level, xp_required, reward_free_type, reward_free_value, reward_premium_type, reward_premium_value)
                   VALUES (?, ?, ?, 'coins', ?, 'coins', ?)""",
                (sid, lvl, xp_need, free_coins, prem_coins)
            )
        quests = [
            ("play_5", "Сыграть 5 игр", 50, "daily", 5),
            ("win_3", "Победить в 3 играх", 80, "daily", 3),
            ("earn_500", "Заработать 500 коинов за сессию", 100, "daily", 500),
            ("fracture_1", "Пройди 1 излом", 60, "daily", 1),
            ("slot_1", "Сыграть в слот 1 раз", 40, "daily", 1),
            ("birzh_1", "Сделать сделку на бирже 1 раз", 40, "daily", 1),
            ("minigame_3", "Сыграть 3 мини-игры", 55, "daily", 3),
            ("play_20", "Сыграть 20 игр", 200, "weekly", 20),
            ("win_10", "Победить в 10 играх", 300, "weekly", 10),
            ("earn_2000", "Заработать 2000 коинов", 250, "weekly", 2000),
            ("risk_5", "Сыграть в 5 разных risk-игр", 200, "weekly", 5),
            ("play_50", "Сыграть 50 игр", 400, "weekly", 50),
            ("win_slot_3", "Победить в слоте 3 раза", 180, "weekly", 3),
            ("earn_5000", "Заработать 5000 коинов", 350, "weekly", 5000),
        ]
        for qk, title, xp, qtype, target in quests:
            await self.execute(
                """INSERT OR IGNORE INTO bp_quests (season_id, quest_key, title, xp_reward, quest_type, target_value) VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, qk, title, xp, qtype, target)
            )

    async def get_current_bp_season(self) -> Optional[Dict[str, Any]]:
        """Текущий сезон боевого пропуска. Если сезон истёк — создаётся следующий."""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT id, name, started_at, ends_at FROM bp_seasons WHERE ends_at > ? ORDER BY ends_at ASC LIMIT 1",
            (now,)
        )
        if not row:
            await self._ensure_next_bp_season(now)
            row = await self.fetchone(
                "SELECT id, name, started_at, ends_at FROM bp_seasons WHERE ends_at > ? ORDER BY ends_at ASC LIMIT 1",
                (now,)
            )
        if not row:
            return None
        return {"id": row[0], "name": row[1], "started_at": row[2], "ends_at": row[3]}

    async def _ensure_next_bp_season(self, now: int) -> None:
        """Создать следующий сезон БП (если текущий истёк)."""
        last = await self.fetchone("SELECT id, name FROM bp_seasons ORDER BY id DESC LIMIT 1")
        next_num = (last[0] + 1) if last else 1
        end_bp = now + 90 * 86400
        await self.execute(
            "INSERT INTO bp_seasons (name, started_at, ends_at) VALUES (?, ?, ?)",
            (f"Боевой пропуск {next_num}", now, end_bp)
        )
        await self._init_bp_levels_and_quests()

    async def get_bp_levels(self, season_id: int, max_level: int = 50) -> List[Dict[str, Any]]:
        """Уровни пропуска: level, xp_required, reward_free_*, reward_premium_*."""
        rows = await self.fetchall(
            "SELECT level, xp_required, reward_free_type, reward_free_value, reward_premium_type, reward_premium_value FROM bp_levels WHERE season_id = ? AND level <= ? ORDER BY level",
            (season_id, max_level)
        )
        return [{"level": r[0], "xp_required": r[1], "reward_free_type": r[2], "reward_free_value": r[3], "reward_premium_type": r[4], "reward_premium_value": r[5]} for r in (rows or [])]

    async def get_user_bp_progress(self, user_id: int, season_id: int) -> Dict[str, Any]:
        """Прогресс пользователя в БП: level, xp."""
        row = await self.fetchone("SELECT level, xp FROM user_bp_progress WHERE user_id = ? AND season_id = ?", (user_id, season_id))
        if row:
            return {"level": row[0], "xp": row[1]}
        await self.execute("INSERT OR IGNORE INTO user_bp_progress (user_id, season_id, level, xp) VALUES (?, ?, 1, 0)", (user_id, season_id))
        return {"level": 1, "xp": 0}

    async def add_bp_xp(self, user_id: int, season_id: int, xp: int) -> None:
        """Добавить XP и при необходимости повысить уровень (по суммарному XP)."""
        await self.execute(
            "INSERT OR IGNORE INTO user_bp_progress (user_id, season_id, level, xp) VALUES (?, ?, 1, 0)",
            (user_id, season_id)
        )
        await self.execute(
            "UPDATE user_bp_progress SET xp = xp + ? WHERE user_id = ? AND season_id = ?",
            (xp, user_id, season_id)
        )
        row = await self.fetchone("SELECT level, xp FROM user_bp_progress WHERE user_id = ? AND season_id = ?", (user_id, season_id))
        if not row:
            return
        _, total_xp = row[0], row[1]
        levels = await self.get_bp_levels(season_id, max_level=50)
        xp_sum = 0
        new_level = 1
        for L in sorted(levels, key=lambda x: x["level"]):
            xp_sum += L["xp_required"]
            if total_xp >= xp_sum:
                new_level = L["level"]
        await self.execute(
            "UPDATE user_bp_progress SET level = ? WHERE user_id = ? AND season_id = ?",
            (new_level, user_id, season_id)
        )

    async def get_bp_quests(self, season_id: int) -> List[Dict[str, Any]]:
        """Список квестов сезона БП."""
        rows = await self.fetchall(
            "SELECT quest_key, title, xp_reward, quest_type, target_value FROM bp_quests WHERE season_id = ? ORDER BY quest_type, quest_key",
            (season_id,)
        )
        return [{"quest_key": r[0], "title": r[1], "xp_reward": r[2], "quest_type": r[3], "target_value": r[4]} for r in (rows or [])]

    async def get_user_bp_quest_progress(self, user_id: int, season_id: int) -> Dict[str, int]:
        """Прогресс по квестам: quest_key -> progress."""
        rows = await self.fetchall(
            "SELECT quest_key, progress FROM user_bp_quest_progress WHERE user_id = ? AND season_id = ?",
            (user_id, season_id)
        )
        return {r[0]: r[1] for r in (rows or [])}

    async def progress_bp_quest(self, user_id: int, season_id: int, quest_key: str, delta: int = 1) -> bool:
        """Увеличить прогресс квеста на delta. Возвращает True если квест выполнен и XP начислен."""
        from datetime import date
        today = date.today().isoformat()
        row = await self.fetchone(
            "SELECT progress, reset_date FROM user_bp_quest_progress WHERE user_id = ? AND season_id = ? AND quest_key = ?",
            (user_id, season_id, quest_key)
        )
        qrow = await self.fetchone("SELECT target_value, xp_reward, quest_type FROM bp_quests WHERE season_id = ? AND quest_key = ?", (season_id, quest_key))
        if not qrow:
            return False
        target, xp_reward, qtype = int(qrow[0]), int(qrow[1]), qrow[2]
        old_progress = int(row[0]) if row and row[0] is not None else 0
        reset_date = row[1] if row and row[1] else None
        if qtype == "daily" and reset_date != today:
            progress = delta
            reset_date = today
        else:
            progress = old_progress + delta
            if not reset_date:
                reset_date = today
        await self.execute(
            """INSERT INTO user_bp_quest_progress (user_id, season_id, quest_key, progress, reset_date) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, season_id, quest_key) DO UPDATE SET progress = excluded.progress, reset_date = excluded.reset_date""",
            (user_id, season_id, quest_key, progress, reset_date)
        )
        if progress >= target:
            await self.add_bp_xp(user_id, season_id, xp_reward)
            return True
        return False

    async def claim_bp_level_reward(self, user_id: int, season_id: int, level: int, is_premium: bool) -> bool:
        """Забрать награду за уровень. Возвращает True если награда выдана."""
        row = await self.fetchone(
            "SELECT 1 FROM user_bp_claimed WHERE user_id = ? AND season_id = ? AND level = ? AND is_premium = ?",
            (user_id, season_id, level, 1 if is_premium else 0)
        )
        if row:
            return False
        lrow = await self.fetchone(
            "SELECT reward_free_type, reward_free_value, reward_premium_type, reward_premium_value FROM bp_levels WHERE season_id = ? AND level = ?",
            (season_id, level)
        )
        if not lrow:
            return False
        rtype = lrow[2] if is_premium else lrow[0]
        rval = lrow[3] if is_premium else lrow[1]
        await self.execute(
            "INSERT INTO user_bp_claimed (user_id, season_id, level, is_premium) VALUES (?, ?, ?, ?)",
            (user_id, season_id, level, 1 if is_premium else 0)
        )
        if rtype == "coins" and rval > 0:
            await self.update_balance(user_id, rval, "income", "bp_reward", f"БП ур.{level}")
            await self.update_total_coins(user_id, rval)
        return True

    async def get_bp_claimed_levels(self, user_id: int, season_id: int) -> set:
        """Множество (level, is_premium) уже полученных наград."""
        rows = await self.fetchall(
            "SELECT level, is_premium FROM user_bp_claimed WHERE user_id = ? AND season_id = ?",
            (user_id, season_id)
        )
        return {(r[0], bool(r[1])) for r in (rows or [])}

    # ==================== ДНЕВНЫЕ ЗАДАНИЯ БИРЖИ ====================

    BIRZH_QUEST_TYPES = {
        "buy_jd_100": ("Купить 100 ЖД", "buy", "jd", 500),
        "sell_kris_100": ("Продать 100 Mr.Kris", "sell", "kris", 300),
        "buy_sharaga_100": ("Купить 100 Шарага", "buy", "sharaga", 50),
        "sell_lisaya_100": ("Продать 100 MR.lisaya", "sell", "lisaya", 1000),
    }

    async def get_birzh_daily_quest(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Дневное задание биржи на сегодня (одно на пользователя в день)."""
        from datetime import date
        import random
        today = date.today().isoformat()
        row = await self.fetchone(
            "SELECT quest_type, completed, reward_claimed FROM birzh_daily_quests WHERE user_id = ? AND quest_date = ? LIMIT 1",
            (user_id, today)
        )
        if row:
            qtype, completed, reward_claimed = row[0], bool(row[1]), bool(row[2])
            title, action, coin, reward_coins = self.BIRZH_QUEST_TYPES[qtype]
            return {"quest_type": qtype, "title": title, "action": action, "coin": coin, "reward_coins": reward_coins, "completed": completed, "reward_claimed": reward_claimed}
        qtype = random.choice(list(self.BIRZH_QUEST_TYPES.keys()))
        title, action, coin, reward_coins = self.BIRZH_QUEST_TYPES[qtype]
        await self.execute(
            "INSERT INTO birzh_daily_quests (user_id, quest_date, quest_type, completed, reward_claimed) VALUES (?, ?, ?, 0, 0)",
            (user_id, today, qtype)
        )
        return {"quest_type": qtype, "title": title, "action": action, "coin": coin, "reward_coins": reward_coins, "completed": False, "reward_claimed": False}

    async def complete_birzh_quest(self, user_id: int, action: str, coin: str) -> Optional[Dict[str, Any]]:
        """Если действие совпадает с заданием — отметить выполненным. Вернуть задание с наградой или None."""
        from datetime import date
        today = date.today().isoformat()
        for qtype, (title, a, c, reward_coins) in self.BIRZH_QUEST_TYPES.items():
            if a == action and c == coin:
                await self.execute(
                    "UPDATE birzh_daily_quests SET completed = 1 WHERE user_id = ? AND quest_date = ? AND quest_type = ?",
                    (user_id, today, qtype)
                )
                return {"quest_type": qtype, "title": title, "reward_coins": reward_coins}
        return None

    async def claim_birzh_quest_reward(self, user_id: int, quest_type: str) -> bool:
        """Получить награду за задание (один раз). Возвращает True если награда выдана."""
        from datetime import date
        today = date.today().isoformat()
        row = await self.fetchone(
            "SELECT completed, reward_claimed FROM birzh_daily_quests WHERE user_id = ? AND quest_date = ? AND quest_type = ?",
            (user_id, today, quest_type)
        )
        if not row or not row[0] or row[1]:
            return False
        await self.execute(
            "UPDATE birzh_daily_quests SET reward_claimed = 1 WHERE user_id = ? AND quest_date = ? AND quest_type = ?",
            (user_id, today, quest_type)
        )
        return True

    def _birzh_portfolio_value(self, balances: Dict[str, int], prices: Dict[str, Any]) -> float:
        """Стоимость портфеля биржи в коинах: сумма (баланс_монет * цена_за_100 / 100)."""
        total = 0.0
        for coin in ("sharaga", "kris", "jd", "lisaya"):
            amt = balances.get(coin, 0) or 0
            pr = prices.get(coin)
            if pr is not None:
                total += amt * (pr / 100.0)
        return total

    async def ensure_birzh_morning_snapshot(self, user_id: int, snapshot_date: str, portfolio_value: float) -> None:
        """Записать утренний снимок портфеля, если на эту дату ещё нет записи."""
        await self.execute(
            "INSERT OR IGNORE INTO birzh_daily_snapshot (user_id, snapshot_date, morning_value) VALUES (?, ?, ?)",
            (user_id, snapshot_date, portfolio_value)
        )

    async def get_birzh_morning_snapshot(self, user_id: int, snapshot_date: str) -> Optional[float]:
        """Утренняя стоимость портфеля за дату или None."""
        row = await self.fetchone(
            "SELECT morning_value FROM birzh_daily_snapshot WHERE user_id = ? AND snapshot_date = ?",
            (user_id, snapshot_date)
        )
        return float(row[0]) if row and row[0] is not None else None

    async def check_birzh_10pct_achievement(self, user_id: int, current_portfolio_value: float) -> bool:
        """Если портфель вырос на 10% относительно утреннего снимка — выдать достижение. Возвращает True если выдано."""
        from datetime import date
        today = date.today().isoformat()
        morning = await self.get_birzh_morning_snapshot(user_id, today)
        if morning is None or morning <= 0:
            return False
        if current_portfolio_value >= morning * 1.1:
            return await self.unlock_achievement(user_id, "birzh_10pct_day")
        return False

    # ==================== ГЛОБАЛЬНЫЕ СОБЫТИЯ ====================

    async def get_global_event(self, event_type: str) -> Optional[Dict[str, Any]]:
        """Активно ли глобальное событие (день слота, день биржи)."""
        row = await self.fetchone("SELECT ends_at FROM global_events WHERE event_type = ?", (event_type,))
        if not row or row[0] <= int(datetime.now().timestamp()):
            return None
        return {"event_type": event_type, "ends_at": row[0]}

    async def set_global_event(self, event_type: str, duration_seconds: int) -> None:
        """Включить глобальное событие на duration_seconds."""
        now = int(datetime.now().timestamp())
        ends = now + duration_seconds
        await self.execute(
            "INSERT OR REPLACE INTO global_events (event_type, ends_at) VALUES (?, ?)",
            (event_type, ends)
        )

    RISK40_SLUGS_TUPLE = (
        "reactor", "vault", "dicepath", "overheat", "mindlock", "bombline", "liftx", "doza",
        "shum", "signal", "freeze", "tunnel", "escape", "code", "magnet", "candle",
        "pulse", "orbit", "wall", "watcher",
        "controlroom", "firesector", "mutation", "satellite", "mine", "clock", "lab", "bunker",
        "storm", "navigator", "icepath", "coinstack", "target", "fuse", "web", "logicgate",
        "depth", "field", "ritual", "trace",
    )

    async def get_risk40_distinct_count(self, user_id: int) -> int:
        """Сколько разных risk-игр (из 40) пользователь уже играл."""
        placeholders = ",".join("?" * len(self.RISK40_SLUGS_TUPLE))
        row = await self.fetchone(
            f"SELECT COUNT(DISTINCT game_type) FROM games_sessions WHERE user_id = ? AND game_type IN ({placeholders})",
            (user_id,) + self.RISK40_SLUGS_TUPLE
        )
        return row[0] if row and row[0] is not None else 0
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С НАЛОГОМ ====================
    
    async def get_tax_state(self, user_id: int) -> Dict[str, Any]:
        """Получение состояния налога пользователя"""
        row = await self.fetchone(
            "SELECT last_tax_time, tax_due, is_paid FROM tax_states WHERE user_id = ?",
            (user_id,)
        )
        if row:
            return {
                "last_tax_time": row[0],
                "tax_due": row[1],
                "is_paid": bool(row[2])
            }
        # Создаем запись если нет
        await self.execute(
            "INSERT OR IGNORE INTO tax_states (user_id, is_paid) VALUES (?, 1)",
            (user_id,)
        )
        return {
            "last_tax_time": None,
            "tax_due": 0,
            "is_paid": True
        }
    
    async def set_tax_due(self, user_id: int, tax_amount: int):
        """Установка суммы налога к оплате"""
        now = int(datetime.now().timestamp())
        await self.execute(
            """UPDATE tax_states SET tax_due = ?, last_tax_time = ?, is_paid = 0
               WHERE user_id = ?""",
            (tax_amount, now, user_id)
        )

    async def init_tax_timer(self, user_id: int):
        """Старт таймера налога при первом заходе (не блокировать команды). Устанавливает last_tax_time, is_paid=1."""
        now = int(datetime.now().timestamp())
        await self.execute(
            """UPDATE tax_states SET last_tax_time = ?, tax_due = 0, is_paid = 1 WHERE user_id = ?""",
            (now, user_id)
        )
        # Если строки не было (UPDATE затронул 0 строк) — создаём
        await self.execute(
            """INSERT OR IGNORE INTO tax_states (user_id, last_tax_time, tax_due, is_paid) VALUES (?, ?, 0, 1)""",
            (user_id, now)
        )

    async def pay_tax(self, user_id: int):
        """Оплата налога пользователем"""
        await self.execute(
            "UPDATE tax_states SET tax_due = 0, is_paid = 1 WHERE user_id = ?",
            (user_id,)
        )

    # ==================== ПЕРЕРОЖДЕНИЯ ====================

    REBIRTH_BASE_COST = 1_000_000

    async def get_rebirth_count(self, user_id: int) -> int:
        """Количество перерождений пользователя."""
        row = await self.fetchone("SELECT rebirth_count FROM rebirths WHERE user_id = ?", (user_id,))
        return row[0] if row else 0

    async def get_rebirth_cost(self, user_id: int) -> int:
        """Стоимость следующего перерождения: 1M * 2^count."""
        count = await self.get_rebirth_count(user_id)
        return self.REBIRTH_BASE_COST * (2 ** count)

    async def do_rebirth(self, user_id: int) -> Tuple[bool, int, str]:
        """
        Выполнить перерождение. Требует balance >= cost. Обнуляет баланс, +1 к rebirth_count.
        Returns: (успех, новый rebirth_count, сообщение об ошибке)
        """
        balance = await self.get_balance(user_id)
        cost = await self.get_rebirth_cost(user_id)
        if balance < cost:
            return False, 0, f"Нужно минимум <b>{cost:,}</b> коинов. У тебя: <b>{balance:,}</b>."
        await self.set_balance_direct(user_id, 0)
        count = await self.get_rebirth_count(user_id)
        new_count = count + 1
        if count == 0:
            await self.execute("INSERT INTO rebirths (user_id, rebirth_count) VALUES (?, 1)", (user_id,))
        else:
            await self.execute(
                "UPDATE rebirths SET rebirth_count = rebirth_count + 1 WHERE user_id = ?",
                (user_id,)
            )
        return True, new_count, ""

    # ==================== ИГРОВЫЕ НОВОСТИ ====================

    async def get_all_play_counts_24h(self) -> Dict[str, int]:
        """Все команды и количество запусков за 24 ч (command без слеша: slot, mirror, ...). Для анализа новостей."""
        now = int(datetime.now().timestamp())
        last_24 = now - 86400
        rows = await self.fetchall(
            """SELECT command, COUNT(*) as cnt FROM admin_game_logs
               WHERE created_at >= ? AND command IS NOT NULL AND command != ''
               GROUP BY command""",
            (last_24,)
        )
        out = {}
        for r in (rows or []):
            cmd = (r[0] or "").strip().lstrip("/")
            if cmd:
                out[cmd] = out.get(cmd, 0) + (r[1] or 0)
        return out

    async def get_current_news(self) -> Optional[Dict[str, Any]]:
        """Активная новость: не истекшая. Одна запись — последняя по expires_at."""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT id, news_type, game_slug, expires_at, flavor_text FROM game_news WHERE expires_at > ? ORDER BY expires_at DESC LIMIT 1",
            (now,)
        )
        if not row:
            return None
        return {
            "id": row[0],
            "news_type": row[1],
            "game_slug": row[2],
            "expires_at": row[3],
            "flavor_text": row[4] or "",
        }

    async def insert_game_news(self, news_type: str, game_slug: str, expires_at: int, flavor_text: str = None) -> int:
        """Добавить новость. Возвращает id."""
        now = int(datetime.now().timestamp())
        cursor = await self.execute(
            "INSERT INTO game_news (news_type, game_slug, expires_at, flavor_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (news_type, game_slug, expires_at, flavor_text or "", now)
        )
        return cursor.lastrowid
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ФРИСПИНАМИ ====================
    
    async def add_free_spins(self, user_id: int, spins_count: int, expires_at: int = None):
        """Добавление фриспинов пользователю"""
        if expires_at is None:
            expires_at = int((datetime.now() + timedelta(days=30)).timestamp())
        
        await self.execute(
            """INSERT OR REPLACE INTO free_spins (user_id, spins_count, expires_at)
               VALUES (?, COALESCE((SELECT spins_count FROM free_spins WHERE user_id = ?), 0) + ?, ?)""",
            (user_id, user_id, spins_count, expires_at)
        )
    
    async def get_free_spins(self, user_id: int) -> int:
        """Получение количества активных фриспинов"""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            "SELECT spins_count FROM free_spins WHERE user_id = ? AND expires_at > ?",
            (user_id, now)
        )
        return row[0] if row else 0
    
    async def use_free_spin(self, user_id: int) -> bool:
        """Использование одного фриспина"""
        spins = await self.get_free_spins(user_id)
        if spins > 0:
            await self.execute(
                "UPDATE free_spins SET spins_count = spins_count - 1 WHERE user_id = ?",
                (user_id,)
            )
            return True
        return False
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ИГРАМИ ====================
    
    async def log_game_session(self, user_id: int, game_type: str, bet: int,
                              result: str, amount_change: int, multiplier: float = 1.0):
        """Логирование игровой сессии и прогресс боевого пропуска."""
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT INTO games_sessions
               (user_id, game_type, bet, result, amount_change, multiplier, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, game_type, bet, result, amount_change, multiplier, now)
        )
        try:
            bp = await self.get_current_bp_season()
            if bp:
                await self.progress_bp_quest(user_id, bp["id"], "play_5", 1)
                await self.progress_bp_quest(user_id, bp["id"], "play_20", 1)
                await self.progress_bp_quest(user_id, bp["id"], "play_50", 1)
                if result == "win":
                    await self.progress_bp_quest(user_id, bp["id"], "win_3", 1)
                    await self.progress_bp_quest(user_id, bp["id"], "win_10", 1)
                if amount_change > 0:
                    await self.progress_bp_quest(user_id, bp["id"], "earn_500", min(amount_change, 5000))
                    await self.progress_bp_quest(user_id, bp["id"], "earn_2000", min(amount_change, 10000))
                    await self.progress_bp_quest(user_id, bp["id"], "earn_5000", min(amount_change, 10000))
                if game_type == "fracture":
                    await self.progress_bp_quest(user_id, bp["id"], "fracture_1", 1)
                if game_type == "slot":
                    await self.progress_bp_quest(user_id, bp["id"], "slot_1", 1)
                    if result == "win":
                        await self.progress_bp_quest(user_id, bp["id"], "win_slot_3", 1)
                if game_type in self.RISK40_SLUGS_TUPLE:
                    await self.progress_bp_quest(user_id, bp["id"], "risk_5", 1)
                if game_type in ("coin", "guess", "dice", "even", "highlow", "redblack", "lucky7", "double", "triple", "spin"):
                    await self.progress_bp_quest(user_id, bp["id"], "minigame_3", 1)
        except Exception:
            pass

    # ==================== АДМИН-ЛОГИ (ИГРЫ) ====================

    async def log_admin_game(self, user_id: int, username: str, command: str, bet: int,
                             result: str, balance_change: int, tax: Optional[int] = 0):
        """Логирование игры для админа: user_id, username, команда, ставка, результат, изменение баланса, налог."""
        now = int(datetime.now().timestamp())
        tax_val = 0 if tax is None else tax
        await self.execute(
            """INSERT INTO admin_game_logs
               (user_id, username, command, bet, result, balance_change, tax, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username or "", command, bet, result, balance_change, tax_val, now)
        )

    async def get_admin_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Последние записи логов игр для /logs (только создатель)."""
        rows = await self.fetchall(
            """SELECT user_id, username, command, bet, result, balance_change, tax, created_at
               FROM admin_game_logs ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        )
        return [
            {
                "user_id": r[0],
                "username": r[1] or "",
                "command": r[2],
                "bet": r[3],
                "result": r[4],
                "balance_change": r[5],
                "tax": r[6],
                "created_at": r[7],
            }
            for r in (rows or [])
        ]

    async def get_economy_stats(self) -> Dict[str, Any]:
        """Статистика для /economy: оборот, налог Технолога, топ выигрышей/проигрышей."""
        turnover_row = await self.fetchone(
            "SELECT COALESCE(SUM(ABS(amount)), 0) FROM transactions WHERE transaction_type IN ('income', 'expense')"
        )
        turnover = int(turnover_row[0]) if turnover_row and turnover_row[0] is not None else 0
        tax_row = await self.fetchone(
            "SELECT COALESCE(SUM(tax), 0) FROM admin_game_logs"
        )
        total_tax = int(tax_row[0]) if tax_row and tax_row[0] is not None else 0
        top_wins = await self.fetchall(
            """SELECT user_id, username, command, bet, balance_change, created_at
               FROM admin_game_logs WHERE result = 'win' ORDER BY balance_change DESC LIMIT 10"""
        )
        top_losses = await self.fetchall(
            """SELECT user_id, username, command, bet, balance_change, created_at
               FROM admin_game_logs WHERE result = 'loss' ORDER BY balance_change ASC LIMIT 10"""
        )
        return {
            "turnover": turnover,
            "total_tax": total_tax,
            "top_wins": [
                {"user_id": r[0], "username": r[1] or "", "command": r[2], "bet": r[3], "balance_change": r[4], "created_at": r[5]}
                for r in (top_wins or [])
            ],
            "top_losses": [
                {"user_id": r[0], "username": r[1] or "", "command": r[2], "bet": r[3], "balance_change": r[4], "created_at": r[5]}
                for r in (top_losses or [])
            ],
        }

    async def get_bot_stats(self) -> Dict[str, Any]:
        """Общая статистика бота для /stats."""
        users_row = await self.fetchone("SELECT COUNT(*) FROM users")
        games_row = await self.fetchone("SELECT COUNT(*) FROM games_sessions")
        balance_row = await self.fetchone("SELECT COALESCE(SUM(balance), 0) FROM users")
        return {
            "users": users_row[0] if users_row else 0,
            "games_total": games_row[0] if games_row else 0,
            "total_balance": int(balance_row[0]) if balance_row and balance_row[0] is not None else 0,
        }
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С ПОДАРКАМИ ====================
    
    async def log_gift(self, sender_id: int, receiver_id: int, item_name: str, quality_level: int):
        """Логирование дарения подарка"""
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT INTO gifts (sender_id, receiver_id, item_name, quality_level, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (sender_id, receiver_id, item_name, quality_level, now)
        )
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ СО СТАТУСАМИ ====================
    
    async def get_all_statuses(self) -> List[Dict[str, Any]]:
        """Получение всех доступных статусов"""
        rows = await self.fetchall(
            "SELECT status_name, price, description, emoji FROM statuses ORDER BY price"
        )
        return [
            {
                "status_name": row[0],
                "price": row[1],
                "description": row[2],
                "emoji": row[3]
            }
            for row in rows
        ]
    
    async def set_user_status(self, user_id: int, status: str):
        """Установка статуса пользователю"""
        await self.execute(
            "UPDATE users SET status = ? WHERE user_id = ?",
            (status, user_id)
        )
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С АНТИСПАМОМ ====================
    
    async def update_antispam(self, user_id: int, message_count: int, window_start: int,
                            is_muted: bool = False, mute_until: int = None,
                            messages_left_to_ban: int = None, last_message_at: int = None):
        """Обновление данных антиспама для пользователя"""
        await self.execute(
            """INSERT OR REPLACE INTO antispam 
               (user_id, message_count, window_start, is_muted, mute_until, messages_left_to_ban, last_message_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, message_count, window_start, 1 if is_muted else 0, mute_until,
             messages_left_to_ban, last_message_at)
        )
    
    async def get_antispam(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение данных антиспама пользователя"""
        row = await self.fetchone(
            """SELECT message_count, window_start, is_muted, mute_until, messages_left_to_ban, last_message_at
               FROM antispam WHERE user_id = ?""",
            (user_id,)
        )
        if row:
            return {
                "message_count": row[0],
                "window_start": row[1],
                "is_muted": bool(row[2]),
                "mute_until": row[3],
                "messages_left_to_ban": row[4] if len(row) > 4 else None,
                "last_message_at": row[5] if len(row) > 5 else None
            }
        return None
    
    # ==================== FREEDUREV (ОДНОРАЗОВЫЙ НА ВСЕГО БОТА) ====================
    
    async def get_freedurev_global_activator(self) -> Optional[int]:
        """Кто первый активировал /freedurev (1 раз на всего бота). None если ещё никто."""
        row = await self.fetchone("SELECT user_id FROM freedurev_global WHERE id = 1")
        return row[0] if row else None
    
    async def set_freedurev_global(self, user_id: int) -> bool:
        """Записать первого активировавшего /freedurev. True если вставлено (первый), False если уже активировано."""
        now = int(datetime.now().timestamp())
        try:
            await self.execute(
                "INSERT INTO freedurev_global (id, user_id, activated_at) VALUES (1, ?, ?)",
                (user_id, now)
            )
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            if "UNIQUE" in str(e) or "constraint" in str(e).lower():
                return False
            raise
    
    # ==================== CHISLA PvP ====================
    
    async def create_chisla_session(self, session_id: str, player1_id: int, player2_id: int,
                                    amount: int, message_id: int, chat_id: int, ttl_seconds: int = 300) -> bool:
        """Создать сессию /chisla. TTL 5 минут."""
        now = int(datetime.now().timestamp())
        expires_at = now + ttl_seconds
        try:
            await self.execute(
                """INSERT INTO chisla_sessions 
                   (session_id, player1_id, player2_id, amount, status, message_id, chat_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (session_id, player1_id, player2_id, amount, message_id, chat_id, now, expires_at)
            )
            return True
        except Exception:
            return False
    
    async def get_chisla_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Получить сессию /chisla"""
        row = await self.fetchone(
            """SELECT session_id, player1_id, player2_id, amount, status, message_id, chat_id,
                      created_at, expires_at, player1_choice, player2_choice, player1_mult, player2_mult
               FROM chisla_sessions WHERE session_id = ?""",
            (session_id,)
        )
        if row:
            return {
                "session_id": row[0], "player1_id": row[1], "player2_id": row[2], "amount": row[3],
                "status": row[4], "message_id": row[5], "chat_id": row[6], "created_at": row[7],
                "expires_at": row[8], "player1_choice": row[9], "player2_choice": row[10],
                "player1_mult": row[11], "player2_mult": row[12]
            }
        return None
    
    async def get_chisla_session_by_players(self, player1_id: int, player2_id: int) -> Optional[Dict[str, Any]]:
        """Найти активную сессию по паре игроков"""
        now = int(datetime.now().timestamp())
        row = await self.fetchone(
            """SELECT session_id FROM chisla_sessions 
               WHERE ((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))
               AND status IN ('pending', 'active') AND expires_at > ?""",
            (player1_id, player2_id, player2_id, player1_id, now)
        )
        return await self.get_chisla_session(row[0]) if row else None
    
    async def update_chisla_accepted(self, session_id: str):
        """Игрок2 принял вызов — статус active"""
        await self.execute("UPDATE chisla_sessions SET status = 'active' WHERE session_id = ?", (session_id,))
    
    async def update_chisla_choice(self, session_id: str, player_id: int, choice: int, mult: float):
        """Записать выбор игрока (кнопка 0-5) и множитель"""
        sess = await self.get_chisla_session(session_id)
        if not sess:
            return
        if sess["player1_id"] == player_id:
            await self.execute(
                "UPDATE chisla_sessions SET player1_choice = ?, player1_mult = ? WHERE session_id = ?",
                (choice, mult, session_id)
            )
        else:
            await self.execute(
                "UPDATE chisla_sessions SET player2_choice = ?, player2_mult = ? WHERE session_id = ?",
                (choice, mult, session_id)
            )
    
    async def finish_chisla_session(self, session_id: str):
        """Завершить сессию"""
        await self.execute("UPDATE chisla_sessions SET status = 'finished' WHERE session_id = ?", (session_id,))
    
    async def delete_chisla_session(self, session_id: str):
        """Удалить сессию (очистка)"""
        await self.execute("DELETE FROM chisla_sessions WHERE session_id = ?", (session_id,))
    
    # ==================== PREMIUM CHAT GREETING (7 ДНЕЙ, РАЗ В 24Ч) ====================
    
    async def get_premium_chat_greeting(self, chat_id: int, user_id: int) -> Optional[int]:
        """Время последнего приветствия в чате для пользователя"""
        row = await self.fetchone(
            "SELECT last_greeting_at FROM premium_chat_greeting WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        return row[0] if row else None
    
    async def set_premium_chat_greeting(self, chat_id: int, user_id: int):
        """Отметить приветствие в чате"""
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT OR REPLACE INTO premium_chat_greeting (chat_id, user_id, last_greeting_at) VALUES (?, ?, ?)""",
            (chat_id, user_id, now)
        )
    
    # ==================== РОЛИ (АДМИН, МОДЕР, МЛ. МОДЕР) ====================
    
    async def add_role(self, user_id: int, role: str, granted_by: int, until_ts: int = None):
        """Выдать роль (admin, moder, juniormoder). until_ts = None — навсегда."""
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT OR REPLACE INTO user_roles (user_id, role, until_ts, granted_by, created_at) VALUES (?, ?, ?, ?, ?)""",
            (user_id, role, until_ts, granted_by, now)
        )
    
    async def remove_role(self, user_id: int, role: str):
        """Снять роль."""
        await self.execute("DELETE FROM user_roles WHERE user_id = ? AND role = ?", (user_id, role))
    
    async def get_users_with_role(self, role: str) -> List[int]:
        """Список user_id с активной ролью (until_ts is null or > now)."""
        now = int(datetime.now().timestamp())
        rows = await self.fetchall(
            "SELECT user_id FROM user_roles WHERE role = ? AND (until_ts IS NULL OR until_ts > ?)",
            (role, now)
        )
        return [r[0] for r in rows]
    
    async def get_user_roles(self, user_id: int) -> List[str]:
        """Список активных ролей пользователя из БД."""
        now = int(datetime.now().timestamp())
        rows = await self.fetchall(
            "SELECT role FROM user_roles WHERE user_id = ? AND (until_ts IS NULL OR until_ts > ?)",
            (user_id, now)
        )
        return [r[0] for r in rows]
    
    # ==================== МЕТОДЫ ДЛЯ РАБОТЫ С АКТИВНЫМИ СЕССИЯМИ KRIPTA ====================
    
    async def create_kripta_session(
        self, 
        user_id: int, 
        bet: int, 
        message_id: int, 
        chat_id: int,
        crash_at: Optional[int] = None
    ) -> int:
        """
        Создание активной сессии kripta
        
        Args:
            user_id: ID пользователя
            bet: Ставка
            message_id: ID сообщения с игрой
            chat_id: ID чата
            crash_at: Время обвала (None = случайное)
            
        Returns:
            ID созданной сессии
        """
        now = int(datetime.now().timestamp())
        multiplier_interval = 10  # секунды
        next_update_at = now + multiplier_interval
        
        # Если crash_at не указан, генерируем случайное время обвала
        if crash_at is None:
            # Генерируем случайный момент обвала (от 10 сек до 1000 сек = x100)
            max_intervals = 100  # максимум x100
            crash_interval = random.randint(1, max_intervals)
            crash_at = now + (crash_interval * multiplier_interval)
        
        await self.execute(
            """INSERT OR REPLACE INTO kripta_sessions 
               (user_id, bet, current_multiplier, message_id, chat_id, started_at, 
                next_update_at, crash_at, is_active)
               VALUES (?, ?, 1.0, ?, ?, ?, ?, ?, 1)""",
            (user_id, bet, message_id, chat_id, now, next_update_at, crash_at)
        )
        
        return user_id
    
    async def get_kripta_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение активной сессии kripta"""
        row = await self.fetchone(
            """SELECT user_id, bet, current_multiplier, message_id, chat_id, 
                      started_at, next_update_at, crash_at, is_active
               FROM kripta_sessions WHERE user_id = ? AND is_active = 1""",
            (user_id,)
        )
        if row:
            return {
                "user_id": row[0],
                "bet": row[1],
                "current_multiplier": row[2],
                "message_id": row[3],
                "chat_id": row[4],
                "started_at": row[5],
                "next_update_at": row[6],
                "crash_at": row[7],
                "is_active": bool(row[8])
            }
        return None
    
    async def update_kripta_multiplier(self, user_id: int, multiplier: float, next_update_at: int):
        """Обновление множителя в активной сессии"""
        await self.execute(
            "UPDATE kripta_sessions SET current_multiplier = ?, next_update_at = ? WHERE user_id = ?",
            (multiplier, next_update_at, user_id)
        )
    
    async def close_kripta_session(self, user_id: int):
        """Закрытие активной сессии kripta"""
        await self.execute(
            "UPDATE kripta_sessions SET is_active = 0 WHERE user_id = ?",
            (user_id,)
        )
    
    async def cleanup_expired_kripta_sessions(self):
        """Очистка истекших сессий kripta"""
        now = int(datetime.now().timestamp())
        await self.execute(
            "UPDATE kripta_sessions SET is_active = 0 WHERE crash_at <= ? AND is_active = 1",
            (now,)
        )


# Глобальный экземпляр БД
db = Database()


# Функция инициализации БД (вызывается при старте бота)
async def init_db():
    """
    Инициализация базы данных
    Создает подключение и все необходимые таблицы
    """
    await db.connect()
    await db.create_tables()
    logger.info("База данных инициализирована")


# Функция закрытия БД (вызывается при остановке бота)
async def close_db():
    """Закрытие соединения с БД"""
    await db.close()
