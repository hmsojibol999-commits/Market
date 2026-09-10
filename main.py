import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from config import BOT_TOKEN, LOGGING_LEVEL
from database import init_db
from handlers.start import start_router
from handlers.admin import admin_router
from handlers.search import search_router
from handlers.channel import channel_router

# Logging configuration
logging.basicConfig(
    level=LOGGING_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Register routers
dp.include_router(start_router)
dp.include_router(admin_router)
dp.include_router(search_router)
dp.include_router(channel_router)


async def set_default_commands():
    """Set default bot commands"""
    commands = [
        BotCommand(command="start", description="🚀 Start the bot"),
        BotCommand(command="search", description="🔍 Search movies"),
        BotCommand(command="help", description="❓ Get help"),
    ]
    await bot.set_my_commands(commands)


async def main():
    """Main bot function"""
    try:
        # Initialize database
        init_db()
        logger.info("Database initialized")
        
        # Set default commands
        await set_default_commands()
        logger.info("Bot commands configured")
        
        # Start polling
        logger.info("Bot started polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
