import asyncio
import sqlite3
import random
import logging
import time
import aiohttp
import hmac
import hashlib
import urllib.parse
import json
import os
import io
import qrcode
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
                           InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, BufferedInputFile)

# ==========================================
# 1. CONFIGURATION 
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8977319194:AAGYrRa4SzPqNIzS7307P3CmThFj7TIcQ20")
BOT_USERNAME = "@UNKNOWNFFPANEL_BOT"
ADMIN_ID = 5255460348
ADMIN_CONTACT = "@Unknown_143_1"

# Exchange Rate for Binance USDT Deposits
USDT_TO_INR = 90.0

# FreePanel / FamAPI payment gateway
FREEPANEL_API_URL = "https://py.freepanel.in/api/v1/orders"
FREEPANEL_REDIRECT_URL_DEFAULT = "https://t.me/UNKNOWNFFPANEL12_BOT"

# FreePanel settings are stored in the DB 'settings' table and set via /admin.
# Environment variables remain as a fallback.
def get_freepanel_api_key():
    return (get_setting('freepanel_api_key') or os.getenv("FREEPANEL_API_KEY", "")).strip()

def get_freepanel_redirect_url():
    return (get_setting('freepanel_redirect_url')
            or os.getenv("FREEPANEL_REDIRECT_URL", FREEPANEL_REDIRECT_URL_DEFAULT)).strip()

def get_freepanel_status_url():
    return (get_setting('freepanel_status_url')
            or os.getenv("FREEPANEL_STATUS_API_URL", "")).strip()

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

def fmt_curr(amount):
    return f"₹{amount:.2f}"

