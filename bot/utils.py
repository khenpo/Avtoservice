"""Утилиты для работы с API бота и форматирования данных."""

import httpx
import os
import markdown
import re
from backend.logger_setup import logger
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)
# Базовый URL вашего FastAPI (в Docker это имя сервиса, локально - localhost)

API_BASE_URL = os.environ.get("API_BASE_URL")

# Карта статусов (дублируем из main.py или импортируем, если возможно)
STATUS_MAP = {
    1: "Новая", 2: "Экстренная", 3: "Подтверждена",
    4: "Поступила", 5: "В работе", 6: "Выполнена", 7: "Завершена"
}

async def fetch_api(method: str, endpoint: str, data: dict = None):
    """
    Универсальная функция для запросов к вашему API с обработкой ошибок.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            url = f"{API_BASE_URL}{endpoint}"
            if method.upper() == "GET":
                response = await client.get(url)
            else:
                response = await client.post(url, json=data)
            
            # Если статус 4xx или 5xx, будет выброшено исключение
            response.raise_for_status()
            return response.json()
        
        except httpx.HTTPStatusError as e:
            # Логируем как ERROR, чтобы пришло уведомление в ТГ админу
            logger.error(f"Ошибка API {e.response.status_code} | {url} | Ответ: {e.response.text}")
            return None
        except Exception as e:
            # exception запишет весь Traceback — крайне полезно для отладки
            logger.exception(f"Критический сбой при запросе к {url}")
            return None

async def get_user_vehicles_data(telegram_id: str):
    """Возвращает список машин как данные из API"""
    data = await fetch_api("GET", f"/api/vehicles/{telegram_id}")
    logger.info(data)
    return data if data else []

async def get_active_orders(telegram_id: str):
    """Получает список активных заказов через API"""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{API_BASE_URL}/api/orders/active/{telegram_id}")
            return r.json() if r.status_code == 200 else []
        except Exception as e:
            logger.error(f"Ошибка получения статусов: {e}")
            return None

async def delete_vehicle(telegram_id: str, license_plate: str):
    """Отправляет запрос на удаление автомобиля"""
    async with httpx.AsyncClient() as client:
        try:
            # Предполагаем, что у вас есть эндпоинт DELETE или POST для удаления
            # Если нет, его нужно добавить в main.py
            r = await client.request(
                "DELETE", 
                f"{API_BASE_URL}/api/vehicles/{telegram_id}/{license_plate}"
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка при удалении авто: {e}")
            return False

def md_to_tg_html(md_text: str) -> str:
    """
    Конвертирует Markdown в HTML, который корректно отображается в Telegram.
    Telegram поддерживает ограниченный набор HTML-тегов, поэтому мы преобразуем Markdown 
    в HTML и очищаем его от неподдерживаемых тегов.
    """
    # 1. Конвертируем MD в HTML
    html = markdown.markdown(md_text)

    # 2. УДАЛЯЕМ теги, которые Telegram не понимает
    
    # Заменяем <hr> (горизонтальная линия) на текстовый разделитель
    html = re.sub(r'<hr\s*/?>', '—' * 15 + '\n', html)

    # Заменяем <br> на обычный перенос строки
    html = re.sub(r'<br\s*/?>', r'\n', html)

    # Заменяем заголовки h1-h6 на жирный текст
    html = re.sub(r'<h[1-6]>(.*?)</h[1-6]>', r'<b>\1</b>\n', html)
    
    # Заменяем параграфы на переносы
    html = html.replace('<p>', '').replace('</p>', '\n')
    
    # Заменяем списки
    html = html.replace('<li>', '• ').replace('</li>', '\n')
    html = re.sub(r'</?(ul|ol)>', '', html)
    
    # Заменяем strong/em на b/i (стандарт Telegram)
    html = html.replace('<strong>', '<b>').replace('</strong>', '</b>')
    html = html.replace('<em>', '<i>').replace('</em>', '</i>')

    # 3. Убираем множественные пустые строки
    html = re.sub(r'\n\s*\n', '\n\n', html)
    
    return html.strip()
