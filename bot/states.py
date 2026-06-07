from aiogram.fsm.state import State, StatesGroup

class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone_number = State()
    waiting_for_brand = State()
    waiting_for_vin = State()
    waiting_for_plate = State()

class CreateOrder(StatesGroup):
    selecting_car = State()
    waiting_for_description = State()

class EmergencyState(StatesGroup):
    waiting_for_voice = State()
    
class GarageState(StatesGroup):
    viewing = State()