from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

channel_router = Router()


@channel_router.message(Command("channel"))
async def channel_command(message: Message):
    """Handle /channel command"""
    await message.answer(
        "📺 Channel Management Panel\n\n"
        "Select an option below:"
    )
