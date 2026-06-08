"""database.py - Модуль для работы с базой данных SQLite в приложении FastAPI для управления 
заявками в автосервисе. Здесь определены:
- SQLAlchemy модели для хранения данных о заявках и клиентах.
- Функция для инициализации базы данных."""

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import datetime
import os

# 1. Вычисляем путь к корню проекта (на уровень выше папки backend)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "service_station.db")

# 2. Формируем URL для SQLite (три слэша для относительного, четыре для абсолютного в некоторых системах,
# но sqlite:////abspath работает везде в Linux)
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
with engine.connect() as conn:
    conn.execute(text("PRAGMA journal_mode=WAL;"))
SessionLocal = sessionmaker(        # pylint: disable=invalid-name
    autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Модель записи о заявке / клиенте
class Application(Base):
    """    Application - SQLAlchemy модель для хранения данных о заявках и клиентах
    в базе данных SQLite. Каждая запись может представлять собой либо заявку (если 
    description не NULL), либо просто информацию о клиенте (если description NULL).
    Поля:"""
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    date = Column(DateTime, default=datetime.datetime.now)
    telegram_id = Column(String, nullable=True)
    tg_name = Column(String, nullable=True)
    client_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    brand = Column(String, nullable=True)         # марка
    license_plate = Column(String, nullable=True) # регистарационный номер
    vin = Column(String, nullable=True)
    mileage = Column(Integer, nullable=True)    # пробег
    order_number = Column(String, nullable=True)      # Номер наряда (присваивается оператором)
    status = Column(Integer, default=1)         # Состояние (1-7)
    description = Column(Text, nullable=True)   # Если NULL — это запись о клиенте
    note = Column(Text, nullable=True)          # Примечание

# Создание таблицы при запуске
def init_db():
    """    Инициализирует базу данных, создавая все необходимые таблицы.    """
    Base.metadata.create_all(bind=engine)

# Вспомогательная функция для генерации номера
def generate_next_order_number(db: Session):
    """    Генерирует следующий номер наряда    """

    today = datetime.date.today()
    day_str = today.strftime("%d")  # DD (текущий день месяца)

    # Определяем начало текущего дня для фильтрации в БД
    start_of_day = datetime.datetime.combine(today, datetime.time.min)

    # Ищем последнюю запись за сегодня, у которой уже есть номер заказа
    last_entry = db.query(Application).filter(
        Application.date >= start_of_day,
        Application.order_number.is_not(None)
    ).order_by(Application.id.desc()).first()

    next_idx = 1
    if last_entry and last_entry.order_number:
        try:
            # Пытаемся вытащить XX из формата XX/DD
            parts = last_entry.order_number.split('/')
            last_idx = int(parts[0])
            next_idx = last_idx + 1
        except (ValueError, IndexError):
            next_idx = 1

    # Форматируем как 01/DD, 02/DD и т.д.
    return f"{next_idx:02d}/{day_str}"
