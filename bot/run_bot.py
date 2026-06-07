"""Этот скрипт предназначен для запуска Telegram-бота в режиме Polling.
Он подключает все необходимые обработчики и запускает цикл Polling для получения обновлений от Telegram"""
# run_bot.py
import asyncio
import os
from bot_instance import dp, bot
from handlers import router
from logger_setup import logger, setup_logging
from dotenv import load_dotenv

# Укажите ваш токен здесь или в переменных окружения
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

TOKEN = os.environ.get("BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL") # URL  FastAPI внутри сети Docker

setup_logging("bot")

async def main():
    """Основная функция для запуска бота в режиме Polling."""
    # Подключаем обработчики
    dp.include_router(router)

    # Удаляем вебхук, если он был установлен ранее (чтобы заработал Polling)
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Бот запущен локально в режиме Polling...")

    from handlers import set_main_menu
    await set_main_menu(bot)

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при работе бота: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
