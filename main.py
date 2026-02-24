"""
Главный файл запуска бота YandexPticaGPT v1.0
Инициализация, регистрация роутеров, middleware, запуск polling
"""

import asyncio
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

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
    Логи пишутся в файл и в консоль
    """
    # Создаем директорию для логов если её нет
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
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
    
    # Handler для файла с ротацией
    file_handler = RotatingFileHandler(
        filename=str(config.LOG_FILE),
        maxBytes=config.LOG_MAX_SIZE_MB * 1024 * 1024,  # Конвертируем MB в байты
        backupCount=config.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(getattr(logging, config.LOG_LEVEL))
    file_handler.setFormatter(log_format)
    
    # Handler для консоли
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    
    # Добавляем handlers
    root_logger.addHandler(file_handler)
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
        
        # Базовые команды (help, start)
        try:
            from handlers import base
            if hasattr(base, 'router'):
                dp.include_router(base.router)
                logger.info("Роутер base зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.base не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера base: {e}")

        # Новости /news
        try:
            from handlers import news
            if hasattr(news, 'router'):
                dp.include_router(news.router)
                logger.info("Роутер news зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.news не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера news: {e}")
        
        # Экономика (balance, refill, donate, top)
        try:
            from handlers import economy
            if hasattr(economy, 'router'):
                dp.include_router(economy.router)
                logger.info("Роутер economy зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.economy не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера economy: {e}")
        
        # Premium (premium, timeprem, effect)
        try:
            from handlers import premium
            if hasattr(premium, 'router'):
                dp.include_router(premium.router)
                logger.info("Роутер premium зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.premium не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера premium: {e}")
        
        # Игры (plsdon, slot, konopla, kripta)
        try:
            from handlers import games
            if hasattr(games, 'router'):
                dp.include_router(games.router)
                logger.info("Роутер games зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.games не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера games: {e}")

        # Боевой пропуск (/bp, /battlepass)
        try:
            from handlers import battlepass
            if hasattr(battlepass, 'router'):
                dp.include_router(battlepass.router)
                logger.info("Роутер battlepass зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.battlepass не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера battlepass: {e}")

        # Мини-игры (coin, guess, dice, even, highlow, redblack, lucky7, double, triple, spin)
        try:
            from handlers import minigames
            if hasattr(minigames, 'router'):
                dp.include_router(minigames.router)
                logger.info("Роутер minigames зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.minigames не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера minigames: {e}")
        
        # Инвентарь (market, tehnologmarket, inventory)
        try:
            from handlers import inventory
            if hasattr(inventory, 'router'):
                dp.include_router(inventory.router)
                logger.info("Роутер inventory зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.inventory не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера inventory: {e}")
        
        # Аккаунт (account, accountphoto, accountinfo, status, lvl)
        try:
            from handlers import account
            if hasattr(account, 'router'):
                dp.include_router(account.router)
                logger.info("Роутер account зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.account не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера account: {e}")
        
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
            logger.error(f"Ошибка регистрации роутера rofl: {e}")

        # Админ (если нужен)
        try:
            from handlers import admin
            if hasattr(admin, 'router'):
                dp.include_router(admin.router)
                logger.info("Роутер admin зарегистрирован")
        except ImportError:
            logger.warning("Модуль handlers.admin не найден, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка регистрации роутера admin: {e}")
        
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
        # Получаем информацию о боте
        bot_info = await bot.get_me()
        logger.info("=" * 50)
        logger.info(f"Бот запущен: @{bot_info.username}")
        logger.info(f"ID бота: {bot_info.id}")
        logger.info(f"Имя бота: {bot_info.first_name}")
        logger.info("=" * 50)
        
        # Проверяем наличие необходимых директорий
        required_dirs = [config.LOGS_DIR, config.ASSETS_DIR, config.IMAGES_DIR, config.AUDIO_DIR, config.VIDEO_DIR]
        for directory in required_dirs:
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                logger.info(f"Создана директория: {directory}")
        
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
        logger.info("Инициализация базы данных...")
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
        
        # Регистрация обработчиков событий жизненного цикла
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)
        
        # Запуск polling
        logger.info("Запуск polling...")
        logger.info(f"Режим работы: {config.ENVIRONMENT}")
        
        try:
            # В aiogram 3.x start_polling автоматически обрабатывает graceful shutdown
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
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
