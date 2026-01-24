import json
import logging
import os
import time
from functools import wraps
from uuid import uuid4
from dotenv import load_dotenv
import gnupg
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, CallbackContext)

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    print("Please set TELEGRAM_TOKEN in environment or .env file")
    exit(1)

DATA_FILE = os.path.join(os.path.dirname(__file__), 'data.json')
GNUPG_HOME = os.path.join(os.path.dirname(__file__), '.gnupg')

# Create .gnupg directory if it doesn't exist
os.makedirs(GNUPG_HOME, exist_ok=True)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize GPG - use the correct API for python-gnupg 0.5.6
try:
    gpg = gnupg.GPG(gnupghome=GNUPG_HOME)
except TypeError:
    # Fallback for older API
    gpg = gnupg.GPG()
    gpg.gnupghome = GNUPG_HOME

# Conversation states
ASK_SECRET, ASK_COUNTRY, MAIN_MENU, CHECKOUT_ADDR, CHECKOUT_NOTES, CHECKOUT_PAYTYPE = range(6)

# Helper JSON functions

def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


# PGP helper functions
def generate_pgp_keys():
    """Generate a PGP key pair if not exists."""
    data = load_data()
    pgp_config = data.get('pgp_config', {})

    if pgp_config.get('key_generated'):
        return pgp_config.get('public_key')

    # Generate new key
    input_data = gpg.gen_key_input(
        key_type='RSA',
        key_length=2048,
        name_email='bot@ecommerce.local',
        name_real='Ecommerce Bot'
    )
    key = gpg.gen_key(input_data)
    key_id = str(key)

    # Export public key
    public_key = gpg.export_keys(key_id)

    # Save to data.json
    data.setdefault('pgp_config', {})
    data['pgp_config']['key_generated'] = True
    data['pgp_config']['public_key'] = public_key
    data['pgp_config']['key_id'] = key_id
    save_data(data)

    return public_key


def encrypt_address(address):
    """Encrypt delivery address with the bot's public key."""
    data = load_data()
    public_key = data.get('pgp_config', {}).get('public_key')

    if not public_key:
        # Generate keys if not exists
        public_key = generate_pgp_keys()

    # Import public key
    import_result = gpg.import_keys(public_key)
    key_id = import_result.fingerprints[0] if import_result.fingerprints else None

    if not key_id:
        # Fallback: try to get from existing keys
        data = load_data()
        key_id = data.get('pgp_config', {}).get('key_id')

    # Encrypt
    encrypted_data = gpg.encrypt(address, key_id, always_trust=True)
    return str(encrypted_data)


