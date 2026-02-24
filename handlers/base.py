"""
Tehnolog Games — базовые команды: /help, /start, /balance, /top, /report, /admins
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Router
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.filters import Command, F

from config import config
from db import db
from utils import delete_message_after, format_message_with_username

# Создаем роутер для базовых команд
router = Router()

logger = logging.getLogger(__name__)


@router.message(Command("start"))
async def cmd_start(message: Message):
    """
    /start — приветствие и краткая навигация. Бот подстраивается под тип игрока (новичок/про).
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    base_text = (
        "👋 Привет! Я Tehnolog Games — бот с играми на коины, экономикой и профилем.\n\n"
        "• <b>/help</b> — полный список команд и разделов (игры, экономика, профиль).\n"
        "• <b>/balance</b> — твой баланс и уровень.\n"
        "• <b>/helpgame название</b> — подробные правила любой игры (например: /helpgame slot или /helpgame fracture).\n\n"
    )
    try:
        user = await db.get_user(user_id)
        if not user:
            await db.create_user(user_id, username)
        tier = await db.get_user_tier(user_id)
        if tier == "newcomer":
            base_text += "🆕 Ты новичок — загляни в <b>/tutorial</b>, там покажем достижения, биржу, лиги и квесты.\n\nНачни с /help — там всё по полочкам."
        elif tier == "pro":
            base_text += "🔥 Ты уже в деле — не забудь <b>/bp</b> (боевой пропуск), <b>/season</b> и <b>/cup</b> за наградами.\n\nНачни с /help — там всё по полочкам."
        else:
            base_text += "Начни с /help — там всё по полочкам."
    except Exception:
        base_text += "Начни с /help — там всё по полочкам."

    help_text = format_message_with_username(base_text, username, first_name)
    keyboard = None
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Обучение — где что искать", callback_data="tutorial_main")],
        ])
    except Exception:
        pass
    sent_message = await message.answer(help_text, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent_message, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Пользователь {user_id} использовал /start")


@router.message(Command("tutorial"))
async def cmd_tutorial(message: Message):
    """Обучение для новичков: достижения, статусы, биржа, лиги, мини-игры."""
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    text = format_message_with_username(
        "📖 <b>Обучение</b> — что есть в боте кроме игр:\n\n"
        "🏅 <b>Достижения</b> — открой в профиле (/profile). Первая победа, 100 игр, миллионер, все 40 risk-игр, биржа +10% за день и др. Дают бейджи.\n\n"
        "🏷️ <b>Статусы</b> — /statusmarket. Покупай «Богач», «Пубертат страны» и др. — отображаются в профиле.\n\n"
        "📈 <b>Биржа</b> — /birzh. Шарага, Mr.Kris, ЖД, MR.lisayaderektrisa. Есть дневные задания с наградой коинами.\n\n"
        "📊 <b>Лиги и сезоны</b> — /profile и /season. MMR растёт за победы. Топ лиги получают награды. Кубки по играм — /cup slot, /cup fracture.\n\n"
        "🎫 <b>Боевой пропуск</b> — /bp. Квесты дают XP и уровни; с Premium — доп. награды (как Brawl Pass).\n\n"
        "🎲 <b>Мини-игры</b> — /minigames. Орёл/решка (/coin), угадай число (/guess), кость (/dice) и ещё 7 штук. Быстрый раунд 10–500 коинов.\n\n"
        "💬 <b>Обращение</b> — в профиле кнопка «Изменить обращение»: как бот тебя зовёт (дружок, царь батюшка и т.д.).",
        username, first_name
    )
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Профиль и достижения", callback_data="tutorial_profile"), InlineKeyboardButton(text="Биржа и задания", callback_data="tutorial_birzh")],
        [InlineKeyboardButton(text="Лиги и кубки", callback_data="tutorial_season"), InlineKeyboardButton(text="Мини-игры", callback_data="tutorial_minigames")],
    ])
    sent = await message.answer(text, reply_markup=keyboard)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.callback_query(F.data.startswith("tutorial_"))
