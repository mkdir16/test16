import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart

TOKEN = "ВАШ_ТОКЕН"
# URL, где развернут ваш фронтенд (например, на Vercel или GitHub Pages)
WEB_APP_URL = "https://your-mini-app-url.vercel.app"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: types.Message):
    # Кнопка для открытия Mini App
    kb = [
        [KeyboardButton(text="Пройти тест", web_app=WebAppInfo(url=WEB_APP_URL))]
    ]
    keyboard = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    await message.answer("Привет! Нажми на кнопку ниже, чтобы начать тест.", reply_markup=keyboard)

# Обработка данных, пришедших из Mini App (через tg.sendData)
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    data = json.loads(message.web_app_data.data)
    result = data.get("result")
    total = data.get("total")
    
    await message.answer(f"Тест завершен! ✅\nВаш результат: {result} из {total}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())