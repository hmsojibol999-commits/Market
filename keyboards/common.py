from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_back_button() -> ReplyKeyboardMarkup:
    """Get back button keyboard"""
    buttons = [[KeyboardButton(text="⬅️ Back")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_cancel_button() -> ReplyKeyboardMarkup:
    """Get cancel button keyboard"""
    buttons = [[KeyboardButton(text="❌ Cancel")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_navigation_buttons(previous=True, next=True) -> ReplyKeyboardMarkup:
    """Get navigation buttons"""
    buttons = []
    row = []
    
    if previous:
        row.append(KeyboardButton(text="⬅️ Previous"))
    if next:
        row.append(KeyboardButton(text="Next ➡️"))
    
    if row:
        buttons.append(row)
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
