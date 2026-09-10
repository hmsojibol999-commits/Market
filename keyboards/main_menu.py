from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu() -> ReplyKeyboardMarkup:
    """Get main menu keyboard"""
    buttons = [
        [KeyboardButton(text="🔍 Search"), KeyboardButton(text="📺 Channels")],
        [KeyboardButton(text="❤️ Favorites"), KeyboardButton(text="📚 My List")],
        [KeyboardButton(text="⚙️ Settings"), KeyboardButton(text="❓ Help")],
    ]
    
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=False
    )


def get_search_menu() -> ReplyKeyboardMarkup:
    """Get search menu keyboard"""
    buttons = [
        [KeyboardButton(text="🎬 Movies"), KeyboardButton(text="🎭 Series")],
        [KeyboardButton(text="⬅️ Back")],
    ]
    
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=False
    )
