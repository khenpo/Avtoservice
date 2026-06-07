"""
    schemas.py - Схемы данных для валидации и сериализации
    в приложении FastAPI для управления заявками в автосервисе.
    Здесь определены Pydantic модели, которые используются для:
    - Валидации входящих данных при создании и редактировании заявок.
    - Сериализации данных при выдаче информации о заявках на фронтенд.
"""


# schemas.py
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class ExternalOrder(BaseModel):
    """    Схема для создания новой заявки (наряда)    """
    # Обязательные поля для создания заявки
    client_name: str = Field(..., min_length=2, max_length=100)
    date: datetime = Field(default_factory=datetime.now)

    # Необязательные поля (могут быть None)
    telegram_id: Optional[str] = None
    tg_name: Optional[str] = None
    description: Optional[str] = Field(..., min_length=5)
    phone_number: str | None
    brand: Optional[str] = None
    vin: Optional[str] = None
    license_plate: Optional[str] = None
    mileage: Optional[int] = Field(None, ge=0) # Пробег не может быть меньше 0
    status: Optional[int] = 1 # По умолчанию "Новая"
    note: Optional[str] = None

    class Config:
        """        Позволяет Pydantic работать с моделями SQLAlchemy        """
        from_attributes = True

# Схема для выдачи данных о заказе
class OrderShortResponse(BaseModel):
    """    Схема для выдачи краткой информации о заказе    """
    # Разрешаем работу с объектами SQLAlchemy
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: Optional[str]
    date: datetime
    brand: Optional[str]
    license_plate: Optional[str]
    status: int
    status_name: str # Добавим текстовое описание (Новая, В работе и т.д.)
    description: str
    note: Optional[str]

class VehicleResponse(BaseModel):
    """    Схема для выдачи информации о транспортном средстве клиента    """
    model_config = ConfigDict(from_attributes=True)

    client_name: str
    phone_number: Optional[str] = None
    brand: Optional[str] = None
    license_plate: Optional[str] = None
    vin: Optional[str] = None
