from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message

import config


class AdminOnly(BaseMiddleware):
    """Пропускает только сообщения от ADMIN_ID для обработки команд."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.from_user and event.from_user.id != config.ADMIN_ID:
                # Не команда — пропускаем дальше (может быть контент)
                if event.text and event.text.startswith("/"):
                    return  # игнорируем команды от не-администраторов
        return await handler(event, data)
