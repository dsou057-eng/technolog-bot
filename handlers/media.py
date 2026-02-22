"""
Медиа-команды бота YandexPticaGPT v0.5
Фото: /olegtemni → photo_1.jpg, … /oleg → photo_5.jpg
Камеры: /cam1–/cam5. Аудио: /cityboy, /ignat. Фото+звук: /olegdexter.
Доставка: /dostavka — спрашивает куда/что, затем стадии по таймерам.
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message, FSInputFile, InputMediaPhoto
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from utils import delete_message_after, format_message_with_username

router = Router()
logger = logging.getLogger(__name__)


class DostavkaStates(StatesGroup):
    """Состояния диалога доставки: бот спрашивает куда, затем что."""
    wait_where = State()
    wait_what = State()

DELETE_SEC = config.MESSAGE_DELETE_TIMEOUT

# По README: photo_1.jpg … photo_5.jpg; fallback 1.jpg … 5.jpg
PHOTO_COMMANDS = {
    "olegtemni": ("photo_1.jpg", "1.jpg"),
    "detimoi": ("photo_2.jpg", "2.jpg"),
    "deniska": ("photo_3.jpg", "3.jpg"),
    "kb": ("photo_4.jpg", "4.jpg"),
    "oleg": ("photo_5.jpg", "5.jpg"),
}

CAM_COMMANDS = {f"cam{i}": f"cam{i}.jpg" for i in range(1, 6)}


def _get_photo_path(name: str):
    p = config.get_image_path(name)
    return p if p.exists() else None


def _get_photo_path_or_fallback(primary: str, fallback: str):
    return _get_photo_path(primary) or _get_photo_path(fallback)


def _get_audio_path(name: str):
    p = config.get_audio_path(name)
    return p if p.exists() else None


@router.message(Command("olegtemni", "detimoi", "deniska", "kb", "oleg"))
async def cmd_photo(message: Message):
    """Фото: olegtemni → photo_1/1.jpg, detimoi → photo_2/2.jpg и т.д."""
    cmd = (message.text or "").strip().split()[0].lstrip("/").split("@")[0]
    pair = PHOTO_COMMANDS.get(cmd, ("photo_1.jpg", "1.jpg"))
    path = _get_photo_path_or_fallback(pair[0], pair[1])
    username = message.from_user.username
    first_name = message.from_user.first_name

    if not path:
        sent = await message.answer(
            format_message_with_username(f"Медиа {pair[0]} пока нет. Добавь в assets/images.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))
        return

    try:
        sent = await message.answer_photo(FSInputFile(str(path)))
    except Exception as e:
        logger.error(f"photo {cmd} {e}")
        sent = await message.answer(format_message_with_username("Не удалось отправить фото.", username, first_name))
    asyncio.create_task(delete_message_after(sent, DELETE_SEC))


@router.message(Command("cam1", "cam2", "cam3", "cam4", "cam5"))
async def cmd_cam(message: Message):
    """Камеры: /cam1–/cam5 отправляют cam1.jpg–cam5.jpg."""
    cmd = (message.text or "").strip().split()[0].lstrip("/").split("@")[0]
    filename = CAM_COMMANDS.get(cmd, "cam1.jpg")
    path = _get_photo_path(filename)
    username = message.from_user.username
    first_name = message.from_user.first_name

    if not path:
        sent = await message.answer(
            format_message_with_username(f"Медиа {filename} пока нет. Добавь в assets/images.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))
        return

    try:
        sent = await message.answer_photo(FSInputFile(str(path)))
    except Exception as e:
        logger.error(f"cam {cmd} {e}")
        sent = await message.answer(format_message_with_username("Не удалось отправить фото.", username, first_name))
    asyncio.create_task(delete_message_after(sent, DELETE_SEC))


@router.message(Command("cityboy"))
async def cmd_cityboy(message: Message):
    """Аудио cityboy + фото photo_6.jpg / 6.jpg (если есть)."""
    username = message.from_user.username
    first_name = message.from_user.first_name

    audio_path = _get_audio_path("cityboy.ogg") or _get_audio_path("cityboy.mp3")
    photo_path = _get_photo_path("photo_6.jpg") or _get_photo_path("6.jpg") or _get_photo_path("5.jpg")

    if not audio_path:
        sent = await message.answer(format_message_with_username("Аудио cityboy пока нет в assets/audio.", username, first_name))
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))
        return

    try:
        audio = FSInputFile(str(audio_path))
        if photo_path:
            sent = await message.answer_photo(FSInputFile(str(photo_path)))
            msg2 = await message.answer_audio(audio)
            asyncio.create_task(delete_message_after(sent, DELETE_SEC))
            asyncio.create_task(delete_message_after(msg2, DELETE_SEC))
        else:
            sent = await message.answer_audio(audio)
            asyncio.create_task(delete_message_after(sent, DELETE_SEC))
    except Exception as e:
        logger.error(f"cityboy {e}")
        sent = await message.answer(format_message_with_username("Медиа недоступно.", username, first_name))
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))


@router.message(Command("ignat"))
async def cmd_ignat(message: Message):
    """Аудио ignat."""
    username = message.from_user.username
    first_name = message.from_user.first_name
    audio_path = _get_audio_path("ignat.ogg") or _get_audio_path("ignat.mp3")
    if not audio_path:
        sent = await message.answer(format_message_with_username("Аудио ignat пока нет в assets/audio.", username, first_name))
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))
        return
    try:
        sent = await message.answer_audio(FSInputFile(str(audio_path)))
    except Exception as e:
        logger.error(f"ignat {e}")
        sent = await message.answer(format_message_with_username("Аудио недоступно.", username, first_name))
    asyncio.create_task(delete_message_after(sent, DELETE_SEC))


@router.message(Command("olegdexter"))
async def cmd_olegdexter(message: Message):
    """Фото + звук: audio_dexter.jpg + audio_dexter.mp3."""
    username = message.from_user.username
    first_name = message.from_user.first_name
    audio_path = _get_audio_path("audio_dexter.mp3")
    photo_path = _get_photo_path("audio_dexter.jpg") or _get_photo_path("photo_5.jpg") or _get_photo_path("5.jpg")
    try:
        if audio_path:
            m = await message.answer_audio(FSInputFile(str(audio_path)))
            asyncio.create_task(delete_message_after(m, DELETE_SEC))
        if photo_path:
            sent = await message.answer_photo(FSInputFile(str(photo_path)))
        else:
            sent = await message.answer(format_message_with_username("Олег Декстер 🎬", username, first_name))
    except Exception as e:
        logger.error(f"olegdexter {e}")
        sent = await message.answer(format_message_with_username("Медиа недоступно.", username, first_name))
    asyncio.create_task(delete_message_after(sent, DELETE_SEC))


async def _run_dostavka_stages(
    bot,
    chat_id: int,
    user_id: int,
    username: str,
    first_name: str,
    where: str,
    what: str,
):
    """
    Запуск стадий доставки по таймерам после ответов пользователя.
    Казаки ползут → казаки на месте → птица летит → финал.
    """
    def wrap(t: str) -> str:
        return format_message_with_username(t, username or "user", first_name or "")

    photo_path = config.get_image_path("dostavka.jpg")
    audio_path = config.get_audio_path("dostavka.mp3")
    has_photo = photo_path.exists()
    has_audio = audio_path and audio_path.exists()

    stages = [
        (4, f"🚚 <b>ДОСТАВКА</b>\n\n📍 Куда: <i>{where}</i>\n📦 Что: <i>{what}</i>\n\n3️⃣ Казаки ползут... ползут... ты думал быстро? 🐌🦎 Ничего личного, просто логистика 2026."),
        (5, "🚚 <b>ДОСТАВКА</b>\n\n4️⃣ Казаки на месте! Стоят, курят, ждут птицу. Классика жанра ✅"),
        (5, "🚚 <b>ДОСТАВКА</b>\n\n5️⃣ Птица полетела! Не догонишь, даже не пытайся 🐦💨"),
        (4, "🚚 <b>ДОСТАВКА</b>\n\n6️⃣ Финал. Всё доставлено, можно расходиться. Целуйте экран 🎉"),
    ]

    try:
        caption_0 = (
            f"🚚 <b>ДОСТАВКА</b>\n\n"
            f"📍 Куда: <i>{where}</i>\n📦 Что: <i>{what}</i>\n\n"
            f"3️⃣ Казаки ползут... ползут... ты думал быстро? 🐌🦎"
        )
        if has_photo:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(str(photo_path)),
                caption=wrap(caption_0)
            )
        else:
            sent = await bot.send_message(chat_id=chat_id, text=wrap(caption_0))
        if has_audio:
            try:
                m2 = await bot.send_audio(chat_id=chat_id, audio=FSInputFile(str(audio_path)))
                asyncio.create_task(delete_message_after(m2, DELETE_SEC))
            except Exception as e:
                logger.warning(f"dostavka stages audio {e}")
        for delay, caption_text in stages[1:]:
            await asyncio.sleep(delay)
            try:
                if has_photo:
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=sent.message_id,
                        media=InputMediaPhoto(
                            media=FSInputFile(str(photo_path)),
                            caption=wrap(caption_text)
                        )
                    )
                else:
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=sent.message_id,
                        caption=wrap(caption_text)
                    )
            except Exception as e:
                logger.warning(f"dostavka edit stage: {e}")
                break
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))
    except Exception as e:
        logger.error(f"dostavka stages {e}")


@router.message(Command("dostavka"))
async def cmd_dostavka(message: Message, state: FSMContext):
    """
    /dostavka по README: 1) спрашивает «Куда доставляем?»
    2) после ответа спрашивает «Что доставляем?»
    3) запускает стадии по таймерам: казаки ползут → на месте → птица летит → финал.
    dostavka.jpg, dostavka.mp3.
    """
    username = message.from_user.username
    first_name = message.from_user.first_name

    await state.set_state(DostavkaStates.wait_where)
    text = format_message_with_username(
        "🚚 <b>ДОСТАВКА</b>\n\n"
        "1️⃣ Куда везём? Пиши куда угодно — хоть на Луну, хоть к теще на блины. Казаки доставят 🥞",
        username, first_name
    )
    photo_path = config.get_image_path("dostavka.jpg")
    audio_path = config.get_audio_path("dostavka.mp3")

    try:
        if photo_path.exists():
            await message.answer_photo(FSInputFile(str(photo_path)), caption=text)
        else:
            await message.answer(text)
        if audio_path and audio_path.exists():
            try:
                m2 = await message.answer_audio(FSInputFile(str(audio_path)))
                asyncio.create_task(delete_message_after(m2, DELETE_SEC))
            except Exception as e:
                logger.warning(f"dostavka audio {e}")
    except Exception as e:
        logger.error(f"dostavka {e}")
        await state.clear()


@router.message(StateFilter(DostavkaStates.wait_where), F.text, ~F.text.startswith("/"))
async def dostavka_where_answer(message: Message, state: FSMContext):
    """Пользователь ответил «куда доставляем» — спрашиваем «что доставляем»."""
    where = (message.text or "").strip() or "не указано"
    await state.update_data(where=where)
    await state.set_state(DostavkaStates.wait_what)
    username = message.from_user.username
    first_name = message.from_user.first_name
    text = format_message_with_username(
        "🚚 <b>ДОСТАВКА</b>\n\n"
        "2️⃣ Что везём? Пицца с ананасами, технолог-коины, казаки — что скажешь, то и поедет 📦",
        username, first_name
    )
    try:
        await message.answer(text)
    except Exception as e:
        logger.error(f"dostavka where answer {e}")
        await state.clear()


@router.message(StateFilter(DostavkaStates.wait_what), F.text, ~F.text.startswith("/"))
async def dostavka_what_answer(message: Message, state: FSMContext):
    """Пользователь ответил «что доставляем» — запускаем стадии по таймерам."""
    what = (message.text or "").strip() or "не указано"
    data = await state.get_data()
    where = data.get("where", "не указано")
    await state.clear()

    username = message.from_user.username
    first_name = message.from_user.first_name

    asyncio.create_task(
        _run_dostavka_stages(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            username=username,
            first_name=first_name,
            where=where,
            what=what,
        )
    )
    try:
        await message.answer(
            format_message_with_username(
                "🚚 Погнали! Следи за казаками и птицей — сейчас будет огонь 🔥",
                username, first_name
            )
        )
    except Exception as e:
        logger.warning(f"dostavka what answer {e}")


@router.message(Command("linux"))
async def cmd_linux(message: Message):
    """
    Медиа-команда /linux: отправляет linux.jpg, затем linux.mp3 из assets.
    Если файла нет — бот не падает, отправляет только доступное.
    """
    username = message.from_user.username
    first_name = message.from_user.first_name

    photo_path = _get_photo_path("linux.jpg")
    audio_path = _get_audio_path("linux.mp3")

    sent = None
    try:
        if photo_path:
            sent = await message.answer_photo(FSInputFile(str(photo_path)))
            asyncio.create_task(delete_message_after(sent, DELETE_SEC))
        if audio_path:
            m2 = await message.answer_audio(FSInputFile(str(audio_path)))
            asyncio.create_task(delete_message_after(m2, DELETE_SEC))
        if not photo_path and not audio_path:
            sent = await message.answer(
                format_message_with_username(
                    "Медиа linux.jpg / linux.mp3 пока нет в assets/images и assets/audio.",
                    username, first_name
                )
            )
            asyncio.create_task(delete_message_after(sent, DELETE_SEC))
    except Exception as e:
        logger.warning(f"linux %s", e)
        if sent is None:
            try:
                sent = await message.answer(
                    format_message_with_username("Медиа недоступно.", username, first_name)
                )
                asyncio.create_task(delete_message_after(sent, DELETE_SEC))
            except Exception:
                pass


@router.message(Command("mramordpop"))
async def cmd_mramordpop(message: Message):
    """Медиа-команда /mramordpop: dpop.jpg. Проверка assets — бот не падает при отсутствии файла."""
    username = message.from_user.username
    first_name = message.from_user.first_name

    photo_path = _get_photo_path("dpop.jpg")

    if not photo_path:
        sent = await message.answer(
            format_message_with_username("Медиа dpop.jpg пока нет в assets/images.", username, first_name)
        )
        asyncio.create_task(delete_message_after(sent, DELETE_SEC))
        return
    try:
        sent = await message.answer_photo(FSInputFile(str(photo_path)))
    except Exception as e:
        logger.warning(f"mramordpop {e}")
        sent = await message.answer(format_message_with_username("Не удалось отправить фото.", username, first_name))
    asyncio.create_task(delete_message_after(sent, DELETE_SEC))
