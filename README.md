Telegram Ecommerce Bot

Overview:
- Simple Telegram bot that stores users by secret phrase and country in `data.json`.
- Shows a basic ecommerce flow: products, cart, checkout, orders, support.

Setup:
1. Create a bot token with BotFather on Telegram.
2. Create a `.env` file next to `bot.py` with:

TELEGRAM_TOKEN=YOUR_TOKEN_HERE

3. Install dependencies (recommend inside a virtualenv):

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

Run:

```powershell
python bot.py
```

Usage notes:
- On first /start the bot asks for your secret phrase and country.
- Products, users, payment info and orders are stored in `data.json`.
- This is a minimal example; for production use, secure storage and validation are required.
