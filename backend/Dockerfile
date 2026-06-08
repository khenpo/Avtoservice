# backend/Dockerfile
FROM python:3.14-slim

WORKDIR /app

# Копируем зависимости
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект, чтобы были доступны и backend, и bot
COPY . .

# Открываем порт
EXPOSE 8000

# Запуск (указываем путь к модулю внутри папки backend)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]  