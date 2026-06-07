# Из внутренних модулей выносим основные объекты "наружу"
from .bot_instance import bot, dp
from .handlers import router

# Теперь в main.py можно будет написать: from bot import bot, dp, router