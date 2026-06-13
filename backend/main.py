"""
main.py
Основной файл приложения FastAPI для управления заявками в автосервисе.   
"""

# Добавьте RedirectResponse в импорты сверху
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException, Request, Form, Depends, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import ValidationError

import time
from typing import List


from backend.database import SessionLocal, engine, Application, Base, generate_next_order_number
from backend.llm import get_ai_work_summary
from backend.schemas import ExternalOrder, OrderShortResponse, VehicleResponse
from backend.logger_setup import setup_logging, logger

from aiogram import types

from bot.bot_instance import bot, dp
from bot.handlers import router # ваш роутер с хендлерами

import bcrypt
# Хак для совместимости passlib и bcrypt в Python 3.12+
if not hasattr(bcrypt, "__about__"):
    class About:
        __version__ = bcrypt.__version__
    bcrypt.__about__ = About()

from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone


# Инициализируем логи при старте
setup_logging("backend")
logger.info("Приложение запущено")

from dotenv import load_dotenv

# Укажите ваш токен здесь или в переменных окружения
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

BASE_URL = os.getenv("BASE_URL") # Например, https://yourdomain.com
WEBHOOK_PATH = f"/webhook/{os.getenv('BOT_TOKEN')}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"


SECRET_KEY = os.getenv("SECRET_KEY") 
ALGORITHM = os.getenv("ALGORITHM", "HS256")
# Хешируем пароль из .env один раз при старте
RAW_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

def get_password_hash(password: str) -> str:
    """Хеширование пароля через bcrypt напрямую"""
    # Bcrypt требует байты, обрезаем до 72 байт
    password_bytes = password.encode('utf-8')[:72]
    # Генерируем соль и хеш
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля через bcrypt напрямую"""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8')[:72],
            hashed_password.encode('utf-8')
        )
    except Exception as e:
        logger.error(f"Ошибка проверки пароля: {e}")
        return False
    
ADMIN_PASSWORD_HASH = get_password_hash(RAW_ADMIN_PASSWORD)

def create_access_token(data: dict):
    """
    Создаем токен  для пользователя
    """
    
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(request: Request):
    """
    Зависимость для проверки авторизации через Cookies.
    """
    token = request.cookies.get("access_token")
    redirect_to_login = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    if not token:
        # Если это HTMX запрос, шлем спец. заголовок для редиректа всей страницы
        if request.headers.get("HX-Request"):
            return Response(headers={"HX-Redirect": "/login"})
        raise HTTPException(status_code=303, detail="Not authenticated", headers={"Location": "/login"})

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username != "admin":
            raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    except JWTError:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    
    return username

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Управление жизненным циклом приложения: заменяет on_event startup/shutdown
    """
    # --- ДЕЙСТВИЯ ПРИ ЗАПУСКЕ (STARTUP) ---
    logger.info("Инициализация бота и вебхука...")
    
    # Подключаем обработчики
    dp.include_router(router)
    
    try:
        await bot.set_my_commands([
            types.BotCommand(command="start", description="📱 Главное меню / Запуск бота")
        ])
        logger.info("Нативные команды бота успешно зарегистрированы.")
    except Exception as e:
        logger.error(f"Не удалось зарегистрировать команды бота: {e}")


    # Устанавливаем вебхук
    await bot.set_webhook(
        url=WEBHOOK_URL,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "voice"]
    )
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")

    yield  # В этой точке сервер начинает принимать запросы

    # --- ДЕЙСТВИЯ ПРИ ОСТАНОВКЕ (SHUTDOWN) ---
    logger.info("Остановка приложения, удаление вебхука...")
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Сессии закрыты.")

# Инициализируем FastAPI с параметром lifespan
app = FastAPI(lifespan=lifespan)

# Эндпоинт для вебхука
@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    """Прием обновлений от Telegram"""
    update_data = await request.json()
    update = types.Update.model_validate(update_data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"status": "ok"}


# Создаем таблицы
Base.metadata.create_all(bind=engine)

current_dir = os.path.dirname(os.path.abspath(__file__))
templates_path = os.path.join(current_dir, "templates")

templates = Jinja2Templates(directory=templates_path)

STATUS_MAP = {
    1: "Новая", 2: "Экстренная", 3: "Подтверждена",
    4: "Поступила", 5: "В работе", 6: "Выполнена", 7: "Завершена"
}


