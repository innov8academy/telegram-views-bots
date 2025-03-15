import os
import json
import telebot
from telebot import types
from datetime import datetime, timedelta
import time
import threading
import logging
import requests
from dotenv import load_dotenv
from urllib.parse import urlparse
import string
import random
import atexit
import sys
import tempfile
import threading
from flask import Flask, render_template
from functools import wraps

# Import database module
import database as db

app = Flask(__name__)

@app.route('/')
def home():
    """
    Home route to keep the bot alive on Render.com
    """
    return render_template('index.html', status="Bot is running")

def run_flask():
    """
    Run Flask in a separate thread
    """
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# Define the function that will be called to start the web server
def start_web_server():
    """
    Start the web server in a separate thread
    """
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Web server started")

# Set up logging globally
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Process lock mechanism
def create_lock_file():
    lock_file = os.path.join(tempfile.gettempdir(), 'telegram_bot.lock')
    if os.path.exists(lock_file):
        try:
            with open(lock_file, 'r') as f:
                pid = int(f.read().strip())
                # Check if process is still running - platform specific
                if sys.platform == 'win32':
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    SYNCHRONIZE = 0x00100000
                    process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                    if process != 0:
                        kernel32.CloseHandle(process)
                        logger.error(f"Another bot instance is already running (PID: {pid})")
                        sys.exit(1)
                else:
                    # Unix-based systems
                    try:
                        os.kill(pid, 0)
                        logger.error(f"Another bot instance is already running (PID: {pid})")
                        sys.exit(1)
                    except OSError:
                        # Process is not running, we can create a new lock
                        pass
        except (ValueError, IOError):
            pass
    
    # Create lock file with current process ID
    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))
    
    # Register cleanup function
    atexit.register(lambda: os.remove(lock_file) if os.path.exists(lock_file) else None)
    return lock_file

# Create lock file before initializing bot
lock_file = create_lock_file()

# Load environment variables
load_dotenv()

# Get the bot token from environment variable
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.critical("No TELEGRAM_BOT_TOKEN found in environment variables")
    sys.exit(1)

# Get admin IDs from environment variable (comma-separated list)
ADMIN_IDS = []
admin_ids_env = os.environ.get('ADMIN_IDS', '')
if admin_ids_env:
    try:
        ADMIN_IDS = [int(admin_id.strip()) for admin_id in admin_ids_env.split(',')]
        logger.info(f"Admin IDs loaded from environment: {ADMIN_IDS}")
    except ValueError:
        logger.error("Invalid ADMIN_IDS format in environment variables")

# Bot polling settings
BOT_POLLING_TIMEOUT = 60  # Polling timeout in seconds
BOT_POLLING_INTERVAL = 1  # Polling interval in seconds
BOT_LONG_POLLING_TIMEOUT = 30  # Long polling timeout in seconds

# Initialize bot with custom settings
bot = telebot.TeleBot(TOKEN, threaded=False)  # Disable threading to prevent timeout issues

# Global data containers
users_data = {}
payments_data = []
orders_data = []
settings_data = db.DEFAULT_SETTINGS
order_timers = {}  # Store timer objects for delayed orders

# Data management functions
def load_data(file_path, default=None):
    """
    Load data from database or local JSON file
    """
    if file_path == db.USERS_FILE:
        return db.load_data(db.USERS_TABLE, file_path, default)
    elif file_path == db.ORDERS_FILE:
        return db.load_data(db.ORDERS_TABLE, file_path, default)
    elif file_path == db.PAYMENTS_FILE:
        return db.load_data(db.PAYMENTS_TABLE, file_path, default)
    elif file_path == db.SETTINGS_FILE:
        return db.load_data(db.SETTINGS_TABLE, file_path, default)
    else:
        return db.load_from_file(file_path, default)

def save_data(file_path, data):
    """
    Save data to database or local JSON file
    """
    if file_path == db.USERS_FILE:
        return db.save_data(db.USERS_TABLE, file_path, data)
    elif file_path == db.ORDERS_FILE:
        return db.save_data(db.ORDERS_TABLE, file_path, data)
    elif file_path == db.PAYMENTS_FILE:
        return db.save_data(db.PAYMENTS_TABLE, file_path, data)
    elif file_path == db.SETTINGS_FILE:
        return db.save_data(db.SETTINGS_TABLE, file_path, data)
    else:
        return db.save_to_file(file_path, data)

# Load initial data
def init_data():
    global users_data, payments_data, orders_data, settings_data, ADMIN_IDS
    users_data = load_data(db.USERS_FILE, {})
    payments_data = load_data(db.PAYMENTS_FILE, [])
    orders_data = load_data(db.ORDERS_FILE, [])
    settings_data = load_data(db.SETTINGS_FILE, db.DEFAULT_SETTINGS)
    # Update global ADMIN_IDS with settings
    admin_ids_from_settings = settings_data.get("admin_ids", [])
    if admin_ids_from_settings:
        ADMIN_IDS = admin_ids_from_settings
    # If no admins, add the first user who starts the bot as admin
    if not ADMIN_IDS:
        logger.warning("No admin IDs found in settings. First user to start the bot will be made admin.")
    
    # Standardize orders after loading
    standardize_orders()