# ==========================================
# 2. DATABASE ARCHITECTURE (UPDATED - NO REFERRAL/SPIN/RESELLER)
# ==========================================
def init_db():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, 
        phone TEXT, 
        first_name TEXT, 
        balance REAL DEFAULT 0.0, 
        orders_count INTEGER DEFAULT 0, 
        spent REAL DEFAULT 0.0, 
        joined_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        category TEXT, 
        name TEXT, 
        price_inr REAL, 
        stock INTEGER, 
        apk_link TEXT, 
        validity TEXT DEFAULT 'Lifetime', 
        device_limit TEXT DEFAULT '1 Device'
    )''')
    _cols = [r[1] for r in c.execute("PRAGMA table_info(products)").fetchall()]
    if 'api_pid' not in _cols:
        c.execute("ALTER TABLE products ADD COLUMN api_pid TEXT")
    if 'api_duration' not in _cols:
        c.execute("ALTER TABLE products ADD COLUMN api_duration TEXT")
    if 'needs_android_id' not in _cols:
        c.execute("ALTER TABLE products ADD COLUMN needs_android_id INTEGER DEFAULT 0")
    if 'photo_file_id' not in _cols:
        c.execute("ALTER TABLE products ADD COLUMN photo_file_id TEXT")
    # FreePanel payment metadata for deposits
    dep_cols = [r[1] for r in c.execute("PRAGMA table_info(deposits)").fetchall()]
    if 'gateway' not in dep_cols:
        c.execute("ALTER TABLE deposits ADD COLUMN gateway TEXT DEFAULT 'UPI'")
    if 'gateway_order_id' not in dep_cols:
        c.execute("ALTER TABLE deposits ADD COLUMN gateway_order_id TEXT")
    if 'payment_link' not in dep_cols:
        c.execute("ALTER TABLE deposits ADD COLUMN payment_link TEXT")
    c.execute('''CREATE TABLE IF NOT EXISTS product_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        product_id INTEGER, 
        key_text TEXT, 
        is_used INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER, 
        product_name TEXT, 
        price_paid REAL, 
        delivered_key TEXT, 
        purchase_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER, 
        message TEXT, 
        status TEXT DEFAULT 'Open'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, 
        value TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS coupons (
        code TEXT PRIMARY KEY, 
        amount REAL, 
        uses_left INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS redeemed (
        user_id INTEGER, 
        code TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        order_id TEXT PRIMARY KEY, 
        user_id INTEGER, 
        amount_inr REAL, 
        status TEXT, 
        timestamp INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS crypto_txns (
        txid TEXT PRIMARY KEY, 
        user_id INTEGER, 
        amount_usdt REAL, 
        timestamp INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        screenshot_file_id TEXT,
        status TEXT DEFAULT 'pending',
        timestamp INTEGER
    )''')
    conn.commit()
    conn.close()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=True):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute(query, params)
    res = c.fetchone() if fetchone else c.fetchall() if fetchall else None
    if commit: conn.commit()
    conn.close()
    return res

def get_setting(key):
    r = db_query("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return r[0] if r else None

# ==========================================
# 3. MAINTENANCE MIDDLEWARE
# ==========================================
class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if event.from_user.id == ADMIN_ID:
            return await handler(event, data)
        status_check = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
        status = status_check[0] if status_check else 'ON'
        if status == 'OFF':
            msg = "⚠️ <b>Store Maintenance</b>\n\nThe store is currently offline for updates. Please check back later!"
            if isinstance(event, Message): await event.answer(msg)
            elif isinstance(event, CallbackQuery): await event.answer("⚠️ Bot is currently OFF.", show_alert=True)
            return
        return await handler(event, data)

dp.message.middleware(MaintenanceMiddleware())
dp.callback_query.middleware(MaintenanceMiddleware())

# ==========================================
# 4. FSM STATES
# ==========================================
class UserStates(StatesGroup):
    wait_for_ticket = State()
    wait_for_redeem = State()
    wait_for_custom_amount = State()
    wait_for_freepanel_amount = State()
    wait_for_crypto_txid = State() 
    wait_for_payment_screenshot = State()
    wait_for_android_id = State()
    
class AdminStates(StatesGroup):
    add_prod_category = State()
    add_prod_name = State()
    add_prod_validity = State() 
    add_prod_device_limit = State() 
    add_prod_price = State()
    add_prod_apk = State()
    add_prod_photo = State()
    add_prod_pid = State()
    add_prod_duration = State()
    add_prod_androidid = State()
    add_prod_keys = State()
    edit_prod_field = State()
    wait_for_new_value = State()
    wait_for_product_photo = State()
    wait_for_add_keys = State()
    broadcast_msg = State()
    add_coupon_code = State()
    add_coupon_amount = State()
    add_coupon_uses = State()
    wait_for_upi_id = State()
    wait_for_binance_api = State()
    wait_for_binance_secret = State()
    wait_for_binance_address = State()
    ticket_reply_msg = State()
    wait_for_delete_key = State()
    wait_for_reseller_api_key = State()
    wait_for_reseller_master_key = State()
    wait_for_freepanel_api_key = State()
    wait_for_freepanel_status_url = State()
    wait_for_freepanel_redirect_url = State()

# ==========================================
# 5. KEYBOARDS (NO RESELLER, NO SPIN, NO REFERRAL)
# ==========================================
def contact_kb(): 
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Tap here to verify contact", request_contact=True)]],
        resize_keyboard=True, 
        one_time_keyboard=True
    )

def main_menu_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Buy Key", callback_data="menu_shop")],
            [
                InlineKeyboardButton(text="🧾 My Orders", callback_data="menu_orders"),
                InlineKeyboardButton(text="👑 Profile", callback_data="menu_profile")
            ],
            [
                InlineKeyboardButton(text="💳 Add Balance", callback_data="menu_add_balance"),
                InlineKeyboardButton(text="🔑 Tutorial ", callback_data="menu_how_to")
            ],
            [
                InlineKeyboardButton(text="📥 Check Update", callback_data="menu_Update"),
                InlineKeyboardButton(text="🌹 Selling Proof ", callback_data="menu_Feed")
            ],
            [
                InlineKeyboardButton(text="💬 Support Center", callback_data="menu_support")
            ]
        ]
    )

def back_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Back to Menu", callback_data="back_main")]
        ]
    )

def admin_kb():
    status = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
    status_val = status[0] if status else 'ON'
    pending = db_query("SELECT COUNT(*) FROM deposits WHERE status='pending'", fetchone=True)[0]
    open_tickets = db_query("SELECT COUNT(*) FROM tickets WHERE status='Open'", fetchone=True)[0]
    pending_label = f"💰 Deposits ({pending})" if pending else "💰 Deposits"
    ticket_label = f"🎫 Tickets ({open_tickets})" if open_tickets else "🎫 Tickets"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Dashboard", callback_data="admin_dashboard"),
         InlineKeyboardButton(text="👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton(text="➕ Add Product", callback_data="admin_add_prod"),
         InlineKeyboardButton(text="📦 Products", callback_data="admin_manage_prods")],
        [InlineKeyboardButton(text=pending_label, callback_data="admin_deposits"),
         InlineKeyboardButton(text=ticket_label, callback_data="admin_view_tickets")],
        [InlineKeyboardButton(text="🎟 Coupons", callback_data="admin_coupons"),
         InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast_btn")],
        [InlineKeyboardButton(text="💳 UPI Setup", callback_data="admin_setup_upi"),
         InlineKeyboardButton(text="🪙 Binance", callback_data="admin_setup_binance")],
        [InlineKeyboardButton(text="💠 FreePanel Setup", callback_data="admin_setup_freepanel")],
        [InlineKeyboardButton(text="🔑 Reseller API", callback_data="admin_setup_reseller"),
         InlineKeyboardButton(text="⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton(text=f"{'🟢' if status_val == 'ON' else '🔴'} Bot: {status_val}",
                               callback_data="admin_toggle_bot")]
    ])

def admin_back_kb(): 
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]])

# ==========================================
# 6. ADVANCED NOTIFICATION SYSTEM
# ==========================================
async def send_advanced_notification(user_id, notif_type, amount, product=None, key=None, gateway="UPI"):
    user_info = db_query("SELECT first_name, phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    name = user_info[0] if user_info else "Unknown"
    phone = user_info[1] if user_info and user_info[1] else "Not Provided"
    
    try:
        chat = await bot.get_chat(user_id)
        username = f"@{chat.username}" if chat.username else "None"
    except:
        username = "None"
        
    time_now = datetime.now().strftime("%d-%m-%Y %I:%M %p")
    
    if notif_type == "ORDER":
        title = "🛒 NEW ORDER! 🛒"
        details = f"📦 Product: {product}\n🔑 Key: <code>{key}</code>\n💰 Amount: ₹{amount}\n📅 Time: {time_now}"
    else:
        title = "💰 NEW DEPOSIT! 💰"
        details = f"💵 Amount Added: ₹{amount}\n🧾 Gateway: {gateway}\n🆔 Reference: <code>{product}</code>\n📅 Time: {time_now}"

    msg = (
        f"<b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: {name}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📱 Phone: {phone}\n"
        f"👤 Username: {username}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{details}"
    )
    try:
        await bot.send_message(ADMIN_ID, msg)
    except Exception as e:
        logging.error(f"Failed to send notif: {e}")

async def send_stock_alert(prod_id, prod_category, prod_name, user_id):
    """Send a detailed alert to admin when a product goes out of stock."""
    user_info = db_query("SELECT first_name, phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    name = user_info[0] if user_info else "Unknown"
    phone = user_info[1] if user_info and user_info[1] else "Not Provided"
    
    try:
        chat = await bot.get_chat(user_id)
        username = f"@{chat.username}" if chat.username else "None"
    except:
        username = "None"
    
    msg = (
        f"🚨 <b>⚠️ PRODUCT OUT OF STOCK ⚠️</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Product:</b> {prod_category} ({prod_name})\n"
        f"🆔 <b>Product ID:</b> <code>{prod_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Buyer:</b> {name}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Phone:</b> {phone}\n"
        f"👤 <b>Username:</b> {username}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <b>Time:</b> {datetime.now().strftime('%d-%m-%Y %I:%M %p')}"
    )
    try:
        await bot.send_message(ADMIN_ID, msg)
    except Exception as e:
        logging.error(f"Failed to send stock alert: {e}")

# ==========================================
# 9. ONBOARDING & DEEP LINK INTERCEPT (NO REFERRAL)
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    args = message.text.split()

    # Referral deep link ignored – no referral system
    user = db_query("SELECT phone FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
    if not user or not user[0]:
        db_query("INSERT OR IGNORE INTO users (user_id, first_name, joined_date) VALUES (?, ?, ?)", 
                 (message.from_user.id, message.from_user.first_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await message.answer("<b>🛡 VERIFICATION REQUIRED</b>\n\nTo safeguard your orders, we need to verify your account.\n👇 <b>Tap the button below:</b>", reply_markup=contact_kb())
    else:
        await send_main_menu(message)

@dp.message(F.contact)
async def handle_contact(message: Message):
    if message.contact.user_id == message.from_user.id:
        db_query("UPDATE users SET phone=? WHERE user_id=?", (message.contact.phone_number, message.from_user.id))
        await message.answer("✅ Verification successful!", reply_markup=ReplyKeyboardRemove())
        await send_main_menu(message)
    else:
        await message.answer("❌ Please share your own contact.")

async def send_main_menu(ctx):
    if isinstance(ctx, Message):
        user_id = ctx.from_user.id
    else:  # CallbackQuery
        user_id = ctx.from_user.id

    user_data = db_query("SELECT balance FROM users WHERE user_id=?", (user_id,), fetchone=True)
    balance = user_data[0] if user_data else 0.0

    text = (f"👋 Welcome, <b>{ctx.from_user.first_name}!</b>\n\n"
            f"⭐ <b>— ABHI DEMO —</b>⭐\n\n"
            f"🔑 Premium Game Keys & Panels\n"
            f"⚡ Instant Auto Delivery 24×7\n"
            f"🔒 100% Secure & Trusted Store\n"
            f"💎 Best Prices Guaranteed\n"
            f"🎁 Daily Rewards & Special Offers\n"
            f"☎️ 24/7 Live Support Available\n"
            f"🌹 Trusted by Hundreds of Users\n\n"
            f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
            f"💰 <b>Your Balance:</b> {fmt_curr(balance)}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 <b>Tap Shop Now to Start!</b>")

    if isinstance(ctx, Message):
        await ctx.answer(text, reply_markup=main_menu_kb())
    else:
        await ctx.message.edit_text(text, reply_markup=main_menu_kb())

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(call)

# ==========================================
# 10. DUAL PAYMENT GATEWAY SYSTEM
# ==========================================
async def create_freepanel_order(user_id: int, amount_inr: float):
    """Create a FreePanel/FamAPI order.

    The create-order endpoint is the endpoint currently configured for this bot.
    The exact payment-status endpoint must be supplied by your FreePanel account/docs
    through FREEPANEL_STATUS_API_URL; this code never guesses it.
    """
    api_key = get_freepanel_api_key()
    if not api_key:
        return None, "FreePanel API key is not configured. Set it in /admin → FreePanel Setup."

    amount_paise = int(round(float(amount_inr) * 100))
    receipt = f"tg_{user_id}_{int(time.time())}_{random.randint(1000,9999)}"
    payload = {
        "amount": amount_paise,
        "redirect_url": get_freepanel_redirect_url(),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(FREEPANEL_API_URL, json=payload, headers=headers) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"raw": raw}

                if resp.status < 200 or resp.status >= 300:
                    logging.error("FreePanel create order failed: %s %s", resp.status, raw[:1000])
                    return None, f"FreePanel API error ({resp.status}). Please try again later."

                def pick(obj, keys):
                    if isinstance(obj, dict):
                        for k in keys:
                            if obj.get(k):
                                return obj[k]
                        for container in (obj.get("data"), obj.get("order"), obj.get("result")):
                            if isinstance(container, dict):
                                found = pick(container, keys)
                                if found:
                                    return found
                    return None

                order_id = pick(data, ["order_id", "orderId", "id", "order"])
                payment_link = pick(data, [
                    "payment_link", "paymentLink", "payment_url", "paymentUrl",
                    "url", "upi_link", "upiLink"
                ])

                if not payment_link:
                    logging.error("FreePanel response did not contain payment link: %s", raw[:1500])
                    return None, "FreePanel did not return a payment link. Please check the API response/docs."

                return {
                    "order_id": str(order_id or receipt),
                    "payment_link": str(payment_link),
                    "raw": data,
                }, None
    except Exception:
        logging.exception("FreePanel order creation error")
        return None, "Unable to connect to FreePanel right now. Please try again later."


def make_upi_qr_bytes(payment_link: str) -> bytes:
    """Create a QR image from the UPI/payment link returned by FreePanel."""
    qr = qrcode.QRCode(version=None, box_size=10, border=4)
    qr.add_data(payment_link)
    qr.make(fit=True)
    image = qr.make_image()
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def freepanel_payment_kb(order_id, payment_link):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay Now", url=payment_link)],
        [InlineKeyboardButton(text="🔄 Check Status", callback_data=f"fp_status_{order_id}")],
        [InlineKeyboardButton(text="❌ Cancel Payment", callback_data="menu_add_balance")]
    ])


@dp.callback_query(F.data == "gateway_freepanel")
async def add_balance_freepanel(call: CallbackQuery):
    await call.message.edit_text(
        "💳 <b>FAMPAY UPI PAYMENT</b>\n\n"
        "Select an amount to create a secure payment order.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="₹10", callback_data="fp_amount_10"), InlineKeyboardButton(text="₹50", callback_data="fp_amount_50")],
            [InlineKeyboardButton(text="₹100", callback_data="fp_amount_100"), InlineKeyboardButton(text="₹300", callback_data="fp_amount_300")],
            [InlineKeyboardButton(text="✏️ Custom Amount", callback_data="fp_custom")],
            [InlineKeyboardButton(text="« Back", callback_data="menu_add_balance")]
        ])
    )


@dp.callback_query(F.data.startswith("fp_amount_"))
async def freepanel_fixed_amount(call: CallbackQuery):
    amount = float(call.data.rsplit("_", 1)[1])
    await start_freepanel_payment(call.from_user.id, amount, call.message)


@dp.callback_query(F.data == "fp_custom")
async def freepanel_custom_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "💰 <b>Custom Amount</b>\n\nEnter amount in INR.\nMinimum: ₹10\nMaximum: ₹5,000",
        reply_markup=back_kb()
    )
    await state.set_state(UserStates.wait_for_freepanel_amount)


@dp.message(UserStates.wait_for_freepanel_amount)
async def freepanel_custom_amount(m: Message, state: FSMContext):
    try:
        amount = float(m.text.strip())
        if amount < 10:
            return await m.answer("❌ Minimum amount is ₹10.")
        if amount > 5000:
            return await m.answer("❌ Maximum amount is ₹5,000.")
        await state.clear()
        await start_freepanel_payment(m.from_user.id, amount, m)
    except (ValueError, TypeError):
        await m.answer("❌ Please enter a valid amount, e.g. 100 or 250.50")


async def start_freepanel_payment(user_id: int, amount_inr: float, message_obj):
    result, error = await create_freepanel_order(user_id, amount_inr)
    if error:
        text = f"❌ <b>FamPay Payment</b>\n\n{error}"
        if isinstance(message_obj, Message):
            await message_obj.answer(text, reply_markup=back_kb())
        else:
            await message_obj.edit_text(text, reply_markup=back_kb())
        return

    order_id = result["order_id"]
    payment_link = result["payment_link"]
    ts = int(time.time())

    db_query(
        "INSERT INTO deposits (user_id, amount, screenshot_file_id, status, timestamp, gateway, gateway_order_id, payment_link) VALUES (?, ?, NULL, 'payment_created', ?, 'FreePanel', ?, ?)",
        (user_id, amount_inr, ts, order_id, payment_link)
    )

    text = (
        f"💳 <b>FAMPAY UPI PAYMENT</b>\n\n"
        f"💰 <b>Amount:</b> {fmt_curr(amount_inr)}\n"
        f"🧾 <b>Order ID:</b> <code>{order_id}</code>\n\n"
        "📲 Scan the QR below or tap <b>Pay Now</b>.\n"
        "💡 Pay the exact amount.\n"
        "⏱️ Payment/order validity depends on the gateway.\n\n"
        "After payment, tap <b>Check Status</b>."
    )
    kb = freepanel_payment_kb(order_id, payment_link)
    qr_bytes = make_upi_qr_bytes(payment_link)
    photo = BufferedInputFile(qr_bytes, filename=f"payment_{order_id}.png")

    if isinstance(message_obj, Message):
        await message_obj.answer_photo(photo=photo, caption=text, reply_markup=kb)
    else:
        await message_obj.delete()
        await bot.send_photo(user_id, photo=photo, caption=text, reply_markup=kb)


async def get_freepanel_payment_status(order_id: str):
    """Check payment status only when an exact status URL is configured.

    Set FREEPANEL_STATUS_API_URL to the exact endpoint from your FreePanel/FamAPI docs.
    Use {order_id} in the URL if the endpoint needs the order ID in its path.
    """
    status_url = get_freepanel_status_url()
    if not status_url:
        return None, "Payment status API URL is not configured."

    url = status_url.replace("{order_id}", urllib.parse.quote(str(order_id), safe=""))
    headers = {
        "Authorization": f"Bearer {get_freepanel_api_key()}",
        "Content-Type": "application/json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {"raw": raw}
                if resp.status < 200 or resp.status >= 300:
                    logging.error("FreePanel status failed: %s %s", resp.status, raw[:1000])
                    return None, f"Status API error ({resp.status})."
                return data, None
    except Exception:
        logging.exception("FreePanel status error")
        return None, "Unable to check payment status right now."


def extract_payment_status(data):
    """Extract a status string from common response shapes without assuming one schema."""
    if not isinstance(data, dict):
        return None
    keys = ["status", "payment_status", "paymentStatus", "state"]
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value.lower().strip()
    for container_key in ("data", "order", "result", "payment"):
        container = data.get(container_key)
        if isinstance(container, dict):
            found = extract_payment_status(container)
            if found:
                return found
    return None


@dp.callback_query(F.data.startswith("fp_status_"))
async def freepanel_check_status(call: CallbackQuery):
    order_id = call.data[len("fp_status_"):]
    dep = db_query(
        "SELECT id, user_id, amount, status FROM deposits WHERE gateway='FreePanel' AND gateway_order_id=? ORDER BY id DESC LIMIT 1",
        (order_id,), fetchone=True
    )
    if not dep or dep[1] != call.from_user.id:
        return await call.answer("❌ Payment order not found.", show_alert=True)

    if dep[3] == "approved":
        return await call.answer("✅ Payment already credited.", show_alert=True)

    data, error = await get_freepanel_payment_status(order_id)
    if error:
        await call.answer("⚠️ Status API is not configured yet.", show_alert=True)
        await call.message.answer(
            "⚠️ <b>Automatic verification is not enabled yet.</b>\n\n"
            "The QR/payment order was created successfully, but this bot needs the exact FreePanel payment-status API endpoint from your account/docs before it can safely auto-credit balance.\n\n"
            f"Order ID: <code>{order_id}</code>\n"
            f"Amount: <b>{fmt_curr(dep[2])}</b>",
            reply_markup=main_menu_kb()
        )
        return

    status = extract_payment_status(data)
    if status in {"paid", "success", "successful", "completed", "complete", "captured", "approved"}:
        # Credit exactly once.
        current = db_query("SELECT status FROM deposits WHERE id=?", (dep[0],), fetchone=True)
        if not current or current[0] == "approved":
            return await call.answer("✅ Payment already credited.", show_alert=True)
        db_query("UPDATE deposits SET status='approved' WHERE id=? AND status!='approved'", (dep[0],))
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (dep[2], call.from_user.id))
        await call.answer("✅ Payment verified! Balance added.", show_alert=True)
        await call.message.edit_text(
            f"✅ <b>PAYMENT VERIFIED</b>\n\n"
            f"💰 Amount: <b>{fmt_curr(dep[2])}</b>\n"
            f"🧾 Order ID: <code>{order_id}</code>\n\n"
            "Your balance has been credited.",
            reply_markup=main_menu_kb()
        )
        await send_advanced_notification(call.from_user.id, "DEPOSIT", dep[2], product=order_id, gateway="FreePanel")
        return

    if status in {"failed", "failure", "cancelled", "canceled", "expired", "rejected"}:
        db_query("UPDATE deposits SET status=? WHERE id=? AND status!='approved'", (status, dep[0]))
        await call.answer(f"❌ Payment status: {status}", show_alert=True)
        return

    await call.answer(f"⏳ Payment status: {status or 'pending'}", show_alert=True)


@dp.callback_query(F.data == "menu_add_balance")
async def select_gateway_menu(call: CallbackQuery):
    bal = db_query("SELECT balance FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)[0]
    text = (
    f"✨ <b>ADD BALANCE</b> ✨\n\n"
    f"💳 <b>Current Balance:</b> {fmt_curr(bal)}\n\n"
    "✨ Select Your Preferred Payment Method.\n\n"
    "┣ 💳 <b>UPI</b> — Fast Indian Payments\n"
    "┣ 🪙 <b>Binance</b> — USDT Payments\n\n"
    "✨ Payments are verified securely."
)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 FreePanel", callback_data="gateway_freepanel")],
        [InlineKeyboardButton(text="💵 UPI", callback_data="gateway_inr"), InlineKeyboardButton(text="🪙 Binance (USDT)", callback_data="gateway_crypto")],
        [InlineKeyboardButton(text="« Back to Menu", callback_data="back_main")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

# ---- MANUAL UPI FLOW (screenshot verification by admin) ----
@dp.callback_query(F.data == "gateway_inr")
async def add_balance_inr(call: CallbackQuery):
    text = f"💵 <b> ADD BALANCE </b> 💵\n\nSelect Amount To Deposit:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₹10", callback_data="pay_10"), InlineKeyboardButton(text="₹50", callback_data="pay_50")],
        [InlineKeyboardButton(text="₹100", callback_data="pay_100"), InlineKeyboardButton(text="₹300", callback_data="pay_300")],
        [InlineKeyboardButton(text="✏️ Enter Custom Amount", callback_data="custom_deposit_btn")],
        [InlineKeyboardButton(text="« Back", callback_data="menu_add_balance")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "custom_deposit_btn")
async def custom_deposit_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("💰 <b>Custom Deposit</b>\n\nPlease enter the amount (in INR):\n⚠️ <i>Minimum ₹10 is required.</i>", reply_markup=back_kb())
    await state.set_state(UserStates.wait_for_custom_amount)

@dp.message(UserStates.wait_for_custom_amount)
async def process_custom_amount(m: Message, state: FSMContext):
    try:
        inr_amount = float(m.text)
        if inr_amount < 10:  
            await m.answer("❌ You must add at least <b>₹10</b>.")
            return
        await state.clear()
        await show_upi_payment_details(m.from_user.id, inr_amount, m)
    except ValueError:
        await m.answer("❌ Please type numbers only.")

@dp.callback_query(F.data.startswith("pay_"))
async def process_amount_selected(call: CallbackQuery):
    inr_amount = float(call.data.split("_")[1])
    await show_upi_payment_details(call.from_user.id, inr_amount, call.message)

async def show_upi_payment_details(user_id, inr_amount, message_obj: Message):
    upi_check = db_query("SELECT value FROM settings WHERE key='upi_id'", fetchone=True)
    if not upi_check or not upi_check[0]:
        text = "⚠️ UPI payments are currently offline. Admin needs to set a UPI ID."
        if isinstance(message_obj, Message) and message_obj.from_user and message_obj.from_user.is_bot:
            await message_obj.edit_text(text, reply_markup=back_kb())
        else:
            await message_obj.answer(text, reply_markup=back_kb())
        return

    upi_id = upi_check[0]
    text = (
        f"💵 <b>PAY VIA UPI</b> 💵\n\n"
        f"Amount: <b>{fmt_curr(inr_amount)}</b>\n\n"
        f"👇 <b>Pay to this UPI ID:</b>\n<code>{upi_id}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"1️⃣ Open any UPI app and pay the above amount to this UPI ID.\n"
        f"2️⃣ After paying, tap <b>Verify Payment</b> below.\n"
        f"3️⃣ Send a <b>screenshot</b> of the successful payment.\n"
        f"4️⃣ Admin will review and add your balance shortly."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Verify Payment", callback_data=f"verify_pay_{inr_amount}")],
        [InlineKeyboardButton(text="« Cancel", callback_data="menu_add_balance")]
    ])

    if isinstance(message_obj, Message) and message_obj.from_user and message_obj.from_user.is_bot:
        await message_obj.edit_text(text, reply_markup=kb)
    else:
        await message_obj.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("verify_pay_"))
async def verify_payment_start(call: CallbackQuery, state: FSMContext):
    inr_amount = float(call.data.split("_", 2)[2])
    await state.update_data(deposit_amount=inr_amount)
    await call.message.edit_text(
        f"📸 <b>Send a screenshot</b> of your payment of {fmt_curr(inr_amount)}.\n\nYour deposit will be added once admin approves it.",
        reply_markup=back_kb()
    )
    await state.set_state(UserStates.wait_for_payment_screenshot)

@dp.message(UserStates.wait_for_payment_screenshot, F.photo)
async def process_payment_screenshot(m: Message, state: FSMContext):
    data = await state.get_data()
    inr_amount = data.get('deposit_amount')
    if inr_amount is None:
        await state.clear()
        return await m.answer("❌ Something went wrong. Please start again from Add Balance.", reply_markup=main_menu_kb())

    file_id = m.photo[-1].file_id
    user_id = m.from_user.id
    ts = int(time.time())
    db_query("INSERT INTO deposits (user_id, amount, screenshot_file_id, status, timestamp) VALUES (?, ?, ?, 'pending', ?)",
              (user_id, inr_amount, file_id, ts))
    dep_id = db_query("SELECT id FROM deposits WHERE user_id=? AND timestamp=? ORDER BY id DESC LIMIT 1", (user_id, ts), fetchone=True)[0]

    await m.answer("✅ <b>Screenshot received!</b>\n\nYour deposit is pending admin approval. You'll be notified once it's reviewed.", reply_markup=main_menu_kb())
    await state.clear()

    user_info = db_query("SELECT first_name, phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    name = user_info[0] if user_info else "Unknown"
    phone = user_info[1] if user_info and user_info[1] else "Not Provided"
    try:
        chat = await bot.get_chat(user_id)
        username = f"@{chat.username}" if chat.username else "None"
    except:
        username = "None"

    caption = (
        f"💰 <b>NEW DEPOSIT REQUEST</b> 💰\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: {name}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📱 Phone: {phone}\n"
        f"👤 Username: {username}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Amount: {fmt_curr(inr_amount)}\n"
        f"🆔 Deposit ID: <code>{dep_id}</code>\n"
        f"📅 Time: {datetime.now().strftime('%d-%m-%Y %I:%M %p')}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Add Balance", callback_data=f"dep_approve_{dep_id}"),
         InlineKeyboardButton(text="❌ Reject", callback_data=f"dep_reject_{dep_id}")]
    ])
    try:
        await bot.send_photo(ADMIN_ID, file_id, caption=caption, reply_markup=kb)
    except Exception as e:
        logging.error(f"Failed to send deposit notif: {e}")

@dp.message(UserStates.wait_for_payment_screenshot)
async def process_payment_screenshot_invalid(m: Message):
    await m.answer("📸 Please send a <b>screenshot image</b> of your payment (not text).")

@dp.callback_query(F.data.startswith("dep_approve_"))
async def admin_approve_deposit(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    dep_id = int(call.data.split("_")[2])
    dep = db_query("SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,), fetchone=True)
    if not dep:
        return await call.answer("❌ Deposit not found.", show_alert=True)
    if dep[2] != 'pending':
        return await call.answer(f"⚠️ Already {dep[2]}.", show_alert=True)

    user_id, amount = dep[0], dep[1]
    db_query("UPDATE deposits SET status='approved' WHERE id=?", (dep_id,))
    db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))

    gateway_row = db_query("SELECT gateway, gateway_order_id FROM deposits WHERE id=?", (dep_id,), fetchone=True)
    gateway_name = gateway_row[0] if gateway_row and gateway_row[0] else "UPI"
    gateway_ref = gateway_row[1] if gateway_row and gateway_row[1] else str(dep_id)
    try:
        await bot.send_message(user_id, f"🎉 <b>DEPOSIT APPROVED!</b>\n\n✅ {fmt_curr(amount)} has been added to your balance.\n💳 Gateway: {gateway_name}\n🧾 Reference: <code>{gateway_ref}</code>", reply_markup=main_menu_kb())
    except: pass

    await call.answer("✅ Balance added!", show_alert=True)
    try:
        await call.message.edit_caption(caption=call.message.caption + "\n\n✅ <b>APPROVED — Balance Added</b>", reply_markup=None)
    except: pass

@dp.callback_query(F.data.startswith("dep_reject_"))
async def admin_reject_deposit(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    dep_id = int(call.data.split("_")[2])
    dep = db_query("SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,), fetchone=True)
    if not dep:
        return await call.answer("❌ Deposit not found.", show_alert=True)
    if dep[2] != 'pending':
        return await call.answer(f"⚠️ Already {dep[2]}.", show_alert=True)

    user_id, amount = dep[0], dep[1]
    db_query("UPDATE deposits SET status='rejected' WHERE id=?", (dep_id,))

    try:
        await bot.send_message(user_id, f"❌ <b>DEPOSIT REJECTED</b>\n\nYour payment of {fmt_curr(amount)} could not be verified. Contact {ADMIN_CONTACT} if you believe this is an error.", reply_markup=main_menu_kb())
    except: pass

    await call.answer("❌ Deposit rejected.", show_alert=True)
    try:
        await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>REJECTED</b>", reply_markup=None)
    except: pass

# ---- BINANCE CRYPTO FLOW ----
@dp.callback_query(F.data == "gateway_crypto")
async def add_balance_crypto(call: CallbackQuery, state: FSMContext):
    address_check = db_query("SELECT value FROM settings WHERE key='binance_address'", fetchone=True)
    if not address_check or not address_check[0]:
        return await call.message.edit_text("⚠️ Binance Gateway is currently offline. Admin has not set a deposit address.", reply_markup=back_kb())
        
    deposit_address = address_check[0]
    
    msg = (f"🪙 <b>— BINANCE USDT DEPOSIT —</b> 🪙\n\n"
           f"💵 <b>Exchange Rate:</b> 1 USDT = ₹{USDT_TO_INR}\n"
           f"⚠️ <b>Network:</b> Please send via <b>TRC20</b> or <b>BEP20</b>.\n\n"
           f"👇 <b>Send your USDT to this exact address:</b>\n"
           f"<code>{deposit_address}</code>\n\n"
           f"━━━━━━━━━━━━━━━━━━\n"
           f"✅ <b>After sending the USDT, reply to this message with your exact TxID (Transaction Hash) to instantly claim your balance.</b>")
           
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Cancel", callback_data="menu_add_balance")]])
    await call.message.edit_text(msg, reply_markup=kb)
    await state.set_state(UserStates.wait_for_crypto_txid)

@dp.message(UserStates.wait_for_crypto_txid)
async def process_crypto_txid(m: Message, state: FSMContext):
    txid = m.text.strip()
    user_id = m.from_user.id
    
    if len(txid) < 10:
        return await m.answer("❌ That doesn't look like a valid TxID. Please try again or type /cancel.")
        
    # Check if TXID already used
    if db_query("SELECT txid FROM crypto_txns WHERE txid=?", (txid,), fetchone=True):
        return await m.answer("⚠️ This Transaction ID has already been claimed!", reply_markup=back_kb())
        
    api_key_check = db_query("SELECT value FROM settings WHERE key='binance_api'", fetchone=True)
    secret_key_check = db_query("SELECT value FROM settings WHERE key='binance_secret'", fetchone=True)
    
    if not api_key_check or not secret_key_check:
        return await m.answer("⚠️ Binance API is missing on the server. Contact Admin.", reply_markup=back_kb())
        
    await m.answer("🔄 <b>Verifying your TxID with Binance Blockchain...</b>\n<i>Please wait...</i>")
    
    api_key = api_key_check[0]
    secret_key = secret_key_check[0]
    
    # Binance API Signature Logic
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    
    headers = {'X-MBX-APIKEY': api_key}
    url = f"https://api.binance.com/sapi/v1/capital/deposit/hisrec?{query_string}&signature={signature}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    history = await resp.json()
                    found = False
                    
                    for deposit in history:
                        # Status 1 = Success
                        if deposit.get("txId") == txid and deposit.get("status") == 1:
                            found = True
                            usdt_amount = float(deposit.get("amount"))
                            inr_amount = usdt_amount * USDT_TO_INR
                            
                            # Log transaction and credit user
                            db_query("INSERT INTO crypto_txns (txid, user_id, amount_usdt, timestamp) VALUES (?, ?, ?, ?)", (txid, user_id, usdt_amount, int(time.time())))
                            db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (inr_amount, user_id))
                            
                            await m.answer(f"🎉 <b>CRYPTO DEPOSIT SUCCESSFUL!</b>\n\n✅ We received <b>{usdt_amount} USDT</b>.\n💰 <b>{fmt_curr(inr_amount)}</b> has been added to your balance!", reply_markup=main_menu_kb())
                            
                            await send_advanced_notification(user_id, "DEPOSIT", inr_amount, product=txid, gateway="Binance Crypto")
                            await state.clear()
                            break
                            
                    if not found:
                        await m.answer("❌ <b>TxID Not Found or Still Pending!</b>\nMake sure the transaction is fully confirmed on the blockchain and you sent it to the correct address. Try again in 2 minutes.", reply_markup=back_kb())
                else:
                    await m.answer(f"⚠️ <b>Binance Server Error:</b> HTTP {resp.status}. Please tell admin.", reply_markup=back_kb())
        except Exception as e:
            await m.answer(f"⚠️ <b>Connection Error:</b> {str(e)}", reply_markup=back_kb())


# ==========================================
# 10.5 RESELLER KEY-GENERATION API (ON-DEMAND KEYS)
# ==========================================
RESELLER_API_URL = "https://bantibhaiya.com/api/reseller_v1.php"

async def generate_key_via_api(pid, duration, android_id=None):
    """Call the reseller API to generate a key on demand. Returns (key, error)."""
    api_key = get_setting('reseller_api_key')
    master_key = get_setting('reseller_master_key')
    if not api_key or not master_key:
        return None, "Reseller API is not configured. Contact admin."

    data = {'api_key': api_key, 'action': 'buy', 'product_id': pid}
    if duration:
        data['duration'] = duration
    if android_id:
        data['android_id'] = android_id

    headers = {
        'x-master-key': master_key,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(RESELLER_API_URL, data=data, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=20), ssl=False) as resp:
                raw = await resp.text()
    except Exception as e:
        return None, f"Connection error: {e}"

    try:
        j = json.loads(raw)
    except Exception:
        return None, f"Invalid API response: {raw[:300]}"

    # --- Flexible parser: adjust once you see the provider's real JSON format ---
    key = None
    if isinstance(j, dict):
        if isinstance(j.get('data'), dict):
            key = j['data'].get('key') or j['data'].get('license_key') or j['data'].get('license')
        key = key or j.get('key') or j.get('license_key') or j.get('license')
        if not key:
            return None, f"API error: {j.get('message') or j.get('error') or raw[:200]}"
    if key:
        return str(key), None
    return None, f"Key not found in API response: {raw[:300]}"

# ==========================================
# 11. SHOP & NESTED PRODUCTS UI
# ==========================================
@dp.callback_query(F.data == "menu_shop")
async def shop_categories(call: CallbackQuery):
    cats = db_query("SELECT DISTINCT category FROM products", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if not cats:
        kb.inline_keyboard.append([InlineKeyboardButton(text="« Back", callback_data="back_main")])
        await call.message.edit_text("🛒 Store is empty.", reply_markup=kb)
        return
        
    text = "✨ <b>Available Products</b>\n\n💎 Premium Keys\n⚡ Instant Delivery\n🔒 Secure Payment\n\n🛒 <b>Select a product below:</b>"
    for c in cats: 
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"➪ {str(c[0])}", callback_data=f"cat_{str(c[0])[:40]}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="« Back to Menu", callback_data="back_main")])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("cat_"))
async def view_products(call: CallbackQuery):
    cat_sliced = call.data.split("cat_", 1)[1]
    prods = db_query("SELECT id, name, price_inr, stock FROM products WHERE category LIKE ?", (f"{cat_sliced}%",), fetchall=True)
    if not prods:
        await call.answer("❌ Durations not found.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"🎮 🛒 <b>{cat_sliced.upper()}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    
    for p in prods:
        prod_id, duration_name, price_inr, stock = p
        price_usd = price_inr / 90.0  
        stock_status = "✅ In Stock" if stock > 0 else "❌ Out of Stock"
        
        text += f"⏱ <b>{duration_name}</b>\n💰 ${price_usd:.2f} ({fmt_curr(price_inr)})\n📦 {stock_status}\n\n"
        
        if stock > 0:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"📦 Buy {duration_name} - ${price_usd:.2f} ({fmt_curr(price_inr)})", callback_data=f"buy_{prod_id}")])
        else:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ {duration_name} (Out of Stock)", callback_data="ignore_stock_click")])
            
    text += "👇 <b>Select duration below:</b>"
    kb.inline_keyboard.append([InlineKeyboardButton(text="« Back to Shop", callback_data="menu_shop")])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "ignore_stock_click")
async def ignore_stock_click(call: CallbackQuery):
    await call.answer("⚠️ This duration is Out of Stock!", show_alert=True)

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(call: CallbackQuery, state: FSMContext):
    prod_id = int(call.data.split("_")[1])
    prod = db_query("SELECT name, price_inr, stock, apk_link, validity, device_limit, category, api_pid, api_duration, needs_android_id FROM products WHERE id=?", (prod_id,), fetchone=True)
    if not prod: return await call.answer("❌ Item not found!", show_alert=True)

    user = db_query("SELECT balance FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)

    # Insufficient balance => show Add Balance option
    if user[0] < prod[1]:
        insufficient_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Add Balance", callback_data="menu_add_balance")],
            [InlineKeyboardButton(text="« Back to Shop", callback_data="menu_shop")]
        ])
        await call.message.edit_text(
            f"❌ <b>Insufficient Balance!</b>\n\n"
            f"You need {fmt_curr(prod[1])} to buy this product.\n"
            f"Your current balance: {fmt_curr(user[0])}\n\n"
            f"👉 Please add balance to continue.",
            reply_markup=insufficient_kb
        )
        return

    # Device-bound (API) product: collect Android ID before generating the key
    if prod[7] and prod[9]:
        await state.update_data(pending_prod_id=prod_id)
        await call.message.edit_text(
            "📱 <b>DEVICE-BOUND KEY</b>\n\nThis key is locked to one device.\nSend your <b>Android ID</b> now (find it in the app / device settings):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Cancel", callback_data="menu_shop")]])
        )
        await state.set_state(UserStates.wait_for_android_id)
        return

    await state.clear()
    await complete_purchase(call.message, prod_id, android_id=None)

@dp.message(UserStates.wait_for_android_id)
async def process_android_id(m: Message, state: FSMContext):
    data = await state.get_data()
    prod_id = data.get('pending_prod_id')
    if not prod_id:
        await state.clear()
        return await m.answer("❌ Session expired. Please start again from Shop.", reply_markup=main_menu_kb())
    android_id = m.text.strip()
    if len(android_id) < 6:
        return await m.answer("❌ That doesn't look like a valid Android ID. Try again:")
    await state.clear()
    await complete_purchase(m, prod_id, android_id=android_id)

async def complete_purchase(message_obj, prod_id, android_id=None):
    user_id = message_obj.chat.id
    prod = db_query("SELECT name, price_inr, stock, apk_link, validity, device_limit, category, api_pid, api_duration, needs_android_id FROM products WHERE id=?", (prod_id,), fetchone=True)
    if not prod:
        return await message_obj.answer("❌ Product no longer exists.", reply_markup=back_kb())

    user = db_query("SELECT balance FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not user or user[0] < prod[1]:
        return await message_obj.answer("❌ Insufficient balance. Please add balance first.", reply_markup=back_kb())

    db_query("UPDATE users SET balance=?, spent=spent+?, orders_count=orders_count+1 WHERE user_id=?", (user[0] - prod[1], prod[1], user_id))

    delivered_key = ""

    if prod[7]:  # API-generated product (has api_pid)
        delivered_key, api_error = await generate_key_via_api(prod[7], prod[8] or prod[4], android_id)
        if not delivered_key:
            # Refund: user is never charged for a failed key generation
            db_query("UPDATE users SET balance=balance+?, spent=spent-?, orders_count=orders_count-1 WHERE user_id=?", (prod[1], prod[1], user_id))
            await message_obj.answer(
                f"⚠️ <b>Key generation failed — you were NOT charged.</b>\n\n{api_error}\nTry again or contact {ADMIN_CONTACT}.",
                reply_markup=back_kb()
            )
            try:
                await bot.send_message(ADMIN_ID, f"🚨 <b>API KEY GEN FAILED</b>\nProduct: {prod[6]} ({prod[0]})\nUser: <code>{user_id}</code>\nError: {api_error}")
            except: pass
            return
    elif prod[2] > 0:  # static stock fallback
        key_data = db_query("SELECT id, key_text FROM product_keys WHERE product_id=? AND is_used=0 LIMIT 1", (prod_id,), fetchone=True)
        if key_data:
            delivered_key = key_data[1]
            db_query("UPDATE product_keys SET is_used=1 WHERE id=?", (key_data[0],))
            db_query("UPDATE products SET stock=stock-1 WHERE id=?", (prod_id,))

            # Stock out alert
            new_stock = db_query("SELECT stock FROM products WHERE id=?", (prod_id,), fetchone=True)[0]
            if new_stock == 0:
                await send_stock_alert(prod_id, prod[6], prod[0], user_id)
        else:
            delivered_key = "OUT_OF_STOCK_CONTACT_ADMIN"
    else:
        delivered_key = "OUT_OF_STOCK_CONTACT_ADMIN"

    db_query("INSERT INTO orders (user_id, product_name, price_paid, delivered_key, purchase_date) VALUES (?, ?, ?, ?, ?)", (user_id, f"{prod[6]} ({prod[0]})", prod[1], delivered_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    await send_advanced_notification(user_id, "ORDER", prod[1], product=f"{prod[6]} ({prod[0]})", key=delivered_key)

    msg = f"✅ <b>PURCHASE SUCCESSFUL!</b>\n━━━━━━━━━━━━━━━━━━\n📦 <b>Product:</b> {prod[6]} ({prod[0]})\n⏳ <b>Validity:</b> {prod[4]}\n📱 <b>Device Limit:</b> {prod[5]}\n━━━━━━━━━━━━━━━━━━\n"
    if prod[3] and prod[3].startswith("http"): msg += f"📥 <b>APK Link:</b> <a href='{prod[3]}'>Download Here</a>\n\n"

    if "OUT_OF_STOCK" in delivered_key:
        msg += f"⚠️ <b>STOCK OUT</b>\nAmount deducted, but key is out of stock. Contact Admin: {ADMIN_CONTACT}\n"
    else:
        msg += f"🔑 <b>Your Key:</b> <code>{delivered_key}</code>\n\n<i>Contact admin for issues: {ADMIN_CONTACT}</i>"

    await message_obj.answer(msg, reply_markup=back_kb(), disable_web_page_preview=True)

# ==========================================
# 12. USER DASHBOARD & COUPONS
# ==========================================
@dp.callback_query(F.data == "menu_orders")
async def my_orders(call: CallbackQuery):
    orders = db_query("SELECT product_name, delivered_key, purchase_date FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (call.from_user.id,), fetchall=True)
    if not orders: return await call.message.edit_text("🧾 You haven't made any purchases yet.", reply_markup=back_kb())
    text = "🧾 <b>— YOUR RECENT ORDERS —</b> 🧾\n\n"
    for o in orders: text += f"📦 {o[0]}\n🔑 <code>{o[1]}</code>\n📅 {o[2]}\n\n"
    await call.message.edit_text(text, reply_markup=back_kb())

@dp.callback_query(F.data == "menu_profile")
async def show_profile(call: CallbackQuery):
    u = db_query("SELECT user_id, first_name, balance, orders_count, spent, joined_date FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    text = (f"👤 <b>— YOUR PROFILE —</b> 👤\n\n🆔 <b>User ID:</b> <code>{u[0]}</code>\n📛 <b>Name:</b> {u[1]}\n\n"
            f"💰 <b>— Balance —</b>\n💳 <b>Current:</b> {fmt_curr(u[2])}\n\n📊 <b>— Statistics —</b>\n📦 <b>Orders:</b> {u[3]}\n💸 <b>Spent:</b> {fmt_curr(u[4])}\n\n📅 <b>Joined:</b> {u[5]}")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎟 Redeem Coupon", callback_data="redeem_coupon")], [InlineKeyboardButton(text="« Back to Menu", callback_data="back_main")]])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "redeem_coupon")
async def redeem_coupon_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("🎟 <b>Please enter your redeem code below:</b>", reply_markup=back_kb())
    await state.set_state(UserStates.wait_for_redeem)

@dp.message(UserStates.wait_for_redeem)
async def process_redeem(m: Message, state: FSMContext):
    code = m.text.strip().upper()
    user_id = m.from_user.id
    if db_query("SELECT * FROM redeemed WHERE user_id=? AND code=?", (user_id, code), fetchone=True):
        await m.answer("❌ You already redeemed this code!", reply_markup=main_menu_kb())
        await state.clear(); return
        
    coupon = db_query("SELECT amount, uses_left FROM coupons WHERE code=?", (code,), fetchone=True)
    if not coupon: 
        await m.answer("❌ Invalid code!", reply_markup=main_menu_kb())
    elif coupon[1] <= 0: 
        await m.answer("❌ Code is fully claimed.", reply_markup=main_menu_kb())
    else:
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (coupon[0], user_id))
        db_query("UPDATE coupons SET uses_left = uses_left - 1 WHERE code=?", (code,))
        db_query("INSERT INTO redeemed (user_id, code) VALUES (?, ?)", (user_id, code))
        await m.answer(f"🎉 <b>Success!</b>\nAdded {fmt_curr(coupon[0])} to your balance!", reply_markup=main_menu_kb())
        
        try:
            user_info = db_query("SELECT first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
            uname = user_info[0] if user_info else "Unknown User"
            await bot.send_message(ADMIN_ID, f"🎟 <b>COUPON REDEEMED!</b>\n👤 User: {uname} (<code>{user_id}</code>)\n🔖 Code: <b>{code}</b>\n💵 Amount: {fmt_curr(coupon[0])}")
        except Exception as e: pass

    await state.clear()

@dp.callback_query(F.data == "menu_how_to")
async def how_to_buy(call: CallbackQuery):
    await call.message.edit_text(
        " 🛒<b>— HOW TO BUY KEY </b> 🛒\n\n1️⃣ Add Balance tutorial \n2️⃣ Open Product Store\n3️⃣ Select Your Panel \n4️⃣ Buy & Receive Key Instantly\n\n📺 Watch the full video",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📺 Watch Full Video",
                        url="https://youtu.be/OYE2BbDzb5M"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="« Back to Menu",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )

@dp.callback_query(F.data == "menu_Update")
async def all_panel_update(call: CallbackQuery):
    await call.message.edit_text(
        "🚀 <b>ALL PANEL APK & FILES </b> 💎\n\n⚡Fast Downloads • Daily Updates \n🛡️ 100% Safe, File\n📖 Complete Setup Guide for Easy\n\n━━━━━━━━━━━━━━━━━━━━━━━\n👇 <b>Tap the Button Below & Start Downloading!</b> 📥",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📥 Download Apk",
                        url="https://t.me/+G_WxZLKjWO80MmE1"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="« Back to Menu",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )

@dp.callback_query(F.data == "menu_Feed")
async def selling_proof(call: CallbackQuery):
    await call.message.edit_text(
"🛒 <b>SELLING PROOF</b> 📦\n\n100% Genuine Selling Proofs.\nTrusted By Hundreds Of Happy Customers ✅\n\n━━━━━━━━━━━━━━━━━━━━━━━\n💬 <b>CUSTOMER FEEDBACK</b> ⭐\n\nReal Reviews From Satisfied Customers.\nYour Trust Our Biggest Achievement ❤️",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌹 Selling Proof-Feedback",
                        url="https://t.me/+74ehSpy2msVlYTJl"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="« Back to Menu",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )

@dp.callback_query(F.data == "menu_support")
async def support_center(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎫 Open Ticket", callback_data="open_ticket"), InlineKeyboardButton(text="📋 My Tickets", callback_data="my_tickets")], [InlineKeyboardButton(text="« Back", callback_data="back_main")]])
    await call.message.edit_text("📞 <b>— SUPPORT CENTER —</b> 📞\n\nNeed help? Open a ticket.", reply_markup=kb)

@dp.callback_query(F.data == "my_tickets")
async def view_my_tickets(call: CallbackQuery):
    tickets = db_query("SELECT id, message, status FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 5", (call.from_user.id,), fetchall=True)
    if not tickets:
        await call.message.edit_text("📋 You do not have any support tickets.", reply_markup=back_kb())
        return
        
    text = "📋 <b>— Your Recent Tickets —</b> 📋\n\n"
    for t in tickets:
        status_icon = "🟢" if t[2] == 'Open' else "🔴"
        text += f"🎫 <b>Ticket #{t[0]}</b> | Status: {status_icon} <b>{t[2]}</b>\n📝 <i>{t[1][:80]}...</i>\n\n"
        
    await call.message.edit_text(text, reply_markup=back_kb())

@dp.callback_query(F.data == "open_ticket")
async def open_ticket_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("📝 <b>Type your message below:</b>", reply_markup=back_kb())
    await state.set_state(UserStates.wait_for_ticket)

@dp.message(UserStates.wait_for_ticket)
async def process_ticket(m: Message, state: FSMContext):
    db_query("INSERT INTO tickets (user_id, message) VALUES (?, ?)", (m.from_user.id, m.text))
    await m.answer("✅ <b>Ticket Submitted!</b>", reply_markup=main_menu_kb())
    
    try: await bot.send_message(ADMIN_ID, f"🚨 <b>NEW TICKET</b>\nFrom: <code>{m.from_user.id}</code>\nMsg: {m.text}")
    except: pass
    
    await state.clear()

# ==========================================
# 13. ADMIN PANEL: SETUPS
# ==========================================
@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    await message.answer(admin_dashboard_text(), reply_markup=admin_dashboard_kb())

@dp.callback_query(F.data == "admin_panel_back")
async def back_to_admin(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("⚙️ <b>Admin Control Panel</b>", reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    res = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('bot_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

# --- UPI ADMIN SETUP ---
@dp.callback_query(F.data == "admin_setup_upi")
async def setup_upi_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💳 Send me the <b>UPI ID</b> users should pay to (e.g. yourname@upi):\n<i>(Type /cancel to abort)</i>", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.wait_for_upi_id)

@dp.message(AdminStates.wait_for_upi_id)
async def save_upi_id(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear(); return await m.answer("Cancelled.", reply_markup=admin_kb())
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('upi_id', ?)", (m.text.strip(),))
    await m.answer("✅ <b>UPI ID Saved!</b>", reply_markup=admin_kb())
    await state.clear()

# --- BINANCE ADMIN SETUP ---
@dp.callback_query(F.data == "admin_setup_binance")
async def setup_binance_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🪙 <b>Step 1/3:</b> Send me your <b>Binance API Key</b>:\n<i>(Type /cancel to abort)</i>", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.wait_for_binance_api)

@dp.message(AdminStates.wait_for_binance_api)
async def setup_binance_api(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear(); return await m.answer("Cancelled.", reply_markup=admin_kb())
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_api', ?)", (m.text.strip(),))
    await m.answer("🪙 <b>Step 2/3:</b> Now send me your <b>Binance Secret Key</b>:")
    await state.set_state(AdminStates.wait_for_binance_secret)

@dp.message(AdminStates.wait_for_binance_secret)
async def setup_binance_secret(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_secret', ?)", (m.text.strip(),))
    await m.answer("🪙 <b>Step 3/3:</b> Finally, send your <b>USDT Deposit Address (TRC20/BEP20)</b>\nThis is what users will see to send payments:")
    await state.set_state(AdminStates.wait_for_binance_address)

@dp.message(AdminStates.wait_for_binance_address)
async def setup_binance_address(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_address', ?)", (m.text.strip(),))
    await m.answer("✅ <b>Binance Setup Complete!</b> Gateway is now fully active.", reply_markup=admin_kb())
    await state.clear()


# --- RESELLER API ADMIN SETUP ---
@dp.callback_query(F.data == "admin_setup_reseller")
async def setup_reseller_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🔑 <b>Step 1/2:</b> Send your <b>Reseller API Key</b>:\n<i>(Type /cancel to abort)</i>", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.wait_for_reseller_api_key)

@dp.message(AdminStates.wait_for_reseller_api_key)
async def setup_reseller_api_key(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear(); return await m.answer("Cancelled.", reply_markup=admin_kb())
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('reseller_api_key', ?)", (m.text.strip(),))
    await m.answer("🔑 <b>Step 2/2:</b> Now send your <b>x-master-key</b>:")
    await state.set_state(AdminStates.wait_for_reseller_master_key)

@dp.message(AdminStates.wait_for_reseller_master_key)
async def setup_reseller_master_key(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('reseller_master_key', ?)", (m.text.strip(),))
    await m.answer("✅ <b>Reseller API configured!</b> Now set each product's API PID from Manage Products.", reply_markup=admin_kb())
    await state.clear()


# --- FREE PANEL ADMIN SETUP ---
def _masked(key: str) -> str:
    if not key:
        return "<i>(not set)</i>"
    return f"<code>{key[:14]}…{key[-4:]}</code>" if len(key) > 20 else f"<code>{key}</code>"

@dp.callback_query(F.data == "admin_setup_freepanel")
async def setup_freepanel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        f"💠 <b>FreePanel / FamPay Setup — Step 1/3</b>\n\n"
        f"Current API key: {_masked(get_freepanel_api_key())}\n\n"
        f"Send your <b>FreePanel API Key</b> (starts with <code>FAM_LIVE_sk_...</code>):\n"
        f"<i>(Type /cancel to abort)</i>",
        reply_markup=admin_back_kb())
    await state.set_state(AdminStates.wait_for_freepanel_api_key)

@dp.message(AdminStates.wait_for_freepanel_api_key)
async def setup_freepanel_api_key(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear(); return await m.answer("Cancelled.", reply_markup=admin_kb())
    key = m.text.strip()
    if not key.startswith("FAM_LIVE_sk_"):
        return await m.answer("⚠️ That doesn't look like a FreePanel key (should start with <code>FAM_LIVE_sk_</code>). Try again or /cancel:")
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('freepanel_api_key', ?)", (key,))
    await m.answer(
        "💠 <b>Step 2/3:</b> Send the <b>payment-status API URL</b> for checking orders.\n\n"
        "Use <code>{order_id}</code> where the order ID goes, e.g.:\n"
        "<code>https://py.freepanel.in/api/v1/orders/{order_id}/status</code>\n\n"
        "Type <b>none</b> to skip (deposits will then need manual approval).")
    await state.set_state(AdminStates.wait_for_freepanel_status_url)

@dp.message(AdminStates.wait_for_freepanel_status_url)
async def setup_freepanel_status_url(m: Message, state: FSMContext):
    url = m.text.strip()
    if url.lower() != 'none' and not url.startswith("http"):
        return await m.answer("❌ Must be a URL starting with http(s)://, or type <b>none</b>.")
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('freepanel_status_url', ?)",
             ("" if url.lower() == 'none' else url))
    await m.answer(
        f"💠 <b>Step 3/3:</b> Send the <b>redirect URL</b> users return to after paying.\n\n"
        f"Recommended: <code>{FREEPANEL_REDIRECT_URL_DEFAULT}</code> — type <b>default</b> to use it, or send your own URL.")
    await state.set_state(AdminStates.wait_for_freepanel_redirect_url)

@dp.message(AdminStates.wait_for_freepanel_redirect_url)
async def setup_freepanel_redirect_url(m: Message, state: FSMContext):
    url = m.text.strip()
    if url.lower() == 'default':
        url = FREEPANEL_REDIRECT_URL_DEFAULT
    if not url.startswith("http"):
        return await m.answer("❌ Must be a URL starting with http(s)://, or type <b>default</b>.")
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('freepanel_redirect_url', ?)", (url,))
    await m.answer("✅ <b>FreePanel Setup Complete!</b>\n💳 Gateway is now fully active.", reply_markup=admin_kb())
    await state.clear()

# ==========================================
# 13.5 ADMIN DASHBOARD & OPERATIONS
# ==========================================
def admin_dashboard_text():
    users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    products = db_query("SELECT COUNT(*) FROM products", fetchone=True)[0]
    stock = db_query("SELECT COALESCE(SUM(stock),0) FROM products", fetchone=True)[0]
    orders = db_query("SELECT COUNT(*) FROM orders", fetchone=True)[0]
    revenue = db_query("SELECT COALESCE(SUM(price_paid),0) FROM orders", fetchone=True)[0]
    pending = db_query("SELECT COUNT(*) FROM deposits WHERE status='pending'", fetchone=True)[0]
    pending_amount = db_query("SELECT COALESCE(SUM(amount),0) FROM deposits WHERE status='pending'", fetchone=True)[0]
    open_tickets = db_query("SELECT COUNT(*) FROM tickets WHERE status='Open'", fetchone=True)[0]
    balance = db_query("SELECT COALESCE(SUM(balance),0) FROM users", fetchone=True)[0]
    today = datetime.now().strftime('%Y-%m-%d')
    today_orders = db_query("SELECT COUNT(*), COALESCE(SUM(price_paid),0) FROM orders WHERE purchase_date LIKE ?", (today+'%',), fetchone=True)
    return (
        "📊 <b>ADMIN DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Users:</b> {users}\n"
        f"📦 <b>Products:</b> {products}\n"
        f"🔑 <b>Available Stock:</b> {stock}\n"
        f"🛒 <b>Total Orders:</b> {orders}\n"
        f"💰 <b>Total Sales:</b> ₹{revenue:.2f}\n"
        f"💳 <b>User Balances:</b> ₹{balance:.2f}\n"
        f"⏳ <b>Pending Deposits:</b> {pending} (₹{pending_amount:.2f})\n"
        f"🎫 <b>Open Tickets:</b> {open_tickets}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📅 <b>Today:</b> {today_orders[0]} orders / ₹{today_orders[1]:.2f}\n"
        f"🕐 <b>Server Time:</b> {datetime.now().strftime('%d-%m-%Y %I:%M %p')}"
    )


def admin_dashboard_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_dashboard")],
        [InlineKeyboardButton(text="💰 Pending Deposits", callback_data="admin_deposits"),
         InlineKeyboardButton(text="🎫 Open Tickets", callback_data="admin_view_tickets")],
        [InlineKeyboardButton(text="📦 Products", callback_data="admin_manage_prods"),
         InlineKeyboardButton(text="👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]
    ])


@dp.callback_query(F.data == "admin_dashboard")
async def admin_dashboard(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(admin_dashboard_text(), reply_markup=admin_dashboard_kb())
    await call.answer("Dashboard refreshed")


@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    total = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    verified = db_query("SELECT COUNT(*) FROM users WHERE phone IS NOT NULL AND phone != ''", fetchone=True)[0]
    top = db_query("SELECT first_name, user_id, balance, orders_count, spent FROM users ORDER BY spent DESC LIMIT 5", fetchall=True)
    text = f"👥 <b>USER MANAGEMENT</b>\n━━━━━━━━━━━━━━━━━━\n👤 Total Users: <b>{total}</b>\n📱 Verified: <b>{verified}</b>\n\n🏆 <b>Top Customers</b>\n"
    if top:
        for i, u in enumerate(top, 1):
            text += f"{i}. {u[0] or 'Unknown'} — <code>{u[1]}</code>\n   💰 Balance ₹{u[2]:.2f} | 🛒 {u[3]} orders | 💸 ₹{u[4]:.2f}\n"
    else:
        text += "No users yet.\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Dashboard", callback_data="admin_dashboard")],
        [InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]
    ])
    await call.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == "admin_deposits")
async def admin_deposits(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    deposits = db_query("SELECT id, user_id, amount, timestamp, gateway FROM deposits WHERE status='pending' ORDER BY id DESC LIMIT 10", fetchall=True)
    total = db_query("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM deposits WHERE status='pending'", fetchone=True)
    text = f"💰 <b>PENDING DEPOSITS</b>\n━━━━━━━━━━━━━━━━━━\n📋 Pending: {total[0]}\n💵 Amount: ₹{total[1]:.2f}\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if not deposits:
        text += "✅ No pending deposits."
    else:
        for d in deposits:
            text += f"🧾 <b>#{d[0]}</b> — ₹{d[2]:.2f} — {d[4] or 'UPI'} — User <code>{d[1]}</code>\n"
            kb.inline_keyboard.append([
                InlineKeyboardButton(text=f"✅ Approve #{d[0]}", callback_data=f"dep_approve_{d[0]}"),
                InlineKeyboardButton(text=f"❌ Reject #{d[0]}", callback_data=f"dep_reject_{d[0]}")
            ])
    kb.inline_keyboard += [
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_deposits")],
        [InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]
    ]
    await call.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == "admin_coupons")
async def admin_coupons(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    coupons = db_query("SELECT code, amount, uses_left FROM coupons ORDER BY rowid DESC LIMIT 15", fetchall=True)
    text = "🎟 <b>COUPON MANAGEMENT</b>\n━━━━━━━━━━━━━━━━━━\n"
    if not coupons:
        text += "No coupons created yet.\n"
    else:
        for c in coupons:
            status = "🟢 Active" if c[2] > 0 else "🔴 Used Up"
            text += f"<code>{c[0]}</code> — ₹{c[1]:.2f} — {c[2]} uses — {status}\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Create Coupon", callback_data="admin_create_coupon")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_coupons")],
        [InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]
    ])
    await call.message.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data == "admin_settings")
async def admin_settings(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    upi = db_query("SELECT value FROM settings WHERE key='upi_id'", fetchone=True)
    binance = db_query("SELECT value FROM settings WHERE key='binance_address'", fetchone=True)
    reseller = bool(get_setting('reseller_api_key') and get_setting('reseller_master_key'))
    freepanel = bool(get_freepanel_api_key())
    status = get_setting('bot_status') or 'ON'
    text = ("⚙️ <b>ADMIN SETTINGS</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Bot Status: <b>{status}</b>\n"
            f"💠 FreePanel: <b>{'Configured' if freepanel else 'Not configured'}</b>\n"
            f"💳 UPI: <b>{'Configured' if upi and upi[0] else 'Not configured'}</b>\n"
            f"🪙 Binance: <b>{'Configured' if binance and binance[0] else 'Not configured'}</b>\n"
            f"🔑 Reseller API: <b>{'Configured' if reseller else 'Not configured'}</b>\n\n"
            "Use the buttons below to update gateway settings.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 UPI Setup", callback_data="admin_setup_upi"),
         InlineKeyboardButton(text="🪙 Binance Setup", callback_data="admin_setup_binance")],
        [InlineKeyboardButton(text="💠 FreePanel Setup", callback_data="admin_setup_freepanel"),
         InlineKeyboardButton(text="🔑 Reseller API", callback_data="admin_setup_reseller")],
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_settings")],
        [InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]
    ])
    await call.message.edit_text(text, reply_markup=kb)


# ==========================================
# 14. ADMIN PANEL: PRODUCTS & MANAGEMENT
# ==========================================
@dp.callback_query(F.data == "admin_add_prod")
async def add_prod_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    cats = db_query("SELECT DISTINCT category FROM products", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if cats:
        for c in cats: kb.inline_keyboard.append([InlineKeyboardButton(text=f"📁 Add to: {c[0]}", callback_data=f"selcat_{str(c[0])[:40]}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="➕ Create NEW Main Product", callback_data="selcat_new")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")])
    await call.message.edit_text("<b>Step 1: Choose Main Product</b>\n\nClick a folder to add Durations.", reply_markup=kb)

@dp.callback_query(F.data.startswith("selcat_"))
async def process_cat_selection(call: CallbackQuery, state: FSMContext):
    cat_choice = call.data.split("selcat_", 1)[1]
    if cat_choice == "new":
        await call.message.edit_text("<b>Step 1:</b> Enter <b>NEW MAIN PRODUCT NAME</b>", reply_markup=admin_back_kb())
        await state.set_state(AdminStates.add_prod_category)
    else:
        full_cat = db_query("SELECT DISTINCT category FROM products WHERE category LIKE ?", (f"{cat_choice}%",), fetchone=True)
        cat_name = full_cat[0] if full_cat else cat_choice
        await state.update_data(cat=cat_name)
        await call.message.edit_text(f"✅ Selected: <b>{cat_name}</b>\n\n<b>Step 2:</b> Enter <b>DURATION / VARIANT NAME</b> (e.g. 7 Days)", reply_markup=admin_back_kb())
        await state.set_state(AdminStates.add_prod_name)

@dp.message(AdminStates.add_prod_category)
async def add_prod_cat(m: Message, state: FSMContext):
    await state.update_data(cat=m.text.strip())
    await m.answer("<b>Step 2:</b> Enter <b>DURATION / VARIANT NAME</b>")
    await state.set_state(AdminStates.add_prod_name)

@dp.message(AdminStates.add_prod_name)
async def add_prod_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await m.answer("⏳ Enter Product Validity (e.g., '1 Day', '30 Days', 'Lifetime'):")
    await state.set_state(AdminStates.add_prod_validity)

@dp.message(AdminStates.add_prod_validity)
async def add_prod_validity(m: Message, state: FSMContext):
    await state.update_data(validity=m.text.strip())
    await m.answer("📱 Enter Device Limit (e.g., '1 Device', '2 Devices'):")
    await state.set_state(AdminStates.add_prod_device_limit)

@dp.message(AdminStates.add_prod_device_limit)
async def add_prod_device_limit(m: Message, state: FSMContext):
    await state.update_data(device_limit=m.text.strip())
    await m.answer("💰 Enter Selling Price in Rupees (₹), e.g. 99 or 149.50:")
    await state.set_state(AdminStates.add_prod_price)

@dp.message(AdminStates.add_prod_price)
async def add_prod_price(m: Message, state: FSMContext):
    try:
        price = float(m.text.strip())
        if price < 0: raise ValueError
        await state.update_data(price=price)
        await m.answer("🔗 Enter APK Download Link (or type 'none'):")
        await state.set_state(AdminStates.add_prod_apk)
    except ValueError:
        await m.answer("❌ Invalid price. Please enter a number, e.g. 99 or 149.50:")

@dp.message(AdminStates.add_prod_apk)
async def add_prod_apk(m: Message, state: FSMContext):
    await state.update_data(apk="" if m.text.strip().lower() == 'none' else m.text.strip())
    await m.answer("🖼 Send the <b>Product Photo</b> now, or type <b>none</b> to skip:")
    await state.set_state(AdminStates.add_prod_photo)

@dp.message(AdminStates.add_prod_photo, F.photo)
async def add_prod_photo(m: Message, state: FSMContext):
    await state.update_data(photo_file_id=m.photo[-1].file_id)
    await m.answer("🆔 Enter the <b>API Product PID</b> manually.\n\nExample: <code>123</code>\nType <b>none</b> for a static-key product:")
    await state.set_state(AdminStates.add_prod_pid)

@dp.message(AdminStates.add_prod_photo)
async def add_prod_photo_skip(m: Message, state: FSMContext):
    if m.text and m.text.strip().lower() == 'none':
        await state.update_data(photo_file_id="")
        await m.answer("🆔 Enter the <b>API Product PID</b> manually.\n\nExample: <code>123</code>\nType <b>none</b> for a static-key product:")
        await state.set_state(AdminStates.add_prod_pid)
    else:
        await m.answer("🖼 Please send a product photo, or type <b>none</b> to skip.")

@dp.message(AdminStates.add_prod_pid)
async def add_prod_pid(m: Message, state: FSMContext):
    pid = m.text.strip()
    await state.update_data(api_pid="" if pid.lower() == 'none' else pid)
    await m.answer("⏱ Enter the <b>API Duration</b> manually.\n\nExamples: <code>1d</code>, <code>7d</code>, <code>30d</code>, <code>lifetime</code>\nType <b>none</b> if the API product does not need a duration:")
    await state.set_state(AdminStates.add_prod_duration)

@dp.message(AdminStates.add_prod_duration)
async def add_prod_duration(m: Message, state: FSMContext):
    duration = m.text.strip()
    await state.update_data(api_duration="" if duration.lower() == 'none' else duration)
    await m.answer("📱 Require <b>Android ID</b> for this product?\nReply <b>1</b> = Yes / <b>0</b> = No")
    await state.set_state(AdminStates.add_prod_androidid)

@dp.message(AdminStates.add_prod_androidid)
async def add_prod_androidid(m: Message, state: FSMContext):
    val = m.text.strip()
    if val not in ('0', '1'):
        return await m.answer("❌ Reply only <b>1</b> (Yes) or <b>0</b> (No):")
    await state.update_data(needs_android_id=int(val))
    data = await state.get_data()
    if data.get('api_pid'):
        await m.answer("📥 This is an API product. Static keys are optional.\nSend static keys one per line, or type <b>none</b> to skip:")
    else:
        await m.answer("📥 Send the <b>static keys</b> one per line, or type <b>none</b> if you will configure the API later:")
    await state.set_state(AdminStates.add_prod_keys)

@dp.message(AdminStates.add_prod_keys)
async def add_prod_keys(m: Message, state: FSMContext):
    raw = (m.text or '').strip()
    keys = [] if raw.lower() == 'none' else [k.strip() for k in raw.splitlines() if k.strip()]
    data = await state.get_data()
    # API products do not require static stock; static products require at least one key.
    if not data.get('api_pid') and not keys:
        return await m.answer("❌ No API PID is configured, so at least one static key is required. Send keys one per line, or go back and configure an API PID.")
    stock = len(keys)
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute("""INSERT INTO products
        (category, name, price_inr, stock, apk_link, validity, device_limit, api_pid, api_duration, needs_android_id, photo_file_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data['cat'], data['name'], data['price'], stock, data['apk'], data['validity'],
         data['device_limit'], data.get('api_pid',''), data.get('api_duration',''),
         data.get('needs_android_id',0), data.get('photo_file_id','')))
    prod_id = c.lastrowid
    for k in keys:
        c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (prod_id, k))
    conn.commit(); conn.close()
    mode = f"API PID: <code>{data.get('api_pid')}</code> | Duration: <code>{data.get('api_duration') or data['validity']}</code>" if data.get('api_pid') else "Static-key mode"
    await m.answer(
        f"✅ <b>Product Created Successfully!</b>\n\n"
        f"📁 Main Product: <b>{data['cat']}</b>\n"
        f"⏱ Variant: <b>{data['name']}</b>\n"
        f"💰 Selling Price: <b>₹{data['price']:.2f}</b>\n"
        f"📱 Device-Bound: <b>{'Yes' if data.get('needs_android_id') else 'No'}</b>\n"
        f"📦 Static Stock: <b>{stock}</b>\n"
        f"⚙️ Mode: {mode}", reply_markup=admin_kb())
    await state.clear()

@dp.callback_query(F.data == "admin_manage_prods")
async def admin_manage_prods(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    prods = db_query("SELECT id, name, category, stock FROM products", fetchall=True)
    if not prods: return await call.message.edit_text("📦 Store is empty.", reply_markup=admin_back_kb())
        
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in prods:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"[{p[2]}] {p[1]} (Stock: {p[3]})", callback_data=f"admin_view_p_{p[0]}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")])
    
    await call.message.edit_text("📦 <b>Select a product to Read/Edit:</b>", reply_markup=kb)

@dp.callback_query(F.data.startswith("admin_view_p_"))
async def admin_view_product(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[3])
    prod = db_query("SELECT * FROM products WHERE id=?", (p_id,), fetchone=True)
    
    if not prod: return await call.answer("❌ Product not found!", show_alert=True)
        
    text = (f"📦 <b>PRODUCT DETAILS</b>\n━━━━━━━━━━━━━━━━━━\n<b>ID:</b> <code>{prod[0]}</code>\n<b>Main Product:</b> {prod[1]}\n<b>Variant Name:</b> {prod[2]}\n"
            f"<b>Price:</b> ₹{prod[3]:.2f}\n<b>Stock:</b> {prod[4]}\n<b>APK Link:</b> {prod[5] if prod[5] else 'None'}\n<b>Validity:</b> {prod[6]}\n<b>Device Limit:</b> {prod[7]}\n"
            f"<b>Photo:</b> {'Yes' if len(prod) > 11 and prod[11] else 'No'}\n<b>API PID:</b> {prod[8] if len(prod) > 8 and prod[8] else 'None (static stock)'}\n<b>API Duration:</b> {prod[9] if len(prod) > 9 and prod[9] else '-'}\n<b>Device-Bound:</b> {'Yes' if len(prod) > 10 and prod[10] else 'No'}\n━━━━━━━━━━━━━━━━━━")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Edit Name", callback_data=f"edit_p_{p_id}_name"), InlineKeyboardButton(text="💰 Edit Price", callback_data=f"edit_p_{p_id}_price")],
        [InlineKeyboardButton(text="⏳ Edit Validity", callback_data=f"edit_p_{p_id}_validity"), InlineKeyboardButton(text="📱 Edit Device", callback_data=f"edit_p_{p_id}_device")],
        [InlineKeyboardButton(text="🔗 Edit APK", callback_data=f"edit_p_{p_id}_apk"), InlineKeyboardButton(text="🖼 Photo", callback_data=f"edit_p_{p_id}_photo")],
        [InlineKeyboardButton(text="➕ Add Keys", callback_data=f"edit_p_{p_id}_keys")],
        [InlineKeyboardButton(text="🆔 API PID", callback_data=f"edit_p_{p_id}_pid"), InlineKeyboardButton(text="⏱ API Duration", callback_data=f"edit_p_{p_id}_apidur")],
        [InlineKeyboardButton(text="📱 Device-Bound ON/OFF", callback_data=f"edit_p_{p_id}_androidid")],
        [InlineKeyboardButton(text="🗑 Delete Key", callback_data=f"delkey_p_{p_id}"), InlineKeyboardButton(text="🗑 Delete Product", callback_data=f"delete_p_{p_id}")],
        [InlineKeyboardButton(text="« Back to Products", callback_data="admin_manage_prods")]
    ])
    await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("edit_p_"))
async def start_edit_product(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    data = call.data.split("_")
    p_id, field = int(data[2]), data[3]
    await state.update_data(edit_p_id=p_id, edit_field=field)
    
    if field == 'keys':
        await call.message.edit_text("📥 Send the <b>NEW KEYS</b> to add to stock (1 key per line):", reply_markup=admin_back_kb())
        await state.set_state(AdminStates.wait_for_add_keys)
    elif field == 'photo':
        await call.message.edit_text("🖼 Send the <b>NEW PRODUCT PHOTO</b>, or type <b>none</b> to remove it:", reply_markup=admin_back_kb())
        await state.set_state(AdminStates.wait_for_product_photo)
    else:
        field_name_map = {'name': 'New Variant Name', 'price': 'New Price in ₹ (e.g., 99.00)', 'validity': 'New Validity (e.g., 30 Days)', 'device': 'New Device Limit (e.g., 1 Device)', 'apk': 'New APK Link (or type "none")', 'pid': 'API Product PID (or "none" to disable API)', 'apidur': 'API Duration Name (e.g. "1 Day") — "none" to clear', 'androidid': 'Reply 1 to require Android ID, 0 to disable'}
        await call.message.edit_text(f"✏️ Send the <b>{field_name_map[field]}</b>:", reply_markup=admin_back_kb())
        await state.set_state(AdminStates.wait_for_new_value)

@dp.message(AdminStates.wait_for_product_photo, F.photo)
async def process_product_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    db_query("UPDATE products SET photo_file_id=? WHERE id=?", (m.photo[-1].file_id, data['edit_p_id']))
    await m.answer("✅ Product photo updated.", reply_markup=admin_kb())
    await state.clear()

@dp.message(AdminStates.wait_for_product_photo)
async def process_product_photo_text(m: Message, state: FSMContext):
    if m.text and m.text.strip().lower() == 'none':
        data = await state.get_data()
        db_query("UPDATE products SET photo_file_id='' WHERE id=?", (data['edit_p_id'],))
        await m.answer("✅ Product photo removed.", reply_markup=admin_kb())
        await state.clear()
    else:
        await m.answer("🖼 Please send a photo, or type <b>none</b> to remove it.")

@dp.message(AdminStates.wait_for_new_value)
async def process_edit_value(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id, field, new_val = data['edit_p_id'], data['edit_field'], m.text
    
    if field == 'price':
        try: new_val = float(new_val)
        except: return await m.answer("❌ Price must be a number! Try again:")
    elif field == 'androidid':
        if new_val not in ('0', '1'):
            return await m.answer("❌ Reply with 1 (device-bound) or 0 (not).")
        new_val = int(new_val)
    elif field in ('apk', 'pid', 'apidur') and new_val.lower() == 'none': new_val = ""

    db_col_map = {'name': 'name', 'price': 'price_inr', 'validity': 'validity', 'device': 'device_limit', 'apk': 'apk_link', 'pid': 'api_pid', 'apidur': 'api_duration', 'androidid': 'needs_android_id'}
    db_query(f"UPDATE products SET {db_col_map[field]}=? WHERE id=?", (new_val, p_id))
    await m.answer("✅ <b>Product Updated Successfully!</b>", reply_markup=admin_kb())
    await state.clear()

@dp.message(AdminStates.wait_for_add_keys)
async def process_add_keys(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']
    keys = [k.strip() for k in m.text.strip().split('\n') if k.strip()]
    
    if len(keys) == 0:
        return await m.answer("❌ No valid keys found.", reply_markup=admin_kb())
        
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    for k in keys: c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (p_id, k))
    c.execute("UPDATE products SET stock = stock + ? WHERE id=?", (len(keys), p_id))
    conn.commit()
    conn.close()
    
    await m.answer(f"✅ <b>Success!</b> {len(keys)} new keys added to the product.", reply_markup=admin_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("delete_p_"))
async def admin_delete_product(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    
    db_query("DELETE FROM products WHERE id=?", (p_id,))
    db_query("DELETE FROM product_keys WHERE product_id=?", (p_id,))
    
    await call.answer("✅ Product and all its keys deleted successfully!", show_alert=True)
    await admin_manage_prods(call)

@dp.callback_query(F.data.startswith("delkey_p_"))
async def admin_delete_key_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    
    await state.update_data(del_p_id=p_id)
    await call.message.edit_text("🗑 Send the <b>exact key text</b> you want to delete from this product:", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.wait_for_delete_key)

@dp.message(AdminStates.wait_for_delete_key)
async def process_delete_key(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['del_p_id']
    key_to_delete = m.text.strip()
    
    key_data = db_query("SELECT id, is_used FROM product_keys WHERE product_id=? AND key_text=?", (p_id, key_to_delete), fetchone=True)
    
    if not key_data:
        return await m.answer("❌ Key not found in this product. Check your spelling and try again.", reply_markup=admin_back_kb())
    
    if key_data[1] == 1:
        return await m.answer("⚠️ This key has already been sold! You cannot delete used keys. Try another:", reply_markup=admin_back_kb())
        
    db_query("DELETE FROM product_keys WHERE id=?", (key_data[0],))
    db_query("UPDATE products SET stock = stock - 1 WHERE id=?", (p_id,))
    
    await m.answer(f"✅ Key <code>{key_to_delete}</code> deleted successfully!\n📦 Stock has been updated.", reply_markup=admin_kb())
    await state.clear()

# ==========================================
# 15. ADMIN PANEL: TICKETS, BROADCAST & COUPONS
# ==========================================
@dp.callback_query(F.data == "admin_view_tickets")
async def admin_view_tickets(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tickets = db_query("SELECT id, user_id, message FROM tickets WHERE status='Open' LIMIT 1", fetchall=True)
    if not tickets: return await call.answer("✅ No open tickets right now!", show_alert=True)
    t = tickets[0]
    text = f"🎫 <b>TICKET #{t[0]}</b>\n👤 <b>User ID:</b> <code>{t[1]}</code>\n📝 <b>Message:</b>\n{t[2]}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Reply to User", callback_data=f"reply_ticket_{t[0]}_{t[1]}")],
        [InlineKeyboardButton(text="❌ Close Ticket", callback_data=f"close_ticket_{t[0]}")],
        [InlineKeyboardButton(text="« Back to Admin", callback_data="admin_panel_back")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("close_ticket_"))
async def close_ticket(call: CallbackQuery):
    ticket_id = call.data.split("_")[2]
    db_query("UPDATE tickets SET status='Closed' WHERE id=?", (ticket_id,))
    await call.answer("✅ Ticket Closed!", show_alert=True)
    await admin_view_tickets(call) 

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def reply_ticket_start(call: CallbackQuery, state: FSMContext):
    data = call.data.split("_")
    ticket_id, user_id = data[2], data[3]
    await state.update_data(ticket_id=ticket_id, user_id=user_id)
    await call.message.edit_text(f"💬 Send your reply message for User <code>{user_id}</code>:", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.ticket_reply_msg)

@dp.message(AdminStates.ticket_reply_msg)
async def send_ticket_reply(m: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['user_id'], f"📞 <b>Admin Reply (Ticket #{data['ticket_id']}):</b>\n\n{m.text}")
        db_query("UPDATE tickets SET status='Closed' WHERE id=?", (data['ticket_id'],))
        await m.answer("✅ Reply sent and ticket closed!", reply_markup=admin_kb())
    except Exception as e: await m.answer(f"❌ Failed to send: {e}", reply_markup=admin_kb())
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast_btn")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📢 Send the message you want to broadcast:", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.broadcast_msg)

@dp.message(AdminStates.broadcast_msg)
async def admin_broadcast_send(message: Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    sent, failed = 0, 0
    m = await message.answer("⏳ Broadcasting... Please wait.")
    for u in users:
        try:
            await message.send_copy(chat_id=u[0])
            sent += 1
        except Exception: failed += 1
        await asyncio.sleep(0.05)
    await m.edit_text(f"✅ <b>Broadcast Complete!</b>\n\n🟢 Sent: {sent}\n🔴 Failed: {failed}", reply_markup=admin_kb())
    await state.clear()

@dp.callback_query(F.data == "admin_create_coupon")
async def admin_create_coupon_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🎟 Enter the new Coupon Code:", reply_markup=admin_back_kb())
    await state.set_state(AdminStates.add_coupon_code)

@dp.message(AdminStates.add_coupon_code)
async def admin_coupon_code(m: Message, state: FSMContext):
    await state.update_data(code=m.text.strip().upper())
    await m.answer("💰 Enter the reward amount in <b>RUPEES (₹)</b>:")
    await state.set_state(AdminStates.add_coupon_amount)

@dp.message(AdminStates.add_coupon_amount)
async def admin_coupon_amount(m: Message, state: FSMContext):
    try:
        await state.update_data(amount=float(m.text)) 
        await m.answer("👥 Enter maximum number of uses:")
        await state.set_state(AdminStates.add_coupon_uses)
    except ValueError: await m.answer("❌ Invalid amount.")

@dp.message(AdminStates.add_coupon_uses)
async def admin_coupon_uses(m: Message, state: FSMContext):
    try:
        uses = int(m.text)
        data = await state.get_data()
        db_query("INSERT OR REPLACE INTO coupons (code, amount, uses_left) VALUES (?, ?, ?)", (data['code'], data['amount'], uses))
        await m.answer(f"✅ Coupon <b>{data['code']}</b> created successfully!\nReward: ₹{data['amount']:.2f}\nMax Uses: {uses}", reply_markup=admin_kb())
        await state.clear()
    except ValueError: await m.answer("❌ Invalid uses.")

# ==========================================
# 16. BOT STARTUP
# ==========================================
async def main():
    init_db()
    print("🚀 BOT IS ONLINE (FamPay/FreePanel UPI QR Payment)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