def get_db():
    """
    Вспомогательная функция для получения сессии БД в каждом запросе.
     - Создает сессию при начале запроса и гарантирует ее закрытие после.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """ Обработчик ошибок валидации данных от клиентов."""
    # Получаем детали ошибки
    details = exc.errors()
    # Логируем как ERROR, чтобы это прилетело в Telegram
    logger.error(f"Validation Error (422) | Path: {request.url.path} | Details: {details}")

    return JSONResponse(
        status_code=422,
        content={"message": "Ошибка валидации данных", "details": details},
    )


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    """    Логируем ошибку, Loguru сам отправит её в TG """
    logger.exception(f"Критическая ошибка бэкенда:  {exc}")
    return JSONResponse(
        status_code=500,
        content={"message": "Внутренняя ошибка сервера. Администратор уведомлен."},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Заглушка для favicon, чтобы не получать 404 в логах при каждом запросе.  
    """
    return Response(status_code=204)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """ Мидлвар для логирования всех входящих HTTP-запросов.
     - Логирует метод, путь, статус ответа и время обработки.
     - Помогает отслеживать активность и производительность приложения.
    """
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000

    # Логируем: Метод, Путь, Статус и время выполнения
    logger.info(
        f"{request.method} {request.url.path} | "
        f"Status: {response.status_code} | "
        f"Time: {process_time:.2f}ms"
    )
    return response

# --- РОУТЫ АВТОРИЗАЦИИ ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})

@app.post("/login")
async def login(
    response: Response, 
    password: str = Form(...), 
    db: Session = Depends(get_db)
):
    if verify_password(password, ADMIN_PASSWORD_HASH):
        token = create_access_token(data={"sub": "admin"})
        # Редирект на главную панель после входа
        res = RedirectResponse(url="/tasks", status_code=status.HTTP_303_SEE_OTHER)
        res.set_cookie(key="access_token", value=token, httponly=True, samesite="lax")
        return res
    
    return templates.TemplateResponse("login.html", {"request": {}, "error": "Неверный пароль"})

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login")
    res.delete_cookie("access_token")
    return res


# --- ГЛАВНАЯ СТРАНИЦА ---

