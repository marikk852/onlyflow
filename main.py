import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
import database as db
import license
from bot.handlers import admin, content, upload

# ── Логирование ───────────────────────────────────────────────────────────────
Path(config.LOGS_DIR).mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"{config.LOGS_DIR}/contentflow.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("contentflow")


async def main():
    # ── Проверка лицензии ─────────────────────────────────────────────────────
    result = license.validate()
    if not result["ok"]:
        # Пробуем активировать (первый запуск)
        result = license.activate()
        if not result["ok"]:
            logger.error(f"Лицензия не валидна: {result['error']}")
            sys.exit(1)
        logger.info(f"Лицензия активирована для агентства: {result['agency']}")
    else:
        logger.info(f"Лицензия валидна. Агентство: {result['agency']}")

    db.init_db()
    logger.info("База данных инициализирована.")

    # Создаём нужные директории
    for d in [config.DOWNLOADS_DIR, config.PROFILES_DIR]:
        Path(d).mkdir(exist_ok=True)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Подключаем роутеры (порядок важен: admin и upload раньше content)
    dp.include_router(admin.router)
    dp.include_router(upload.router)
    dp.include_router(content.router)

    logger.info("ContentFlow бот запущен.")
    try:
        await bot.send_message(config.ADMIN_ID, "✓ ContentFlow запущен и готов к работе.")
    except Exception:
        logger.warning("Не удалось отправить стартовое сообщение — напиши боту /start в Telegram.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
