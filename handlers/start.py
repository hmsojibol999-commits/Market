from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from keyboards.main_menu import get_main_menu
from utils.texts import START_MESSAGE

start_router = Router()


@start_router.message(CommandStart())
async def start_command(message: Message):
    """Handle /start command"""
    await message.answer(
        START_MESSAGE.format(name=message.from_user.first_name),
        reply_markup=get_main_menu()
    )
