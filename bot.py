import json
import logging
import os
import time
from functools import wraps
from uuid import uuid4
from dotenv import load_dotenv
from telegram import (Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, CallbackContext)

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    print("Please set TELEGRAM_TOKEN in environment or .env file")

DATA_FILE = os.path.join(os.path.dirname(__file__), 'data.json')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
ASK_SECRET, ASK_COUNTRY, MAIN_MENU = range(3)

# Helper JSON functions

def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


# Decorator to ensure user exists
def ensure_user(func):
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user = update.effective_user
        chat_id = update.effective_chat.id
        data = load_data()
        # try to find user by telegram username or id
        found = None
        for secret, u in data.get('users', {}).items():
            if u.get('telegram_id') == user.id or u.get('username') == (user.username or ''):
                found = secret
                break
        if not found:
            # Not registered
            update.message.reply_text("You need to /start and register with a secret phrase first.")
            return ConversationHandler.END
        context.user_data['secret'] = found
        return func(update, context, *args, **kwargs)
    return wrapped


# /start handler
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Welcome! Please send me your secret key phrase (this identifies you).\n" 
        "Pick something unique — this will be used as your account identifier.")
    return ASK_SECRET


def ask_country(update: Update, context: CallbackContext):
    secret = update.message.text.strip()
    data = load_data()
    users = data.setdefault('users', {})
    if secret in users:
        update.message.reply_text("This secret phrase is already registered. If it's yours, continue.\nPlease select your country:")
    else:
        users[secret] = {
            'username': update.effective_user.username or '',
            'telegram_id': update.effective_user.id,
            'country': None,
            'cart': [],
            'orders': []
        }
        save_data(data)
        update.message.reply_text("Secret saved. Now select your country:")
    # country selection keyboard
    countries = ['USA', 'UK', 'Nigeria', 'India', 'Other']
    keyboard = ReplyKeyboardMarkup([[c] for c in countries], one_time_keyboard=True, resize_keyboard=True)
    context.user_data['pending_secret'] = secret
    return ASK_COUNTRY, update.message.reply_text('Choose your country:', reply_markup=keyboard)


