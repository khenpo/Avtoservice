""" 
Состояния FSM
"""

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from logger_setup import logger
import os

load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL") # URL  FastAPI внутри сети Docker

# Настройка Loguru
logger.add("logs/bot_errors.log", rotation="1 week", level="ERROR")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
#storage = RedisStorage.from_url(REDIS_URL)
from aiogram.fsm.storage.memory import MemoryStorage
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