# Function to standardize orders
def standardize_orders():
    global orders_data, ORDERS_FILE, save_data, logger
    logger.info("Standardizing order format")
    
    standardized_orders = []
    for order in orders_data:
        # Generate new order ID if old format
        order_id = order.get("id", "")
        if not order_id.startswith("ORD_"):
            order_id = generate_order_id()
            logger.info(f"Standardizing order ID from {order.get('id', '')} to {order_id}")
        
        # Create standardized order format
        standardized_order = {
            "id": order_id,
            "user_id": str(order.get("user_id", "")),
            "post_link": order.get("post_link", ""),
            "quantity": order.get("quantity", order.get("views", 0)),  # Handle both "quantity" and "views"
            "price": order.get("price", 0),
            "delivery": order.get("delivery", order.get("delivery_method", "maximum")),
            "delivery_desc": order.get("delivery_desc", "Maximum Speed"),
            "api_runs": order.get("api_runs", order.get("runs", None)),
            "api_interval": order.get("api_interval", order.get("interval", None)),
            "start_delay": order.get("start_delay", 0),
            "status": order.get("status", "pending"),
            "created_at": order.get("created_at", order.get("order_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
            "api_order_id": order.get("api_order_id", None),
            "api_response": order.get("api_response", None),
            "last_attempt": order.get("last_attempt", None),
            "processing_started": order.get("processing_started", None),
            "error": order.get("error", None)
        }
        standardized_orders.append(standardized_order)
    
    # Update orders data and save
    orders_data = standardized_orders
    save_data(ORDERS_FILE, orders_data)
    logger.info("Orders standardized successfully")

# Retry decorator for API operations
def with_retry(max_retries=3, retry_delay=5):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    return result
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed: {str(e)}, retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        continue
                    raise
            return None
        return wrapper
    return decorator

# Function to update order status
def update_order_status(order_id, status, error=None, api_response=None):
    """
    Update the status of an order in the database
    """
    global orders_data
    
    # Update in database
    db.update_order_status(order_id, status, error, api_response)
    
    # Also update in memory
    for order in orders_data:
        if order["id"] == order_id:
            order["status"] = status
            if error:
                order["error"] = error
            if api_response:
                order["api_response"] = api_response
            break
    
    logger.info(f"Updated order {order_id} status to {status}")

@with_retry(max_retries=3, retry_delay=5)
def send_view_order_to_api(order):
    global logger, API_KEY, API_URL, TELEGRAM_VIEWS_SERVICE_ID, API_TIMEOUT
    logger.info(f"Preparing API request for order {order['id']}")
    
    try:
        # Prepare API request data
        api_data = {
            "key": API_KEY,
            "action": "add",
            "service": TELEGRAM_VIEWS_SERVICE_ID,
            "link": order['post_link'],
            "quantity": order['quantity']
        }
        
        # Add drip feed parameters if specified
        if order.get('api_runs') and order.get('api_interval'):
            api_data["runs"] = order['api_runs']
            api_data["interval"] = order['api_interval']
            logger.info(f"Adding drip feed: {order['api_runs']} runs, {order['api_interval']} min intervals")
        
        logger.info(f"API request data: {api_data}")
        
        # Send request to API with increased timeout
        response = requests.post(API_URL, data=api_data, timeout=API_TIMEOUT)
        response_data = response.json()
        
        # Log API response
        logger.info(f"API response for order {order['id']}: {response_data}")
        
        # Check if order was successful
        if 'order' in response_data:
            return True, response_data['order']
        else:
            error_msg = response_data.get('error', 'Unknown API error')
            logger.error(f"API error for order {order['id']}: {error_msg}")
            return False, error_msg
            
    except Exception as e:
        logger.error(f"Error sending order to API: {e}")
        return False, str(e)

def process_order_to_api(order_id):
    global logger, orders_data
    logger.info(f"Processing order {order_id}")
    
    try:
        # Find the order in the orders data
        order = next((o for o in orders_data if o["id"] == order_id), None)
        
        if not order:
            logger.error(f"Order {order_id} not found for API processing")
            return
            
        # Check if order is still pending
        if order["status"] != "pending":
            logger.info(f"Order {order_id} is no longer pending (status: {order['status']}), skipping API request")
            return
            
        # Update order status to processing
        update_order_status(order_id, "processing")
        
        try:
            # Send order to API with retries (handled by decorator)
            success, result = send_view_order_to_api(order)
            
            if success:
                update_order_status(order_id, "processing", api_response=result)
                logger.info(f"Order {order_id} successfully sent to API, order ID: {result}")
            else:
                update_order_status(order_id, "failed", error=result)
                logger.error(f"Order {order_id} failed: {result}")
                
        except Exception as e:
            error_msg = str(e)
            update_order_status(order_id, "failed", error=error_msg)
            logger.error(f"Order {order_id} failed: {error_msg}")
            
    except Exception as e:
        logger.error(f"Error processing order {order_id}: {e}")
        update_order_status(order_id, "failed", error=str(e))

def process_delayed_order(order_id):
    global logger, orders_data
    logger.info(f"Processing delayed order {order_id}")
    
    try:
        # Find the order in the orders data
        order = next((o for o in orders_data if o["id"] == order_id), None)
        
        if not order:
            logger.error(f"Order {order_id} not found for delayed processing")
            return
            
        # Check if order is still pending
        if order["status"] != "pending":
            logger.info(f"Order {order_id} is no longer pending (status: {order['status']}), skipping API request")
            return
            
        # Process the order using the main processing function
        process_order_to_api(order_id)
        
    except Exception as e:
        logger.error(f"Error processing delayed order {order_id}: {e}")
        update_order_status(order_id, "failed", error=str(e))

# User management functions
def get_user(user_id):
    """
    Get user data from database
    """
    global users_data
    
    user_id = str(user_id)  # Convert to string for JSON storage
    
    # Get from database
    user = db.get_user(user_id)
    
    # Update in-memory cache
    users_data[user_id] = user
    
    return user

def update_user(user_id, data):
    """
    Update user data in database
    """
    global users_data
    
    user_id = str(user_id)  # Convert to string for JSON storage
    
    # Update in database
    db.update_user(user_id, data)
    
    # Update in-memory cache
    users_data[user_id] = data

# Helper function to create a keyboard with cancel button
def get_cancel_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('❌ Cancel'))
    return markup

# Helper function to restore main menu keyboard
def restore_main_menu_keyboard(chat_id, message=None):
    global logger, bot
    logger.info(f"Restoring main menu keyboard for chat {chat_id}")
    
    try:
        keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        view_btn = types.KeyboardButton('👁 View')
        account_btn = types.KeyboardButton('👤 My account')
        buy_coins_btn = types.KeyboardButton('💳 Buy coins')
        support_btn = types.KeyboardButton('🆘 Support')

        keyboard.add(view_btn, account_btn)
        keyboard.add(buy_coins_btn, support_btn)
        
        if message:
            bot.send_message(chat_id, message, reply_markup=keyboard)
        else:
            bot.send_message(chat_id, "Main menu:", reply_markup=keyboard)
        logger.info(f"Main menu keyboard restored for chat {chat_id}")
    except Exception as e:
        logger.error(f"Error restoring main menu keyboard: {e}")
        # Try a simpler approach as fallback
        try:
            bot.send_message(chat_id, "Please use /menu to return to the main menu.")
        except:
            pass

# API functions
def submit_order(post_link, quantity, runs=None, interval=None):
    """
    Submit a new order to the system
    """
    global orders_data
    
    # Generate a unique order ID
    order_id = generate_order_id()
    
    # Create order data
    order_data = {
        "id": order_id,
        "post_link": post_link,
        "quantity": quantity,
        "status": "pending",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Add API parameters if provided
    if runs:
        order_data["api_runs"] = runs
    if interval:
        order_data["api_interval"] = interval
    
    # Add order to database
    db.add_order(order_data)
    
    # Add to in-memory cache
    orders_data.append(order_data)
    
    logger.info(f"Submitted new order {order_id} for {quantity} views")
    
    return order_id

def check_order_status(order_id):
    """Check the status of an order"""
    try:
        payload = {
            'key': API_KEY,
            'action': 'status',
            'order': order_id
        }

        logger.info(f"Checking status for order: {order_id}")
        response = requests.post(API_URL, data=payload, timeout=30)

        if response.status_code == 200:
            result = response.json()
            logger.info(f"Order status response: {result}")

            if 'status' in result:
                return {
                    'success': True,
                    'status': result['status']
                }
            elif 'error' in result:
                return {
                    'success': False,
                    'error': result['error']
                }
            else:
                # If response is successful but doesn't match expected format
                logger.warning(f"Unexpected API status response format: {result}")
                return {
                    'success': False,
                    'error': f"Unexpected API response format: {result}"
                }

        logger.error(f"Status check failed with status code: {response.status_code}")
        logger.error(f"Response content: {response.text}")
        return {
            'success': False,
            'error': f"Status check failed with status code: {response.status_code}"
        }
    except requests.exceptions.Timeout:
        logger.error("API status request timed out")
        return {
            'success': False,
            'error': "API request timed out. Please try again later."
        }
    except requests.exceptions.ConnectionError:
        logger.error("Connection error when checking order status")
        return {
            'success': False,
            'error': "Connection error. Please check your network and try again."
        }
    except Exception as e:
        logger.error(f"Error checking order status: {e}")
        return {
            'success': False,
            'error': str(e)
        }

# User management functions
def get_user(user_id):
    """
    Get user data from database
    """
    global users_data
    
    user_id = str(user_id)  # Convert to string for JSON storage
    
    # Get from database
    user = db.get_user(user_id)
    
    # Update in-memory cache
    users_data[user_id] = user
    
    return user

def update_user(user_id, data):
    """
    Update user data in database
    """
    global users_data
    
    user_id = str(user_id)  # Convert to string for JSON storage
    
    # Update in database
    db.update_user(user_id, data)
    
    # Update in-memory cache
    users_data[user_id] = data

# Helper function to create a keyboard with cancel button
def get_cancel_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton('❌ Cancel'))
    return markup

# Helper function to restore main menu keyboard
def restore_main_menu_keyboard(chat_id, message=None):
    global logger, bot
    logger.info(f"Restoring main menu keyboard for chat {chat_id}")
    
    try:
        keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        view_btn = types.KeyboardButton('👁 View')
        account_btn = types.KeyboardButton('👤 My account')
        buy_coins_btn = types.KeyboardButton('💳 Buy coins')
        support_btn = types.KeyboardButton('🆘 Support')

        keyboard.add(view_btn, account_btn)
        keyboard.add(buy_coins_btn, support_btn)
        
        if message:
            bot.send_message(chat_id, message, reply_markup=keyboard)
        else:
            bot.send_message(chat_id, "Main menu:", reply_markup=keyboard)
        logger.info(f"Main menu keyboard restored for chat {chat_id}")
    except Exception as e:
        logger.error(f"Error restoring main menu keyboard: {e}")
        # Try a simpler approach as fallback
        try:
            bot.send_message(chat_id, "Please use /menu to return to the main menu.")
        except:
            pass

# Start command handler
@bot.message_handler(commands=['start'])
def start_command(message):
    global logger, bot, get_user, update_user, restore_main_menu_keyboard, ADMIN_IDS, settings_data, SETTINGS_FILE, save_data
    logger.info(f"Received /start command from user {message.from_user.id}")

    user_id = message.from_user.id
    username = message.from_user.username or f"user{user_id}"

    # Get or create user
    user = get_user(user_id)
    user["username"] = username
    update_user(user_id, user)

    # If no admins exist, make this user an admin
    if not ADMIN_IDS:
        ADMIN_IDS.append(user_id)
        settings_data["admin_ids"] = ADMIN_IDS
        save_data(SETTINGS_FILE, settings_data)
        logger.info(f"First user {user_id} has been made admin")

    # Welcome message
    welcome_msg = (
        f"👋 Welcome to the Telegram Views Bot!\n\n"
        f"This bot helps you increase views on your Telegram posts.\n"
        f"Use /menu to access all features."
    )

    logger.info(f"Sending welcome message to {user_id}")
    try:
        bot.send_message(message.chat.id, welcome_msg)
        logger.info("Welcome message sent successfully")
        restore_main_menu_keyboard(message.chat.id)
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

# Main menu function
@bot.message_handler(commands=['menu'])
def menu_command(message):
    global logger, restore_main_menu_keyboard
    logger.info(f"Received /menu command from user {message.from_user.id}")
    restore_main_menu_keyboard(message.chat.id)

# My Account handler
@bot.message_handler(func=lambda message: message.text == '👤 My account')
def my_account(message):
    global logger, bot, get_user, users_data
    logger.info(f"Received My Account request from user {message.from_user.id}")

    try:
        # Clear any pending input states
        user_id = str(message.from_user.id)
        if user_id in users_data:
            for key in list(users_data[user_id].keys()):
                if key.startswith('temp_'):
                    del users_data[user_id][key]

        user = get_user(message.from_user.id)

        account_info = (
            f"👤 *Your Account*\n\n"
            f"User ID: `{user_id}`\n"
            f"Username: @{user['username']}\n"
            f"Join Date: {user['join_date']}\n"
            f"Coins Balance: {user['coins']}\n\n"
            f"Use the '💳 Buy coins' button to add more coins."
        )

        bot.send_message(message.chat.id, account_info, parse_mode="Markdown")
        logger.info(f"Account info sent to user {user_id}")
    except Exception as e:
        logger.error(f"Error handling My Account: {e}")

# Buy Coins handler with improved cancel option
@bot.message_handler(func=lambda message: message.text == '💳 Buy coins')
def buy_coins(message):
    global logger, bot, settings_data, users_data
    logger.info(f"Received Buy Coins request from user {message.from_user.id}")

    try:
        # Clear any pending input states
        user_id = str(message.from_user.id)
        if user_id in users_data:
            for key in list(users_data[user_id].keys()):
                if key.startswith('temp_'):
                    del users_data[user_id][key]

        # Get the current price per 1000 coins from settings
        price_per_1000 = settings_data.get("price_per_1000", 0.034)  # Default price if not set
        
        # Use the cancel keyboard helper
        markup = get_cancel_keyboard()
        
        # Ask user how many coins they want
        bot.send_message(
            message.chat.id,
            f"💰 *Buy Coins*\n\n"
            f"Current rate: ${price_per_1000:.3f} per 1000 coins\n\n"
            f"Please enter how many coins you want to purchase (minimum 1000):\n\n"
            f"Or press ❌ Cancel to return to the main menu.",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
        # Register next step handler
        bot.register_next_step_handler(message, process_coin_purchase_amount)
        logger.info(f"Asked user {message.from_user.id} for coin purchase amount")
    except Exception as e:
        logger.error(f"Error handling Buy Coins: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id, "An error occurred. Returning to main menu.")

# Process coin purchase amount with improved cancel option
def process_coin_purchase_amount(message):
    global logger, bot, settings_data, payments_data, PAYMENTS_FILE, save_data
    logger.info(f"Processing coin purchase amount from user {message.from_user.id}: {message.text}")

    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            restore_main_menu_keyboard(message.chat.id, "Operation cancelled. Returning to main menu.")
            logger.info(f"User {message.from_user.id} cancelled coin purchase")
            return
            
        # Parse the amount
        try:
            coin_amount = int(message.text.strip())
            if coin_amount < 1000:
                markup = get_cancel_keyboard()
                
                bot.send_message(
                    message.chat.id,
                    "Minimum purchase is 1000 coins. Please enter a larger number:",
                    reply_markup=markup
                )
                bot.register_next_step_handler(message, process_coin_purchase_amount)
                return
        except ValueError:
            markup = get_cancel_keyboard()
            
            bot.send_message(
                message.chat.id,
                "Please enter a valid number:",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, process_coin_purchase_amount)
            return
        
        # Calculate price based on the amount
        price_per_1000 = settings_data.get("price_per_1000", 0.034)  # Default price if not set
        total_price = (coin_amount / 1000) * price_per_1000
        
        # Generate payment reference
        payment_ref = f"PMT-{message.from_user.id}-{int(time.time())}"

        # Create payment record
        payment = {
            "reference": payment_ref,
            "user_id": str(message.from_user.id),
            "coins": coin_amount,
            "price": total_price,
            "status": "pending",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        payments_data.append(payment)
        save_data(PAYMENTS_FILE, payments_data)

        # Get the payment admin username from settings
        payment_admin = settings_data.get("payment_admin_username", "AdminPaymentUser")
        
        # Create a message with instructions to contact the admin
        payment_instructions = (
            f"📖 You've requested {coin_amount:,} coins for ${total_price:.2f}\n\n"
            f"To complete your purchase, please contact:\n"
            f"👤 @{payment_admin}\n\n"
            f"Send them the following information:\n"
            f"- Your Payment Reference: `{payment_ref}`\n"
            f"- Amount: {coin_amount:,} coins\n"
            f"- Price: ${total_price:.2f}\n\n"
            f"Once your payment is verified, the coins will be added to your account."
        )
        
        # Add a button to contact the admin directly
        inline_markup = types.InlineKeyboardMarkup(row_width=1)
        inline_markup.add(
            types.InlineKeyboardButton(f"Contact @{payment_admin}", url=f"https://t.me/{payment_admin}")
        )

        bot.send_message(
            message.chat.id,
            payment_instructions,
            reply_markup=inline_markup,
            parse_mode="Markdown"
        )
        
        # Restore the main menu keyboard
        restore_main_menu_keyboard(message.chat.id, "You can continue using the bot:")
        
        logger.info(f"Payment instructions sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error processing coin purchase amount: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id, "An error occurred. Returning to main menu.")

# View handler with cancel option
@bot.message_handler(func=lambda message: message.text == '👁 View')
def view_service(message):
    global logger, bot, types, users_data
    logger.info(f"Received View service request from user {message.from_user.id}")

    try:
        # Clear any pending input states
        user_id = str(message.from_user.id)
        if user_id in users_data:
            for key in list(users_data[user_id].keys()):
                if key.startswith('temp_'):
                    del users_data[user_id][key]

        # Use the cancel keyboard helper
        markup = get_cancel_keyboard()
        
        bot.send_message(
            message.chat.id,
            "Please send the link to your Telegram post that you want to add views to:",
            reply_markup=markup
        )
        
        bot.register_next_step_handler(message, process_post_link)
        logger.info(f"Asked user {message.from_user.id} for post link")
    except Exception as e:
        logger.error(f"Error handling View service: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id, "An error occurred. Returning to main menu.")

# Process post link with cancel option
def process_post_link(message):
    global logger, bot, types
    logger.info(f"Processing post link from user {message.from_user.id}: {message.text}")

    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            restore_main_menu_keyboard(message.chat.id, "Operation cancelled. Returning to main menu.")
            logger.info(f"User {message.from_user.id} cancelled view service")
            return
            
        # Validate the link (basic check)
        post_link = message.text.strip()
        if not post_link.startswith('https://t.me/') and not post_link.startswith('http://t.me/'):
            markup = get_cancel_keyboard()
            
            bot.send_message(
                message.chat.id,
                "Invalid link format. Please send a valid Telegram post link (https://t.me/...):",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, process_post_link)
            return
            
        # Store the link in user session or context
        user_id = str(message.from_user.id)
        if user_id not in users_data:
            users_data[user_id] = {}
        
        users_data[user_id]['temp_post_link'] = post_link
        
        # Ask for view quantity
        markup = get_cancel_keyboard()
        
        bot.send_message(
            message.chat.id,
            "How many views do you want to add? (minimum 100):",
            reply_markup=markup
        )
        
        bot.register_next_step_handler(message, process_view_quantity)
        logger.info(f"Asked user {message.from_user.id} for view quantity")
    except Exception as e:
        logger.error(f"Error processing post link: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id, "An error occurred. Returning to main menu.")

# Function to generate a unique order ID
def generate_order_id():
    # Generate a random 8-character alphanumeric ID
    chars = string.ascii_uppercase + string.digits
    order_id = ''.join(random.choice(chars) for _ in range(8))
    
    # Add timestamp to ensure uniqueness
    timestamp = int(time.time()) % 10000
    return f"ORD_{order_id}{timestamp}"

# Function to calculate view price based on quantity
def calculate_view_price(quantity):
    """
    Calculate the price for requested number of views
    Price is 1 coin per view
    """
    # Price is simply the quantity (1 coin per view)
    price = quantity
    
    # Ensure minimum price
    return max(10, price)  # Minimum 10 coins

# Process view quantity with improved UI
def process_view_quantity(message):
    global logger, bot, types, settings_data, users_data, get_user, update_user, datetime, save_data, USERS_FILE
    logger.info(f"Processing view quantity from user {message.from_user.id}: {message.text}")

    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            restore_main_menu_keyboard(message.chat.id, "Operation cancelled. Returning to main menu.")
            logger.info(f"User {message.from_user.id} cancelled view service")
            return
            
        # Parse the quantity
        try:
            quantity = int(message.text.strip())
            if quantity < 100:
                markup = get_cancel_keyboard()
                
                bot.send_message(
                    message.chat.id,
                    "Minimum quantity is 100 views. Please enter a larger number:",
                    reply_markup=markup
                )
                bot.register_next_step_handler(message, process_view_quantity)
                return
            
            if quantity > 100000:
                markup = get_cancel_keyboard()
                
                bot.send_message(
                    message.chat.id,
                    "Maximum quantity is 100,000 views. Please enter a smaller number:",
                    reply_markup=markup
                )
                bot.register_next_step_handler(message, process_view_quantity)
                return
                
        except ValueError:
            markup = get_cancel_keyboard()
            
            bot.send_message(
                message.chat.id,
                "Please enter a valid number:",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, process_view_quantity)
            return
        
        # Initialize users_data structure if needed
        user_id = str(message.from_user.id)
        if user_id not in users_data:
            users_data[user_id] = {
                "coins": 0,
                "username": message.from_user.username or "",
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "orders": []
            }
            save_data(USERS_FILE, users_data)
        elif "coins" not in users_data[user_id]:
            users_data[user_id]["coins"] = 0
            save_data(USERS_FILE, users_data)
        
        # Store the quantity in user session
        users_data[user_id]['temp_quantity'] = quantity
        
        
        # Calculate price based on quantity (1 coin per view)
        price = calculate_view_price(quantity)
        users_data[user_id]['temp_price'] = price
        
        # Get user data for balance check
        user = users_data[user_id]
        
        # Show delivery options with improved UI
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        # Cancel button (full width)
        markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_view_order"))
        
        # Speed options (side by side)
        markup.add(
            types.InlineKeyboardButton("⚡ Maximum Speed", callback_data="speed_maximum"),
            types.InlineKeyboardButton("🐢 Slow Delivery", callback_data="speed_slow")
        )
        
        # Drip feed options (each on its own row)
        markup.add(types.InlineKeyboardButton("🕐 Starting after 1 min, Every 3 mins 100 views", callback_data="drip_1_3_100"))
        markup.add(types.InlineKeyboardButton("🕑 Starting after 1 min, Every 3 mins 150 views", callback_data="drip_1_3_150"))
        markup.add(types.InlineKeyboardButton("🕒 Starting after 1 min, Every 5 mins 100 views", callback_data="drip_1_5_100"))
        markup.add(types.InlineKeyboardButton("🕓 Starting after 1 min, Every 1 min 100 views", callback_data="drip_1_1_100"))
        
        # Format the message with price details and balance
        price_message = (
            f"👁‍🗨 Please confirm your order for {quantity:,} views.\n"
            f"Your balance: {user['coins']:,} coins\n"
            f"Price: {price:,} coins (1 coin per view)\n\n"
            f"💡 All orders will be processed according to your chosen speed up to 100,000 views. "
            f"For larger orders, we'll continue at the optimal rate to complete your order.\n\n"
            f"⏱ Choose a progress speed using the buttons below:"
        )
        
        bot.send_message(
            message.chat.id,
            price_message,
            reply_markup=markup
        )
        
        logger.info(f"Sent delivery options to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error processing view quantity: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id)

# Handle speed selection callbacks
@bot.callback_query_handler(func=lambda call: (call.data.startswith('speed_') or call.data.startswith('drip_') or call.data == "cancel_view_order"))
def handle_speed_selection(call):
    global logger, bot, users_data, get_user, update_user, orders_data, ORDERS_FILE, save_data, order_timers
    logger.info(f"Received speed selection from user {call.from_user.id}: {call.data}")
    
    try:
        user_id = str(call.from_user.id)
        
        # Check if we have the necessary data
        if user_id not in users_data or 'temp_quantity' not in users_data[user_id] or 'temp_price' not in users_data[user_id] or 'temp_post_link' not in users_data[user_id]:
            logger.error(f"Missing user data for user {user_id}")
            bot.answer_callback_query(call.id, "Your session has expired. Please start again.")
            restore_main_menu_keyboard(call.message.chat.id)
            return
            
        # Handle cancellation
        if call.data == "cancel_view_order":
            bot.answer_callback_query(call.id, "Order cancelled")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Order cancelled. Returning to main menu."
            )
            restore_main_menu_keyboard(call.message.chat.id)
            return
            
        # Process speed selection
        delivery_desc = ""
        quantity = users_data[user_id]['temp_quantity']
        
        # Initialize API parameters
        api_runs = None
        api_interval = None
        start_delay = 0  # Default: no delay
        
        if call.data == "speed_maximum":
            users_data[user_id]['temp_delivery'] = 'maximum'
            delivery_desc = "Maximum Speed (Instant)"
            # No drip feed for maximum speed
        
        elif call.data == "speed_slow":
            users_data[user_id]['temp_delivery'] = 'slow'
            # Calculate appropriate number of runs based on quantity
            batch_size = max(100, min(quantity // 10, 1000))  # Between 100 and 1000 views per batch
            runs = max(1, quantity // batch_size)
            api_runs = runs
            api_interval = 30  # 30 minutes between runs
            delivery_desc = f"Slow (~{batch_size} views every 30 min, {runs} batches)"
            
        elif call.data.startswith("drip_"):
            # Parse drip feed parameters
            parts = call.data.split('_')
            if len(parts) >= 4:  # Format is drip_delay_interval_batchsize
                start_delay = int(parts[1])
                interval = int(parts[2])
                batch_size = int(parts[3])
                
                # Calculate runs based on quantity and batch size
                runs = max(1, quantity // batch_size)
                
                users_data[user_id]['temp_delivery'] = call.data
                api_runs = runs
                api_interval = interval
                
                # Format the delivery description
                delivery_desc = f"Starting after {start_delay} min, Every {interval} mins {batch_size} views ({runs} batches)"
            else:
                # Invalid format
                bot.answer_callback_query(call.id, "Invalid option")
                restore_main_menu_keyboard(call.message.chat.id)
                return
        
        else:
            # Unknown option, return to main menu
            bot.answer_callback_query(call.id, "Invalid option")
            restore_main_menu_keyboard(call.message.chat.id)
            return
            
        users_data[user_id]['temp_delivery_desc'] = delivery_desc
        users_data[user_id]['temp_api_runs'] = api_runs
        users_data[user_id]['temp_api_interval'] = api_interval
        users_data[user_id]['temp_start_delay'] = start_delay
        
        # Get user data
        user = get_user(call.from_user.id)
        price = users_data[user_id]['temp_price']
        post_link = users_data[user_id]['temp_post_link']
        
        # Check if user has enough coins
        if user['coins'] < price:
            bot.answer_callback_query(call.id, "Insufficient coins")
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"You don't have enough coins. You need {price:,} coins but you only have {user['coins']:,} coins.\n\nPlease use '💳 Buy coins' to add more coins to your account."
            )
            restore_main_menu_keyboard(call.message.chat.id)
            return
            
        # Generate order ID
        order_id = generate_order_id()
        
        # Create order record
        order = {
            "id": order_id,
            "user_id": user_id,
            "post_link": post_link,
            "quantity": quantity,
            "price": price,
            "delivery": users_data[user_id]['temp_delivery'],
            "delivery_desc": delivery_desc,
            "api_runs": api_runs,
            "api_interval": api_interval,
            "start_delay": start_delay,
            "status": "pending",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "api_order_id": None,
            "api_response": None,
            "last_attempt": None,
            "processing_started": None,
            "error": None
        }
        
        # Deduct coins from user
        user['coins'] -= price
        update_user(call.from_user.id, user)
        
        # Add order to orders data
        orders_data.append(order)
        save_data(ORDERS_FILE, orders_data)
        
        # Answer the callback
        bot.answer_callback_query(call.id, "Order confirmed!")
        
        # Send confirmation message
        confirmation_text = (
            f"☑️ Order received. Your tracking code is {order_id}\n\n"
            f"Request: {quantity:,} views\n"
            f"Delivery: {delivery_desc}\n"
            f"Status: Processing\n\n"
            f"Your order is now being processed. You will be notified when it's completed."
        )
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=confirmation_text
        )
        
        # Process the order (with delay if specified)
        if start_delay > 0:
            # Schedule the API request after the delay
            timer = threading.Timer(start_delay * 60, process_delayed_order, args=[order_id])
            order_timers[order_id] = timer  # Store the timer object
            timer.start()
            logger.info(f"Scheduled order {order_id} to be sent to API after {start_delay} minutes")
        else:
            # Send to API immediately
            process_order_to_api(order_id)
        
        # Clear temporary data
        for key in list(users_data[user_id].keys()):
            if key.startswith('temp_'):
                del users_data[user_id][key]
        
        # Restore main menu
        restore_main_menu_keyboard(call.message.chat.id)
        
        logger.info(f"Created order {order_id} for user {call.from_user.id}")
    except Exception as e:
        logger.error(f"Error handling speed selection: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        restore_main_menu_keyboard(call.message.chat.id)

# Function to process a delayed order
def process_delayed_order(order_id):
    global logger, orders_data, ORDERS_FILE, save_data
    logger.info(f"Processing delayed order {order_id}")
    
    try:
        # Find the order in the orders data
        order = next((o for o in orders_data if o["id"] == order_id), None)
        
        if not order:
            logger.error(f"Order {order_id} not found for delayed processing")
            return
            
        # Check if order is still pending
        if order["status"] != "pending":
            logger.info(f"Order {order_id} is no longer pending (status: {order['status']}), skipping API request")
            return
            
        # Update order status to processing
        for i, o in enumerate(orders_data):
            if o["id"] == order_id:
                orders_data[i]["status"] = "processing"
                orders_data[i]["processing_started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        save_data(ORDERS_FILE, orders_data)
            
        # Send the order to the API with retries
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                success, result = send_view_order_to_api(order)
                
                # Update order with API result
                for i, o in enumerate(orders_data):
                    if o["id"] == order_id:
                        if success:
                            orders_data[i]["api_order_id"] = result
                            orders_data[i]["status"] = "processing"
                            orders_data[i]["api_response"] = result
                            orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            logger.info(f"Order {order_id} successfully sent to API, order ID: {result}")
                            save_data(ORDERS_FILE, orders_data)
                            return
                        else:
                            if attempt < max_retries - 1:
                                logger.warning(f"Attempt {attempt + 1} failed for order {order_id}, retrying in {retry_delay} seconds...")
                                time.sleep(retry_delay)
                                continue
                            else:
                                orders_data[i]["status"] = "failed"
                                orders_data[i]["error"] = result
                                orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                logger.error(f"Order {order_id} failed to send to API after {max_retries} attempts: {result}")
                                save_data(ORDERS_FILE, orders_data)
                                return
                                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Attempt {attempt + 1} failed for order {order_id}: {e}, retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"Order {order_id} failed after {max_retries} attempts: {e}")
                    # Update order status to failed
                    for i, o in enumerate(orders_data):
                        if o["id"] == order_id:
                            orders_data[i]["status"] = "failed"
                            orders_data[i]["error"] = str(e)
                            orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            break
                    save_data(ORDERS_FILE, orders_data)
                    return
        
    except Exception as e:
        logger.error(f"Error processing delayed order {order_id}: {e}")
        # Update order status to failed if there's an error
        for i, o in enumerate(orders_data):
            if o["id"] == order_id:
                orders_data[i]["status"] = "failed"
                orders_data[i]["error"] = str(e)
                orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        save_data(ORDERS_FILE, orders_data)

# Function to send an order to the API
def process_order_to_api(order_id):
    global logger, orders_data, ORDERS_FILE, save_data
    logger.info(f"Sending order {order_id} to API")
    
    try:
        # Find the order in the orders data
        order = next((o for o in orders_data if o["id"] == order_id), None)
        
        if not order:
            logger.error(f"Order {order_id} not found for API processing")
            return
            
        # Check if order is still pending (not cancelled)
        if order["status"] != "pending":
            logger.info(f"Order {order_id} is no longer pending (status: {order['status']}), skipping API request")
            return
            
        # Update order status to processing with timestamp
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, o in enumerate(orders_data):
            if o["id"] == order_id:
                orders_data[i]["status"] = "processing"
                orders_data[i]["processing_started"] = current_time
                orders_data[i]["last_attempt"] = current_time
                break
        save_data(ORDERS_FILE, orders_data)
            
        # Send order to API with retries
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                success, result = send_view_order_to_api(order)
                
                # Update order with API result
                for i, o in enumerate(orders_data):
                    if o["id"] == order_id:
                        if success:
                            orders_data[i]["api_order_id"] = result
                            orders_data[i]["status"] = "processing"
                            orders_data[i]["api_response"] = result
                            orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            logger.info(f"Order {order_id} successfully sent to API, order ID: {result}")
                            save_data(ORDERS_FILE, orders_data)
                            return  # Success, exit the function
                        else:
                            if attempt < max_retries - 1:
                                logger.warning(f"Attempt {attempt + 1} failed for order {order_id}, retrying in {retry_delay} seconds...")
                                time.sleep(retry_delay)
                                continue
                            else:
                                orders_data[i]["status"] = "failed"
                                orders_data[i]["error"] = result
                                orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                logger.error(f"Order {order_id} failed to send to API after {max_retries} attempts: {result}")
                                save_data(ORDERS_FILE, orders_data)
                                return
                        break
                
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Attempt {attempt + 1} failed for order {order_id}: {e}, retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"Order {order_id} failed after {max_retries} attempts: {e}")
                    # Update order status to failed
                    for i, o in enumerate(orders_data):
                        if o["id"] == order_id:
                            orders_data[i]["status"] = "failed"
                            orders_data[i]["error"] = str(e)
                            orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            break
                    save_data(ORDERS_FILE, orders_data)
                    return
        
    except Exception as e:
        logger.error(f"Error processing order {order_id}: {e}")
        # Update order status to failed if there's an error
        for i, o in enumerate(orders_data):
            if o["id"] == order_id:
                orders_data[i]["status"] = "failed"
                orders_data[i]["error"] = str(e)
                orders_data[i]["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        save_data(ORDERS_FILE, orders_data)

# Function to send view order to the API
def send_view_order_to_api(order):
    global logger, API_KEY, API_URL, TELEGRAM_VIEWS_SERVICE_ID, API_TIMEOUT, API_RETRIES, API_RETRY_DELAY
    logger.info(f"Preparing API request for order {order['id']}")
    
    for attempt in range(API_RETRIES):
        try:
            # Prepare API request data
            api_data = {
                "key": API_KEY,
                "action": "add",
                "service": TELEGRAM_VIEWS_SERVICE_ID,
                "link": order['post_link'],
                "quantity": order['quantity']
            }
            
            # Add drip feed parameters if specified
            if order.get('api_runs') and order.get('api_interval'):
                api_data["runs"] = order['api_runs']
                api_data["interval"] = order['api_interval']
                logger.info(f"Adding drip feed: {order['api_runs']} runs, {order['api_interval']} min intervals")
            
            logger.info(f"API request data: {api_data}")
            
            # Send request to API with increased timeout
            response = requests.post(API_URL, data=api_data, timeout=API_TIMEOUT)
            response_data = response.json()
            
            # Log API response
            logger.info(f"API response for order {order['id']}: {response_data}")
            
            # Check if order was successful
            if 'order' in response_data:
                return True, response_data['order']
            else:
                error_msg = response_data.get('error', 'Unknown API error')
                logger.error(f"API error for order {order['id']}: {error_msg}")
                if attempt < API_RETRIES - 1:
                    logger.info(f"Retrying in {API_RETRY_DELAY} seconds... (Attempt {attempt + 1}/{API_RETRIES})")
                    time.sleep(API_RETRY_DELAY)
                    continue
                return False, error_msg
                
        except requests.exceptions.Timeout:
            logger.error(f"API request timed out on attempt {attempt + 1}")
            if attempt < API_RETRIES - 1:
                logger.info(f"Retrying in {API_RETRY_DELAY} seconds... (Attempt {attempt + 1}/{API_RETRIES})")
                time.sleep(API_RETRY_DELAY)
                continue
            return False, "API request timed out after multiple attempts"
            
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error on attempt {attempt + 1}")
            if attempt < API_RETRIES - 1:
                logger.info(f"Retrying in {API_RETRY_DELAY} seconds... (Attempt {attempt + 1}/{API_RETRIES})")
                time.sleep(API_RETRY_DELAY)
                continue
            return False, "Connection error after multiple attempts"
            
        except Exception as e:
            logger.error(f"Error sending order to API: {e}")
            if attempt < API_RETRIES - 1:
                logger.info(f"Retrying in {API_RETRY_DELAY} seconds... (Attempt {attempt + 1}/{API_RETRIES})")
                time.sleep(API_RETRY_DELAY)
                continue
            return False, str(e)
    
    return False, "All retry attempts failed"

# Support button handler
@bot.message_handler(func=lambda message: message.text == '🆘 Support')
def support_handler(message):
    global logger, bot, settings_data, users_data
    logger.info(f"Support request from user {message.from_user.id}")
    
    try:
        # Clear any pending input states
        user_id = str(message.from_user.id)
        if user_id in users_data:
            for key in list(users_data[user_id].keys()):
                if key.startswith('temp_'):
                    del users_data[user_id][key]

        # Reload settings to ensure we have the latest support username
        settings_file = os.path.join('data', 'settings.json')
        if os.path.exists(settings_file):
            with open(settings_file, 'r') as f:
                try:
                    loaded_settings = json.load(f)
                    if 'support_username' in loaded_settings:
                        settings_data['support_username'] = loaded_settings['support_username']
                except json.JSONDecodeError:
                    logger.error(f"Error decoding settings.json")

        support_username = settings_data.get("support_username", "SupportUser")
        # Remove @ if present in the username
        support_username = support_username.replace('@', '')
        
        # If support username is still the default, show a message to set it up
        if support_username == "SupportUser" and message.from_user.id in ADMIN_IDS:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⚙️ Set Support Username", callback_data="admin_change_support"))
            
            bot.send_message(
                message.chat.id,
                "⚠️ *Support Username Not Configured*\n\n"
                "You need to set up a support username in the admin panel.\n"
                "Click the button below to configure it now.",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        
        support_message = (
            f"🆘 *Support*\n\n"
            f"Need help? Contact our support team:\n"
            f"👤 @{support_username}\n\n"
            f"We'll help you with any questions or issues you have."
        )
        
        # Create inline keyboard with support link
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(f"Contact @{support_username}", url=f"https://t.me/{support_username}"))
        
        bot.send_message(
            message.chat.id,
            support_message,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        logger.info(f"Support information sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error handling support request: {e}")
        bot.send_message(
            message.chat.id, 
            "Sorry, there was an error processing your support request. Please try again later."
        )
        # Show main menu as fallback
        restore_main_menu_keyboard(message.chat.id)

# Admin command handler
@bot.message_handler(commands=['admin'])
def admin_command(message):
    global logger, bot, ADMIN_IDS
    logger.info(f"Received /admin command from user {message.from_user.id}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin access attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Show admin panel
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error handling admin command: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")

# Function to show admin panel
def show_admin_panel(chat_id):
    global logger, bot, types, restore_main_menu_keyboard
    logger.info(f"Showing admin panel to chat_id {chat_id}")
    
    try:
        # Ensure the main menu keyboard is restored
        restore_main_menu_keyboard(chat_id)
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("💰 Add Coins to User", callback_data="admin_add_coins"),
            types.InlineKeyboardButton("💳 Change Payment Username", callback_data="admin_change_payment_username"),
            types.InlineKeyboardButton("💲 Change Coin Price", callback_data="admin_change_coin_price"),
            types.InlineKeyboardButton("👥 Manage Admins", callback_data="admin_manage_admins"),
            types.InlineKeyboardButton("🆘 Change Support Username", callback_data="admin_change_support"),
            types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu")
        )

        bot.send_message(
            chat_id,
            "🔐 *Admin Panel*\n\nSelect an action:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        logger.info(f"Admin panel buttons sent to chat {chat_id}")
    except Exception as e:
        logger.error(f"Error showing admin panel buttons: {e}")
        bot.send_message(chat_id, f"Error showing admin panel: {str(e)}")
        # Try to restore main menu as fallback
        try:
            restore_main_menu_keyboard(chat_id)
        except:
            pass

# Admin callback handler
@bot.callback_query_handler(func=lambda call: (call.data.startswith('admin_') and not call.data == "admin_back_to_panel") or call.data == "back_to_menu")
def admin_callback_handler(call):
    global logger, bot, ADMIN_IDS, users_data, settings_data, SETTINGS_FILE, save_data
    logger.info(f"Received admin callback from user {call.from_user.id}: {call.data}")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin callback attempt by user {call.from_user.id}")
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Handle different admin actions
        if call.data == "admin_add_coins":
            bot.answer_callback_query(call.id)
            
            # Add a reply keyboard with cancel button
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add(types.KeyboardButton('❌ Cancel'))
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="💰 *Add Coins to User*\n\nEnter user ID to add coins to:",
                parse_mode="Markdown"
            )
            
            # Send a new message with the reply keyboard
            bot.send_message(
                chat_id=call.message.chat.id,
                text="Enter user ID or press Cancel to return to admin panel:",
                reply_markup=markup
            )
            
            bot.register_next_step_handler(call.message, admin_get_user_id_for_coins)
            
        elif call.data == "admin_change_payment_username":
            bot.answer_callback_query(call.id)
            current_username = settings_data.get("payment_admin_username", "AdminPaymentUser")
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"💳 *Change Payment Username*\n\nCurrent payment username: @{current_username}\n\nEnter new payment username (without @):",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(call.message, admin_change_payment_username)
            
        elif call.data == "admin_change_coin_price":
            bot.answer_callback_query(call.id)
            current_price = settings_data.get("price_per_1000", 0.034)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"💲 *Change Coin Price*\n\nCurrent price per 1000 coins: ${current_price:.3f}\n\nEnter new price per 1000 coins (e.g., 0.05):",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(call.message, admin_change_coin_price)
            
        elif call.data == "admin_manage_admins":
            bot.answer_callback_query(call.id)
            current_admins = settings_data.get("admin_ids", [])
            admin_list = "\n".join([f"• {admin_id}" for admin_id in current_admins]) if current_admins else "No admins found"
            
            # Create inline keyboard with admin management options
            markup = types.InlineKeyboardMarkup(row_width=2)
            
            # Add admin button
            markup.add(types.InlineKeyboardButton("➕ Add New Admin", callback_data="admin_add_new_admin"))
            
            # Remove admin buttons (one for each admin)
            for admin_id in current_admins:
                markup.add(types.InlineKeyboardButton(f"❌ Remove Admin {admin_id}", callback_data=f"admin_remove_{admin_id}"))
            
            # Back button
            markup.add(types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back_to_panel"))
            
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"👥 *Admin Management*\n\nCurrent admins:\n{admin_list}\n\n"
                f"Select an action:",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            
        elif call.data == "admin_add_new_admin":
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_manage"))
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="➕ *Add New Admin*\n\nPlease enter the user ID of the new admin:",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(call.message, process_new_admin_id)
            
        elif call.data == "admin_change_support":
            bot.answer_callback_query(call.id)
            current_support = settings_data.get("support_username", "SupportUser")
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"🆘 *Change Support Username*\n\nCurrent support username: @{current_support}\n\nEnter new support username (without @):",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(call.message, admin_change_support_username)
            
        elif call.data == "back_to_menu":
            bot.answer_callback_query(call.id)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            show_admin_panel(call.message.chat.id)
    except Exception as e:
        logger.error(f"Error handling admin callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        bot.send_message(call.message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(call.message.chat.id)

# Admin function to get user ID for adding coins
def admin_get_user_id_for_coins(message):
    global logger, bot
    logger.info(f"Admin {message.from_user.id} entered user ID: {message.text}")
    
    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            bot.send_message(message.chat.id, "Operation cancelled. Returning to admin panel.")
            show_admin_panel(message.chat.id)
            return

        user_id = message.text.strip()
        
        # Validate user ID
        try:
            int(user_id)  # Check if it's a valid integer
        except ValueError:
            # Add a reply keyboard with cancel button
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add(types.KeyboardButton('❌ Cancel'))
            
            bot.send_message(
                message.chat.id, 
                "⚠️ Please enter a valid user ID (numbers only).",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, admin_get_user_id_for_coins)
            return
            
        # Check if user exists
        if str(user_id) not in users_data:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("Create New User", callback_data=f"admin_create_user_{user_id}"))
            markup.add(types.InlineKeyboardButton("Try Different ID", callback_data="admin_retry_user_id"))
            markup.add(types.InlineKeyboardButton("Back to Admin Panel", callback_data="admin_back_to_panel"))
            
            bot.send_message(
                message.chat.id, 
                f"⚠️ User ID {user_id} not found. Would you like to create a new user with this ID?",
                reply_markup=markup
            )
            return
        
        # Store the user ID in admin session
        admin_id = str(message.from_user.id)
        if admin_id not in users_data:
            users_data[admin_id] = {}
        users_data[admin_id]['admin_temp_user_id'] = user_id
        
        # Ask for coin amount with cancel button in reply keyboard
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(types.KeyboardButton('❌ Cancel'))
        
        bot.send_message(
            message.chat.id, 
            f"💰 Enter amount of coins to add to user {user_id}:",
            reply_markup=markup
        )
        bot.register_next_step_handler(message, admin_add_coins_to_user)
    except Exception as e:
        logger.error(f"Error getting user ID for coins: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Admin function to add coins to user
def admin_add_coins_to_user(message):
    global logger, bot, users_data, USERS_FILE, save_data, get_user, update_user
    logger.info(f"Admin {message.from_user.id} entered coin amount: {message.text}")
    
    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            bot.send_message(message.chat.id, "Operation cancelled. Returning to admin panel.")
            show_admin_panel(message.chat.id)
            return

        admin_id = str(message.from_user.id)
        
        # Check if we have the temp user ID
        if admin_id not in users_data or 'admin_temp_user_id' not in users_data[admin_id]:
            bot.send_message(message.chat.id, "⚠️ Session expired. Please start again.")
            show_admin_panel(message.chat.id)
            return
            
        user_id = users_data[admin_id]['admin_temp_user_id']
        
        # Validate coin amount
        try:
            coins = int(message.text.strip())
            if coins <= 0:
                # Add a reply keyboard with cancel button
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                markup.add(types.KeyboardButton('❌ Cancel'))
                
                bot.send_message(
                    message.chat.id, 
                    "⚠️ Please enter a positive number of coins.",
                    reply_markup=markup
                )
                bot.register_next_step_handler(message, admin_add_coins_to_user)
                return
        except ValueError:
            # Add a reply keyboard with cancel button
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add(types.KeyboardButton('❌ Cancel'))
            
            bot.send_message(
                message.chat.id, 
                "⚠️ Please enter a valid number.",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, admin_add_coins_to_user)
            return
            
        # Get user data
        user = get_user(int(user_id))
        
        # Add coins
        old_balance = user['coins']
        user['coins'] += coins
        update_user(int(user_id), user)
        
        # Confirm
        bot.send_message(
            message.chat.id,
            f"✅ Added {coins:,} coins to user {user_id}\n\n"
            f"Old balance: {old_balance:,} coins\n"
            f"New balance: {user['coins']:,} coins"
        )
        
        # Return to admin panel
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error adding coins to user: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Admin function to change payment username
def admin_change_payment_username(message):
    global logger, bot, settings_data, SETTINGS_FILE, save_data
    logger.info(f"Admin {message.from_user.id} changing payment username to: {message.text}")
    
    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            bot.send_message(message.chat.id, "Operation cancelled. Returning to admin panel.")
            show_admin_panel(message.chat.id)
            return

        new_username = message.text.strip()
        
        # Validate username (basic check)
        if not new_username or ' ' in new_username or '@' in new_username:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
            bot.send_message(
                message.chat.id, 
                "⚠️ Invalid username format. Please enter a valid Telegram username without @ or spaces.",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, admin_change_payment_username)
            return
            
        # Update settings
        settings_data['payment_admin_username'] = new_username
        save_result = save_data(SETTINGS_FILE, settings_data)
        
        if save_result:
            # Confirm
            bot.send_message(
                message.chat.id,
                f"✅ Payment username updated to: @{new_username}"
            )
        else:
            bot.send_message(
                message.chat.id,
                "⚠️ Error saving settings. Please try again."
            )
        
        # Return to admin panel
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error changing payment username: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Admin function to change coin price
def admin_change_coin_price(message):
    global logger, bot, settings_data, SETTINGS_FILE, save_data
    logger.info(f"Admin {message.from_user.id} changing coin price to: {message.text}")
    
    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            bot.send_message(message.chat.id, "Operation cancelled. Returning to admin panel.")
            show_admin_panel(message.chat.id)
            return

        # Validate price
        try:
            new_price = float(message.text.strip())
            if new_price <= 0:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
                bot.send_message(message.chat.id, "⚠️ Please enter a positive price.", reply_markup=markup)
                bot.register_next_step_handler(message, admin_change_coin_price)
                return
        except ValueError:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
            bot.send_message(message.chat.id, "⚠️ Please enter a valid number.", reply_markup=markup)
            bot.register_next_step_handler(message, admin_change_coin_price)
            return
            
        # Update settings
        old_price = settings_data.get('price_per_1000', 0.034)
        settings_data['price_per_1000'] = new_price
        save_result = save_data(SETTINGS_FILE, settings_data)
        
        if save_result:
            # Confirm
            bot.send_message(
                message.chat.id,
                f"✅ Price per 1000 coins updated:\n\n"
                f"Old price: ${old_price:.3f}\n"
                f"New price: ${new_price:.3f}"
            )
        else:
            bot.send_message(
                message.chat.id,
                "⚠️ Error saving settings. Please try again."
            )
        
        # Return to admin panel
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error changing coin price: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Admin function to change support username
def admin_change_support_username(message):
    global logger, bot, settings_data, SETTINGS_FILE, save_data
    logger.info(f"Admin {message.from_user.id} changing support username to: {message.text}")
    
    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            bot.send_message(message.chat.id, "Operation cancelled. Returning to admin panel.")
            show_admin_panel(message.chat.id)
            return

        new_username = message.text.strip()
        
        # Remove @ if present
        if new_username.startswith('@'):
            new_username = new_username[1:]
        
        # Validate username (basic check)
        if not new_username or ' ' in new_username:
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add('❌ Cancel')
            bot.send_message(
                message.chat.id, 
                "⚠️ Invalid username format. Please enter a valid Telegram username without spaces.",
                reply_markup=markup
            )
            bot.register_next_step_handler(message, admin_change_support_username)
            return
            
        # Update settings
        old_username = settings_data.get('support_username', 'SupportUser')
        settings_data['support_username'] = new_username
        
        # Ensure the settings directory exists
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        
        # Save settings
        save_result = save_data(SETTINGS_FILE, settings_data)
        
        if save_result:
            # Confirm
            bot.send_message(
                message.chat.id,
                f"✅ Support username updated:\n\n"
                f"Old username: @{old_username}\n"
                f"New username: @{new_username}"
            )
            logger.info(f"Support username updated from {old_username} to {new_username}")
        else:
            bot.send_message(
                message.chat.id,
                "⚠️ Error saving settings. Please try again."
            )
            logger.error(f"Failed to save settings when updating support username")
        
        # Return to admin panel
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error changing support username: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Additional admin callback handlers
@bot.callback_query_handler(func=lambda call: call.data.startswith(('admin_create_user_', 'admin_retry_user_id', 'admin_back_to_panel')))
def admin_user_management_callback(call):
    global logger, bot, users_data, USERS_FILE, save_data
    logger.info(f"Received admin user management callback from user {call.from_user.id}: {call.data}")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin callback attempt by user {call.from_user.id}")
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        if call.data.startswith("admin_create_user_"):
            # Create new user
            user_id = call.data.split('_')[-1]
            
            # Initialize new user
            users_data[user_id] = {
                "coins": 0,
                "username": f"user{user_id}",
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "orders": []
            }
            
            save_result = save_data(USERS_FILE, users_data)
            
            if save_result:
                bot.answer_callback_query(call.id, "User created successfully")
                
                # Store the user ID in admin session
                admin_id = str(call.from_user.id)
                if admin_id not in users_data:
                    users_data[admin_id] = {}
                users_data[admin_id]['admin_temp_user_id'] = user_id
                
                # Ask for coin amount
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"✅ User {user_id} created successfully.\n\n💰 Enter amount of coins to add:"
                )
                bot.register_next_step_handler(call.message, admin_add_coins_to_user)
            else:
                bot.answer_callback_query(call.id, "Error creating user")
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text="⚠️ Error creating user. Please try again."
                )
                show_admin_panel(call.message.chat.id)
                
        elif call.data == "admin_retry_user_id":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="💰 *Add Coins to User*\n\nEnter user ID to add coins to:",
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(call.message, admin_get_user_id_for_coins)
            
        elif call.data == "admin_back_to_panel":
            bot.answer_callback_query(call.id)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            show_admin_panel(call.message.chat.id)
            
    except Exception as e:
        logger.error(f"Error handling admin user management callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        show_admin_panel(call.message.chat.id)

# Admin function to manage admins
def admin_manage_admins(message):
    global logger, bot, settings_data, SETTINGS_FILE, save_data, ADMIN_IDS
    logger.info(f"Admin {message.from_user.id} managing admins: {message.text}")
    
    try:
        current_admins = settings_data.get("admin_ids", [])
        admin_list = "\n".join([f"• {admin_id}" for admin_id in current_admins])
        
        # Create inline keyboard with admin management options
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        # Add admin button
        markup.add(types.InlineKeyboardButton("➕ Add New Admin", callback_data="admin_add_new_admin"))
        
        # Remove admin buttons (one for each admin)
        for admin_id in current_admins:
            markup.add(types.InlineKeyboardButton(f"❌ Remove Admin {admin_id}", callback_data=f"admin_remove_{admin_id}"))
            
        # Back button
        markup.add(types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back_to_panel"))
        
        # Show admin list with buttons
        bot.send_message(
            message.chat.id,
            f"👥 *Admin Management*\n\nCurrent admins:\n{admin_list}\n\n"
            f"Select an action:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error managing admins: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Add new admin callback handler
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_new_admin")
def admin_add_new_admin_callback(call):
    global logger, bot, ADMIN_IDS
    logger.info(f"Admin {call.from_user.id} adding new admin")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Create inline keyboard for user selection
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_manage"))
        
        # Ask for user ID
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="➕ *Add New Admin*\n\nPlease enter the user ID of the new admin:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        
        # Register next step handler
        bot.register_next_step_handler(call.message, process_new_admin_id)
        
    except Exception as e:
        logger.error(f"Error in admin add callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        show_admin_panel(call.message.chat.id)

# Process new admin ID
def process_new_admin_id(message):
    global logger, bot, settings_data, SETTINGS_FILE, save_data, ADMIN_IDS
    logger.info(f"Processing new admin ID from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user wants to cancel
        if message.text == '❌ Cancel':
            bot.send_message(message.chat.id, "Operation cancelled. Returning to admin panel.")
            show_admin_panel(message.chat.id)
            return

        # Validate user ID
        try:
            user_id = int(message.text.strip())
            current_admins = settings_data.get("admin_ids", [])
            
            if user_id in current_admins:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
                bot.send_message(
                    message.chat.id,
                    f"⚠️ User {user_id} is already an admin.",
                    reply_markup=markup
                )
                admin_manage_admins(message)
                return
                
            # Add new admin
            current_admins.append(user_id)
            settings_data["admin_ids"] = current_admins
            save_data(SETTINGS_FILE, settings_data)
            # Update the global ADMIN_IDS list
            ADMIN_IDS = current_admins
            
            bot.send_message(
                message.chat.id,
                f"✅ Successfully added user {user_id} as admin."
            )
            
        except ValueError:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_back_to_panel"))
            bot.send_message(
                message.chat.id,
                "⚠️ Please enter a valid user ID (numbers only).",
                reply_markup=markup
            )
            # Register next step handler again
            bot.register_next_step_handler(message, process_new_admin_id)
            return
            
        # Show updated admin list
        admin_manage_admins(message)
        
    except Exception as e:
        logger.error(f"Error processing new admin ID: {e}")
        bot.send_message(message.chat.id, f"⚠️ Error: {str(e)}")
        show_admin_panel(message.chat.id)

# Remove admin callback handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_remove_"))
def admin_remove_admin_callback(call):
    global logger, bot, settings_data, SETTINGS_FILE, save_data, ADMIN_IDS
    logger.info(f"Admin {call.from_user.id} removing admin")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Get admin ID from callback data
        admin_id = int(call.data.split('_')[2])
        current_admins = settings_data.get("admin_ids", [])
        
        # Prevent removing the last admin
        if len(current_admins) <= 1:
            bot.answer_callback_query(call.id, "Cannot remove the last admin")
            return
            
        # Remove admin
        current_admins.remove(admin_id)
        settings_data["admin_ids"] = current_admins
        save_data(SETTINGS_FILE, settings_data)
        # Update the global ADMIN_IDS list
        ADMIN_IDS = current_admins
        
        bot.answer_callback_query(call.id, f"Removed admin {admin_id}")
        
        # Show updated admin list
        admin_manage_admins(call.message)
        
    except Exception as e:
        logger.error(f"Error in admin remove callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        show_admin_panel(call.message.chat.id)

# Back to admin panel callback handler
@bot.callback_query_handler(func=lambda call: call.data == "admin_back_to_panel")
def admin_back_to_panel_callback(call):
    global logger, bot, ADMIN_IDS, restore_main_menu_keyboard
    logger.info(f"Admin {call.from_user.id} returning to admin panel")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Answer the callback query
        bot.answer_callback_query(call.id)
        
        # Delete the current message
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
        # Restore the main menu keyboard in case it was replaced by a custom keyboard
        restore_main_menu_keyboard(call.message.chat.id)
        
        # Show the admin panel
        show_admin_panel(call.message.chat.id)
        
    except Exception as e:
        logger.error(f"Error in back to panel callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        restore_main_menu_keyboard(call.message.chat.id)
        show_admin_panel(call.message.chat.id)

# Back to manage admins callback handler
@bot.callback_query_handler(func=lambda call: call.data == "admin_back_to_manage")
def admin_back_to_manage_callback(call):
    global logger, bot
    logger.info(f"Admin {call.from_user.id} returning to admin management")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        bot.answer_callback_query(call.id)
        admin_manage_admins(call.message)
        
    except Exception as e:
        logger.error(f"Error in back to manage callback: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        show_admin_panel(call.message.chat.id)

# Initialize data and start bot
if __name__ == '__main__':
    try:
        logger.info("Starting bot...")
        init_data()
        
        # Start the web server
        start_web_server()
        
        logger.info("Bot started")
        
        # Start polling with error handling and retries
        while True:
            try:
                bot.polling(
                    none_stop=True, 
                    interval=BOT_POLLING_INTERVAL, 
                    timeout=BOT_POLLING_TIMEOUT, 
                    long_polling_timeout=BOT_LONG_POLLING_TIMEOUT
                )
            except requests.exceptions.ReadTimeout:
                logger.warning("Bot polling timed out, restarting...")
                time.sleep(5)  # Wait before retrying
                continue
            except requests.exceptions.ConnectionError:
                logger.warning("Connection error, retrying in 5 seconds...")
                time.sleep(5)
                continue
            except Exception as e:
                logger.error(f"Unexpected error in bot polling: {e}")
                time.sleep(5)
                continue
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Critical error: {e}")
        sys.exit(1)
    finally:
        # Clean up lock file
        if os.path.exists(db.LOCK_FILE):
            try:
                os.remove(db.LOCK_FILE)
            except:
                pass

# Cancel order command handler
@bot.message_handler(func=lambda message: message.text.startswith('/cancel_'))
def cancel_order(message):
    global logger, bot, orders_data, ORDERS_FILE, save_data, get_user, update_user, order_timers
    logger.info(f"Received cancel order request from user {message.from_user.id}")
    
    try:
        # Extract order ID from command
        order_id = message.text.split('_')[1]
        
        # Find the order
        order = next((o for o in orders_data if o["id"] == order_id), None)
        
        if not order:
            bot.send_message(message.chat.id, "❌ Order not found.")
            return
            
        # Check if user owns this order
        if str(message.from_user.id) != order["user_id"]:
            bot.send_message(message.chat.id, "❌ You can only cancel your own orders.")
            return
            
        # Check if order can be cancelled
        if order["status"] != "pending":
            bot.send_message(message.chat.id, f"❌ Order cannot be cancelled. Current status: {order['status']}")
            return
            
        # Cancel the timer if it exists
        if order_id in order_timers:
            order_timers[order_id].cancel()
            del order_timers[order_id]
            logger.info(f"Cancelled timer for order {order_id}")
            
        # Update order status to cancelled
        for i, o in enumerate(orders_data):
            if o["id"] == order_id:
                orders_data[i]["status"] = "cancelled"
                orders_data[i]["cancelled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                orders_data[i]["cancelled_by"] = str(message.from_user.id)
                break
                
        # Save updated orders data
        save_data(ORDERS_FILE, orders_data)
        
        # Refund coins to user
        user = get_user(message.from_user.id)
        user["coins"] += order["price"]
        update_user(message.from_user.id, user)
        
        # Send confirmation message
        bot.send_message(
            message.chat.id,
            f"✅ Order {order_id} has been cancelled.\n"
            f"💰 {order['price']:,} coins have been refunded to your account."
        )
        
        logger.info(f"Order {order_id} cancelled by user {message.from_user.id}")
        
    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        bot.send_message(message.chat.id, "❌ An error occurred while cancelling the order.")

# Cancel command handler
@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    global logger, bot, orders_data
    logger.info(f"Received /cancel command from user {message.from_user.id}")
    
    try:
        user_id = str(message.from_user.id)
        
        # Get user's pending orders
        user_orders = [order for order in orders_data if order["user_id"] == user_id and order["status"] == "pending"]
        
        if not user_orders:
            bot.send_message(
                message.chat.id,
                "❌ You don't have any pending orders to cancel.\n\n"
                "Use '/menu' to return to the main menu."
            )
            return
            
        # Create inline keyboard with cancel buttons
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        for order in user_orders:
            markup.add(types.InlineKeyboardButton(
                f"Cancel Order {order['id']} ({order['quantity']:,} views)",
                callback_data=f"cancel_order_{order['id']}"
            ))
            
        markup.add(types.InlineKeyboardButton("❌ Close", callback_data="close_cancel_menu"))
        
        bot.send_message(
            message.chat.id,
            "Select an order to cancel:",
            reply_markup=markup
        )
        logger.info(f"Sent cancel options to user {message.from_user.id}")
        
    except Exception as e:
        logger.error(f"Error handling cancel command: {e}")
        bot.send_message(message.chat.id, "❌ An error occurred while fetching your orders.")

# Cancel order callback handler
@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_order_') or call.data == "close_cancel_menu")
def cancel_order_callback(call):
    global logger, bot, orders_data, ORDERS_FILE, save_data, get_user, update_user, order_timers
    logger.info(f"Received cancel order callback from user {call.from_user.id}: {call.data}")
    
    try:
        if call.data == "close_cancel_menu":
            bot.answer_callback_query(call.id)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            return
            
        # Extract order ID from callback data
        order_id = call.data.replace('cancel_order_', '')
        logger.info(f"Attempting to cancel order {order_id}")
        
        # Find the order
        order = next((o for o in orders_data if o["id"] == order_id), None)
        
        if not order:
            logger.error(f"Order {order_id} not found")
            bot.answer_callback_query(call.id, "❌ Order not found.")
            return
            
        # Check if user owns this order
        if str(call.from_user.id) != order["user_id"]:
            logger.warning(f"User {call.from_user.id} attempted to cancel order {order_id} owned by user {order['user_id']}")
            bot.answer_callback_query(call.id, "❌ You can only cancel your own orders.")
            return
            
        # Check if order is in a cancellable state
        if order["status"] != "pending":
            logger.warning(f"User {call.from_user.id} attempted to cancel order {order_id} with status {order['status']}")
            bot.answer_callback_query(call.id, f"❌ Only pending orders can be cancelled. This order is {order['status']}.")
            return
            
        # Cancel the order
        order["status"] = "cancelled"
        
        # Refund coins to user
        user_id = str(call.from_user.id)
        user = get_user(user_id)
        user["coins"] = user.get("coins", 0) + order.get("price", 0)
        update_user(user_id, user)
        
        # Save updated orders
        save_data(ORDERS_FILE, orders_data)
        
        # Cancel any scheduled timers for this order
        if order_id in order_timers:
            order_timers[order_id].cancel()
            del order_timers[order_id]
            logger.info(f"Cancelled timer for order {order_id}")
        
        # Update user's order history
        bot.send_message(
            call.message.chat.id,
            text=f"✅ Order {order_id} has been cancelled.\n💰 {order['price']:,} coins have been refunded to your account.\n\nUse '/cancel' to view all your orders.",
            parse_mode="Markdown"
        )
        logger.info(f"Order {order_id} cancelled successfully")
        
    except Exception as e:
        logger.error(f"Error handling cancel order callback: {e}")
        bot.send_message(call.message.chat.id, "❌ An error occurred while cancelling your order.")

def show_main_menu(chat_id):
    global logger, bot, types
    logger.info(f"Showing main menu to chat_id {chat_id}")
    try:
        keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        view_btn = types.KeyboardButton('👁 View')
        account_btn = types.KeyboardButton('👤 My account')
        buy_coins_btn = types.KeyboardButton('💳 Buy coins')
        support_btn = types.KeyboardButton('🆘 Support')

        keyboard.add(view_btn, account_btn)
        keyboard.add(buy_coins_btn, support_btn)

        bot.send_message(chat_id, "Main menu:", reply_markup=keyboard)
        logger.info(f"Main menu sent to chat {chat_id}")
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        # Try a simpler approach as fallback
        try:
            bot.send_message(chat_id, "Please use /menu to return to the main menu.")
        except:
            pass

# Start the bot
if __name__ == "__main__":
    try:
        logger.info("Starting bot...")
        init_data()
        logger.info("Bot started")
        bot.polling(none_stop=True, timeout=60)
    except Exception as e:
        logger.critical(f"Critical error: {e}")
        sys.exit(1)
