"""
Главный файл запуска бота YandexPticaGPT v1.0
Инициализация, регистрация роутеров, middleware, запуск polling
"""

import asyncio
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from config import config
from db import init_db, close_db
from middlewares import (
    AntifloodMiddleware,
    AntiAbuseMiddleware,
    BanMiddleware,
    CooldownMiddleware,
    CommissionMiddleware,
    TaxMiddleware,
    LoggingMiddleware,
    UpdateUserDataMiddleware,
    ReklamaBlockMiddleware,
    AdTriggerMiddleware
)
from services.effects import effects_service

# Импорт роутеров (будут созданы позже)
# from handlers import base, economy, premium, games, inventory, account, media, admin


def setup_logging():
    """
    Настройка системы логирования
    Логи пишутся в файл и в консоль. На сервере (Railway) при read-only ФС не падаем.
    """
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.warning("Не удалось создать директорию логов (возможно read-only): %s", e)
    
    # Настройка формата логов
    log_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Настройка root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.LOG_LEVEL))
    
    # Очистка существующих handlers
    root_logger.handlers.clear()
    
    try:
        file_handler = RotatingFileHandler(
            filename=str(config.LOG_FILE),
            maxBytes=config.LOG_MAX_SIZE_MB * 1024 * 1024,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, config.LOG_LEVEL))
        file_handler.setFormatter(log_format)
        root_logger.addHandler(file_handler)
    except OSError as e:
        logging.warning("Не удалось открыть лог-файл (возможно read-only): %s", e)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)
    
    # Настройка логирования для aiogram (уменьшаем шум)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    logger.info("Логирование настроено успешно")
    logger.info(f"Уровень логирования: {config.LOG_LEVEL}")
    logger.info(f"Логи пишутся в: {config.LOG_FILE}")
    
    return logger