async def cb_tutorial(callback: CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    part = callback.data.replace("tutorial_", "")
    username = callback.from_user.username or ""
    first_name = callback.from_user.first_name or ""
    if part == "main":
        text = "📖 Нажми /tutorial в чате — там полный обзор: достижения, статусы, биржа, лиги, мини-игры."
    elif part == "profile":
        text = "👤 /profile — баланс, лига, MMR, достижения. Кнопка «Изменить обращение». /statusmarket — магазин статусов."
    elif part == "birzh":
        text = "📈 /birzh — курсы и покупка/продажа. Задание на день: например «Купи 100 ЖД» — награда коинами."
    elif part == "season":
        text = "📊 /season — текущий сезон и топ. /cup slot и /cup fracture — кубки по победам за сезон."
    elif part == "minigames":
        text = "🎲 /minigames — список. /coin 50, /guess 100 — быстрые раунды с фиксированным множителем."
    else:
        text = "📖 /tutorial — полное обучение."
    await callback.answer()
    try:
        await callback.message.edit_text(format_message_with_username(text, username, first_name))
    except Exception:
        pass


@router.message(Command("help"))
async def cmd_help(message: Message):
    """
    /help — только актуальные команды Tehnolog Games. Без медиа. Игры: честные и азартные.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    help_text = format_message_with_username(
        "🎮 <b>Tehnolog Games</b> v1.2 — игры на коины, экономика, биржа, профиль с лигой\n\n",
        username, first_name
    )
    help_text += "📌 <b>v1.2</b> — биржа: Шарага, Mr.Kris, ЖД, MR.lisayaderektrisa. Исправлен излом решения. /obnova — список изменений.\n\n"
    help_text += "📋 <b>БАЗОВЫЕ КОМАНДЫ</b>\n"
    help_text += "/help — этот список | /balance — баланс и уровень | /top — топ по балансу\n"
    help_text += "/news — игровые новости (влияют на игры; смотри перед ставкой)\n"
    help_text += "/admins — кто управляет | /report — репорт\n\n"
    help_text += "💰 <b>ЭКОНОМИКА</b>\n"
    help_text += "/refill — +100 коинов раз в 2 часа | /donate @user сумма комментарий — перевод\n"
    help_text += "/ref #КОД — реферальный код (одноразовый)\n\n"
    help_text += "🎲 <b>ИГРЫ: ОСНОВНЫЕ</b>\n"
    help_text += "/slot — слоты, один раунд | /konopla — один раунд, крупный выигрыш или проигрыш\n"
    help_text += "/kripta сумма — Lucky Jet: множитель растёт, забери вовремя\n"
    help_text += "/almaz сумма — алмазы: копай или забирай, риск растёт\n"
    help_text += "/chisla @user сумма — PvP-дуэль: оба выбирают карту, выше множитель — забирает банк\n"
    help_text += "/plsdon — «задонать» боту (есть кулдаун)\n\n"
    help_text += "🎰 <b>ИГРЫ: МУЛЬТИПЛЕЕР И РЫНОК</b>\n"
    help_text += "/rulet сумма — русская рулетка (2–8 игроков, выбывание по одному, последний забирает банк)\n"
    help_text += "/frekaz сумма — фреказ (до 5 игроков, через 2 мин победитель по весу ставок)\n"
    help_text += "/perekyp сумма — перекуп: объявления с техникой, рейтинг продавца, торг, перепродажа\n"
    help_text += "/birzh — биржа: Шарага, Mr.Kris, ЖД, MR.lisayaderektrisa (купить/продать по 100), Технолог-коин в ₽\n\n"
    help_text += "🔄 <b>ИГРЫ: РИСК / ЗАБРАТЬ</b> (40 штук)\n"
    help_text += "/reactor, /vault, /dicepath, /overheat, /mindlock, /bombline, /liftx, /doza, /shum, /signal и ещё 31 игра.\n"
    help_text += "Одна механика: множитель растёт, кнопки «Ещё» и «Забрать». Подробнее: /helpgame reactor\n\n"
    help_text += "✨ <b>ОСОБЫЕ ИГРЫ</b> (под стиль и тренды)\n"
    help_text += "/random — судьба технолога: бот сам выбирает игру и ставку, запускает без подтверждения\n"
    help_text += "/gamerandom — сбой матрицы: одна игра из кусков других, случайные механика и награда\n"
    help_text += "/blackmarket [сумма] — чёрный рынок: три сделки (красная/жёлтая/зелёная), риск и подстава\n"
    help_text += "/echo — эхо решений: бот смотрит твои последние игры, даёт архетип и раунд под твой стиль\n"
    help_text += "/topgame — топ игр за сутки и тренд (в тренде / стабильно / падает)\n"
    help_text += "/fracture [ставка] — излом решения: три шага с выбором, итог по цепочке решений\n"
    help_text += "/mirror — зеркало: один раунд против «копии» себя по стилю из последних игр\n\n"
    help_text += "👤 <b>ПРОФИЛЬ И АККАУНТ</b>\n"
    help_text += "/profile — профиль (баланс, лига, достижения) | /accaunt — меню аккаунта\n"
    help_text += "/pererozhd — перерождение: сброс баланса, +0.5x удачи (1M, 2M, 4M…)\n"
    help_text += "/accountphoto — сменить аватарку | /accountinfo — описание «о себе»\n"
    help_text += "/checkaccount @user — чужой профиль | /lvl, /lvlup, /lvlcheck @user\n\n"
    help_text += "⭐ <b>PREMIUM И ЭФФЕКТЫ</b>\n"
    help_text += "/premium — тарифы | /timeprem, /effect — активные эффекты | /kachalka — бафф и снижение КД\n\n"
    help_text += "🛒 <b>МАГАЗИН</b>\n"
    help_text += "/market — зелья удачи | /inventory — инвентарь и предметы\n\n"
    help_text += "🎭 /steal @user — украсть 50 коинов (КД 24ч) | /freedurev — одноразовый промокод на бота\n\n"
    help_text += "📖 <b>ПОДРОБНЫЕ ПРАВИЛА ИГР</b>\n"
    help_text += "Напиши /helpgame и название игры — полное описание без формул.\n"
    help_text += "Примеры: /helpgame slot | /helpgame fracture | /helpgame mirror | /helpgame echo\n"
    help_text += "Список всех игр: /helpgame (без названия)\n\n"
    help_text += "🛑 /cancel — отмена текущей игры | /status — есть ли активная игра | /obnova — что нового\n"
    help_text += "/statusmarket — магазин статусов (Богач, Пубертат страны и т.д.)\n\n"
    help_text += "📌 <b>Общее:</b> каждая игра живёт до 3 минут; при таймауте — возврат ставки или авто-результат. Реклама каждые 60 команд (3 мин блок); у Premium рекламы нет. Tehnolog Games"

    sent_message = await message.answer(help_text)
    asyncio.create_task(delete_message_after(sent_message, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Пользователь {user_id} использовал /help")


@router.message(Command("obnova"))
async def cmd_obnova(message: Message):
    """Описание обновлений: что добавлено и урезано. Только для игроков, без закулисья."""
    username = message.from_user.username
    first_name = message.from_user.first_name
    lines = getattr(config, "OBNOVA_LINES", ["Нет записей об обновлениях."])
    text = format_message_with_username("\n".join(lines), username, first_name)
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))


@router.message(Command("report"))
async def cmd_report(message: Message):
    """Команда /report — репорт бага или игрока. По всем вопросам — @DPOPTH"""
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "user"
    creator = getattr(config, "CREATOR_USERNAME", "DPOPTH")
    text = f"@{username} хочет репортнуть баг или игрока. @{creator} помоги"
    sent = await message.answer(text)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Пользователь {user_id} использовал /report")


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    """
    Команда /balance
    Отправляет фото bal.jpg с подписью: баланс, уровень, статус, VIP/Premium
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Получаем информацию о пользователе
    user = await db.get_user(user_id)
    if not user:
        # Создаем пользователя если его нет
        await db.create_user(user_id, username)
        user = await db.get_user(user_id)
    
    balance = user["balance"]
    level = user["level"]
    status = user["status"] or "Нет статуса"
    premium_until = user["premium_until"]
    
    # Проверяем Premium
    is_premium = await db.is_premium(user_id)
    
    # Формируем подпись
    caption = format_message_with_username(
        f"💰 Баланс: <b>{balance}</b> коинов\n"
        f"📊 Уровень: <b>{level}</b>\n"
        f"🏷️ Статус: {status}\n",
        username, first_name
    )
    
    if is_premium:
        # Вычисляем время окончания Premium
        now = int(datetime.now().timestamp())
        if premium_until and premium_until > now:
            time_left = premium_until - now
            hours = time_left // 3600
            minutes = (time_left % 3600) // 60
            
            if hours > 0:
                caption += f"👑 Premium активен еще {hours}ч {minutes}м\n"
            else:
                caption += f"👑 Premium активен еще {minutes}м\n"
        else:
            caption += "👑 Premium активен\n"
    else:
        caption += "⭐ Premium не активен\n"
    
    # Отправляем фото
    photo_path = config.get_image_path("bal.jpg")
    
    try:
        if photo_path.exists():
            photo = FSInputFile(str(photo_path))
            sent_message = await message.answer_photo(
                photo=photo,
                caption=caption
            )
        else:
            # Если фото нет, отправляем только текст
            sent_message = await message.answer(caption)
            logger.warning(f"Фото bal.jpg не найдено для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки /balance для {user_id}: {e}")
        # Fallback - отправляем только текст
        sent_message = await message.answer(caption)
    
    asyncio.create_task(delete_message_after(sent_message, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Пользователь {user_id} использовал /balance (баланс: {balance}, уровень: {level})")


def _role_display_name(u: dict, fallback_id: int = None) -> str:
    if not u:
        return f"ID{fallback_id}" if fallback_id else "—"
    un = u.get("username")
    return f"@{un}" if un else f"ID{u.get('user_id', fallback_id or '?')}"


@router.message(Command("top"))
async def cmd_top(message: Message):
    """
    /top — создатель, админы, модеры, топ-5 по балансу с VIP-статусами.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    lines = []
    creator_id = config.CREATOR_ID
    admin_ids = config.get_admin_ids_list()
    moder_ids = config.get_moder_ids_list()

    if creator_id:
        u = await db.get_user(creator_id)
        lines.append(f"👑 <b>Создатель:</b> {_role_display_name(u, creator_id)}\n")
    if admin_ids:
        tags = [_role_display_name(await db.get_user(uid), uid) for uid in admin_ids]
        lines.append(f"🛡 <b>Админы:</b> {', '.join(tags)}\n")
    if moder_ids:
        tags = [_role_display_name(await db.get_user(uid), uid) for uid in moder_ids]
        lines.append(f"🔧 <b>Модеры:</b> {', '.join(tags)}\n")

    lines.append("\n🏆 <b>ТОП-5 МАЖОРОВ:</b>\n\n")
    top_users = await db.get_top_users(limit=5)
    current_user = await db.get_user(user_id)

    if not top_users:
        lines.append("Пока никого нет в топе 😢")
    else:
        for idx, user_data in enumerate(top_users, 1):
            top_username = user_data["username"] or "Без имени"
            top_balance = user_data["balance"]
            top_level = user_data["level"]
            top_status = user_data["status"] or "Нет статуса"
            top_premium_until = user_data["premium_until"]
            now = int(datetime.now().timestamp())
            is_top_premium = bool(top_premium_until and top_premium_until > now)
            premium_mark = "👑" if is_top_premium else ""
            user_tag = f"@{top_username}" if top_username != "Без имени" else top_username
            lines.append(
                f"{idx}. {premium_mark} <b>LVL{top_level}</b> {user_tag}\n"
                f"   💰 {top_balance} коинов | {top_status}\n\n"
            )

    user_in_top = any(u["user_id"] == user_id for u in top_users) if top_users else False
    if not user_in_top and current_user:
        lines.append("\n💸 <b>НИЩЕТА</b>\nТы не в топе. Попробуй заработать больше коинов! 💪")

    top_text = format_message_with_username("".join(lines), username, first_name)
    sent_message = await message.answer(top_text)
    asyncio.create_task(delete_message_after(sent_message, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Пользователь {user_id} использовал /top")


@router.message(Command("admins"))
async def cmd_admins(message: Message):
    """
    /admins — список: 👑 Создатель, 🛡 Админы, 🔧 Модеры, 🧩 Младшие модеры.
    Учитываются роли из конфига и выданные через /addadmin, /addmoder, /addjuniormoder.
    """
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    blocks = []
    if config.CREATOR_ID:
        u = await db.get_user(config.CREATOR_ID)
        blocks.append(f"👑 <b>Создатель</b>\n{_role_display_name(u, config.CREATOR_ID)}")
    admin_ids = list(set(config.get_admin_ids_list() + await db.get_users_with_role("admin")))
    if admin_ids:
        tags = [_role_display_name(await db.get_user(uid), uid) for uid in admin_ids]
        blocks.append(f"🛡 <b>Админы</b>\n{', '.join(tags)}")
    moder_ids = list(set(config.get_moder_ids_list() + await db.get_users_with_role("moder")))
    if moder_ids:
        tags = [_role_display_name(await db.get_user(uid), uid) for uid in moder_ids]
        blocks.append(f"🔧 <b>Модеры</b>\n{', '.join(tags)}")
    jr_ids = list(set(config.get_junior_moder_ids_list() + await db.get_users_with_role("juniormoder")))
    if jr_ids:
        tags = [_role_display_name(await db.get_user(uid), uid) for uid in jr_ids]
        blocks.append(f"🧩 <b>Младшие модеры</b>\n{', '.join(tags)}")
    if not blocks:
        blocks.append("Список ролей пуст. Настрой CREATOR_ID в конфиге или используй /addadmin, /addmoder, /addjuniormoder.")

    text = "\n\n".join(blocks)
    out = format_message_with_username(text, username, first_name)
    sent = await message.answer(out)
    asyncio.create_task(delete_message_after(sent, config.MESSAGE_DELETE_TIMEOUT))
    logger.info(f"Пользователь {user_id} использовал /admins")
