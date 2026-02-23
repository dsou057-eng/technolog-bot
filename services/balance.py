"""
Сервис для работы с балансом пользователей
Списания, начисления, защита от отрицательного баланса
Отдельные сообщения на 5 секунд, логирование в БД и файл
"""

import asyncio
import logging
from typing import Optional, Tuple
from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from config import config
from db import db
from utils import delete_message_after, format_message_with_username

logger = logging.getLogger(__name__)


class BalanceService:
    """
    Сервис для работы с балансом пользователей
    Обеспечивает безопасные операции с балансом, логирование и уведомления
    """
    
    def __init__(self):
        """Инициализация сервиса баланса"""
        pass
    
    async def add_balance(
        self,
        user_id: int,
        amount: int,
        command_source: str,
        comment: str = None,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        username: str = None,
        first_name: str = None
    ) -> Tuple[bool, int, int]:
        """
        Начисление баланса пользователю
        
        Args:
            user_id: ID пользователя
            amount: Сумма начисления (должна быть положительной)
            command_source: Команда-источник транзакции
            comment: Комментарий к транзакции
            message: Сообщение для отправки уведомления (опционально)
            bot: Бот для отправки сообщения (если message не указан)
            username: Username пользователя (для форматирования)
            first_name: Имя пользователя (для форматирования)
            
        Returns:
            Кортеж (успех, баланс_до, баланс_после)
        """
        if amount <= 0:
            logger.warning(f"Попытка начисления неположительной суммы {amount} для пользователя {user_id}")
            return False, 0, 0
        
        try:
            # Получаем текущий баланс
            balance_before = await db.get_balance(user_id)
            
            balance_before, balance_after = await db.update_balance(
                user_id=user_id,
                amount=amount,
                transaction_type="income",
                command_source=command_source,
                comment=comment
            )
            await db.update_total_coins(user_id, amount)

            logger.info(
                f"Начисление баланса: user_id={user_id}, amount={amount}, "
                f"balance_before={balance_before}, balance_after={balance_after}, "
                f"source={command_source}, comment={comment or 'N/A'}"
            )
            # Уведомление создателю при резком росте баланса
            thresh = getattr(config, "NOTIFY_CREATOR_BALANCE_THRESHOLD", 100_000)
            am_thresh = getattr(config, "NOTIFY_CREATOR_SINGLE_AMOUNT", 50_000)
            if bot and (balance_after >= thresh or amount >= am_thresh):
                from utils import notify_creator
                asyncio.create_task(notify_creator(bot, f"Рост баланса: user_id={user_id}, +{amount}, баланс={balance_after}, источник={command_source}"))
            await self._send_transaction_notification(
                user_id=user_id,
                amount=amount,
                transaction_type="income",
                balance_after=balance_after,
                message=message,
                bot=bot,
                chat_id=chat_id,
                username=username,
                first_name=first_name
            )
            
            return True, balance_before, balance_after
            
        except Exception as e:
            logger.error(f"Ошибка начисления баланса для пользователя {user_id}: {e}", exc_info=True)
            return False, 0, 0
    
    async def subtract_balance(
        self,
        user_id: int,
        amount: int,
        command_source: str,
        comment: str = None,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        username: str = None,
        first_name: str = None,
        allow_negative: bool = False
    ) -> Tuple[bool, int, int, str]:
        """
        Списание баланса у пользователя с защитой от отрицательного баланса
        
        Args:
            user_id: ID пользователя
            amount: Сумма списания (должна быть положительной)
            command_source: Команда-источник транзакции
            comment: Комментарий к транзакции
            message: Сообщение для отправки уведомления (опционально)
            bot: Бот для отправки сообщения (если message не указан)
            chat_id: ID чата для отправки через bot
            username: Username пользователя (для форматирования)
            first_name: Имя пользователя (для форматирования)
            allow_negative: Разрешить отрицательный баланс (по умолчанию False)
            
        Returns:
            Кортеж (успех, баланс_до, баланс_после, сообщение_об_ошибке)
        """
        """
        Списание баланса у пользователя с защитой от отрицательного баланса
        
        Args:
            user_id: ID пользователя
            amount: Сумма списания (должна быть положительной)
            command_source: Команда-источник транзакции
            comment: Комментарий к транзакции
            message: Сообщение для отправки уведомления (опционально)
            bot: Бот для отправки сообщения (если message не указан)
            username: Username пользователя (для форматирования)
            first_name: Имя пользователя (для форматирования)
            allow_negative: Разрешить отрицательный баланс (по умолчанию False)
            
        Returns:
            Кортеж (успех, баланс_до, баланс_после, сообщение_об_ошибке)
        """
        if amount <= 0:
            logger.warning(f"Попытка списания неположительной суммы {amount} для пользователя {user_id}")
            return False, 0, 0, "Сумма списания должна быть положительной"
        
        try:
            # Получаем текущий баланс
            balance_before = await db.get_balance(user_id)
            
            # Проверяем достаточность средств
            if not allow_negative and balance_before < amount:
                error_msg = (
                    f"Недостаточно средств! "
                    f"Нужно {amount} коинов, у тебя {balance_before} коинов"
                )
                
                # Логируем попытку списания при недостатке средств
                logger.warning(
                    f"Попытка списания при недостатке средств: user_id={user_id}, "
                    f"amount={amount}, balance={balance_before}, source={command_source}"
                )
                
                # Отправляем сообщение об ошибке
                # Определяем chat_id из message или используем переданный
                error_chat_id = chat_id if chat_id else (message.chat.id if message else None)
                await self._send_error_notification(
                    error_msg=error_msg,
                    message=message,
                    bot=bot,
                    chat_id=error_chat_id,
                    user_id=user_id,
                    username=username,
                    first_name=first_name
                )
                
                return False, balance_before, balance_before, error_msg
            
            # Обновляем баланс
            balance_before, balance_after = await db.update_balance(
                user_id=user_id,
                amount=-amount,
                transaction_type="expense",
                command_source=command_source,
                comment=comment,
                allow_negative=allow_negative
            )
            
            # Логируем в файл
            logger.info(
                f"Списание баланса: user_id={user_id}, amount={amount}, "
                f"balance_before={balance_before}, balance_after={balance_after}, "
                f"source={command_source}, comment={comment or 'N/A'}"
            )
            
            # Отправляем уведомление о списании
            await self._send_transaction_notification(
                user_id=user_id,
                amount=amount,
                transaction_type="expense",
                balance_after=balance_after,
                message=message,
                bot=bot,
                chat_id=chat_id,
                username=username,
                first_name=first_name
            )
            
            return True, balance_before, balance_after, ""
            
        except Exception as e:
            logger.error(f"Ошибка списания баланса для пользователя {user_id}: {e}", exc_info=True)
            return False, 0, 0, f"Ошибка при списании: {e}"
    
    async def transfer_balance(
        self,
        sender_id: int,
        receiver_id: int,
        amount: int,
        command_source: str,
        comment: str = None,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        sender_username: str = None,
        sender_first_name: str = None,
        receiver_username: str = None,
        receiver_first_name: str = None
    ) -> Tuple[bool, str]:
        """
        Перевод баланса между пользователями
        
        Args:
            sender_id: ID отправителя
            receiver_id: ID получателя
            amount: Сумма перевода
            command_source: Команда-источник транзакции
            comment: Комментарий к транзакции
            message: Сообщение для отправки уведомления
            bot: Бот для отправки сообщения
            sender_username: Username отправителя
            sender_first_name: Имя отправителя
            receiver_username: Username получателя
            receiver_first_name: Имя получателя
            
        Returns:
            Кортеж (успех, сообщение_об_ошибке)
        """
        if sender_id == receiver_id:
            return False, "Нельзя переводить самому себе"
        
        if amount <= 0:
            return False, "Сумма перевода должна быть положительной"
        
        # Списываем у отправителя
        success, balance_before, balance_after, error = await self.subtract_balance(
            user_id=sender_id,
            amount=amount,
            command_source=command_source,
            comment=f"Перевод пользователю {receiver_id}: {comment or ''}",
            message=message,
            bot=bot,
            chat_id=chat_id,
            username=sender_username,
            first_name=sender_first_name
        )
        
        if not success:
            return False, error
        
        # Начисляем получателю
        success_receive, _, _ = await self.add_balance(
            user_id=receiver_id,
            amount=amount,
            command_source=command_source,
            comment=f"Перевод от пользователя {sender_id}: {comment or ''}",
            message=None,  # Не отправляем уведомление получателю автоматически
            bot=bot,
            chat_id=chat_id,
            username=receiver_username,
            first_name=receiver_first_name
        )
        
        if not success_receive:
            # Откатываем транзакцию отправителя (возвращаем средства)
            await db.update_balance(
                user_id=sender_id,
                amount=amount,
                transaction_type="income",
                command_source="rollback",
                comment=f"Откат перевода пользователю {receiver_id}"
            )
            logger.error(f"Ошибка начисления получателю {receiver_id}, откат транзакции")
            return False, "Ошибка при переводе получателю"
        
        # Логируем успешный перевод
        logger.info(
            f"Перевод баланса: sender_id={sender_id}, receiver_id={receiver_id}, "
            f"amount={amount}, comment={comment or 'N/A'}"
        )
        
        return True, ""
    
    async def charge_commission(
        self,
        user_id: int,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        username: str = None,
        first_name: str = None,
    ) -> bool:
        """
        Списывает комиссию 5 коинов. Вызывать ТОЛЬКО после успешного выполнения платной команды.
        При ошибке/отмене/нехватке баланса комиссию не списывать.
        Отправляет отдельное сообщение «Списано 5» на 5 сек.
        """
        amount = getattr(config, "DEFAULT_COMMISSION", 5)
        success, _, _, _ = await self.subtract_balance(
            user_id=user_id,
            amount=amount,
            command_source="commission",
            comment="Комиссия за команду",
            message=message,
            bot=bot,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            allow_negative=False,
        )
        return success

    async def check_balance(self, user_id: int, required_amount: int) -> Tuple[bool, int]:
        """
        Проверка достаточности баланса
        
        Args:
            user_id: ID пользователя
            required_amount: Требуемая сумма
            
        Returns:
            Кортеж (достаточно_средств, текущий_баланс)
        """
        balance = await db.get_balance(user_id)
        return balance >= required_amount, balance

    async def add_game_win(
        self,
        user_id: int,
        gross_amount: int,
        command_source: str,
        comment: str = None,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        username: str = None,
        first_name: str = None,
        is_premium: bool = None,
    ) -> Tuple[bool, int, int, int]:
        """
        Начисление выигрыша за игру с учётом лимита и налога Технолога.
        Налог: 5% (база) или 2% (премиум). Лимит выигрыша за одну игру — из config.
        Returns: (успех, баланс_до, баланс_после, сумма_налога).
        """
        if gross_amount <= 0:
            return False, 0, 0, 0
        max_win = getattr(config, "MAX_WIN_PER_GAME", 50_000)
        capped = min(gross_amount, max_win)
        if is_premium is None:
            is_premium = await db.is_premium(user_id)
        tax_rate = getattr(config, "TAX_ON_WIN_PERCENT_PREMIUM", 0.02) if is_premium else getattr(config, "TAX_ON_WIN_PERCENT", 0.05)
        tax = int(capped * tax_rate)
        ev = await db.get_active_event(user_id)
        if ev and ev.get("event_type") == "lucky_taxfree":
            tax = 0
        net = int(capped - tax)
        if net <= 0:
            return False, 0, 0, 0
        success, balance_before, balance_after = await self.add_balance(
            user_id=user_id,
            amount=net,
            command_source=command_source,
            comment=comment or "Выигрыш в игре",
            message=message,
            bot=bot,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
        )
        if success:
            thresh = getattr(config, "NOTIFY_CREATOR_BALANCE_THRESHOLD", 100_000)
            am_thresh = getattr(config, "NOTIFY_CREATOR_SINGLE_AMOUNT", 50_000)
            if (bot or message) and (balance_after >= thresh or capped >= am_thresh):
                from utils import notify_creator
                b = bot if bot else (getattr(message, "bot", None) if message else None)
                if b:
                    asyncio.create_task(notify_creator(b, f"Крупный выигрыш: user_id={user_id}, +{net} (налог {tax}), баланс={balance_after}, {command_source}"))
        if success and tax > 0:
            tax_text = format_message_with_username(
                "Технолог забрал свой налог 🧠",
                username, first_name
            )
            try:
                if message:
                    sent = await message.answer(tax_text)
                elif bot and chat_id:
                    sent = await bot.send_message(chat_id=chat_id, text=tax_text)
                else:
                    sent = None
                if sent:
                    asyncio.create_task(delete_message_after(sent, config.TRANSACTION_MESSAGE_TIMEOUT))
            except Exception as e:
                logger.warning(f"Не удалось отправить сообщение о налоге: {e}")
        return success, balance_before, balance_after, tax
    
    async def _send_transaction_notification(
        self,
        user_id: int,
        amount: int,
        transaction_type: str,
        balance_after: int,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        username: str = None,
        first_name: str = None
    ):
        """
        Отправка уведомления о транзакции (начисление/списание)
        Сообщение удаляется через 5 секунд
        
        Args:
            user_id: ID пользователя
            amount: Сумма транзакции
            transaction_type: Тип транзакции (income/expense)
            balance_after: Баланс после транзакции
            message: Сообщение для ответа
            bot: Бот для отправки
            chat_id: ID чата для отправки через bot
            username: Username пользователя
            first_name: Имя пользователя
        """
        if transaction_type == "income":
            text = f"Начислено {amount} коинов 💰"
        else:
            text = f"Списано {amount} коинов 💸"
        
        notification_text = format_message_with_username(text, username, first_name)
        
        try:
            if message:
                sent_message = await message.answer(notification_text)
            elif bot and chat_id:
                sent_message = await bot.send_message(chat_id=chat_id, text=notification_text)
            else:
                logger.warning(f"Не указан message или (bot+chat_id) для отправки уведомления для {user_id}")
                return
            
            # Автоудаление через 5 секунд
            asyncio.create_task(delete_message_after(sent_message, config.TRANSACTION_MESSAGE_TIMEOUT))
            
        except TelegramBadRequest as e:
            logger.error(f"Ошибка отправки уведомления о транзакции для {user_id}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при отправке уведомления для {user_id}: {e}")
    
    async def _send_error_notification(
        self,
        error_msg: str,
        message: Message = None,
        bot: Bot = None,
        chat_id: int = None,
        user_id: int = None,
        username: str = None,
        first_name: str = None
    ):
        """
        Отправка уведомления об ошибке транзакции
        
        Args:
            error_msg: Текст ошибки
            message: Сообщение для ответа
            bot: Бот для отправки
            chat_id: ID чата для отправки через bot
            user_id: ID пользователя
            username: Username пользователя
            first_name: Имя пользователя
        """
        notification_text = format_message_with_username(error_msg, username, first_name)
        
        try:
            if message:
                sent_message = await message.answer(notification_text)
                # Автоудаление через 30 секунд (обычное время для ошибок)
                asyncio.create_task(delete_message_after(sent_message))
            elif bot and chat_id:
                sent_message = await bot.send_message(chat_id=chat_id, text=notification_text)
                asyncio.create_task(delete_message_after(sent_message))
            else:
                logger.warning(f"Не указан message или (bot+chat_id) для отправки уведомления об ошибке для {user_id}")
                
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об ошибке для {user_id}: {e}")


# Глобальный экземпляр сервиса
balance_service = BalanceService()
