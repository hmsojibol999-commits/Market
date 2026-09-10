from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

search_router = Router()


@search_router.message(Command("search"))
async def search_command(message: Message):
    """Handle /search command"""
    await message.answer(
        "🔍 Enter movie name to search:",
    )


@search_router.message(F.text)
async def search_movie(message: Message):
    """Search for movies"""
    query = message.text
    await message.answer(f"Searching for: {query}...")
