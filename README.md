# Movie Bot - Clean UI

A modern Telegram bot for movie information with a clean user interface.

## Features

- 🎬 Movie search and information
- 🎭 Channel management
- 👤 Admin controls
- 💾 Database support
- 🎨 Clean keyboard UI

## Project Structure

```
movie-bot/
├── config.py              # Configuration management
├── database.py            # Database operations
├── main.py                # Bot entry point
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables template
├── .gitignore            # Git ignore rules
├── handlers/             # Command and message handlers
│   ├── __init__.py
│   ├── admin.py         # Admin commands
│   ├── channel.py       # Channel operations
│   ├── search.py        # Search functionality
│   └── start.py         # Start command
├── keyboards/           # Keyboard layouts
│   ├── __init__.py
│   ├── common.py        # Common keyboard buttons
│   └── main_menu.py     # Main menu keyboard
├── middlewares/         # Middleware components
│   └── __init__.py
└── utils/              # Utility functions
    ├── __init__.py
    └── texts.py        # Text messages and templates
```

## Installation

1. Clone the repository:
```bash
git clone https://github.com/hmsojibol999-commits/Market.git
cd Market
git checkout movie-bot
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp .env.example .env
# Edit .env with your settings
```

5. Run the bot:
```bash
python main.py
```

## Configuration

Edit `.env` file with:
- `BOT_TOKEN`: Your Telegram bot token
- `DATABASE_URL`: Your database connection string
- `ADMIN_ID`: Your Telegram user ID

## Architecture

### Core Components

- **config.py**: Centralized configuration management
- **database.py**: Database operations and queries
- **main.py**: Bot initialization and dispatcher setup

### Handlers

- **admin.py**: Administrative commands
- **channel.py**: Channel-specific operations
- **search.py**: Movie search functionality
- **start.py**: Start command and initialization

### UI Components

- **keyboards/**: All keyboard layouts organized by feature
- **utils/texts.py**: Centralized text messages for consistency

## Contributing

Feel free to submit issues and enhancement requests!

## License

MIT License - See LICENSE file for details
