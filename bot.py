import json
import logging
import os
import time
from functools import wraps
from uuid import uuid4
from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, CallbackContext)

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    print("Please set TELEGRAM_TOKEN in environment or .env file")
    exit(1)

DATA_FILE = os.path.join(os.path.dirname(__file__), 'data.json')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
ASK_SECRET, ASK_COUNTRY, MAIN_MENU, CHECKOUT_ADDR, CHECKOUT_NOTES, CHECKOUT_PAYTYPE = range(6)

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
        data = load_data()
        # try to find user by telegram id
        found = None
        for secret, u in data.get('users', {}).items():
            if u.get('telegram_id') == user.id:
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
        update.message.reply_text("This secret phrase is already registered. Welcome back!")
    else:
        users[secret] = {
            'username': update.effective_user.username or '',
            'telegram_id': update.effective_user.id,
            'country': None,
            'cart': [],
            'orders': []
        }
        save_data(data)
        update.message.reply_text("Secret saved!")
    
    # country selection keyboard
    countries = ['USA', 'UK', 'Nigeria', 'India', 'Other']
    keyboard = ReplyKeyboardMarkup([[c] for c in countries], one_time_keyboard=True, resize_keyboard=True)
    context.user_data['pending_secret'] = secret
    update.message.reply_text('Please choose your country:', reply_markup=keyboard)
    return ASK_COUNTRY


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
        update.callback_query.answer()
        update.callback_query.edit_message_text('Main Menu:', reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    else:
        update.message.reply_text('Main Menu:', reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return MAIN_MENU


@ensure_user
def about(update: Update, context: CallbackContext):
    update.message.reply_text('This is a demo ecommerce bot. You can browse products, add to cart, and checkout.')
    return MAIN_MENU


@ensure_user
def support(update: Update, context: CallbackContext):
    update.message.reply_text('Support: contact support@example.com or reply here and an agent will reach out.')
    return MAIN_MENU


@ensure_user
def list_categories(update: Update, context: CallbackContext):
    data = load_data()
    cats = list(data.get('products', {}).keys())
    if not cats:
        update.message.reply_text('No product categories available.')
        return MAIN_MENU
    buttons = [[InlineKeyboardButton(c, callback_data='cat|{}'.format(c))] for c in cats]
    update.message.reply_text('Product categories:', reply_markup=InlineKeyboardMarkup(buttons))
    return MAIN_MENU


def category_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    _, cat = query.data.split('|', 1)
    data = load_data()
    products = data.get('products', {}).get(cat, [])
    if not products:
        query.edit_message_text('No products in this category.')
        return
    text = "Products in {}:\n".format(cat)
    buttons = []
    for p in products:
        name = p['name']
        price = p['price']
        desc = p['description']
        pid = p['id']
        text += "\n{} — ${}\n{}\n".format(name, price, desc)
        buttons.append([InlineKeyboardButton("Add {}".format(name), callback_data='add|{}'.format(pid))])
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
    query.answer("Added {} to cart.".format(product['name']), show_alert=True)


@ensure_user
def view_cart(update: Update, context: CallbackContext):
    secret = context.user_data.get('secret')
    data = load_data()
    user = data['users'].get(secret)
    cart = user.get('cart', [])
    if not cart:
        update.message.reply_text('Your cart is empty.')
        return MAIN_MENU
    text = 'Your cart:\n'
    total = 0
    for idx, item in enumerate(cart, 1):
        text += "{0}. {1} — ${2}\n".format(idx, item['name'], item['price'])
        total += item['price']
    text += "\nTotal: ${:.2f}".format(total)
    update.message.reply_text(text)
    return MAIN_MENU


@ensure_user
def checkout_start(update: Update, context: CallbackContext):
    secret = context.user_data.get('secret')
    data = load_data()
    user = data['users'].get(secret)
    cart = user.get('cart', [])
    if not cart:
        update.message.reply_text('Your cart is empty. Add products first.')
        return MAIN_MENU
    update.message.reply_text('Please enter delivery address:')
    return CHECKOUT_ADDR


def checkout_addr(update: Update, context: CallbackContext):
    addr = update.message.text.strip()
    context.user_data['addr'] = addr
    update.message.reply_text('Any delivery notes? (or send "skip")')
    return CHECKOUT_NOTES


def checkout_notes(update: Update, context: CallbackContext):
    notes = update.message.text.strip()
    if notes.lower() == 'skip':
        context.user_data['notes'] = ''
    else:
        context.user_data['notes'] = notes
    keyboard = ReplyKeyboardMarkup([['BTC', 'USDT']], one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text('Choose payment type:', reply_markup=keyboard)
    return CHECKOUT_PAYTYPE


def checkout_paytype(update: Update, context: CallbackContext):
    pay = update.message.text.strip().upper()
    if pay not in ('BTC', 'USDT'):
        update.message.reply_text('Invalid payment type. Choose BTC or USDT.')
        return CHECKOUT_PAYTYPE
    
    secret = context.user_data.get('secret')
    data = load_data()
    user = data['users'].get(secret)
    cart = user.get('cart', [])
    if not cart:
        update.message.reply_text('Your cart is empty. Aborting.')
        return MAIN_MENU
    
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
    user.setdefault('orders', []).append(order_id)
    user['cart'] = []
    save_data(data)
    
    payinfo = data.get('payment', {})
    addrinfo = payinfo.get('btc_address') if pay == 'BTC' else payinfo.get('usdt_address')
    total = order_total(order)
    msg = "Order {} created!\nTotal: {:.2f} {}\nPay to: {}\n\nThen send /track {} to see status.".format(
        order_id, total, pay, addrinfo, order_id)
    update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
    return show_main_menu(update, context)


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
            update.message.reply_text("Order {}: status {}. Items: {} Total: ${:.2f}".format(oid, o['status'], len(o['items']), order_total(o)))
            return
    update.message.reply_text('Order not found.')


@ensure_user
def order_history(update: Update, context: CallbackContext):
    secret = context.user_data.get('secret')
    data = load_data()
    orders = [o for o in data.get('orders', []) if o.get('user') == secret]
    if not orders:
        update.message.reply_text('No orders yet.')
        return MAIN_MENU
    text = 'Your orders:\n'
    for o in orders:
        oid = o['order_id']
        status = o['status']
        total = order_total(o)
        text += "{} — {} — ${:.2f}\n".format(oid, status, total)
    update.message.reply_text(text)
    return MAIN_MENU


def cancel(update: Update, context: CallbackContext):
    update.message.reply_text('Cancelled.', reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Main conversation handler
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ASK_SECRET: [MessageHandler(Filters.text & ~Filters.command, ask_country)],
            ASK_COUNTRY: [MessageHandler(Filters.text & ~Filters.command, save_country)],
            MAIN_MENU: [
                MessageHandler(Filters.regex('^About$'), about),
                MessageHandler(Filters.regex('^Products$'), list_categories),
                MessageHandler(Filters.regex('^Cart$'), view_cart),
                MessageHandler(Filters.regex('^Checkout$'), checkout_start),
                MessageHandler(Filters.regex('^Order History$'), order_history),
                MessageHandler(Filters.regex('^Support$'), support),
            ],
            CHECKOUT_ADDR: [MessageHandler(Filters.text & ~Filters.command, checkout_addr)],
            CHECKOUT_NOTES: [MessageHandler(Filters.text & ~Filters.command, checkout_notes)],
            CHECKOUT_PAYTYPE: [MessageHandler(Filters.text & ~Filters.command, checkout_paytype)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(category_callback, pattern='^cat\|'))
    dp.add_handler(CallbackQueryHandler(backcats_callback, pattern='^backcats$'))
    dp.add_handler(CallbackQueryHandler(add_to_cart_callback, pattern='^add\|'))
    dp.add_handler(MessageHandler(Filters.regex('^/track'), track_order))

    updater.start_polling()
    print('Bot started')
    updater.idle()


if __name__ == '__main__':
    main()