def decrypt_address(encrypted_address):
    """Decrypt delivery address."""
    decrypted_data = gpg.decrypt(encrypted_address)
    return str(decrypted_data)


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
    # If user already registered, show the start menu; otherwise begin registration
    user = update.effective_user
    data = load_data()
    found = None
    for secret, u in data.get('users', {}).items():
        if u.get('telegram_id') == user.id:
            found = secret
            break
    if found:
        context.user_data['secret'] = found
        return send_start_menu(update, context)

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
    return send_start_menu(update, context)


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
        buttons.append([
            InlineKeyboardButton("Add {}".format(name), callback_data='add|{}'.format(pid)),
            InlineKeyboardButton("❤️ Wishlist", callback_data='wish|{}'.format(pid))
        ])
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
    # Encrypt address
    encrypted_addr = encrypt_address(addr)
    context.user_data['addr'] = encrypted_addr
    context.user_data['addr_plain'] = addr  # Store plain for reference
    update.message.reply_text('Address saved (encrypted). Any delivery notes? (or send "skip")')
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

    # Calculate totals and apply coupon if available
    subtotal = sum(item['price'] for item in cart)
    discount = 0.0
    if user.get('coupon') == 'SAVE10':
        discount = round(subtotal * 0.10, 2)
    total_amount = round(subtotal - discount, 2)

    order_id = str(int(time.time())) + '-' + uuid4().hex[:6]
    order = {
        'order_id': order_id,
        'user': secret,
        'items': cart.copy(),
        'address_encrypted': context.user_data.get('addr'),
        'notes': context.user_data.get('notes', ''),
        'payment_type': pay,
        'status': 'pending',
        'timestamp': int(time.time()),
        'subtotal': subtotal,
        'discount': discount,
        'total': total_amount,
        'coupon': user.get('coupon') if discount > 0 else ''
    }
    data.setdefault('orders', []).append(order)
    user.setdefault('orders', []).append(order_id)
    user['cart'] = []
    # Clear coupon after use
    if 'coupon' in user:
        user.pop('coupon', None)
    save_data(data)

    payinfo = data.get('payment', {})
    addrinfo = payinfo.get('btc_address') if pay == 'BTC' else payinfo.get('usdt_address')
    msg_lines = [
        f"Order {order_id} created!",
        f"Total: {total_amount:.2f} {pay}",
        f"Pay to: {addrinfo or 'N/A'}",
    ]
    if discount > 0:
        msg_lines.append(f"(🎟️ 10% coupon applied: -${discount:.2f} | Subtotal: ${subtotal:.2f})")
    msg_lines.append("")
    msg_lines.append("Your address is encrypted. Send /download_address {} to get your encrypted address file.".format(order_id))
    msg_lines.append("Then send /track {} to see status.".format(order_id))
    update.message.reply_text("\n".join(msg_lines), reply_markup=ReplyKeyboardRemove())
    return send_start_menu(update, context)