async def register_routers(dp: Dispatcher):
    """
    Регистрация всех роутеров из handlers
    
    Args:
        dp: Экземпляр Dispatcher
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Импортируем роутеры
        # В aiogram 3.x роутеры создаются через Router()
        # Пока handlers пустые, но структура готова для регистрации
        
        # Базовые команды (help, start, balance, top, report, admins, tutorial)
        base_loaded = False
        try:
            from handlers import base
            if hasattr(base, 'router'):
                dp.include_router(base.router)
                logger.info("Роутер base зарегистрирован")
                base_loaded = True
        except ImportError as e:
            logger.warning("Модуль handlers.base не найден: %s", e, exc_info=True)
        except Exception as e:
            logger.error("Ошибка регистрации роутера base: %s", e, exc_info=True)
            import traceback
            logger.error("Traceback base: %s", traceback.format_exc())
        if not base_loaded:
            # Fallback: полные тексты /start и /help как в base.py, если модуль base не загрузился на сервере
            from utils import format_message_with_username
            from db import db as _db
            fallback_router = Router()
            @fallback_router.message(Command("start"))
            async def _fallback_start(msg):
                uid = msg.from_user.id
                un = msg.from_user.username or ""
                fn = msg.from_user.first_name or ""
                base_text = (
                    "👋 Привет! Я Tehnolog Games — бот с играми на коины, экономикой и профилем.\n\n"
                    "• <b>/help</b> — полный список команд и разделов (игры, экономика, профиль).\n"
                    "• <b>/balance</b> — твой баланс и уровень.\n"
                    "• <b>/helpgame название</b> — подробные правила любой игры (например: /helpgame slot или /helpgame fracture).\n\n"
                )
                try:
                    u = await _db.get_user(uid)
                    if not u:
                        await _db.create_user(uid, un)
                    tier = await _db.get_user_tier(uid)
                    if tier == "newcomer":
                        base_text += "🆕 Ты новичок — загляни в <b>/tutorial</b>, там покажем достижения, биржу, лиги и квесты.\n\n"
                    elif tier == "pro":
                        base_text += "🔥 Ты уже в деле — не забудь <b>/bp</b> (боевой пропуск), <b>/season</b> и <b>/cup</b> за наградами.\n\n"
                except Exception:
                    pass
                base_text += "Начни с /help — там всё по полочкам."
                await msg.answer(format_message_with_username(base_text, un, fn))
            @fallback_router.message(Command("help"))
            async def _fallback_help(msg):
                un = msg.from_user.username or ""
                fn = msg.from_user.first_name or ""
                help_text = format_message_with_username(
                    "🎮 <b>Tehnolog Games</b> v1.2 — игры на коины, экономика, биржа, профиль с лигой\n\n", un, fn
                )
                help_text += "📌 <b>v1.2</b> — биржа: Шарага, Mr.Kris, ЖД, MR.lisayaderektrisa. Исправлен излом решения. /obnova — список изменений.\n\n"
                help_text += "📋 <b>БАЗОВЫЕ КОМАНДЫ</b>\n"
                help_text += "/help — этот список | /balance — баланс и уровень | /top — топ по балансу\n"
                help_text += "/news — игровые новости | /admins — кто управляет | /report — репорт\n\n"
                help_text += "💰 <b>ЭКОНОМИКА</b>\n"
                help_text += "/refill — +100 коинов раз в 2 часа | /donate @user сумма комментарий — перевод\n\n"
                help_text += "🎲 <b>ИГРЫ: ОСНОВНЫЕ</b>\n"
                help_text += "/slot — слоты | /konopla — один раунд | /kripta сумма — Lucky Jet\n"
                help_text += "/almaz сумма — алмазы | /chisla @user сумма — PvP-дуэль | /plsdon — задонать боту\n\n"
                help_text += "🎰 <b>ИГРЫ: МУЛЬТИПЛЕЕР И РЫНОК</b>\n"
                help_text += "/rulet сумма — рулетка (2–8 игроков) | /frekaz сумма — фреказ | /perekyp сумма — перекуп\n"
                help_text += "/birzh — биржа: Шарага, Mr.Kris, ЖД, MR.lisayaderektrisa, дневные задания\n\n"
                help_text += "🔄 <b>ИГРЫ: РИСК / ЗАБРАТЬ</b> (40 штук)\n"
                help_text += "/reactor, /vault, /dicepath и др. — множитель растёт, «Ещё» и «Забрать». /helpgame reactor\n\n"
                help_text += "✨ <b>ОСОБЫЕ ИГРЫ</b>\n"
                help_text += "/random — судьба технолога | /gamerandom — сбой матрицы | /blackmarket — чёрный рынок\n"
                help_text += "/echo — эхо решений | /fracture [ставка] — излом решения | /mirror — зеркало\n\n"
                help_text += "👤 <b>ПРОФИЛЬ</b>\n"
                help_text += "/profile — профиль, лига, достижения | /pererozhd — перерождение | /premium — тарифы\n\n"
                help_text += "🎫 <b>БОЕВОЙ ПРОПУСК И СЕЗОНЫ</b>\n"
                help_text += "/bp — боевой пропуск (квесты, уровни, награды) | /season — сезон и топ | /cup slot, /cup fracture — кубки\n\n"
                help_text += "📖 /helpgame название — правила игры | /tutorial — обучение для новичков | /obnova — что нового\n"
                help_text += "/cancel — отмена игры | /status — активная игра | /statusmarket — магазин статусов. Tehnolog Games"
                await msg.answer(help_text)
            dp.include_router(fallback_router)
            logger.warning("Подключён fallback-роутер для /start и /help (handlers.base не загружен)")

        # Новости /news
        try:
            from handlers import news
            if hasattr(news, 'router'):
                dp.include_router(news.router)
                logger.info("Роутер news зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.news не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера news: %s", e, exc_info=True)
        
        # Экономика (balance, refill, donate, top)
        try:
            from handlers import economy
            if hasattr(economy, 'router'):
                dp.include_router(economy.router)
                logger.info("Роутер economy зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.economy не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера economy: %s", e, exc_info=True)
        
        # Premium (premium, timeprem, effect)
        try:
            from handlers import premium
            if hasattr(premium, 'router'):
                dp.include_router(premium.router)
                logger.info("Роутер premium зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.premium не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера premium: %s", e, exc_info=True)
        
        # Игры (plsdon, slot, konopla, kripta)
        try:
            from handlers import games
            if hasattr(games, 'router'):
                dp.include_router(games.router)
                logger.info("Роутер games зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.games не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера games: %s", e, exc_info=True)

        # Боевой пропуск (/bp, /battlepass)
        try:
            from handlers import battlepass
            if hasattr(battlepass, 'router'):
                dp.include_router(battlepass.router)
                logger.info("Роутер battlepass зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.battlepass не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера battlepass: %s", e, exc_info=True)

        # Мини-игры (coin, guess, dice, even, highlow, redblack, lucky7, double, triple, spin)
        try:
            from handlers import minigames
            if hasattr(minigames, 'router'):
                dp.include_router(minigames.router)
                logger.info("Роутер minigames зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.minigames не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера minigames: %s", e, exc_info=True)
        
        # Инвентарь (market, tehnologmarket, inventory)
        try:
            from handlers import inventory
            if hasattr(inventory, 'router'):
                dp.include_router(inventory.router)
                logger.info("Роутер inventory зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.inventory не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера inventory: %s", e, exc_info=True)
        
        # Аккаунт (account, accountphoto, accountinfo, status, lvl)
        try:
            from handlers import account
            if hasattr(account, 'router'):
                dp.include_router(account.router)
                logger.info("Роутер account зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.account не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера account: %s", e, exc_info=True)
        
        # Медиа и шуточные команды отключены — бот Tehnolog Games: только игры, экономика, профиль
        # (media, sperm, skinna0 не регистрируем)
        # /steal остаётся в rofl — имеет геймплей (кража коинов)
        try:
            from handlers import rofl
            if hasattr(rofl, 'router'):
                dp.include_router(rofl.router)
                logger.info("Роутер rofl зарегистрирован (steal)")
        except ImportError:
            logger.warning("Модуль handlers.rofl не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера rofl: %s", e, exc_info=True)

        # Админ (если нужен)
        try:
            from handlers import admin
            if hasattr(admin, 'router'):
                dp.include_router(admin.router)
                logger.info("Роутер admin зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.admin не найден, пропускаем")
        except Exception as e:
            logger.error("Ошибка регистрации роутера admin: %s", e, exc_info=True)
        
        logger.info("Регистрация роутеров завершена")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при регистрации роутеров: {e}", exc_info=True)
        raise


def register_middlewares(dp: Dispatcher):
    """
    Регистрация всех middleware
    Порядок регистрации важен - они выполняются в обратном порядке
    
    Args:
        dp: Экземпляр Dispatcher
    """
    logger = logging.getLogger(__name__)
    
    try:
        # 1. UpdateUserDataMiddleware - первый, создает пользователей
        dp.message.middleware(UpdateUserDataMiddleware())
        dp.callback_query.middleware(UpdateUserDataMiddleware())
        logger.info("UpdateUserDataMiddleware зарегистрирован")
        
        # 2. LoggingMiddleware - логирует все действия
        dp.message.middleware(LoggingMiddleware())
        dp.callback_query.middleware(LoggingMiddleware())
        logger.info("LoggingMiddleware зарегистрирован")
        
        # 3a. AntiAbuseMiddleware - задержка между действиями, лимит кнопок, авто-бан при эксплойте
        dp.message.middleware(AntiAbuseMiddleware())
        dp.callback_query.middleware(AntiAbuseMiddleware())
        logger.info("AntiAbuseMiddleware зарегистрирован")
        
        # 3. AntifloodMiddleware - защита от флуда (предупреждение → счётчик → Банановые острова 1ч)
        dp.message.middleware(AntifloodMiddleware())
        dp.callback_query.middleware(AntifloodMiddleware())
        logger.info("AntifloodMiddleware зарегистрирован")
        
        # 3b. BanMiddleware - блокировка забаненных пользователей
        dp.message.middleware(BanMiddleware())
        dp.callback_query.middleware(BanMiddleware())
        logger.info("BanMiddleware зарегистрирован")

        # 3c. AdTriggerMiddleware - счётчик сообщений, реклама каждые ~50
        dp.message.middleware(AdTriggerMiddleware())
        logger.info("AdTriggerMiddleware зарегистрирован")

        # 3d. ReklamaBlockMiddleware - блок команд на 1 мин при рекламе
        dp.message.middleware(ReklamaBlockMiddleware())
        logger.info("ReklamaBlockMiddleware зарегистрирован")
        
        # 4. TaxMiddleware - проверка налога (перед cooldown, чтобы налог блокировал команды)
        dp.message.middleware(TaxMiddleware())
        logger.info("TaxMiddleware зарегистрирован")
        
        # 5. CommissionMiddleware - комиссия 5 коинов (регистрируем до Cooldown, чтобы выполнялся после проверки CD)
        dp.message.middleware(CommissionMiddleware())
        logger.info("CommissionMiddleware зарегистрирован")

        # 6. CooldownMiddleware - проверка cooldown
        dp.message.middleware(CooldownMiddleware())
        logger.info("CooldownMiddleware зарегистрирован")

        logger.info("Все middleware зарегистрированы успешно")
        
    except Exception as e:
        logger.error(f"Ошибка регистрации middleware: {e}", exc_info=True)
        raise


async def on_startup(bot: Bot):
    """
    Функция, вызываемая при старте бота
    
    Args:
        bot: Экземпляр бота
    """
    logger = logging.getLogger(__name__)
    
    try:
        use_wh = getattr(config, "use_webhook", None)
        if callable(use_wh):
            use_wh = use_wh()
        if use_wh and getattr(config, "WEBHOOK_URL", None):
            url = config.WEBHOOK_URL
            if url:
                await bot.set_webhook(url)
                logger.info("Webhook установлен: %s", url)
        # Получаем информацию о боте
        bot_info = await bot.get_me()
        logger.info("=" * 50)
        logger.info(f"Бот запущен: @{bot_info.username}")
        logger.info(f"ID бота: {bot_info.id}")
        logger.info(f"Имя бота: {bot_info.first_name}")
        logger.info("=" * 50)
        
        # Проверяем наличие необходимых директорий (на сервере могут быть read-only)
        required_dirs = [config.LOGS_DIR, config.ASSETS_DIR, config.IMAGES_DIR, config.AUDIO_DIR, config.VIDEO_DIR]
        for directory in required_dirs:
            try:
                if not directory.exists():
                    directory.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Создана директория: {directory}")
            except OSError as e:
                logger.warning("Директория %s: %s", directory, e)
        
        # Проверяем наличие ассетов
        missing_assets = config.validate_assets()
        if missing_assets["images"] or missing_assets["audio"]:
            logger.warning("Отсутствуют некоторые ассеты:")
            if missing_assets["images"]:
                logger.warning(f"  Изображения: {', '.join(missing_assets['images'])}")
            if missing_assets["audio"]:
                logger.warning(f"  Аудио: {', '.join(missing_assets['audio'])}")
        else:
            logger.info("Все необходимые ассеты найдены")
        
        # Запускаем задачу очистки истекших эффектов
        await effects_service.start_cleanup_task()
        logger.info("Задача очистки эффектов запущена")

        # Запускаем планировщик новостей (каждые 2 ч)
        from services.news import news_service
        await news_service.start_scheduler()
        logger.info("Сервис новостей запущен")

        # Автономность: авто-сброс сезона и опция вайпа балансов
        try:
            from services.autonomy import start_autonomy
            start_autonomy(bot)
            logger.info("Сервис автономности запущен")
        except Exception as e:
            logger.warning("Сервис автономности не запущен: %s", e)

        logger.info("Бот готов к работе!")
        
    except Exception as e:
        logger.error(f"Ошибка при старте бота: {e}", exc_info=True)
        raise


async def on_shutdown(bot: Bot):
    """
    Функция, вызываемая при остановке бота
    
    Args:
        bot: Экземпляр бота
    """
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("Остановка бота...")
        # Снимаем webhook, чтобы не осталось «мёртвого» URL и не было конфликта с новым инстансом
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            logger.info("Webhook снят")
        except Exception as e:
            logger.debug("delete_webhook: %s", e)
        
        # Останавливаем задачу очистки эффектов
        await effects_service.stop_cleanup_task()
        logger.info("Задача очистки эффектов остановлена")

        try:
            from services.news import news_service
            await news_service.stop_scheduler()
        except Exception as e:
            logger.debug("news_service stop: %s", e)

        try:
            from services.autonomy import stop_autonomy
            await stop_autonomy()
        except Exception as e:
            logger.debug("autonomy stop: %s", e)

        # Закрываем соединение с БД
        await close_db()
        logger.info("Соединение с БД закрыто")
        
        logger.info("Бот остановлен")
        
    except Exception as e:
        logger.error(f"Ошибка при остановке бота: {e}", exc_info=True)


async def main():
    """
    Главная функция запуска бота
    """
    # Настройка логирования
    logger = setup_logging()
    
    bot = None
    
    try:
        logger.info("=" * 50)
        logger.info("Запуск бота Tehnolog Games")
        logger.info("=" * 50)
        
        # Проверка токена
        if not config.BOT_TOKEN:
            logger.error("BOT_TOKEN не установлен! Проверьте .env файл или переменные окружения")
            sys.exit(1)
        
        # Инициализация БД
        logger.info("Инициализация базы данных... путь: %s", getattr(config, "DB_PATH", None))
        await init_db()
        logger.info("База данных инициализирована")
        
        # Создание бота (опционально через прокси — все запросы к Telegram пойдут через него, как в Chrome с VPN)
        logger.info("Создание бота...")
        proxy_url = getattr(config, "BOT_PROXY_URL", None) or getattr(config, "bot_proxy_url", None)
        if proxy_url:
            session = AiohttpSession(proxy=proxy_url)
            bot = Bot(
                token=config.BOT_TOKEN,
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML if config.PARSE_MODE == "HTML" else ParseMode.MARKDOWN_V2
                ),
                session=session,
            )
            logger.info("Бот будет отправлять запросы через прокси: %s", proxy_url)
        else:
            bot = Bot(
                token=config.BOT_TOKEN,
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML if config.PARSE_MODE == "HTML" else ParseMode.MARKDOWN_V2
                )
            )
        
        # Создание диспетчера с хранилищем состояний
        logger.info("Создание диспетчера...")
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        
        # Регистрация middleware
        logger.info("Регистрация middleware...")
        register_middlewares(dp)
        
        # Регистрация роутеров
        logger.info("Регистрация роутеров...")
        await register_routers(dp)
        
        # Глобальный обработчик ошибок — чтобы пользователь всегда получал ответ при сбое
        @dp.error()
        async def on_error(event: ErrorEvent):
            log = logging.getLogger(__name__)
            exc = event.exception
            log.error("Ошибка при обработке: %s | тип: %s", exc, type(exc).__name__, exc_info=True)
            try:
                u = event.update
                if u.message:
                    await u.message.answer("Произошла ошибка. Попробуй позже или /help.")
                elif u.callback_query:
                    await u.callback_query.answer("Ошибка", show_alert=True)
            except Exception:
                pass
        
        # Регистрация обработчиков событий жизненного цикла
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)
        
        # Запуск: webhook (Railway/сервер) или polling (локально)
        use_wh = getattr(config, "use_webhook", None)
        if callable(use_wh):
            use_wh = use_wh()
        elif not isinstance(use_wh, bool):
            use_wh = bool(getattr(config, "WEBHOOK_URL", None) and getattr(config, "ENVIRONMENT", "") == "prod")
        if use_wh and getattr(config, "WEBHOOK_URL", None):
            from aiohttp import web
            from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
            wh_url = config.WEBHOOK_URL
            port = int(config.PORT)
            logger.info("Режим webhook: URL=%s, PORT=%s — убедись, что polling нигде не запущен (один инстанс)", wh_url, port)
            app = web.Application()
            async def health(_):
                return web.Response(text="ok")
            app.router.add_get("/", health)
            app.router.add_get("/health", health)
            webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
            webhook_requests_handler.register(app, path=config.WEBHOOK_PATH)
            setup_application(app, dp, bot=bot)
            port = int(config.PORT)
            logger.info("Запуск webhook на 0.0.0.0:%s", port)
            web.run_app(app, host="0.0.0.0", port=port)
        else:
            logger.info("Запуск polling")
            if getattr(config, "WEBHOOK_URL", None):
                logger.warning("WEBHOOK_URL задан, но режим polling — задай ENVIRONMENT=prod чтобы использовать webhook на сервере.")
            logger.info("ВНИМАНИЕ: если видишь Conflict (getUpdates), значит бот уже запущен в другом месте — закрой второй инстанс или на сервере задай WEBHOOK_URL и используй webhook.")
            logger.info("Режим работы: %s", config.ENVIRONMENT)
            try:
                await dp.start_polling(
                    bot,
                    allowed_updates=dp.resolve_used_update_types() or None,
                    close_bot_session=True
                )
            except KeyboardInterrupt:
                logger.info("Получен сигнал прерывания (Ctrl+C)")
            except Exception as e:
                logger.error(f"Ошибка при работе polling: {e}", exc_info=True)
                raise
        
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)
    
    finally:
        # Закрытие соединений (если они еще не закрыты)
        logger.info("Завершение работы...")
        try:
            # close_db вызывается в on_shutdown, но на всякий случай вызываем еще раз
            await close_db()
        except Exception as e:
            logger.error(f"Ошибка при закрытии БД: {e}")
        
        logger.info("Работа завершена")


if __name__ == "__main__":
    try:
        # Запуск главной функции
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановка бота...")
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
