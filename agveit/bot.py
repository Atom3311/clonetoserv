
# cat > bot.py << 'EOF'
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8502597347:AAEsc9T8wZvmHWjYnyjJIe5ZjdZZkx2UoYA"
VPN_LINK = "https://t.me/VolnaLink_bot?start=ref_59929476"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_handler(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Перейти в ВПН", url=VPN_LINK)]
        ]
    )

    await message.answer(
        "🛡️ Добро пожаловать!\n\nНажмите кнопку ниже 👇",
        reply_markup=keyboard
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
# EOF