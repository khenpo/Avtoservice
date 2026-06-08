""" 
Модуль для взаимодействия с LLM (OpenRouter) для получения аналитической сводки по заявкам.
"""
import os
from dotenv import load_dotenv
import httpx
from backend.logger_setup import logger
from typing import List
from pathlib import Path

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)


OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL")
MODEL = os.environ.get("MODEL")

# Пути к файлам промптов
BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"

#PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, "system_role.txt")
USER_PROMPT_PATH = os.path.join(PROMPTS_DIR, "work_summary_template.txt")

API_BASE_URL = os.environ.get("API_BASE_URL") # URL  FastAPI внутри сети Docker
BASE_URL = os.environ.get("BASE_URL") # URL  FastAPI внутри сети Docker

def read_prompt(file_path: str, default_text: str) -> str:
    """ Читает текст промпта из файла. Если файла нет — возвращает default. """
    
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        logger.error(f"Ошибка чтения промпта из {file_path}: {e}")
    return default_text


async def get_ai_work_summary(orders_list: List[dict]) -> str:
    """
    Отправляет список заявок в OpenRouter для получения аналитической сводки.
    Каждая заявка в списке должна иметь ключи 'brand' и 'description'.
    """

    if not orders_list:
        return "Нет данных для анализа."

     # 1. Формируем данные о заказах
    formatted_orders = "\n".join(
        [f"{{ {o['brand'] or '?' } }} {o['description']}" for o in orders_list]
    )

    # 2. Читаем промпты из файлов (Runtime загрузка)
    system_content = read_prompt(
        SYSTEM_PROMPT_PATH,
        "Ты профессиональный аналитик автосервиса."
    )

    user_template = read_prompt(
        USER_PROMPT_PATH,
        "Опиши ситуацию::\n{formatted_orders}"
    )
    logger.info(SYSTEM_PROMPT_PATH)
    logger.info(system_content)
    logger.info(USER_PROMPT_PATH)
    logger.info(user_template)

    # Вставляем данные в шаблон
    final_user_prompt = user_template.format(formatted_orders=formatted_orders)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": BASE_URL, # Требование OpenRouter
        "X-Title": "ServiceStationApp"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system", 
                "content": system_content   
            },
            {   "role": "user",
                "content": final_user_prompt}
        ]
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=30.0
            )
            # Вызывает исключение для кодов 4xx и 5xx
            response.raise_for_status()
            result = response.json()

            # Извлекаем данные
            summary = result['choices'][0]['message']['content']
            logger.info("Успешно получена сводка от OpenRouter")
            return summary

    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка API (Status): {e.response.status_code} - {e.response.text}")
        return "Ошибка: Нейросеть вернула статус ошибки."

    except httpx.RequestError as e:
        logger.error(f"Ошибка сети/соединения: {e}")
        return "Ошибка: Не удалось связаться с сервером нейросети."

    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Ошибка парсинга ответа от ИИ: {e}")
        return "Ошибка: Получен некорректный формат ответа от нейросети."

    # Если вы всё же хотите оставить "страховку" для логов,
    # добавьте комментарий для подавления ворнинга Pylint:
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error(f"Непредвиденная системная ошибка: {e}")
        return "Произошла критическая ошибка при формировании сводки."