def save_country(update: Update, context: CallbackContext):
    country = update.message.text.strip()
    secret = context.user_data.get('pending_secret')
    if not secret:
        update.message.reply_text('Session expired, please /start again.', reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    data = load_data()
    users = data.setdefault('users', {})
    user = users.get(secret)
    if not user:
        update.message.reply_text('User not found. Please /start again.', reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    user['country'] = country
    user['username'] = update.effective_user.username or user.get('username', '')
    user['telegram_id'] = update.effective_user.id
    save_data(data)
    context.user_data['secret'] = secret

    update.message.reply_text('Registration complete. Welcome!', reply_markup=ReplyKeyboardRemove())
    return show_main_menu(update, context)


# Main menu
def show_main_menu(update: Update, context: CallbackContext):
    keyboard = [
        ['About', 'Products'],
        ['Cart', 'Checkout'],
        ['Order History', 'Support']
    ]
    if update.callback_query:
        # if called from callback query
        update.callback_query.answer()
        update.callback_query.edit_message_text('Main Menu', reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=False, resize_keyboard=True))
    else:
        update.message.reply_text('Main Menu:', reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return MAIN_MENU


@ensure_user
def about(update: Update, context: CallbackContext):
    update.message.reply_text('This is a demo ecommerce bot. You can browse products, add to cart, and checkout.')


@ensure_user
def support(update: Update, context: CallbackContext):
    update.message.reply_text('Support: contact support@example.com or reply here and an agent will reach out.')


@ensure_user
def list_categories(update: Update, context: CallbackContext):
    data = load_data()
    cats = list(data.get('products', {}).keys())
    if not cats:
        update.message.reply_text('No product categories available.')
        return
    buttons = [[InlineKeyboardButton(c, callback_data=f'cat|{c}')] for c in cats]
    update.message.reply_text('Product categories:', reply_markup=InlineKeyboardMarkup(buttons))


def category_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    _, cat = query.data.split('|', 1)
    data = load_data()
    products = data.get('products', {}).get(cat, [])
    if not products:
        query.edit_message_text('No products in this category.')
        return
    text = f"Products in {cat}:\n"
    buttons = []
    for p in products:
        text += f"\n{p['name']} — ${p['price']}\n{p['description']}\n"
        buttons.append([InlineKeyboardButton(f"Add {p['name']}", callback_data=f'add|{p['id']}')])
    buttons.append([InlineKeyboardButton('Back to categories', callback_data='backcats')])
    query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


def backcats_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    list_categories(update, context)


def add_to_cart_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    _, pid = query.data.split('|', 1)
    data = load_data()
    product = None
    for cat, items in data.get('products', {}).items():
        for p in items:
            if p['id'] == pid:
                product = p
                break
    if not product:
        query.edit_message_text('Product not found.')
        return
    # find user by telegram id
    user = update.effective_user
    secret = None
    for s, u in data.get('users', {}).items():
        if u.get('telegram_id') == user.id:
            secret = s
            break
    if not secret:
        query.edit_message_text('User not registered. Use /start to register.')
        return
    users = data['users']
    cart = users[secret].setdefault('cart', [])
    cart.append({'id': product['id'], 'name': product['name'], 'price': product['price']})
    save_data(data)
    query.edit_message_text(f"Added {product['name']} to cart.")


@ensure_user
def view_cart(update: Update, context: CallbackContext):
    secret = context.user_data.get('secret')
    data = load_data()
    user = data['users'].get(secret)
    cart = user.get('cart', [])
    if not cart:
        update.message.reply_text('Your cart is empty.')
        return
    text = 'Your cart:\n'
    total = 0
    for idx, item in enumerate(cart, 1):
        text += f"{idx}. {item['name']} — ${item['price']}\n"
        total += item['price']
    text += f"\nTotal: ${total:.2f}"
    update.message.reply_text(text)


@ensure_user
def checkout_start(update: Update, context: CallbackContext):
    secret = context.user_data.get('secret')
    data = load_data()
    user = data['users'].get(secret)
    cart = user.get('cart', [])
    if not cart:
        update.message.reply_text('Your cart is empty. Add products first.')
        return
    update.message.reply_text('Please enter delivery address:')
    return 'ADDR'


def checkout_addr(update: Update, context: CallbackContext):
    addr = update.message.text.strip()
    context.user_data['addr'] = addr
    update.message.reply_text('Any delivery notes? (or send /skip)')
    return 'NOTES'


def checkout_notes(update: Update, context: CallbackContext):
    notes = update.message.text.strip()
    context.user_data['notes'] = notes
    # ask payment type
    keyboard = ReplyKeyboardMarkup([['BTC', 'USDT']], one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text('Choose payment type:', reply_markup=keyboard)
    return 'PAYTYPE'


def checkout_skip_notes(update: Update, context: CallbackContext):
    context.user_data['notes'] = ''
    keyboard = ReplyKeyboardMarkup([['BTC', 'USDT']], one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text('Choose payment type:', reply_markup=keyboard)
    return 'PAYTYPE'


def checkout_paytype(update: Update, context: CallbackContext):
    pay = update.message.text.strip().upper()
    if pay not in ('BTC', 'USDT'):
        update.message.reply_text('Invalid payment type. Choose BTC or USDT.')
        return 'PAYTYPE'
    # create order
    secret = context.user_data.get('secret')
    data = load_data()
    user = data['users'].get(secret)
    cart = user.get('cart', [])
    if not cart:
        update.message.reply_text('Your cart is empty. Aborting.')
        return ConversationHandler.END
    order_id = str(int(time.time())) + '-' + uuid4().hex[:6]
    order = {
        'order_id': order_id,
        'user': secret,
        'items': cart.copy(),
        'address': context.user_data.get('addr'),
        'notes': context.user_data.get('notes', ''),
        'payment_type': pay,
        'status': 'pending',
        'timestamp': int(time.time())
    }
    data.setdefault('orders', []).append(order)
    # attach to user's orders and clear cart
    user.setdefault('orders', []).append(order_id)
    user['cart'] = []
    save_data(data)
    # give payment info
    payinfo = data.get('payment', {})
    addrinfo = payinfo.get('btc_address') if pay == 'BTC' else payinfo.get('usdt_address')
    update.message.reply_text(f"Order {order_id} created. Pay {order_total(order)} {pay} to: {addrinfo}\nThen send /track {order_id} to see status.")
    return ConversationHandler.END


def order_total(order):
    return sum(item['price'] for item in order['items'])


@ensure_user
def track_order(update: Update, context: CallbackContext):
    args = update.message.text.split()
    if len(args) < 2:
        update.message.reply_text('Usage: /track ORDER_ID')
        return
    oid = args[1]
    data = load_data()
    for o in data.get('orders', []):
        if o['order_id'] == oid:
            update.message.reply_text(f"Order {oid}: status {o['status']}. Items: {len(o['items'])} Total: ${order_total(o):.2f}")
            return
    update.message.reply_text('Order not found.')


@ensure_user
def order_history(update: Update, context: CallbackContext):
    secret = context.user_data.get('secret')
    data = load_data()
    orders = [o for o in data.get('orders', []) if o.get('user') == secret]
    if not orders:
        update.message.reply_text('No orders yet.')
        return
    text = 'Your orders:\n'
    for o in orders:
        text += f"{o['order_id']} — {o['status']} — ${order_total(o):.2f}\n"
    update.message.reply_text(text)


def cancel(update: Update, context: CallbackContext):
    update.message.reply_text('Cancelled.', reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ASK_SECRET: [MessageHandler(Filters.text & ~Filters.command, ask_country)],
            ASK_COUNTRY: [MessageHandler(Filters.text & ~Filters.command, save_country)],
            MAIN_MENU: [
                MessageHandler(Filters.regex('^(About)$'), about),
                MessageHandler(Filters.regex('^(Products)$'), list_categories),
                MessageHandler(Filters.regex('^(Cart)$'), view_cart),
                MessageHandler(Filters.regex('^(Checkout)$'), checkout_start),
                MessageHandler(Filters.regex('^(Order History)$'), order_history),
                MessageHandler(Filters.regex('^(Support)$'), support),
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(category_callback, pattern='^cat\|'))
    dp.add_handler(CallbackQueryHandler(backcats_callback, pattern='^backcats$'))
    dp.add_handler(CallbackQueryHandler(add_to_cart_callback, pattern='^add\|'))

    # Checkout sub-conversation handlers
    dp.add_handler(MessageHandler(Filters.regex('^/track'), track_order))
    dp.add_handler(CommandHandler('orders', order_history))

    # Handlers for checkout steps (simple implementation using message handlers with state stored in user_data)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, lambda u, c: None))

    # For simplicity, add separate handlers for checkout path using a lightweight approach
    # We'll monitor user_data['checkout'] flags
    def precheckout_filter(update: Update, context: CallbackContext):
        return True

    # Manual handlers for the checkout conversation flow
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, lambda update, context: None), group=1)

    # Add handlers via simple dispatcher for checkout states using callback_data markers
    # Instead, use a MessageHandler for the specific custom states by checking context.user_data
    def generic_message(update: Update, context: CallbackContext):
        if context.user_data.get('awaiting') == 'ADDR':
            return checkout_addr(update, context)
        if context.user_data.get('awaiting') == 'NOTES':
            return checkout_notes(update, context)
        if context.user_data.get('awaiting') == 'PAYTYPE':
            return checkout_paytype(update, context)
        return

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, generic_message), group=2)

    # When checkout_start called, we set awaiting state to 'ADDR'
    # Modify checkout_start to set awaiting flag properly
    def checkout_start_wrapper(update: Update, context: CallbackContext):
        ret = checkout_start(update, context)
        if ret == 'ADDR':
            context.user_data['awaiting'] = 'ADDR'
        return ret

    dp.add_handler(MessageHandler(Filters.regex('^(Checkout)$'), checkout_start_wrapper), group=3)

    updater.start_polling()
    print('Bot started')
    updater.idle()


if __name__ == '__main__':
    main()
