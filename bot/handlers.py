"""
Этот файл содержит все обработчики сообщений и коллбеков для Telegram бота.
Обновлено: Полная поддержка InlineKeyboardMarkup и защита от ошибок редактирования.
"""
import httpx
import os
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, ErrorEvent
from aiogram.exceptions import TelegramBadRequest

from loguru import logger
from dotenv import load_dotenv

from bot.bot_instance import bot
from bot.states import Registration, CreateOrder, EmergencyState
from bot.utils import get_active_orders, delete_vehicle, API_BASE_URL, get_user_vehicles_data, md_to_tg_html

router = Router()
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

MASTER_ID = os.environ.get("MASTER_ID")

# --- КЛАВИАТУРЫ ---

def main_menu():
    """Главное инлайн-меню бота"""
    kb = [
        [
            InlineKeyboardButton(text="📝 Заявка", callback_data="menu_order"),
            InlineKeyboardButton(text="📊 Статус", callback_data="menu_status")
        ],
        [
            InlineKeyboardButton(text="📍 Как проехать", callback_data="menu_map"),
            InlineKeyboardButton(text="🗞 Вести с полей", callback_data="menu_news")
        ],
        [
            InlineKeyboardButton(text="🆘 Emergency", callback_data="menu_emergency"),
            InlineKeyboardButton(text="🚗 Мой гараж", callback_data="menu_garage")
        ],
        [
            InlineKeyboardButton(text="👤 Регистрация", callback_data="menu_reg")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def cancel_inline_kb():
    """Универсальная кнопка отмены"""
    kb = [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---

async def safe_edit_or_answer(callback: types.CallbackQuery, text: str,
                              reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML"):
    """
    Универсальный метод: пытается отредактировать сообщение, 
    а если это невозможно (например, сообщение-фото), удаляет старое и шлет новое.
    """
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" in e.message:
            await callback.answer("Данные актуальны ✅")
        elif "there is no text in the message to edit" in e.message:
            # Если кнопки нажаты под фото, удаляем фото и шлем текст
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            logger.error(f"Ошибка при редактировании: {e.message}")
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)

# --- ОБЩИЕ ОБРАБОТЧИКИ ---

@router.error()
async def bot_error_handler(event: ErrorEvent):
    """ Глобальный обработчик ошибок бота. Логирует исключения и уведомляет пользователя. """
    logger.exception(f"Ошибка в логике бота: {event.exception}")
    if event.update.message:
        await event.update.message.answer("⚠️ Произошла ошибка. Мы уже чиним её!")

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Приветствие и показ главного меню. Сбрасывает все состояния."""
    await state.clear()
    await message.answer("Добро пожаловать в автосервис! Выберите нужное действие:",
                         reply_markup=main_menu())

@router.callback_query(F.data == "cancel_action")
async def callback_cancel(callback: types.CallbackQuery, state: FSMContext):
    """Общий обработчик для кнопки 'Отмена'. Сбрасывает состояние и возвращает главное меню."""
    await state.clear()
    await callback.answer()
    await safe_edit_or_answer(callback, "Выберите нужное действие:", reply_markup=main_menu())


# --- ЛОГИКА "ЗАЯВКА" ---

@router.callback_query(F.data == "menu_order")
async def create_app_start(callback: types.CallbackQuery, state: FSMContext):
    """Начало создания заявки: проверяем, есть ли  авто, и запрашиваем описание проблемы."""
    uid = str(callback.from_user.id)
    vehicles = await get_user_vehicles_data(uid)

    if not vehicles:
        return await safe_edit_or_answer(callback,
            "Сначала зарегистрируйте автомобиль в разделе 'Регистрация'.", 
            reply_markup=main_menu())

    v = vehicles[0]
    await state.update_data(client_name=v['client_name'], phone_number=v['phone_number'])

    if len(vehicles) > 1:
        kb = []
        for v in vehicles:
            btn_text = f"{v['brand']} ({v['license_plate']})"
            cb_data = f"car_sel_{v['brand']}_{v['license_plate']}"
            kb.append([InlineKeyboardButton(text=btn_text, callback_data=cb_data)])
        kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")])

        await safe_edit_or_answer(callback, "Выберите автомобиль для заявки:",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        await state.set_state(CreateOrder.selecting_car)
    else:
        await state.update_data(brand=v['brand'], license_plate=v['license_plate'], vin=v['vin'])
        await safe_edit_or_answer(callback,
            f"Вы создаете заявку для <b>{v['brand']}</b>.\nОпишите проблему:",
            reply_markup=cancel_inline_kb())
        await state.set_state(CreateOrder.waiting_for_description)

@router.callback_query(CreateOrder.selecting_car, F.data.startswith("car_sel_"))
async def car_selected(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора автомобиля из списка при создании заявки."""
    parts = callback.data.split("_")
    brand, plate = parts[2], parts[3]
    await state.update_data(brand=brand, license_plate=plate)

    await safe_edit_or_answer(callback,
        f"Автомобиль: <b>{brand}</b> ({plate})\nОпишите проблему:",
        reply_markup=cancel_inline_kb())
    await state.set_state(CreateOrder.waiting_for_description)

@router.message(CreateOrder.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    """Обработка описания проблемы при создании заявки. Проверяем длину и отправляем данные на сервер."""
    description_text = message.text.strip() if message.text else ""

    if len(description_text) < 5:
        await message.answer(
            "⚠️ Описание слишком короткое. Пожалуйста, напишите подробнее (минимум 5 символов):",
            reply_markup=cancel_inline_kb()
        )
        return

    user_data = await state.get_data()
    order_data = {
        "client_name": user_data.get('client_name'),
        "phone_number": user_data.get('phone_number'),
        "tg_name": message.from_user.full_name,
        "telegram_id": str(message.from_user.id),
        "brand": user_data['brand'],
        "license_plate": user_data['license_plate'],
        "vin": user_data.get('vin'),
        "description": description_text,
        "status": 1
    }

    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{API_BASE_URL}/api/orders", json=order_data)
            if r.status_code == 201:
                await message.answer("✅ Ваша заявка принята!", reply_markup=main_menu())
            else:
                await message.answer("❌ Ошибка сохранения.", reply_markup=main_menu())
        except Exception:
            await message.answer("🆘 Ошибка связи с сервером.", reply_markup=main_menu())
    await state.clear()

# --- СТАТУС ---

@router.callback_query(F.data == "menu_status")
async def cmd_status(callback: types.CallbackQuery):
    """Показать текущие активные заявки пользователя и их статусы."""
    uid = str(callback.from_user.id)
    orders = await get_active_orders(uid)

    if orders is None:
        return await callback.answer("Ошибка сервера", show_alert=True)

    if not orders:
        return await safe_edit_or_answer(callback, "У вас нет активных заявок.", reply_markup=main_menu())

    response = "🔎 <b>Ваши текущие заявки:</b>\n\n"
    for o in orders:
        num = o.get('order_number') or "В очереди"
        status = o.get('status_name', 'Неизвестно')
        response += (f"<b>№ {num}</b>\n🚗 {o['brand']} ({o['license_plate']})\n"
                     f"Статус: <code>{status}</code>\n\n")

    await safe_edit_or_answer(callback, response, reply_markup=main_menu())

# --- РЕГИСТРАЦИЯ ---

@router.callback_query(F.data == "menu_reg")
async def start_reg(callback: types.CallbackQuery, state: FSMContext):
    """Начало регистрации: проверяем, есть ли уже авто, и запрашиваем ФИО."""
    uid = str(callback.from_user.id)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE_URL}/api/vehicles/{uid}")
            existing_cars = resp.json() if resp.status_code == 200 else []

            if existing_cars:
                profile = existing_cars[0]
                await state.update_data(client_name=profile.get('client_name'),
                                        phone_number=profile.get('phone_number'))

                kb = [
                    [InlineKeyboardButton(text="➕ Добавить машину", callback_data="reg_new_car")],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")]
                ]
                return await safe_edit_or_answer(callback,
                    f"Рады видеть вас, {profile.get('client_name')}!\nДобавить еще одно авто?",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")

    await safe_edit_or_answer(callback, "Введите ваше ФИО:", reply_markup=cancel_inline_kb())
    await state.set_state(Registration.waiting_for_name)

@router.callback_query(F.data == "reg_new_car")
async def reg_new_car_fast(callback: types.CallbackQuery, state: FSMContext):
    """ Быстрый путь для добавления нового авто: если уже есть профиль, запрашиваем только данные машины."""
    data = await state.get_data()
    if not data.get('client_name'):
        return await start_reg(callback, state)

    await safe_edit_or_answer(callback,
        f"Профиль: {data['client_name']}\nВведите марку и модель авто:",
        reply_markup=cancel_inline_kb())
    await state.set_state(Registration.waiting_for_brand)

@router.message(Registration.waiting_for_name)
async def reg_name_input(message: types.Message, state: FSMContext):
    """Обработка ввода ФИО при регистрации. Проверяем длину и сохраняем в состоянии."""
    if len(message.text) < 3:
        return await message.answer("⚠️ Слишком короткое ФИО. Попробуйте еще раз:")

    await state.update_data(client_name=message.text)
    await message.answer("Ваш номер телефона:", reply_markup=cancel_inline_kb())
    await state.set_state(Registration.waiting_for_phone_number)

@router.message(Registration.waiting_for_phone_number)
async def reg_phone_number(message: types.Message, state: FSMContext):
    """Обработка ввода номера телефона при регистрации. Проверяем формат и сохраняем в состоянии."""
    await state.update_data(phone_number=message.text)
    await message.answer("Марка и модель авто:", reply_markup=cancel_inline_kb())
    await state.set_state(Registration.waiting_for_brand)

@router.message(Registration.waiting_for_brand)
async def reg_brand(message: types.Message, state: FSMContext):
    """Обработка ввода марки и модели авто при регистрации. Сохраняем в состоянии и запрашиваем VIN."""
    await state.update_data(brand=message.text, telegram_id=str(message.from_user.id))
    await message.answer("VIN номер:", reply_markup=cancel_inline_kb())
    await state.set_state(Registration.waiting_for_vin)

@router.message(Registration.waiting_for_vin)
async def reg_vin(message: types.Message, state: FSMContext):
    """Обработка ввода VIN номера при регистрации. Сохраняем в состоянии и запрашиваем гос. номер."""
    await state.update_data(vin=message.text)
    await message.answer("Гос. номер:", reply_markup=cancel_inline_kb())
    await state.set_state(Registration.waiting_for_plate)

@router.message(Registration.waiting_for_plate)
async def reg_final(message: types.Message, state: FSMContext):
    """Завершение регистрации: сохраняем данные и создаем профиль/авто на сервере."""
    data = await state.get_data()
    data.update({'license_plate': message.text,
                 'tg_name': message.from_user.full_name, 
                 'description': None})

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{API_BASE_URL}/api/orders", json=data)
        if r.status_code == 201:
            await message.answer("✅ Регистрация успешна!", reply_markup=main_menu())
        else:
            await message.answer("❌ Ошибка регистрации.", reply_markup=main_menu())
    await state.clear()

# --- ВЕСТИ С ПОЛЕЙ ---

@router.callback_query(F.data == "menu_news")
async def field_news(callback: types.CallbackQuery):
    """ Здесь показывается анализ загрузки сервиса работами"""
    await callback.answer()

    # Используем try-except на случай, если нажали под фото
    try:
        await callback.message.edit_text("⏳ <b>Получение информации...</b>\n"\
            "Мастер анализирует ситуацию, это может занять пару минут.", 
                                         parse_mode="HTML")
    except TelegramBadRequest:
        # Если это было фото (карта), удаляем его и пишем текст
        await callback.message.delete()
        await callback.message.answer("⏳ <b>Получение информации...</b>", parse_mode="HTML")

    # Выполняем запрос к API
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.get(f"{API_BASE_URL}/api/work-summary")
            r.raise_for_status() # Проверяем, что сервер ответил 200 OK

            html = md_to_tg_html(r.text)

            # Выводим результат, возвращая кнопки главного меню
            # Используем нашу вспомогательную функцию для безопасности
            await safe_edit_or_answer(callback, html, reply_markup=main_menu())

        except httpx.ReadTimeout:
            logger.error("Таймаут API при получении вестей")
            await safe_edit_or_answer(callback, "⚠️ Сервер слишком долго не отвечает. Попробуйте позже.",
                                     reply_markup=main_menu())
        except Exception as e:
            logger.error(f"Ошибка получения вестей: {e}")
            await safe_edit_or_answer(callback, "⚠️ Не удалось получить данные с сервера.",
                                     reply_markup=main_menu())

# --- КАРТА ---

@router.callback_query(F.data == "menu_map")
async def send_map(callback: types.CallbackQuery):
    """ Отправляет карту с местоположением сервиса. """
    await callback.answer()
    try:
        # Пытаемся удалить текстовое меню перед отправкой фото
        try:
            await callback.message.delete()
        except:
            pass
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        MAP_PATH = os.path.join(project_root, "maps", "map.png")
        photo = types.FSInputFile("/maps/map.png")
        await callback.message.answer_photo(photo,
                                            caption="Мы находимся здесь: 55.7558, 37.6173",
                                            reply_markup=main_menu())
    except Exception:
        await callback.message.answer("Ошибка загрузки карты", reply_markup=main_menu())

# --- EMERGENCY ---

@router.callback_query(F.data == "menu_emergency")
async def emergency_start(callback: types.CallbackQuery, state: FSMContext):
    """Начало экстренной заявки: запрашиваем голосовое сообщение с описанием проблемы."""
    await safe_edit_or_answer(callback,
        "🆘 Отправьте голосовое сообщение с описанием проблемы. Мы создадим экстренную заявку.",
        reply_markup=cancel_inline_kb())
    await state.set_state(EmergencyState.waiting_for_voice)

@router.message(EmergencyState.waiting_for_voice, F.voice)
async def handle_emergency_voice(message: types.Message, state: FSMContext):
    """Обработка голосового сообщения для экстренной заявки. 
    Пересылаем мастеру и создаем заявку с пометкой 'ЭКСТРЕННО'.
    """
    await bot.forward_message(chat_id=MASTER_ID,
                              from_chat_id=message.chat.id,
                              message_id=message.message_id)

    emergency_data = {
        "client_name": message.from_user.full_name,
        "phone_number": "Emergency Voice",
        "tg_name": message.from_user.full_name,
        "telegram_id": str(message.from_user.id),
        "description": "ЭКСТРЕННО: Голосовое сообщение",
        "status": 2
    }
    async with httpx.AsyncClient() as client:
        await client.post(f"{API_BASE_URL}/api/orders", json=emergency_data)

    await message.answer("✅ Сообщение получено!", reply_markup=main_menu())
    await state.clear()

# --- ГАРАЖ ---

@router.callback_query(F.data == "menu_garage")
async def show_garage(callback: types.CallbackQuery, user_id: str = None):
    """Показать список зарегистрированных автомобилей пользователя с возможностью удаления."""
    uid = user_id or str(callback.from_user.id)
    vehicles = await get_user_vehicles_data(uid)

    if not vehicles:
        return await safe_edit_or_answer(callback, "В гараже пусто.", reply_markup=main_menu())

    kb = []
    for v in vehicles:
        kb.append([InlineKeyboardButton(text=f"{v['brand']} ({v['license_plate']}) ❌",
                                        callback_data=f"del_v_{v['license_plate']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cancel_action")])

    await safe_edit_or_answer(callback, "<b>🏠 Ваш гараж</b>\nНажмите ❌ для удаления:",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("del_v_"))
async def process_delete_car(callback: types.CallbackQuery):
    """Обработка удаления автомобиля из гаража."""
    plate = callback.data.replace("del_v_", "")
    uid = str(callback.from_user.id)

    success = await delete_vehicle(uid, plate)
    if success:
        await callback.answer("✅ Удалено")
        await show_garage(callback, user_id=uid)
    else:
        await callback.answer("❌ Ошибка (возможно, есть активный заказ)", show_alert=True)

async def set_main_menu(bot: bot):
    """Устанавливает команды бота, которые отображаются в интерфейсе Telegram."""
    main_menu_commands = [
        BotCommand(command="/start", description="Запустить бота / Главное меню"),
    ]
    await bot.set_my_commands(main_menu_commands)
