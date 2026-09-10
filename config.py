import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

# Database Configuration
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///bot.db')

# API Configuration
API_TIMEOUT = int(os.getenv('API_TIMEOUT', 30))

# Logging Configuration
LOGGING_LEVEL = os.getenv('LOGGING_LEVEL', 'INFO')

# Validation
if not BOT_TOKEN:
    raise ValueError('BOT_TOKEN not found in environment variables')
