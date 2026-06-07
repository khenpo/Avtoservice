"""
Настройка ведения логов
"""
import sys
import os
import httpx
import html
from loguru import logger
from dotenv import load_dotenv


TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.environ.get("ADMIN_ID")

# Ищем .env в текущей папке или на уровень выше (в корне)
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)


def send_telegram_error(message: str):
    """
    Отправляет критические логи в Telegram.
    Используем синхронный вызов внутри обработчика Loguru.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        return
    
    safe_message = html.escape(message[:3500]) # Оставляем запас под теги

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ADMIN_ID,
        "text": f"⚠️ <b>Критическая ошибка системы</b>\n\n<pre>{safe_message}</pre>",
        "parse_mode": "HTML"    
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                # Если все равно 400, пробуем отправить без разметки вообще
                client.post(url, json={
                    "chat_id": TELEGRAM_ADMIN_ID, 
                    "text": f"Критическая ошибка (без разметки):\n{message[:3000]}"
                })
    except Exception as e:
        print(f"FAILED TO SEND LOG TO TG: {e}", file=sys.stderr)

def setup_logging(service_name: str = "app"):
    """
    Полная настройка логирования для приложения.
    service_name: 'bot' или 'backend' - для указания контекста в логах
    """
    # 1. Удаляем стандартный обработчик
    logger.remove()

    # Логирование в файл (с ротацией и сжатием)
    os.makedirs("logs", exist_ok=True)
    logger.add(
        f"logs/{service_name}.log", # Будет logs/bot.log и logs/backend.log
        rotation="10 MB",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
        compression="zip",
        encoding="utf-8"
    )
    # 2. Логирование в консоль (красивое, цветное)

    logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="DEBUG"
    )

    # Telegram - добавляем префикс в сообщение
    def send_tg_with_prefix(message):
        full_message = f"🚀 <b>Service: {service_name.upper()}</b>\n{message}"
        send_telegram_error(full_message)

    # 4. Отправка ошибок в Telegram (только уровень ERROR и выше)
    logger.add(
        send_tg_with_prefix,
        level="ERROR",
        # Фильтр, чтобы не спамить в TG техническими ошибками соединения самого бота,
        # если это критично (опционально)
    )

    logger.info("Logging system initialized.")

# Экспортируем логгер для удобства
__all__ = ["logger", "setup_logging"]