@app.get("/", response_class=HTMLResponse)
async def root_stub():
    """
    Заглушка для корневой страницы сервера
    """
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Автосервис AVTOTAL- Telegram Бот</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #1a1a1a;
                color: #ffffff;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                text-align: center;
                background: #2d2d2d;
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                border: 1px solid #3d3d3d;
                max-width: 400px;
            }
            h1 { color: #f39c12; margin-bottom: 10px; }
            p { color: #bdc3c7; line-height: 1.6; }
            .btn {
                display: inline-block;
                margin-top: 25px;
                padding: 12px 25px;
                background-color: #f39c12;
                color: #fff;
                text-decoration: none;
                border-radius: 5px;
                font-weight: bold;
                transition: background 0.3s;
            }
            .btn:hover { background-color: #e67e22; }
            .footer { margin-top: 30px; font-size: 0.8em; color: #7f8c8d; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🛠 Автосервис AVTOTAL</h1>
            <p>Добро пожаловать! Мы используем систему управления заказами по ремонту автомобилей.</p>
            <p>Для записи на сервис, проверки статуса заказа или связи с мастером используйте нашего официального бота:</p>
            
            <a href="https://t.me/avtotal_bot" class="btn">Открыть Telegram Бота</a>
            
            <div class="footer">
                © 2026 Система управления автосервисом<br>
                г. Москва, проспект 60-лет Октября, д. 11А, Строение 13
            </div>
        </div>
    </body>
    </html>
    """
    
# Страница с текущими заказами (для оператора)

@app.get("/tasks", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Отображение главной страницы со списком заявок.
     - Выбираем из БД все заявки, которые уже имеют номер (принятые в работу) 
     и не имеют статус "Завершена".
    """
    logger.debug("Запрос главной страницы")

    current_apps = db.query(Application).filter(
        Application.order_number.is_not(None),   # принятые в работу
        Application.status != 7             # не имющие статус "Завершена"
    ).all()

    return templates.TemplateResponse(
        request,
        "index.html",
        {"apps": current_apps, "statuses": STATUS_MAP}
    )


# --- СПИСОК (HTMX PARTIAL) ---

@app.get("/list", response_class=HTMLResponse)
async def get_list(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Возвращает только таблицу со списком заявок для HTMX обновления.
    """
    apps = db.query(Application).filter(
        Application.order_number.is_not(None),   # принятые в работу
        Application.status != 7             # не имющие статус "Завершена"
    ).all()
    return templates.TemplateResponse(
        request,
        "partials/app_table.html",
        {"apps": apps, "statuses": STATUS_MAP}
    )


# --- МОДАЛКА: ДОБАВИТЬ ---

@app.get("/modals/add", response_class=HTMLResponse)
async def get_add_modal(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Возвращает модальное окно для принятия в работу новой заявки.
    """
    pending = db.query(Application).filter(
        Application.order_number.is_(None),  # еще не принятые в работу
        Application.description.is_not(None)
    ).all()
    return templates.TemplateResponse(
        request,
        "modal_add.html",
        {"pending": pending}
    )


# --- МОДАЛКА: ИЗМЕНИТЬ ---

@app.get("/modals/edit/{app_id}")
async def get_edit_modal(app_id: int, request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Возвращает модальное окно для редактирования заявки.    
    """
    app_obj = db.query(Application).filter(Application.id == app_id).first()
    edit_statuses = {4: "Поступила", 5: "В работе",
                     6: "Выполнена"}  # Доступные варианты статусов
    return templates.TemplateResponse(
        request,
        "modal_edit.html",
        {"app": app_obj, "edit_statuses": edit_statuses}
    )


# --- МОДАЛКА: ЗАВЕРШИТЬ ---

@app.get("/modals/complete/{app_id}")
async def get_complete_modal(app_id: int, request: Request,
                             db: Session = Depends(get_db),
                             _=Depends(get_current_user)):
    """
    Возвращает модальное окно для завершения заявки.    
     - Здесь оператор вводит пробег и нажимает "Завершить"
    """
    
    # Находим заявку в базе данных
    app_obj = db.query(Application).filter(Application.id == app_id).first()
    
    return templates.TemplateResponse(
        request,
        "modal_complete.html",
            {
                "app_id": app_id,
                "app": app_obj
            }
    )


# --- ЛОГИКА ОБРАБОТКИ (POST/DELETE) ---

@app.post("/update/{app_id}",)
async def update_app(
    app_id: int,
    status: int = Form(...),
    description: str = Form(...),
    note: str = Form(None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Обрабатывает изменения заявки из модального окна редактирования.
     - Обновляет статус, описание и примечание в БД.    
    """

    app_obj = db.query(Application).filter(Application.id == app_id).first()
    if app_obj:
        app_obj.status = status
        app_obj.description = description
        app_obj.note = note
        db.commit()
        logger.info(f"Обновление заявки ID {app_id}: Статус {status}")
    return Response(headers={"HX-Trigger": "refreshList"})


@app.post("/complete/{app_id}")
async def complete_app(app_id: int, mileage: int = Form(...), 
                       db: Session = Depends(get_db),
                       _=Depends(get_current_user)):
    """
    Обрабатывает изменения заявки из модального окна редактирования.
     - Обновляет статус, описание и примечание в БД.    
    """
    app_obj = db.query(Application).filter(Application.id == app_id).first()

    if app_obj:
        app_obj.mileage = mileage
        app_obj.status = 7
        db.commit()

    logger.success(f"Заявка ID {app_id} ЗАВЕРШЕНА. Пробег: {mileage} км.")

    return Response(headers={"HX-Trigger": "refreshList"})


@app.delete("/delete/{app_id}")
async def delete_app(app_id: int, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Удаляет заявку из БД.
     - На главной странице есть кнопка "Удалить", которая вызывает этот эндпоинт через HTMX.
     - После удаления отправляем HTMX-триггер для обновления списка. 
    """
    logger.info(
        f"Удаление записи ID {app_id}!")  # Critical - так как данные пропадают

    app_obj = db.query(Application).filter(Application.id == app_id).first()
    if app_obj:
        db.delete(app_obj)
        db.commit()
    return Response(headers={"HX-Trigger": "refreshList"})


# --- СТРАНИЦА СОЗДАНИЯ НОВОЙ ЗАЯВКИ ---

@app.get("/new-request", response_class=HTMLResponse)
async def new_request_page(request: Request,_=Depends(get_current_user)):
    """Отображение страницы для создания новой заявки.
     - Здесь оператор может ввести данные о клиенте и описать проблему.
     - При отправке формы данные будут валидированы через Pydantic и сохранены в БД."""
    return templates.TemplateResponse(
        request,
        "create_request.html",
        {"statuses": STATUS_MAP}
    )


# Присвоение номера наряда (из очереди) ---

@app.post("/assign_number/{app_id}")
async def assign_number(app_id: int, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """Присваивает номер наряда заявке из очереди "Добавить"."""

    app_obj = db.query(Application).filter(Application.id == app_id).first()
    if app_obj:
        # Генерируем номер автоматически
        old_status = app_obj.status
        app_obj.order_number = generate_next_order_number(db)
        app_obj.status = 4  # Устанавливаем статус "Принята в работу"
        db.commit()
        logger.info(
            f"Заявка ID {app_id} ПРИНЯТА. Номер: {app_obj.order_number}, Статус: {old_status} -> 4")
    return Response(headers={"HX-Trigger": "refreshList"})


# Создание новоого заказа из формы

@app.post("/create-request")
async def create_order(
    client_name: str = Form(...),
    phone_number: str = Form(None),
    brand: str = Form(None),
    vin: str = Form(None),
    license_plate: str = Form(None),
    # Получаем как строку, Pydantic сконвертирует в int
    mileage: str = Form(None),
    telegram_id: str = Form(None),
    description: str = Form(...),
    note: str = Form(None),
    status: int = Form(1),
    assign_now: str = Form(None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """
    Ввод записи о новой заявке при редактировании в форме /create-request.
     - Обязательные поля: client_name, description. Остальные — по желанию.

     """
    # 1. Собираем данные в словарь для валидации
    form_data = {
        "client_name": client_name,
        "phone_number": phone_number,
        "brand": brand,
        "vin": vin,
        "license_plate": license_plate,
        "mileage": int(mileage) if mileage and mileage.isdigit() else None,
        "telegram_id": telegram_id,
        "description": description,
        "note": note,
        "status": status
    }

    # 2. Валидируем через Pydantic
    try:
        validated_order = ExternalOrder(**form_data)
    except ValidationError as e:
        # Если данные неверны, возвращаем простую ошибку
        errors = e.errors()
        error_messages = "; ".join(
            [f"{err['loc'][0]}: {err['msg']}" for err in errors])

        # Логируем подробности ошибки
        logger.warning(f"Ошибка валидации формы: {e.json()}")

        return HTMLResponse(content=f"Ошибка заполнения формы: {error_messages}", status_code=400)

    # 3. Если валидация прошла, создаем объект БД из валидированных данных
    # .model_dump() превращает Pydantic-модель обратно в словарь
    new_app = Application(**validated_order.model_dump())
    new_app.date = datetime.now()

    # Логика автоматического присвоения номера
    if assign_now == "on":
        new_app.order_number = generate_next_order_number(db)
        if new_app.status < 4:
            new_app.status = 4  # Статус "Поступила"

    db.add(new_app)
    db.commit()

    return RedirectResponse(url="/", status_code=303)


@app.get("/archive", response_class=HTMLResponse)
async def archive_page(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Оторажение списка истории  заказов
    """
    completed_apps = db.query(Application).filter(
        Application.status == 7).all()
    return templates.TemplateResponse(request, "archive.html",
                                      {"apps": completed_apps, "statuses": STATUS_MAP})


# --- API ДЛЯ ВНЕШНИХ СИСТЕМ ---

@app.post("/api/orders", status_code=201)
async def create_external_order(order_data: ExternalOrder, db: Session = Depends(get_db)):
    """
    Эндпоинт для приема заявок извне.
    Принимает JSON, валидирует его через Pydantic.
    """
    logger.info(
        f"API запрос от {order_data.client_name} (TG: {order_data.telegram_id})")

    new_app = Application(
        client_name=order_data.client_name,
        telegram_id=order_data.telegram_id,
        tg_name=order_data.tg_name,
        phone_number=order_data.phone_number,
        brand=order_data.brand,
        vin=order_data.vin,
        license_plate=order_data.license_plate,
        mileage=order_data.mileage,
        description=order_data.description,
        note=order_data.note,
        status=order_data.status,
        # order_number остается NULL, чтобы заявка попала в очередь "Добавить"
        date=datetime.now()
    )

    db.add(new_app)
    db.commit()
    db.refresh(new_app)

    return {"status": "success", "order_id": new_app.id}


@app.get("/api/orders/active/{uid}", response_model=List[OrderShortResponse])
async def get_active_orders_by_uid(uid: str, db: Session = Depends(get_db)):
    """
    Получить список всех активных (незавершенных) заказов клиента по его UID (telegram_id).
    """
    # Ищем записи, где telegram_id совпадает, а статус не "Завершена" (7)

    logger.info(f"Запрос активных заказов для UID: {uid}")

    orders = db.query(Application).filter(
        Application.telegram_id == uid,
        # Убедимся, что это запись о заявке, а не просто о клиенте
        Application.description.is_not(None),
        Application.status != 7
    ).order_by(Application.date.desc()).all()

    # Формируем ответ, добавляя текстовое название статуса из нашего STATUS_MAP
    result = []
    for o in orders:
        # 2. Превращаем объект SQLAlchemy в словарь
        data = {
            "id": o.id,
            "order_number": o.order_number,
            "date": o.date,
            "brand": o.brand,
            "license_plate": o.license_plate,
            "status": o.status,
            # Добавляем имя статуса
            "status_name": STATUS_MAP.get(o.status, "Неизвестно"),
            "description": o.description,
            "note": o.note
        }
        # 3. Валидируем словарь через Pydantic
        result.append(OrderShortResponse(**data))

    return result

@app.get("/api/vehicles/{uid}", response_model=List[VehicleResponse])
async def get_user_vehicles(uid: str, db: Session = Depends(get_db)):
    """
    Получить список автомобилей пользователя по его UID (telegram_id).
    """
    logger.info(f"Запрос списка машины для UID: {uid}")
    # Выбираем только поля машины и используем distinct(),
    # чтобы не возвращать одну и ту же машину много раз из разных заявок
    vehicles = db.query(
        Application.client_name,
        Application.phone_number,
        Application.brand,
        Application.license_plate,
        Application.vin
    ).filter(
        Application.telegram_id == uid,
        Application.description.is_(None),  # Убедимся, что это запись о клиенте
    ).distinct().all()
    logger.info(vehicles)
    # SQLAlchemy вернет список кортежей, Pydantic автоматически преобразует их в объекты схемы
    return [v._asdict() for v in vehicles]

@app.get("/api/work-summary", response_class=HTMLResponse)
async def get_work_summary(db: Session = Depends(get_db)):
    """
    Получение сводки по текущим заявкам на выполнение работ.
    """
    # Выбираем только активные (незавершенные) заявки с присвоенным номером
    active_orders = db.query(Application).filter(
        Application.order_number.is_not(None),
        Application.status != 6, # не выполнена
        Application.status != 7 # не завершена
    ).all()

    if not active_orders:
        return "Активных заявок в работе сервиса сейчас нет."

    # Преобразуем объекты SQLAlchemy в простые словари для функции LLM
    orders_data = [
        {"brand": o.brand, "description": o.description}
        for o in active_orders
    ]

    # Вызываем функцию из llm.py
    summary_text = await get_ai_work_summary(orders_data)

    return summary_text

@app.delete("/api/vehicles/{telegram_id}/{license_plate}")
async def delete_vehicle(telegram_id: str, license_plate: str, db: Session = Depends(get_db)):
    """
    Удаляет регистрационную запись об автомобиле.
    Удаляются только записи, где description is None (т.е. не заказы).
    """
    logger.info(f"Запрос на удаление автомобиля {license_plate} для UID: {telegram_id}")
    try:
        # Ищем запись: совпадает ID пользователя, госномер и описание пустое
        vehicle_record = db.query(Application).filter(
            Application.telegram_id == telegram_id,
            Application.license_plate == license_plate,
            Application.description.is_(None)  # Это гарантирует, что мы удаляем "машину", а не "заказ"
        ).first()
        logger.info(f"Найдена запись для удаления: {vehicle_record}")  # Логируем найденную запись
        if not vehicle_record:
            raise HTTPException(
                status_code=404,
                detail="Запись об автомобиле не найдена"
            )

        db.delete(vehicle_record)
        db.commit()

        return {"status": "success", "message": f"Автомобиль {license_plate} удален"}

    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при удалении из БД: {e}")


@app.get("/requests", response_class=HTMLResponse)
async def requests_page(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """
    Страница просмотра новых заявок, которые еще не приняты в работу 
    (у них нет номера наряда).
    """
    # Выбираем заявки без номера, где описание не пустое
    pending_apps = db.query(Application).filter(
        Application.order_number.is_(None),
        Application.description.is_not(None)
    ).order_by(Application.date.desc()).all()

    return templates.TemplateResponse(
        request,
        "requests.html",
        {"apps": pending_apps, "statuses": STATUS_MAP}
    )


# --- СТРАНИЦА КЛИЕНТОВ ---

@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """Отображение списка всех клиентов (где description is NULL)"""
    clients = db.query(Application).filter(Application.description.is_(None)).all()
    return templates.TemplateResponse(
        request,
        "clients.html", 
        {"clients": clients}
    )

# --- МОДАЛКА: ДОБАВИТЬ/РЕДАКТИРОВАТЬ КЛИЕНТА ---

@app.get("/modals/client/add", response_class=HTMLResponse)
async def get_add_client_modal(request: Request,_=Depends(get_current_user)):
    """Возвращает модальное окно для добавления нового клиента"""    
    return templates.TemplateResponse(request, "modal_client.html", {"client": None})

@app.get("/modals/client/edit/{client_id}", response_class=HTMLResponse)
async def get_edit_client_modal(client_id: int, request: Request, 
                                db: Session = Depends(get_db),
                                _=Depends(get_current_user)
                                ):
    """Возвращает модальное окно для редактирования клиента по его ID"""
    client = db.query(Application).filter(Application.id == client_id).first()
    return templates.TemplateResponse(request, "modal_client.html", {"client": client})

# --- ЛОГИКА СОХРАНЕНИЯ КЛИЕНТА ---

@app.post("/clients/save")
@app.post("/clients/save/{client_id}")
async def save_client(
    client_id: int = None,
    client_name: str = Form(...),
    phone_number: str = Form(None),
    telegram_id: str = Form(None),
    brand: str = Form(None),
    license_plate: str = Form(None),
    vin: str = Form(None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """Сохранение данных клиента"""
    if client_id:
        client = db.query(Application).filter(Application.id == client_id).first()
    else:
        client = Application(description=None) # Явно указываем NULL для описания
        db.add(client)

    client.client_name = client_name
    client.phone_number = phone_number
    client.telegram_id = telegram_id
    client.brand = brand
    client.license_plate = license_plate
    client.vin = vin

    db.commit()
    # Возвращаем заголовок для обновления страницы или перенаправления
    return Response(headers={"HX-Redirect": "/clients"})

# --- РЕДАКТИРОВАНИЕ ВХОДЯЩЕЙ ЗАЯВКИ (БЕЗ НОМЕРА) ---

@app.get("/modals/request/edit/{app_id}", response_class=HTMLResponse)
async def get_request_edit_modal(app_id: int, request: Request, 
                                 db: Session = Depends(get_db),
                                 _=Depends(get_current_user)
                                 ):
    """Модальное окно редактирования новой заявки до её принятия в работу"""
    app_obj = db.query(Application).filter(Application.id == app_id).first()
    return templates.TemplateResponse(
        request,
        "modal_request_edit.html", 
        {"app": app_obj}
    )

@app.post("/requests/update/{app_id}")
async def update_pending_request(
    app_id: int,
    description: str = Form(...),
    note: str = Form(None),
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    """Сохранение изменений описания и примечания для входящей заявки"""
    app_obj = db.query(Application).filter(Application.id == app_id).first()
    if app_obj:
        app_obj.description = description
        app_obj.note = note
        db.commit()
    # Возвращаем сигнал HTMX для обновления списка на странице
    return Response(headers={"HX-Trigger": "refreshNewRequests"})

# --- ПОДТВЕРЖДЕНИЕ ЗАЯВКИ (СТАТУС 3) ---

@app.post("/requests/confirm/{app_id}")
async def confirm_request(app_id: int, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """Перевод заявки в статус 'Подтверждена' (3) без присвоения номера наряда"""
    app_obj = db.query(Application).filter(Application.id == app_id).first()
    if app_obj:
        app_obj.status = 3
        db.commit()
        logger.info(f"Заявка ID {app_id} переведена в статус 'Подтверждена'")
    return Response(headers={"HX-Trigger": "refreshNewRequests"})

# Обновим также эндпоинт получения списка для страницы новых заявок (Partial)
@app.get("/requests/list", response_class=HTMLResponse)
async def get_requests_list(request: Request, db: Session = Depends(get_db),_=Depends(get_current_user)):
    """Возвращает только таблицу со списком новых заявок для HTMX обновления 
        на странице /requests
        - Выбираем заявки без номера, где описание не пустое
    """
    pending_apps = db.query(Application).filter(
        Application.order_number.is_(None),
        Application.description.is_not(None)
    ).order_by(Application.date.desc()).all()
    return templates.TemplateResponse(
        request,
        "partials/requests_table.html", 
        {"apps": pending_apps, "statuses": STATUS_MAP}
    )
