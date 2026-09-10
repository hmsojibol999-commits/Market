from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from config import ADMIN_ID
from utils.texts import ADMIN_ONLY_MESSAGE

admin_router = Router()


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id == ADMIN_ID


@admin_router.message(Command("admin"))
async def admin_command(message: Message):
    """Handle /admin command"""
    if not is_admin(message.from_user.id):
        await message.answer(ADMIN_ONLY_MESSAGE)
        return
    
    await message.answer("Admin panel opened! 🔧")
