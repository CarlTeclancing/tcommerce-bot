Telegram Ecommerce Bot

Overview:
- Simple Telegram bot that stores users by secret phrase and country in `data.json`.
- Shows a basic ecommerce flow: products, cart, checkout, orders, support.
- **Delivery addresses are encrypted with PGP** for enhanced privacy and security.
- Users can download their encrypted addresses as files for secure storage.

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
- **PGP Keys**: The bot automatically generates PGP keys on first run. Keys are stored in `.gnupg/` directory.
- **Encrypted Addresses**: When you checkout, your delivery address is encrypted with the bot's public key.
- **Download Encrypted Address**: After order creation, use `/download_address ORDER_ID` to download your encrypted address as an `.asc` file.
- **Order Tracking**: Use `/track ORDER_ID` to view order status.

Security notes:
- This is a minimal example; for production use, secure storage and validation are required.
- PGP encryption provides strong privacy for delivery addresses.
- Keep the `.gnupg/` directory and `data.json` file secure.
- Consider using a database instead of JSON for production systems.

