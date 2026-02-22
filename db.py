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
            logger.info(f"Подключение к БД установлено: {self.db_path}")
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

    async def update_mmr(self, user_id: int, delta: int) -> int:
        """Изменить MMR на delta (может быть отрицательным). Новый MMR не ниже 0. Возвращает новый MMR."""
        current = await self.get_user_mmr(user_id)
        new_mmr = max(0, current + delta)
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
    
    async def pay_tax(self, user_id: int):
        """Оплата налога пользователем"""
        await self.execute(
            "UPDATE tax_states SET tax_due = 0, is_paid = 1 WHERE user_id = ?",
            (user_id,)
        )

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
        """Логирование игровой сессии"""
        now = int(datetime.now().timestamp())
        await self.execute(
            """INSERT INTO games_sessions 
               (user_id, game_type, bet, result, amount_change, multiplier, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, game_type, bet, result, amount_change, multiplier, now)
        )

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
