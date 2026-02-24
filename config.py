"""
Конфигурация бота Tehnolog Games
Production-ready настройки с валидацией и поддержкой разных окружений
"""

import os
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


# Загружаем переменные окружения из .env файла
load_dotenv()


class Config(BaseSettings):
    """Основные настройки бота Tehnolog Games (Pydantic v2 + pydantic-settings)"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Токен бота Tehnolog Games (можно переопределить через env BOT_TOKEN)
    bot_token: str = Field(
        default="8532048159:AAE4p_W9BJ2n7PFFIWusU5bLtsoiJoC1O3I",
        alias="BOT_TOKEN",
        description="Токен Telegram бота Tehnolog Games",
    )
    
    # Для совместимости с кодом, использующим config.BOT_TOKEN
    @property
    def BOT_TOKEN(self) -> str:
        return self.bot_token
    
    # Режим работы (dev/prod)
    ENVIRONMENT: str = Field(default="prod", env="ENVIRONMENT")
    
    # Webhook настройки (для production на Render/Railway/Replit)
    WEBHOOK_HOST: Optional[str] = Field(default=None, env="WEBHOOK_HOST")
    WEBHOOK_PATH: str = Field(default="/webhook", env="WEBHOOK_PATH")
    WEBHOOK_URL: Optional[str] = Field(default=None, env="WEBHOOK_URL")
    
    # Порт для webhook (если используется)
    PORT: int = Field(default=8000, env="PORT")
    
    # Базовый путь проекта
    BASE_DIR: Path = Path(__file__).parent.resolve()
    
    # Пути к файлам и директориям (infobase.db по README; для вайпа можно использовать info.db)
    DB_PATH: Path = Field(default_factory=lambda: Path(__file__).parent / "infobase.db")
    LOGS_DIR: Path = Field(default_factory=lambda: Path(__file__).parent / "logs")
    LOG_FILE: Path = Field(default_factory=lambda: Path(__file__).parent / "logs" / "bot.log")
    ASSETS_DIR: Path = Field(default_factory=lambda: Path(__file__).parent / "assets")
    IMAGES_DIR: Path = Field(default_factory=lambda: Path(__file__).parent / "assets" / "images")
    AUDIO_DIR: Path = Field(default_factory=lambda: Path(__file__).parent / "assets" / "audio")
    VIDEO_DIR: Path = Field(default_factory=lambda: Path(__file__).parent / "assets" / "video")

    # Реклама: каждые 60 выполненных команд от не-Premium, блок 3 минуты
    AD_MESSAGES_THRESHOLD: int = 60
    AD_BLOCK_DURATION: int = 180  # 3 минуты
    AD_CHANNEL_LINK: str = "https://t.me/+wMpwWUp30fwwMjEy"
    # Новости: модификатор шанса для хорошей/плохой новости (игрок проценты не видит)
    NEWS_GOOD_DELTA: float = 0.05
    NEWS_BAD_DELTA: float = -0.05
    
    # Настройки логирования
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_TO_DB: bool = Field(default=True, env="LOG_TO_DB")
    LOG_MAX_SIZE_MB: int = Field(default=10, env="LOG_MAX_SIZE_MB")
    LOG_BACKUP_COUNT: int = Field(default=5, env="LOG_BACKUP_COUNT")
    
    # Настройки aiogram
    PARSE_MODE: str = "HTML"
    DISABLE_WEB_PAGE_PREVIEW: bool = True

    # Прокси для запросов к Telegram (все сообщения бота идут через этот прокси; при VPN в браузере укажи локальный прокси)
    # Примеры: http://127.0.0.1:7890  socks5://127.0.0.1:1080  (для SOCKS нужен aiohttp-socks)
    BOT_PROXY_URL: Optional[str] = Field(default=None, env="BOT_PROXY_URL")

    # Настройки работы бота
    MESSAGE_DELETE_TIMEOUT: int = 30  # секунды
    GAME_RESULT_DELETE_TIMEOUT: int = 20  # секунды для сообщений с результатами игр
    TRANSACTION_MESSAGE_TIMEOUT: int = 5  # секунды для сообщений "Списано/Начислено"
    
    # Cooldown настройки (в секундах)
    DEFAULT_COOLDOWN: int = 60
    PREMIUM_COOLDOWN: int = 15
    KACHALKA_COOLDOWN_REDUCTION: int = 30  # снижение до 30 сек при /kachalka
    KACHALKA_DURATION: int = 600  # 10 минут в секундах
    
    # Автономность бота: авто-сброс сезона и опция вайпа балансов
    AUTO_END_SEASON_ENABLED: bool = Field(default=True, env="AUTO_END_SEASON_ENABLED")
    AUTO_SEASON_CHECK_INTERVAL_HOURS: float = Field(default=24.0, env="AUTO_SEASON_CHECK_INTERVAL_HOURS")
    AUTO_WIPE_BALANCE_CAP: Optional[int] = Field(default=None, env="AUTO_WIPE_BALANCE_CAP")  # если задано — после сброса сезона обрезать балансы выше этого значения (вайп)

    # Экономика
    DEFAULT_COMMISSION: int = 5  # комиссия за команды (коины)
    FREE_COMMANDS: list = ["/help", "/start", "/helpgame", "/balance", "/refill", "/donate", "/top", "/admins", "/report", "/news", "/obnova"]
    # Игровые команды, меню, профиль, медиа — комиссию не списываем
    COMMISSION_EXEMPT: list = [
        "/slot", "/konopla", "/kripta", "/plsdon", "/chisla", "/freedurev", "/almaz",
        "/news", "/rulet", "/frekaz", "/perekyp", "/pererozhd", "/birzh", "/obnova",
        "/helpgame", "/infoslot", "/infokonopla", "/infolucky",
        "/cancel", "/status", "/debug",
        "/reactor", "/vault", "/dicepath", "/overheat", "/mindlock", "/bombline", "/liftx", "/doza",
        "/shum", "/signal", "/freeze", "/tunnel", "/escape", "/code", "/magnet", "/candle",
        "/pulse", "/orbit", "/wall", "/watcher",
        "/controlroom", "/firesector", "/mutation", "/satellite", "/mine", "/clock", "/lab", "/bunker",
        "/storm", "/navigator", "/icepath", "/coinstack", "/target", "/fuse", "/web", "/logicgate",
        "/depth", "/field", "/ritual", "/trace",
        "/accaunt", "/accountphoto", "/accountobrosh", "/accountinfo", "/accountstatus",
        "/status", "/statusmarket", "/checkaccount", "/lvl", "/lvlup", "/lvlcheck", "/vzortehnologa",
        "/market", "/tehnologmarket", "/inventory", "/premium", "/timeprem", "/effect",
        "/kachalka", "/steal", "/sperm", "/skinna0", "/dostavka",
        "/olegtemni", "/detimoi", "/deniska", "/kb", "/oleg", "/cam1", "/cam2", "/cam3", "/cam4", "/cam5",
        "/cityboy", "/ignat", "/olegdexter", "/linux", "/mramordpop",
        "/dongift", "/giftplus",
        "/ban", "/addadmin", "/addmoder", "/addjuniormoder", "/deladmin", "/delmoder", "/deljuniormoder", "/unban", "/adddenga",
        "/admin", "/stats", "/economy", "/logs",
        "/random", "/gamerandom", "/blackmarket", "/echo", "/topgame", "/fracture", "/mirror",
        "/coin", "/guess", "/dice", "/even", "/highlow", "/redblack", "/lucky7", "/double", "/triple", "/spin", "/minigames",
        "/tutorial", "/season", "/cup", "/bp", "/battlepass"
    ]

    # Налог Технолога (периодический — по желанию оставить; ниже налог с выигрыша)
    TAX_INTERVAL_HOURS: int = 4
    TAX_PERCENTAGE: float = 0.25  # 25% от баланса

    # Налог с выигрыша в играх: только при выигрыше; премиум платит меньше
    TAX_ON_WIN_PERCENT: float = 0.05  # 5% с выигрыша (база)
    TAX_ON_WIN_PERCENT_PREMIUM: float = 0.02  # 2% с выигрыша (премиум)
    MAX_WIN_PER_GAME: int = 2_000_000  # макс. выигрыш за одну игру (коинов); при больших ставках /fracture не режет выплату

    # Защита от нищеты: при балансе 0 — 1 бесплатная игра в сутки, выигрыш ограничен
    FREE_GAME_WIN_CAP: int = 100  # макс. выигрыш за бесплатную игру при балансе 0

    # Уведомления создателю: при превышении порогов
    NOTIFY_CREATOR_BALANCE_THRESHOLD: int = 100_000  # баланс пользователя выше
    NOTIFY_CREATOR_SINGLE_AMOUNT: int = 50_000  # одно начисление/выигрыш выше
    
    # Premium настройки
    PREMIUM_PRICES: dict = {
        "1_hour": 2000,
        "1_day": 20000,
        "7_days": 60000
    }
    PREMIUM_WIN_CHANCE_BONUS: float = 0.014  # +1.4%
    PREMIUM_PRICE_DISCOUNT: float = 0.005  # -0.5%
    
    # Игры: дешевле вход, +10% шансы; комиссия не применяется
    SLOT_BET: int = 20
    SLOT_WIN: int = 150
    SLOT_WIN_CHANCE: float = 0.05  # 5% база
    
    KONOPLA_BET: int = 30
    KONOPLA_LOSS: int = 70
    KONOPLA_WIN: int = 250
    KONOPLA_WIN_CHANCE: float = 0.07  # 7% база
    
    KRIPTA_MAX_MULTIPLIER: int = 100
    KRIPTA_MULTIPLIER_INTERVAL: int = 10  # секунды
    # /kripta баланс: дожить до x2 ~20%, x3 значительно меньше, x4+ очень редко, 100x крайне редко
    KRIPTA_SURVIVE_X2_CHANCE: float = 0.20  # ~20% дожить до x2
    KRIPTA_SURVIVE_X3_CHANCE: float = 0.06  # значительно меньше до x3
    KRIPTA_SURVIVE_X4_PLUS_FACTOR: float = 0.35  # каждый следующий интервал с множителем
    
    PLSDON_COOLDOWN: int = 300  # 5 минут
    PLSDON_IGNORE_CHANCE: float = 0.50  # 50%
    PLSDON_LOSS_CHANCE: float = 0.45  # 45%
    PLSDON_WIN_CHANCE: float = 0.05  # 5%
    PLSDON_DONATE_BUTTON_TIMEOUT: int = 15  # секунды
    PLSDON_DONATE_COST: int = 50
    
    STEAL_COOLDOWN: int = 86400  # 24 часа
    STEAL_AMOUNT: int = 50

    # 40 игр «риск/забрать»: мин/макс ставка, шанс обвала за шаг, рост множителя
    RISK40_BET_MIN: int = 10
    RISK40_BET_MAX: int = 5000
    RISK40_BUST_BASE: float = 0.14  # база шанса обвала за шаг
    RISK40_BUST_PER_STEP: float = 0.035  # + за каждый шаг
    RISK40_MULT_STEP: float = 1.18  # множитель за успешный шаг (x1.18 за шаг)
    
    SPERM_COOLDOWN: int = 300  # 5 минут

    # Русская рулетка /rulet: мин 2, макс 8 игроков, выбывание каждые 20 сек
    RULET_MIN_PLAYERS: int = 2
    RULET_MAX_PLAYERS: int = 8
    RULET_ELIMINATION_INTERVAL: int = 20  # секунд
    RULET_BET_MIN: int = 10
    RULET_BET_MAX: int = 10000

    # Фреказ /frekaz: ставка 1000–100000, макс 5 игроков, через 2 мин победитель по шансам
    FREKAZ_BET_MIN: int = 1000
    FREKAZ_BET_MAX: int = 100000
    FREKAZ_MAX_PLAYERS: int = 5
    FREKAZ_DURATION: int = 120  # секунд

    # Перекуп /perekyp: сумма — ориентир для цены объявления, торг даёт выше шанс
    PEREKYP_BET_MIN: int = 100
    PEREKYP_BET_MAX: int = 100000
    PEREKYP_SCROLL_MAX: int = 15  # лимит пролистываний
    PEREKYP_PRICE_MIN: float = 0.85  # цена объявления от суммы (мин)
    PEREKYP_PRICE_MAX: float = 1.15  # цена объявления от суммы (макс)
    PEREKYP_BUY_WIN_CHANCE: float = 0.38  # шанс успешной перепродажи при «Купить» (снижено против фарма)
    PEREKYP_TORG_WIN_CHANCE: float = 0.78  # шанс успеха торга (значительно выше)
    PEREKYP_TORG_DISCOUNT: float = 0.85  # после успешного торга цена *= это
    PEREKYP_WIN_MULT_MIN: float = 1.3
    PEREKYP_WIN_MULT_MAX: float = 3.2

    # Магазины
    POTION_PRICES: dict = {
        "x1.5": 1000,
        "x2": 4000,
        "x5": 8000,
        "x10": 30000
    }
    POTION_DURATION: int = 60  # 1 минута в секундах
    POTION_POISON_CHANCE: float = 0.07  # 7%
    POTION_CURE_COST: int = 320
    
    TOY_PRICE: int = 40000
    TOY_UPGRADE_MULTIPLIER: float = 3.0  # каждый апгрейд в 3 раза дороже
    TOY_QUALITY_LEVELS: list = ["хлам", "отремонтировано", "железо", "медь", "золото"]
    
    POTION_UPGRADE_BASE_COST: int = 5000
    POTION_UPGRADE_COST_MULTIPLIER: float = 1.5  # каждый уровень дороже в 1.5 раза
    POTION_MAX_MULTIPLIER: int = 20  # максимум x20
    
    # Реф-коды
    REFILL_COOLDOWN: int = 7200  # 2 часа
    REFILL_AMOUNT: int = 100
    
    # Уровни
    LEVEL_UP_BASE_COST: int = 500
    LEVEL_UP_COST_MULTIPLIER: float = 2.0  # каждый уровень в 2 раза дороже
    LEVEL_UP_COINS_REQUIREMENT: int = 10000  # каждые 10000 коинов = +1 уровень
    
    # Статусы
    STATUS_PRICE: int = 5000
    STATUSES: list = [
        "Богач🤡🫵",
        "Хомяк🐹",
        "Легенда☠️",
        "Потужномэн💫",
        "Главный пубертат страны💓",
        "Технолог🪑"
    ]
    
    # Доставка
    DELIVERY_STAGES: list = [
        "казаки ползут",
        "казаки на месте",
        "птица летит",
        "финал"
    ]
    
    # Роли (создатель нельзя банить; нельзя выдать роль выше своей)
    CREATOR_USERNAME: str = Field(default="DPOPTH", env="CREATOR_USERNAME")  # @DPOPTH — создатель
    CREATOR_ID: Optional[int] = Field(default=None, env="CREATOR_ID")
    ADMIN_IDS: str = Field(default="", env="ADMIN_IDS")  # через запятую: 123,456
    MODER_IDS: str = Field(default="", env="MODER_IDS")
    JUNIOR_MODER_IDS: str = Field(default="", env="JUNIOR_MODER_IDS")

    # Антиспам: после 10 быстрых сообщений → предупреждение, затем 5→4→3→2→1→БАН (Банановые острова)
    ANTISPAM_MAX_MESSAGES: int = 10  # порог для предупреждения
    ANTISPAM_MESSAGES_TO_BAN: int = 5  # сообщений до бана после предупреждения (5→4→3→2→1→0=бан)
    ANTISPAM_WINDOW_SECONDS: int = 60  # окно "быстрых" сообщений
    ANTISPAM_RESET_SECONDS: int = 30  # сброс счётчика, если пользователь перестал спамить
    ANTISPAM_BAN_DURATION: int = 3600  # 1 час бана (Банановые острова 🍌)

    # Анти-бот: задержка между командами, лимит кнопок, авто-бан при эксплойте
    MIN_DELAY_BETWEEN_ACTIONS: float = 1.0  # мин. секунд между любыми действиями (команда/кнопка)
    MAX_ACTIONS_PER_SECOND: int = 6  # больше действий в 1 сек = авто-бан (автоклик/эксплойт)
    MAX_SAME_CALLBACK_IN_WINDOW: int = 15  # макс. одинаковых нажатий кнопки за окно
    ANTIBOT_WINDOW_SECONDS: int = 60  # окно для подсчёта одинаковых кнопок
    AUTO_BAN_DURATION: int = 3600  # длительность авто-бана при детекте эксплойта (1 час)

    # Лимиты бана по ролям (в секундах): создатель — навсегда, админ — 1ч, модер — 30мин, мл.модер — 10мин
    BAN_MAX_CREATOR: int = 0  # 0 = без ограничения (навсегда)
    BAN_MAX_ADMIN: int = 3600
    BAN_MAX_MODER: int = 1800
    BAN_MAX_JUNIOR_MODER: int = 600

    # +10% к шансам выигрыша во всех играх кроме /kripta
    GAME_WIN_CHANCE_BONUS: float = 0.10
    
    # БД настройки
    DB_TIMEOUT: int = 20  # секунды
    DB_CHECK_SAME_THREAD: bool = False  # для async
    
    # Retry настройки
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0  # секунды

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, v: str) -> str:
        """Валидация токена бота"""
        if not v or len(v) < 10:
            raise ValueError("BOT_TOKEN должен быть валидным токеном Telegram бота")
        if ":" not in v:
            raise ValueError("BOT_TOKEN должен содержать ':' (формат: BOT_ID:TOKEN)")
        return v

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """Валидация окружения"""
        allowed = ["dev", "prod", "test"]
        if v.lower() not in allowed:
            raise ValueError(f"ENVIRONMENT должен быть одним из: {', '.join(allowed)}")
        return v.lower()

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Валидация уровня логирования"""
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"LOG_LEVEL должен быть одним из: {', '.join(allowed)}")
        return v.upper()

    def __init__(self, **kwargs):
        """Инициализация с созданием необходимых директорий"""
        super().__init__(**kwargs)
        self._ensure_directories()
        self._setup_webhook_url()
    
    def _ensure_directories(self):
        """Создание необходимых директорий, если их нет"""
        directories = [
            self.LOGS_DIR,
            self.ASSETS_DIR,
            self.IMAGES_DIR,
            self.AUDIO_DIR,
            self.VIDEO_DIR
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    def _setup_webhook_url(self):
        """Настройка URL для webhook (для production)"""
        if self.WEBHOOK_HOST and not self.WEBHOOK_URL:
            self.WEBHOOK_URL = f"{self.WEBHOOK_HOST}{self.WEBHOOK_PATH}"
        elif not self.WEBHOOK_URL and self.ENVIRONMENT == "prod":
            # Автоматическое определение для Render/Railway/Replit
            render_url = os.getenv("RENDER_EXTERNAL_URL")
            railway_url = os.getenv("RAILWAY_PUBLIC_DOMAIN")
            replit_url = os.getenv("REPL_SLUG")
            
            if render_url:
                self.WEBHOOK_URL = f"https://{render_url}{self.WEBHOOK_PATH}"
            elif railway_url:
                self.WEBHOOK_URL = f"https://{railway_url}{self.WEBHOOK_PATH}"
            elif replit_url:
                replit_id = os.getenv("REPL_ID", "")
                if replit_id:
                    self.WEBHOOK_URL = f"https://{replit_url}.{replit_id}.repl.co{self.WEBHOOK_PATH}"
    
    @property
    def is_production(self) -> bool:
        """Проверка, что бот работает в production режиме"""
        return self.ENVIRONMENT == "prod"
    
    @property
    def is_development(self) -> bool:
        """Проверка, что бот работает в development режиме"""
        return self.ENVIRONMENT == "dev"
    
    @property
    def use_webhook(self) -> bool:
        """Определение, использовать ли webhook"""
        return self.is_production and self.WEBHOOK_URL is not None
    
    def get_image_path(self, filename: str) -> Path:
        """Получить полный путь к изображению"""
        return self.IMAGES_DIR / filename

    def get_game_image_path(self, game_slug: str, variant: str = "start") -> Path:
        """
        Путь к изображению игры по конвенции: <game>.jpg, <game>win.jpg, <game>lose.jpg.
        variant: "start" | "win" | "lose". Если файла нет — вызывающий код может отправить только текст.
        """
        suffix = "" if variant == "start" else ("win" if variant == "win" else "lose")
        return self.IMAGES_DIR / f"{game_slug}{suffix}.jpg"
    
    def get_audio_path(self, filename: str) -> Path:
        """Получить полный путь к аудио файлу"""
        return self.AUDIO_DIR / filename

    def get_video_path(self, filename: str) -> Path:
        """Получить полный путь к видео файлу"""
        return self.VIDEO_DIR / filename

    def get_asset_path(self, subpath: str) -> Path:
        """Получить полный путь к ассету"""
        return self.ASSETS_DIR / subpath

    def get_admin_ids_list(self) -> List[int]:
        """Список ID админов из ADMIN_IDS (через запятую)."""
        if not self.ADMIN_IDS:
            return []
        out = []
        for x in self.ADMIN_IDS.split(","):
            try:
                out.append(int(x.strip()))
            except ValueError:
                pass
        return out

    def get_moder_ids_list(self) -> List[int]:
        """Список ID модеров."""
        if not self.MODER_IDS:
            return []
        out = []
        for x in self.MODER_IDS.split(","):
            try:
                out.append(int(x.strip()))
            except ValueError:
                pass
        return out

    def get_junior_moder_ids_list(self) -> List[int]:
        """Список ID младших модеров."""
        if not self.JUNIOR_MODER_IDS:
            return []
        out = []
        for x in self.JUNIOR_MODER_IDS.split(","):
            try:
                out.append(int(x.strip()))
            except ValueError:
                pass
        return out
    
    def validate_assets(self) -> dict:
        """Проверка наличия необходимых ассетов"""
        required_images = [
            "bal.jpg", "refill.jpg", "norefill.jpg", "zl.jpg", "prem.jpg",
            "kupprem.jpg", "kachalk.jpg", "jail.jpg", "otzhal.jpg", "beg.jpg",
            "1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg",
            "kon.jpg", "konwin.jpg", "kripta.jpg", "kriptalox.jpg", "kriptawin.jpg",
            "market.jpg", "tehmarket.jpg", "inventory.jpg", "status.jpg",
            "dostavka.jpg", "vzor.jpg", "gift.jpg", "steal.jpg",
            "Startkripta.jpg", "Ban.jpg", "accaunt.jpg", "mishka.jpg", "kluch32.jpg",
            "otvertka.jpg", "zelia.jpg",             "durev.jpg", "chisla.jpg", "winchisla.jpg", "loxchislo.jpg",
            "almaz.jpg", "almazwin.jpg", "almazlox.jpg"
        ]
        
        required_audio = [
            "cityboy.ogg", "ignat.ogg", "dostavka.mp3", "audio_dexter.mp3"
        ]
        
        missing = {
            "images": [],
            "audio": []
        }
        
        for img in required_images:
            if not self.get_image_path(img).exists():
                missing["images"].append(img)
        
        for audio in required_audio:
            if not self.get_audio_path(audio).exists():
                missing["audio"].append(audio)
        
        return missing

    # Текст для /obnova — что добавлено и исправлено (без закулисья, только для игроков)
    OBNOVA_LINES: List[str] = [
        "📋 <b>Обновление v1.2</b> — баг-фикс и биржа\n",
        "✅ <b>Исправлено:</b>",
        "• Излом решения (/fracture) — выигрыш теперь всегда зачисляется на баланс.",
        "",
        "✅ <b>Биржа /birzh:</b>",
        "• Шарага-коин — для новичков.",
        "• Mr.Kris коин — курс за 100 штук скачет.",
        "• ЖД коин — курс за 100 штук скачет.",
        "• MR.lisayaderektrisa коин — топовый коин, курс за 100 штук скачет.",
        "• Технолог-коин — отображается в ₽.",
        "",
        "📌 Остальное без изменений: профиль, лиги, статусы в /statusmarket.",
        "",
        "💡 <b>Как разнообразить геймплей:</b> играй в разные игры (слот, излом, зеркало, эхо) — так быстрее растёт рейтинг; пробуй биржу и все коины; заходи в /echo, чтобы бот подстраивался под твой стиль.",
    ]


# Глобальный экземпляр настроек
try:
    config = Config()
except Exception as e:
    raise RuntimeError(
        f"Ошибка загрузки конфигурации: {e}\n"
        f"Убедитесь, что BOT_TOKEN установлен в переменных окружения или .env файле"
    ) from e


# Экспорт для удобства
__all__ = ["config", "Config"]