def order_total(order):
    subtotal = sum(item['price'] for item in order['items'])
    discount = order.get('discount', 0) or 0
    return subtotal - discount


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
def download_address(update: Update, context: CallbackContext):
    """Download encrypted address as a file."""
    args = update.message.text.split()
    if len(args) < 2:
        update.message.reply_text('Usage: /download_address ORDER_ID')
        return

    oid = args[1]
    secret = context.user_data.get('secret')
    data = load_data()

    # Find order
    order = None
    for o in data.get('orders', []):
        if o['order_id'] == oid and o.get('user') == secret:
            order = o
            break

    if not order:
        update.message.reply_text('Order not found or you do not have permission to access it.')
        return

    encrypted_addr = order.get('address_encrypted', '')
    if not encrypted_addr:
        update.message.reply_text('No encrypted address found for this order.')
        return

    # Create temporary file
    filename = "{}_address.asc".format(oid)
    filepath = os.path.join(os.path.dirname(__file__), filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(encrypted_addr)

    # Send file
    with open(filepath, 'rb') as f:
        update.message.reply_document(f, filename=filename, caption="Encrypted delivery address for order {}".format(oid))

    # Clean up
    try:
        os.remove(filepath)
    except:
        pass


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


# Helper: find user secret by telegram id

def find_secret_by_user_id(user_id):
    data = load_data()
    for s, u in data.get('users', {}).items():
        if u.get('telegram_id') == user_id:
            return s
    return None


# Start menu with inline buttons

def send_start_menu(update: Update, context: CallbackContext):
    text = (
        "Welcome to the shop bot 🛍️\n\n"
        "Browse listings, grab a 10% coupon 🎟️, track orders 🛰️, secure your address with PGP 🔐, and more.\n\n"
        "Choose an option:"
    )
    keyboard = [
        [InlineKeyboardButton('🛍️ Listings', callback_data='menu|products'), InlineKeyboardButton('🎟️ 10% Coupon', callback_data='menu|coupon')],
        [InlineKeyboardButton('🛰️ Track', callback_data='menu|track'), InlineKeyboardButton('ℹ️ About', callback_data='menu|about')],
        [InlineKeyboardButton('⭐ Ratings', callback_data='menu|ratings'), InlineKeyboardButton('🔐 PGP', callback_data='menu|pgp')],
        [InlineKeyboardButton('💖 Wishlist', callback_data='menu|wishlist'), InlineKeyboardButton('🛒 Cart', callback_data='menu|cart')],
        [InlineKeyboardButton('📞 Contact', callback_data='menu|contact'), InlineKeyboardButton('➕ Others', callback_data='menu|others')],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    if getattr(update, 'callback_query', None):
        update.callback_query.answer()
        update.callback_query.edit_message_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        update.message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
    return MAIN_MENU


# Central menu callback

def menu_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    choice = query.data.split('|', 1)[1]
    user_id = update.effective_user.id
    data = load_data()
    secret = find_secret_by_user_id(user_id)

    if choice == 'products':
        cats = list(data.get('products', {}).keys())
        if not cats:
            query.edit_message_text('No product categories available.')
            return
        buttons = [[InlineKeyboardButton(c, callback_data=f'cat|{c}')] for c in cats]
        buttons.append([InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')])
        query.edit_message_text('Product categories:', reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'coupon':
        text = (
            "🎟️ 10% OFF COUPON\n"
            "Use code SAVE10. Tap Apply Coupon to attach it to your next order."
        )
        buttons = [
            [InlineKeyboardButton('✅ Apply Coupon', callback_data='applycoupon')],
            [InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]
        ]
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'cart':
        if not secret:
            query.edit_message_text('Please /start to register first.')
            return
        user = data['users'][secret]
        cart = user.get('cart', [])
        if not cart:
            txt = '🛒 Your cart is empty.'
        else:
            total = sum(i['price'] for i in cart)
            lines = ['🛒 Your cart:']
            for idx, item in enumerate(cart, 1):
                lines.append(f"{idx}. {item['name']} — ${item['price']}")
            if user.get('coupon') == 'SAVE10':
                lines.append("\n🎟️ Coupon applied: -10% (will apply at checkout)")
            lines.append(f"\nSubtotal: ${total:.2f}")
            txt = "\n".join(lines)
        buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'wishlist':
        if not secret:
            query.edit_message_text('Please /start to register first.')
            return
        user = data['users'][secret]
        wl = user.get('wishlist', [])
        if not wl:
            txt = '💖 Your wishlist is empty.'
        else:
            lines = ['💖 Your wishlist:']
            for idx, item in enumerate(wl, 1):
                lines.append(f"{idx}. {item['name']} — ${item['price']}")
            txt = "\n".join(lines)
        buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'pgp':
        txt = (
            "🔐 PGP Address Encryption\n"
            "Your delivery address is encrypted before storage. You can also import the bot's public key to encrypt messages to us."
        )
        buttons = [
            [InlineKeyboardButton('📄 Get Public Key', callback_data='getpub')],
            [InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]
        ]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'about':
        txt = 'ℹ️ About\nThis is a demo ecommerce bot. Browse products, add to cart, and checkout securely with PGP.'
        buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'track':
        txt = "🛰️ Track Orders\nSend the command:\n/track ORDER_ID"
        buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'ratings':
        ratings = data.get('ratings', [])
        if ratings:
            avg = sum(r['value'] for r in ratings) / len(ratings)
            stats = f"{len(ratings)} ratings, average {avg:.1f} ⭐"
        else:
            stats = "No ratings yet."
        buttons = [[InlineKeyboardButton('⭐' * i, callback_data=f'rate|{i}')] for i in range(1, 6)]
        buttons.append([InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')])
        query.edit_message_text(f"⭐ Ratings\n{stats}\nTap to rate:", reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'contact':
        txt = "📞 Contact\nSupport: support@example.com\nOr reply here and an agent will reach out."
        buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'others':
        buttons = [
            [InlineKeyboardButton('🧾 Order History', callback_data='menu|history')],
            [InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]
        ]
        query.edit_message_text('➕ Others', reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'history':
        if not secret:
            query.edit_message_text('Please /start to register first.')
            return
        orders = [o for o in data.get('orders', []) if o.get('user') == secret]
        if not orders:
            txt = 'No orders yet.'
        else:
            lines = ['🧾 Your orders:']
            for o in orders:
                lines.append(f"{o['order_id']} — {o['status']} — ${order_total(o):.2f}")
            txt = "\n".join(lines)
        buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
        query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(buttons))

    elif choice == 'main':
        send_start_menu(update, context)


# Apply coupon callback

def applycoupon_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    secret = find_secret_by_user_id(update.effective_user.id)
    if not secret:
        query.edit_message_text('Please /start to register first.')
        return
    data = load_data()
    user = data['users'].get(secret)
    user['coupon'] = 'SAVE10'
    save_data(data)
    buttons = [[InlineKeyboardButton('⬅️ Main Menu', callback_data='menu|main')]]
    query.edit_message_text('✅ Coupon applied. You will get 10% off at checkout.', reply_markup=InlineKeyboardMarkup(buttons))


# Wishlist add callback

def wish_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    secret = find_secret_by_user_id(update.effective_user.id)
    if not secret:
        query.edit_message_text('Please /start to register first.')
        return
    data = load_data()
    # Find product by id
    _prefix, pid = query.data.split('|', 1)
    product = None
    for cat, items in data.get('products', {}).items():
        for p in items:
            if p['id'] == pid:
                product = p
                break
        if product:
            break
    if not product:
        query.edit_message_text('Product not found.')
        return
    user = data['users'][secret]
    wl = user.setdefault('wishlist', [])
    # Avoid duplicates by id
    if not any(w.get('id') == product['id'] for w in wl):
        wl.append({'id': product['id'], 'name': product['name'], 'price': product['price']})
        save_data(data)
    query.answer('Added to wishlist ❤️', show_alert=True)


# Send public key file

def send_public_key_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    public_key = generate_pgp_keys()
    filename = 'bot_public_key.asc'
    filepath = os.path.join(os.path.dirname(__file__), filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(public_key)
    try:
        with open(filepath, 'rb') as f:
            context.bot.send_document(update.effective_chat.id, f, filename=filename, caption='PGP Public Key')
    finally:
        try:
            os.remove(filepath)
        except Exception:
            pass


# Rating submission

def rate_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    _prefix, val = query.data.split('|', 1)
    try:
        rating = int(val)
        if rating < 1 or rating > 5:
            raise ValueError
    except Exception:
        query.edit_message_text('Invalid rating value.')
        return
    secret = find_secret_by_user_id(update.effective_user.id) or 'anonymous'
    data = load_data()
    entry = {'user': secret, 'value': rating, 'ts': int(time.time())}
    data.setdefault('ratings', []).append(entry)
    save_data(data)
    query.edit_message_text(f'Thanks for rating {"⭐" * rating}!')


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

    # Inline callbacks
    dp.add_handler(CallbackQueryHandler(category_callback, pattern='^cat\|'))
    dp.add_handler(CallbackQueryHandler(backcats_callback, pattern='^backcats$'))
    dp.add_handler(CallbackQueryHandler(add_to_cart_callback, pattern='^add\|'))
    dp.add_handler(CallbackQueryHandler(menu_callback, pattern='^menu\|'))
    dp.add_handler(CallbackQueryHandler(applycoupon_callback, pattern='^applycoupon$'))
    dp.add_handler(CallbackQueryHandler(wish_callback, pattern='^wish\|'))
    dp.add_handler(CallbackQueryHandler(send_public_key_callback, pattern='^getpub$'))
    dp.add_handler(CallbackQueryHandler(rate_callback, pattern='^rate\|'))

    # Commands
    dp.add_handler(MessageHandler(Filters.regex('^/track'), track_order))
    dp.add_handler(MessageHandler(Filters.regex('^/download_address'), download_address))

    updater.start_polling()
    print('Bot started')
    updater.idle()


if __name__ == '__main__':
    main()
