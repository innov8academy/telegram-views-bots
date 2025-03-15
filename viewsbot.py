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
from flask import Flask, render_template, jsonify
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

@app.route('/health')
def health():
    """
    Health check endpoint for Render.com
    """
    port = int(os.environ.get('PORT', 10000))
    return jsonify({
        "status": "ok", 
        "message": "Bot is running",
        "port": port,
        "server_url": f"http://0.0.0.0:{port}"
    })

@app.route('/test')
def test():
    """
    Test endpoint to check if the bot is working
    """
    try:
        # Test database connection
        db_status = "ok" if db.test_connection() else "error"
        
        # Test bot connection
        bot_status = "ok"
        try:
            bot_info = bot.get_me()
            bot_username = bot_info.username
        except Exception as e:
            bot_status = f"error: {str(e)}"
            bot_username = "unknown"
        
        return jsonify({
            "status": "ok",
            "database": db_status,
            "bot": bot_status,
            "bot_username": bot_username,
            "admin_ids": ADMIN_IDS,
            "env_vars": {
                "TELEGRAM_BOT_TOKEN": bool(TOKEN),
                "ADMIN_IDS": bool(admin_ids_env),
                "SUPABASE_URL": bool(os.environ.get('SUPABASE_URL')),
                "SUPABASE_KEY": bool(os.environ.get('SUPABASE_KEY')),
                "PORT": os.environ.get('PORT', 10000)
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

def run_flask():
    """
    Run Flask in a separate thread
    """
    # Get port from environment variable (Render sets this)
    port = int(os.environ.get('PORT', 10000))
    print(f"Starting Flask server on port {port}")
    print(f"Server will be available at http://0.0.0.0:{port}")
    sys.stdout.flush()
    # Use threaded=False to avoid conflicts with the bot's threading
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False, use_reloader=False)

# Define the function that will be called to start the web server
def start_web_server():
    """
    Start the web server in a separate thread
    """
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info(f"Web server started on port {os.environ.get('PORT', 10000)}")

# Set up logging globally
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Process lock mechanism
def create_lock_file(force=False):
    # Use the data directory for the lock file
    if not os.path.exists(db.DATA_DIR):
        os.makedirs(db.DATA_DIR, exist_ok=True)
    
    lock_file = db.LOCK_FILE
    
    if os.path.exists(lock_file) and not force:
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
                        logger.error(f"If you're sure no other instance is running, delete {lock_file} or use --force")
                        sys.exit(1)
                else:
                    # Unix-based systems
                    try:
                        os.kill(pid, 0)
                        logger.error(f"Another bot instance is already running (PID: {pid})")
                        logger.error(f"If you're sure no other instance is running, delete {lock_file} or use --force")
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

# API configuration for views service
API_KEY = os.environ.get('API_KEY', '')  # Your API key for the views service
API_URL = os.environ.get('API_URL', 'https://example.com/api')  # API endpoint
TELEGRAM_VIEWS_SERVICE_ID = os.environ.get('TELEGRAM_VIEWS_SERVICE_ID', '1')  # Service ID for Telegram views
API_TIMEOUT = 60  # Timeout for API requests in seconds

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
    global orders_data, logger
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
    
    # Update orders_data with standardized orders
    orders_data = standardized_orders
    
    # Save standardized orders
    save_data(db.ORDERS_FILE, orders_data)

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

# Start command handler
@bot.message_handler(commands=['start'])
def start_command(message):
    global logger, bot, get_user, update_user, restore_main_menu_keyboard, ADMIN_IDS, settings_data, save_data
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
        save_data(db.SETTINGS_FILE, settings_data)
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
    global logger, bot, settings_data, payments_data, save_data
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
        save_data(db.PAYMENTS_FILE, payments_data)

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
            f"- Total Price: ${total_price:.2f}"
        )

        bot.send_message(message.chat.id, payment_instructions, parse_mode="Markdown")
        logger.info(f"Payment instructions sent to user {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error handling coin purchase: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id, "An error occurred. Returning to main menu.")

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
        settings_data = load_data(db.SETTINGS_FILE, db.DEFAULT_SETTINGS)
        support_username = settings_data.get("support_username", "admin")
        
        # Send support information
        support_message = (
            f"🆘 *Support*\n\n"
            f"If you need help or have any questions, please contact our support:\n"
            f"👤 @{support_username}\n\n"
            f"Please include your user ID: `{user_id}` in your message."
        )
        
        bot.send_message(message.chat.id, support_message, parse_mode="Markdown")
        logger.info(f"Support information sent to user {user_id}")
    except Exception as e:
        logger.error(f"Error handling support request: {e}")
        # Ensure user gets back to main menu even if there's an error
        restore_main_menu_keyboard(message.chat.id, "An error occurred. Returning to main menu.")

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
        manage_users_btn = types.InlineKeyboardButton("👥 Manage Users", callback_data="admin_manage_users")
        manage_admins_btn = types.InlineKeyboardButton("👑 Manage Admins", callback_data="admin_manage_admins")
        settings_btn = types.InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")
        stats_btn = types.InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")
        back_btn = types.InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")
        
        markup.add(manage_users_btn, manage_admins_btn, settings_btn, stats_btn, back_btn)
        
        bot.send_message(
            chat_id,
            "👑 *Admin Panel*\n\nSelect an option:",
            parse_mode="Markdown",
            reply_markup=markup
        )
        logger.info(f"Admin panel sent to chat_id {chat_id}")
    except Exception as e:
        logger.error(f"Error showing admin panel: {e}")
        bot.send_message(chat_id, f"Error: {str(e)}")

# Admin callback handler
@bot.callback_query_handler(func=lambda call: (call.data.startswith('admin_') and not call.data == "admin_back_to_panel") or call.data == "back_to_menu")
def admin_callback_handler(call):
    global logger, bot, types, settings_data, users_data, payments_data, orders_data
    logger.info(f"Admin callback from user {call.from_user.id}: {call.data}")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin callback attempt by user {call.from_user.id}")
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Handle different admin actions
        if call.data == "admin_manage_users":
            # Show user management options
            markup = types.InlineKeyboardMarkup(row_width=1)
            add_coins_btn = types.InlineKeyboardButton("💰 Add Coins to User", callback_data="admin_add_coins")
            create_user_btn = types.InlineKeyboardButton("👤 Create New User", callback_data="admin_create_user")
            view_users_btn = types.InlineKeyboardButton("👥 View All Users", callback_data="admin_view_users")
            back_btn = types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back_to_panel")
            
            markup.add(add_coins_btn, create_user_btn, view_users_btn, back_btn)
            
            bot.edit_message_text(
                "👥 *User Management*\n\nSelect an option:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            
        elif call.data == "admin_add_coins":
            # Ask for user ID to add coins to
            bot.edit_message_text(
                "💰 *Add Coins to User*\n\nPlease enter the user ID:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
            
            # Register next step handler
            bot.register_next_step_handler(call.message, admin_get_user_id_for_coins)
            
        elif call.data == "admin_settings":
            # Show settings options
            markup = types.InlineKeyboardMarkup(row_width=1)
            change_payment_btn = types.InlineKeyboardButton("💳 Change Payment Username", callback_data="admin_change_payment")
            change_support_btn = types.InlineKeyboardButton("🆘 Change Support Username", callback_data="admin_change_support")
            change_price_btn = types.InlineKeyboardButton("💲 Change Coin Price", callback_data="admin_change_price")
            back_btn = types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back_to_panel")
            
            markup.add(change_payment_btn, change_support_btn, change_price_btn, back_btn)
            
            bot.edit_message_text(
                "⚙️ *Settings*\n\nSelect an option:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            
        elif call.data == "admin_change_payment":
            # Ask for new payment username
            current_username = settings_data.get("payment_admin_username", "admin")
            
            bot.edit_message_text(
                f"💳 *Change Payment Username*\n\nCurrent payment username: @{current_username}\n\nPlease enter the new payment username (without @):",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
            
            # Register next step handler
            bot.register_next_step_handler(call.message, admin_change_payment_username)
            
        elif call.data == "admin_change_support":
            # Ask for new support username
            current_username = settings_data.get("support_username", "admin")
            
            bot.edit_message_text(
                f"🆘 *Change Support Username*\n\nCurrent support username: @{current_username}\n\nPlease enter the new support username (without @):",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
            
            # Register next step handler
            bot.register_next_step_handler(call.message, admin_change_support_username)
            
        elif call.data == "admin_change_price":
            # Ask for new coin price
            current_price = settings_data.get("price_per_1000", 0.034)
            
            bot.edit_message_text(
                f"💲 *Change Coin Price*\n\nCurrent price: ${current_price:.3f} per 1000 coins\n\nPlease enter the new price per 1000 coins (e.g., 0.034):",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
            
            # Register next step handler
            bot.register_next_step_handler(call.message, admin_change_coin_price)
            
        elif call.data == "admin_manage_admins":
            # Show admin management options
            markup = types.InlineKeyboardMarkup(row_width=1)
            
            # Add button to add new admin
            add_admin_btn = types.InlineKeyboardButton("➕ Add New Admin", callback_data="admin_add_new_admin")
            markup.add(add_admin_btn)
            
            # Add buttons for each existing admin (to remove)
            for admin_id in ADMIN_IDS:
                # Don't allow removing yourself
                if admin_id != call.from_user.id:
                    admin_btn = types.InlineKeyboardButton(f"❌ Remove Admin: {admin_id}", callback_data=f"admin_remove_{admin_id}")
                    markup.add(admin_btn)
            
            back_btn = types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back_to_panel")
            markup.add(back_btn)
            
            bot.edit_message_text(
                "👑 *Admin Management*\n\nCurrent admins:\n" + "\n".join([f"- {admin_id}" for admin_id in ADMIN_IDS]),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            
        elif call.data == "admin_stats":
            # Show statistics
            total_users = len(users_data)
            total_orders = len(orders_data)
            total_payments = len(payments_data)
            
            # Calculate completed orders
            completed_orders = sum(1 for order in orders_data if order.get("status") == "completed")
            
            # Calculate total coins in circulation
            total_coins = sum(user.get("coins", 0) for user_id, user in users_data.items())
            
            stats_message = (
                f"📊 *Bot Statistics*\n\n"
                f"👥 Total Users: {total_users}\n"
                f"📦 Total Orders: {total_orders}\n"
                f"✅ Completed Orders: {completed_orders}\n"
                f"💰 Total Coins in Circulation: {total_coins}\n"
                f"💳 Total Payments: {total_payments}\n"
            )
            
            markup = types.InlineKeyboardMarkup()
            back_btn = types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back_to_panel")
            markup.add(back_btn)
            
            bot.edit_message_text(
                stats_message,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            
        elif call.data == "back_to_menu":
            # Delete the admin panel message
            bot.delete_message(call.message.chat.id, call.message.message_id)
            
            # Show main menu
            restore_main_menu_keyboard(call.message.chat.id, "Returned to main menu.")
            
    except Exception as e:
        logger.error(f"Error handling admin callback: {e}")
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# Admin back to panel callback handler
@bot.callback_query_handler(func=lambda call: call.data == "admin_back_to_panel")
def admin_back_to_panel_callback(call):
    global logger, bot
    logger.info(f"Admin back to panel callback from user {call.from_user.id}")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin callback attempt by user {call.from_user.id}")
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Show admin panel
        show_admin_panel(call.message.chat.id)
        
        # Delete the previous message
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.error(f"Error handling admin back to panel callback: {e}")
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# Admin add new admin callback handler
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_new_admin")
def admin_add_new_admin_callback(call):
    global logger, bot
    logger.info(f"Admin add new admin callback from user {call.from_user.id}")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin callback attempt by user {call.from_user.id}")
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Ask for new admin ID
        bot.edit_message_text(
            "👑 *Add New Admin*\n\nPlease enter the user ID of the new admin:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        
        # Register next step handler
        bot.register_next_step_handler(call.message, process_new_admin_id)
    except Exception as e:
        logger.error(f"Error handling admin add new admin callback: {e}")
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# Process new admin ID
def process_new_admin_id(message):
    global logger, bot, ADMIN_IDS, settings_data, save_data
    logger.info(f"Processing new admin ID from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin action attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Parse the new admin ID
        try:
            new_admin_id = int(message.text.strip())
        except ValueError:
            bot.send_message(message.chat.id, "Invalid user ID. Please enter a valid numeric ID.")
            # Show admin panel again
            show_admin_panel(message.chat.id)
            return
            
        # Check if already an admin
        if new_admin_id in ADMIN_IDS:
            bot.send_message(message.chat.id, f"User {new_admin_id} is already an admin.")
            # Show admin panel again
            show_admin_panel(message.chat.id)
            return
            
        # Add to admin list
        ADMIN_IDS.append(new_admin_id)
        
        # Update settings
        settings_data["admin_ids"] = ADMIN_IDS
        save_data(db.SETTINGS_FILE, settings_data)
        
        bot.send_message(message.chat.id, f"User {new_admin_id} has been added as an admin.")
        logger.info(f"Added new admin: {new_admin_id}")
        
        # Show admin panel again
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error processing new admin ID: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")
        # Show admin panel again
        show_admin_panel(message.chat.id)

# Admin remove admin callback handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_remove_"))
def admin_remove_admin_callback(call):
    global logger, bot, ADMIN_IDS, settings_data, save_data
    logger.info(f"Admin remove admin callback from user {call.from_user.id}: {call.data}")
    
    try:
        # Check if user is an admin
        if call.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin callback attempt by user {call.from_user.id}")
            bot.answer_callback_query(call.id, "You are not authorized to access admin functions.")
            return
            
        # Parse the admin ID to remove
        admin_id_to_remove = int(call.data.replace("admin_remove_", ""))
        
        # Check if trying to remove self
        if admin_id_to_remove == call.from_user.id:
            bot.answer_callback_query(call.id, "You cannot remove yourself as an admin.")
            return
            
        # Remove from admin list
        if admin_id_to_remove in ADMIN_IDS:
            ADMIN_IDS.remove(admin_id_to_remove)
            
            # Update settings
            settings_data["admin_ids"] = ADMIN_IDS
            save_data(db.SETTINGS_FILE, settings_data)
            
            bot.answer_callback_query(call.id, f"Admin {admin_id_to_remove} has been removed.")
            logger.info(f"Removed admin: {admin_id_to_remove}")
            
            # Show admin panel again
            show_admin_panel(call.message.chat.id)
            
            # Delete the previous message
            bot.delete_message(call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, f"User {admin_id_to_remove} is not an admin.")
    except Exception as e:
        logger.error(f"Error handling admin remove admin callback: {e}")
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# Admin get user ID for coins
def admin_get_user_id_for_coins(message):
    global logger, bot, users_data
    logger.info(f"Admin getting user ID for coins from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin action attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Parse the user ID
        try:
            user_id = str(message.text.strip())
        except ValueError:
            bot.send_message(message.chat.id, "Invalid user ID. Please enter a valid ID.")
            # Show admin panel again
            show_admin_panel(message.chat.id)
            return
            
        # Check if user exists
        user = get_user(user_id)
        
        if not user:
            markup = types.InlineKeyboardMarkup(row_width=2)
            create_btn = types.InlineKeyboardButton("Create User", callback_data=f"admin_create_user_{user_id}")
            retry_btn = types.InlineKeyboardButton("Try Again", callback_data="admin_retry_user_id")
            back_btn = types.InlineKeyboardButton("Back", callback_data="admin_back_to_panel")
            markup.add(create_btn, retry_btn, back_btn)
            
            bot.send_message(
                message.chat.id,
                f"User {user_id} does not exist. Would you like to create this user?",
                reply_markup=markup
            )
            return
            
        # Ask for coin amount
        bot.send_message(
            message.chat.id,
            f"💰 *Add Coins to User*\n\nUser: {user_id}\nCurrent coins: {user.get('coins', 0)}\n\nPlease enter the amount of coins to add:",
            parse_mode="Markdown"
        )
        
        # Store user ID in admin's user data
        admin_id = str(message.from_user.id)
        if admin_id not in users_data:
            users_data[admin_id] = {}
        users_data[admin_id]["temp_add_coins_user_id"] = user_id
        
        # Register next step handler
        bot.register_next_step_handler(message, admin_add_coins_to_user)
    except Exception as e:
        logger.error(f"Error getting user ID for coins: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")
        # Show admin panel again
        show_admin_panel(message.chat.id)

# Admin add coins to user
def admin_add_coins_to_user(message):
    global logger, bot, users_data
    logger.info(f"Admin adding coins to user from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin action attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Get the user ID from admin's user data
        admin_id = str(message.from_user.id)
        if admin_id not in users_data or "temp_add_coins_user_id" not in users_data[admin_id]:
            bot.send_message(message.chat.id, "Error: User ID not found. Please try again.")
            # Show admin panel again
            show_admin_panel(message.chat.id)
            return
            
        user_id = users_data[admin_id]["temp_add_coins_user_id"]
        
        # Parse the coin amount
        try:
            coin_amount = int(message.text.strip())
            if coin_amount <= 0:
                bot.send_message(message.chat.id, "Invalid coin amount. Please enter a positive number.")
                bot.register_next_step_handler(message, admin_add_coins_to_user)
                return
        except ValueError:
            bot.send_message(message.chat.id, "Invalid coin amount. Please enter a valid number.")
            bot.register_next_step_handler(message, admin_add_coins_to_user)
            return
            
        # Get user data
        user = get_user(user_id)
        
        # Add coins
        current_coins = user.get("coins", 0)
        new_coins = current_coins + coin_amount
        user["coins"] = new_coins
        
        # Update user data
        update_user(user_id, user)
        
        # Clear temp data
        del users_data[admin_id]["temp_add_coins_user_id"]
        
        # Send confirmation
        bot.send_message(
            message.chat.id,
            f"💰 *Coins Added*\n\nAdded {coin_amount} coins to user {user_id}.\nNew balance: {new_coins} coins.",
            parse_mode="Markdown"
        )
        logger.info(f"Added {coin_amount} coins to user {user_id}")
        
        # Show admin panel again
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error adding coins to user: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")
        # Show admin panel again
        show_admin_panel(message.chat.id)

# Admin change payment username
def admin_change_payment_username(message):
    global logger, bot, settings_data, save_data
    logger.info(f"Admin changing payment username from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin action attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Parse the new username
        new_username = message.text.strip()
        
        # Remove @ if present
        if new_username.startswith('@'):
            new_username = new_username[1:]
            
        # Update settings
        settings_data["payment_admin_username"] = new_username
        save_data(db.SETTINGS_FILE, settings_data)
        
        # Send confirmation
        bot.send_message(
            message.chat.id,
            f"💳 *Payment Username Updated*\n\nPayment username has been updated to @{new_username}.",
            parse_mode="Markdown"
        )
        logger.info(f"Updated payment username to {new_username}")
        
        # Show admin panel again
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error changing payment username: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")
        # Show admin panel again
        show_admin_panel(message.chat.id)

# Admin change coin price
def admin_change_coin_price(message):
    global logger, bot, settings_data, save_data
    logger.info(f"Admin changing coin price from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin action attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Parse the new price
        try:
            new_price = float(message.text.strip())
            if new_price <= 0:
                bot.send_message(message.chat.id, "Invalid price. Please enter a positive number.")
                bot.register_next_step_handler(message, admin_change_coin_price)
                return
        except ValueError:
            bot.send_message(message.chat.id, "Invalid price. Please enter a valid number.")
            bot.register_next_step_handler(message, admin_change_coin_price)
            return
            
        # Update settings
        settings_data["price_per_1000"] = new_price
        save_data(db.SETTINGS_FILE, settings_data)
        
        # Send confirmation
        bot.send_message(
            message.chat.id,
            f"💲 *Coin Price Updated*\n\nCoin price has been updated to ${new_price:.3f} per 1000 coins.",
            parse_mode="Markdown"
        )
        logger.info(f"Updated coin price to {new_price}")
        
        # Show admin panel again
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error changing coin price: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")
        # Show admin panel again
        show_admin_panel(message.chat.id)

# Admin change support username
def admin_change_support_username(message):
    global logger, bot, settings_data, save_data
    logger.info(f"Admin changing support username from user {message.from_user.id}: {message.text}")
    
    try:
        # Check if user is an admin
        if message.from_user.id not in ADMIN_IDS:
            logger.warning(f"Unauthorized admin action attempt by user {message.from_user.id}")
            bot.send_message(message.chat.id, "You are not authorized to access admin functions.")
            return
            
        # Parse the new username
        new_username = message.text.strip()
        
        # Remove @ if present
        if new_username.startswith('@'):
            new_username = new_username[1:]
            
        # Update settings
        settings_data["support_username"] = new_username
        save_data(db.SETTINGS_FILE, settings_data)
        
        # Send confirmation
        bot.send_message(
            message.chat.id,
            f"🆘 *Support Username Updated*\n\nSupport username has been updated to @{new_username}.",
            parse_mode="Markdown"
        )
        logger.info(f"Updated support username to {new_username}")
        
        # Show admin panel again
        show_admin_panel(message.chat.id)
    except Exception as e:
        logger.error(f"Error changing support username: {e}")
        bot.send_message(message.chat.id, f"Error: {str(e)}")
        # Show admin panel again
        show_admin_panel(message.chat.id)

# View handler with cancel option
@bot.message_handler(func=lambda message: message.text == '👁 View')
def view_service(message):
    global logger, bot, types, users_data, get_user, update_user
    logger.info(f"Received View service request from user {message.from_user.id}")

    try:
        # Get or create user data
        user_id = str(message.from_user.id)
        user = get_user(user_id)
        
        # Initialize user data if needed
        if not user or user_id not in users_data:
            users_data[user_id] = {
                "coins": 0,
                "username": message.from_user.username or "",
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "orders": []
            }
            update_user(user_id, users_data[user_id])
        
        # Clear any pending input states
        if user_id in users_data:
            for key in list(users_data[user_id].keys()):
                if key.startswith('temp_'):
                    del users_data[user_id][key]
            
            # Save changes to database
            update_user(user_id, users_data[user_id])

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
    global logger, bot, types, users_data, update_user
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
            users_data[user_id] = {
                "coins": 0,
                "username": message.from_user.username or "",
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "orders": []
            }
        
        users_data[user_id]['temp_post_link'] = post_link
        
        # Save user data to ensure persistence
        update_user(user_id, users_data[user_id])
        
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
    global logger, bot, types, settings_data, users_data, get_user, update_user
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
        
        # Get user data from database to ensure we have the latest
        user = get_user(user_id)
        
        # If user doesn't exist in database, create a new user record
        if not user or user_id not in users_data:
            users_data[user_id] = {
                "coins": 0,
                "username": message.from_user.username or "",
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "orders": []
            }
            # Save the new user to database
            update_user(user_id, users_data[user_id])
        
        # Store the quantity in user session
        users_data[user_id]['temp_quantity'] = quantity
        
        # Calculate price based on quantity (1 coin per view)
        price = calculate_view_price(quantity)
        users_data[user_id]['temp_price'] = price
        
        # Make sure we have the post link
        if 'temp_post_link' not in users_data[user_id]:
            logger.error(f"Missing post link for user {user_id}")
            markup = get_cancel_keyboard()
            bot.send_message(
                message.chat.id,
                "Session error. Please start again by clicking 👁 View.",
                reply_markup=markup
            )
            restore_main_menu_keyboard(message.chat.id)
            return
        
        # Save user data to ensure persistence
        update_user(user_id, users_data[user_id])
        
        # Ensure user has coins field
        if 'coins' not in user:
            user['coins'] = 0
            update_user(message.from_user.id, user)
        
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
    global logger, bot, users_data, get_user, update_user, orders_data, save_data, order_timers
    logger.info(f"Received speed selection from user {call.from_user.id}: {call.data}")
    
    try:
        user_id = str(call.from_user.id)
        
        # Check if we have the necessary data
        if user_id not in users_data:
            # Try to get user data from database
            user = get_user(user_id)
            if not user:
                logger.error(f"User {user_id} not found in database")
                bot.answer_callback_query(call.id, "User not found. Please start again.")
                restore_main_menu_keyboard(call.message.chat.id)
                return
        
        # Check if temp data is missing
        if 'temp_quantity' not in users_data[user_id] or 'temp_price' not in users_data[user_id] or 'temp_post_link' not in users_data[user_id]:
            logger.error(f"Missing temporary data for user {user_id}")
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
        
        # Save user data to ensure persistence
        update_user(user_id, users_data[user_id])
        
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
        save_data(db.ORDERS_FILE, orders_data)
        
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
        
        # Update user data after clearing temp data
        update_user(user_id, users_data[user_id])
        
        # Restore main menu
        restore_main_menu_keyboard(call.message.chat.id)
        
        logger.info(f"Created order {order_id} for user {call.from_user.id}")
    except Exception as e:
        logger.error(f"Error handling speed selection: {e}")
        bot.answer_callback_query(call.id, "An error occurred")
        restore_main_menu_keyboard(call.message.chat.id)

# Generate a unique order ID
def generate_order_id():
    """Generate a unique order ID"""
    timestamp = int(time.time())
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"ORD_{timestamp}_{random_part}"

# Initialize data
if __name__ == "__main__":
    # Initialize data
    init_data()
    
    # Start the web server
    start_web_server()
    
    logger.info("Bot is starting...")
    
    # Start the bot
    try:
        bot.polling(none_stop=True, interval=BOT_POLLING_INTERVAL, timeout=BOT_POLLING_TIMEOUT)
    except Exception as e:
        logger.error(f"Error in bot polling: {e}")
        # Try to remove lock file on error
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logger.info(f"Lock file {lock_file} removed on error")
            except:
                pass
