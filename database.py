import os
import json
import logging
from supabase import create_client, Client
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    logger.warning("Supabase credentials not found in environment variables. Using local JSON files as fallback.")
    USE_SUPABASE = False
else:
    try:
        supabase: Client = create_client(supabase_url, supabase_key)
        USE_SUPABASE = True
        logger.info("Supabase client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        USE_SUPABASE = False

# Table names
USERS_TABLE = "users"
ORDERS_TABLE = "orders"
PAYMENTS_TABLE = "payments"
SETTINGS_TABLE = "settings"

# Default settings
DEFAULT_SETTINGS = {
    "admin_ids": [],
    "price_per_1000": 0.034,
    "payment_username": "admin",
    "support_username": "admin"
}

# Fallback file paths for local storage
DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PAYMENTS_FILE = os.path.join(DATA_DIR, "payments.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOCK_FILE = os.path.join(DATA_DIR, "bot.lock")

# Ensure data directory exists
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

# Database operations
def load_data(table_name, file_path, default=None):
    """
    Load data from Supabase or local JSON file
    """
    if default is None:
        default = {}
    
    if USE_SUPABASE:
        try:
            if table_name == SETTINGS_TABLE:
                # For settings, we need to get the single settings record
                response = supabase.table(table_name).select("*").execute()
                if response.data and len(response.data) > 0:
                    return response.data[0]
                else:
                    # Create default settings if not exists
                    supabase.table(table_name).insert(DEFAULT_SETTINGS).execute()
                    return DEFAULT_SETTINGS
            else:
                # For other tables, get all records
                response = supabase.table(table_name).select("*").execute()
                
                if table_name == USERS_TABLE:
                    # Convert to dictionary with user_id as key
                    result = {}
                    for user in response.data:
                        user_id = user.pop("id")
                        result[user_id] = user
                    return result
                else:
                    # Return as list
                    return response.data or []
        except Exception as e:
            logger.error(f"Error loading data from Supabase {table_name}: {e}")
            # Fall back to local file
            return load_from_file(file_path, default)
    else:
        return load_from_file(file_path, default)

def load_from_file(file_path, default):
    """Helper function to load data from local JSON file"""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
        else:
            with open(file_path, 'w') as f:
                json.dump(default, f)
            return default
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return default

def save_data(table_name, file_path, data):
    """
    Save data to Supabase or local JSON file
    """
    if USE_SUPABASE:
        try:
            if table_name == USERS_TABLE:
                # Handle users table (dictionary with user_id as key)
                for user_id, user_data in data.items():
                    # Check if user exists
                    response = supabase.table(table_name).select("*").eq("id", user_id).execute()
                    
                    if response.data and len(response.data) > 0:
                        # Update existing user
                        supabase.table(table_name).update(user_data).eq("id", user_id).execute()
                    else:
                        # Insert new user with id
                        user_data["id"] = user_id
                        supabase.table(table_name).insert(user_data).execute()
            
            elif table_name == SETTINGS_TABLE:
                # Handle settings table (single record)
                response = supabase.table(table_name).select("*").execute()
                
                if response.data and len(response.data) > 0:
                    # Update existing settings
                    supabase.table(table_name).update(data).eq("id", response.data[0]["id"]).execute()
                else:
                    # Insert new settings
                    supabase.table(table_name).insert(data).execute()
            
            else:
                # Handle other tables (lists)
                # First delete all records
                supabase.table(table_name).delete().neq("id", "placeholder").execute()
                
                # Then insert all records
                if data and len(data) > 0:
                    supabase.table(table_name).insert(data).execute()
            
            logger.info(f"Successfully saved data to Supabase {table_name}")
            
            # Also save to local file as backup
            save_to_file(file_path, data)
            return True
        
        except Exception as e:
            logger.error(f"Error saving to Supabase {table_name}: {e}")
            # Fall back to local file
            return save_to_file(file_path, data)
    else:
        return save_to_file(file_path, data)

def save_to_file(file_path, data):
    """Helper function to save data to local JSON file"""
    try:
        # Ensure directory exists
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Successfully saved data to {file_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving to {file_path}: {e}")
        return False

def update_order_status(order_id, status, error=None, api_response=None):
    """
    Update the status of an order in the database
    """
    if USE_SUPABASE:
        try:
            # Get the current timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Prepare update data
            update_data = {
                "status": status,
                "updated_at": timestamp
            }
            
            if error:
                update_data["error"] = error
            
            if api_response:
                update_data["api_response"] = json.dumps(api_response)
            
            # Update the order in Supabase
            supabase.table(ORDERS_TABLE).update(update_data).eq("id", order_id).execute()
            logger.info(f"Updated order {order_id} status to {status} in Supabase")
            
            # Also update in local file
            orders_data = load_from_file(ORDERS_FILE, [])
            for order in orders_data:
                if order["id"] == order_id:
                    order["status"] = status
                    order["updated_at"] = timestamp
                    if error:
                        order["error"] = error
                    if api_response:
                        order["api_response"] = api_response
                    break
            
            save_to_file(ORDERS_FILE, orders_data)
            return True
        
        except Exception as e:
            logger.error(f"Error updating order status in Supabase: {e}")
            # Fall back to local file
            return update_order_status_local(order_id, status, error, api_response)
    else:
        return update_order_status_local(order_id, status, error, api_response)

def update_order_status_local(order_id, status, error=None, api_response=None):
    """Helper function to update order status in local file"""
    try:
        # Load orders data
        orders_data = load_from_file(ORDERS_FILE, [])
        
        # Get the current timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update the order
        for order in orders_data:
            if order["id"] == order_id:
                order["status"] = status
                order["updated_at"] = timestamp
                if error:
                    order["error"] = error
                if api_response:
                    order["api_response"] = api_response
                break
        
        # Save orders data
        save_to_file(ORDERS_FILE, orders_data)
        logger.info(f"Updated order {order_id} status to {status} in local file")
        return True
    
    except Exception as e:
        logger.error(f"Error updating order status in local file: {e}")
        return False

def get_user(user_id):
    """
    Get user data from database
    """
    user_id = str(user_id)  # Convert to string for JSON storage
    
    if USE_SUPABASE:
        try:
            # Check if user exists in Supabase
            response = supabase.table(USERS_TABLE).select("*").eq("id", user_id).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            else:
                # Create new user
                new_user = {
                    "id": user_id,
                    "coins": 0,
                    "username": "",
                    "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "orders": []
                }
                
                supabase.table(USERS_TABLE).insert(new_user).execute()
                logger.info(f"Created new user with ID {user_id} in Supabase")
                return new_user
        
        except Exception as e:
            logger.error(f"Error getting user from Supabase: {e}")
            # Fall back to local file
            return get_user_local(user_id)
    else:
        return get_user_local(user_id)

def get_user_local(user_id):
    """Helper function to get user from local file"""
    try:
        # Load users data
        users_data = load_from_file(USERS_FILE, {})
        
        # Initialize user if not exists
        if user_id not in users_data:
            users_data[user_id] = {
                "coins": 0,
                "username": "",
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "orders": []
            }
            
            # Save users data
            save_to_file(USERS_FILE, users_data)
            logger.info(f"Created new user with ID {user_id} in local file")
        
        # Ensure all required fields exist
        if "coins" not in users_data[user_id]:
            users_data[user_id]["coins"] = 0
        if "username" not in users_data[user_id]:
            users_data[user_id]["username"] = ""
        if "join_date" not in users_data[user_id]:
            users_data[user_id]["join_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if "orders" not in users_data[user_id]:
            users_data[user_id]["orders"] = []
        
        return users_data[user_id]
    
    except Exception as e:
        logger.error(f"Error getting user from local file: {e}")
        return {
            "coins": 0,
            "username": "",
            "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "orders": []
        }

def update_user(user_id, data):
    """
    Update user data in database or local file
    """
    global logger
    
    user_id = str(user_id)  # Convert to string for JSON storage
    
    # Create a copy of data without temp fields for Supabase
    supabase_data = {k: v for k, v in data.items() if not k.startswith('temp_')}
    
    if USE_SUPABASE:
        try:
            # Update in Supabase
            supabase.table(USERS_TABLE).update(supabase_data).eq("id", user_id).execute()
            logger.info(f"Updated user {user_id} in Supabase")
        except Exception as e:
            logger.error(f"Error updating user in Supabase: {e}")
            # Fall back to local file
            update_user_in_file(user_id, data)
    else:
        # Update in local file
        update_user_in_file(user_id, data)
        
    return data

def update_user_in_file(user_id, data):
    """Helper function to update user in local file"""
    try:
        users_data = load_from_file(USERS_FILE, {})
        users_data[user_id] = data
        save_to_file(USERS_FILE, users_data)
        logger.info(f"Updated user {user_id} in local file")
        return True
    except Exception as e:
        logger.error(f"Error updating user in local file: {e}")
        return False

def add_order(order_data):
    """
    Add a new order to the database
    """
    if USE_SUPABASE:
        try:
            # Insert order into Supabase
            supabase.table(ORDERS_TABLE).insert(order_data).execute()
            logger.info(f"Added order {order_data['id']} to Supabase")
            
            # Also add to local file
            orders_data = load_from_file(ORDERS_FILE, [])
            orders_data.append(order_data)
            save_to_file(ORDERS_FILE, orders_data)
            return True
        
        except Exception as e:
            logger.error(f"Error adding order to Supabase: {e}")
            # Fall back to local file
            return add_order_local(order_data)
    else:
        return add_order_local(order_data)

def add_order_local(order_data):
    """Helper function to add order to local file"""
    try:
        # Load orders data
        orders_data = load_from_file(ORDERS_FILE, [])
        
        # Add order
        orders_data.append(order_data)
        
        # Save orders data
        save_to_file(ORDERS_FILE, orders_data)
        logger.info(f"Added order {order_data['id']} to local file")
        return True
    
    except Exception as e:
        logger.error(f"Error adding order to local file: {e}")
        return False

def get_settings():
    """
    Get settings from database
    """
    if USE_SUPABASE:
        try:
            # Get settings from Supabase
            response = supabase.table(SETTINGS_TABLE).select("*").execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            else:
                # Create default settings
                supabase.table(SETTINGS_TABLE).insert(DEFAULT_SETTINGS).execute()
                logger.info("Created default settings in Supabase")
                return DEFAULT_SETTINGS
        
        except Exception as e:
            logger.error(f"Error getting settings from Supabase: {e}")
            # Fall back to local file
            return get_settings_local()
    else:
        return get_settings_local()

def get_settings_local():
    """Helper function to get settings from local file"""
    try:
        # Load settings data
        settings_data = load_from_file(SETTINGS_FILE, DEFAULT_SETTINGS)
        return settings_data
    
    except Exception as e:
        logger.error(f"Error getting settings from local file: {e}")
        return DEFAULT_SETTINGS

def update_settings(settings_data):
    """
    Update settings in database
    """
    if USE_SUPABASE:
        try:
            # Check if settings exist
            response = supabase.table(SETTINGS_TABLE).select("*").execute()
            
            if response.data and len(response.data) > 0:
                # Update existing settings
                supabase.table(SETTINGS_TABLE).update(settings_data).eq("id", response.data[0]["id"]).execute()
            else:
                # Insert new settings
                supabase.table(SETTINGS_TABLE).insert(settings_data).execute()
            
            logger.info("Updated settings in Supabase")
            
            # Also update in local file
            save_to_file(SETTINGS_FILE, settings_data)
            return True
        
        except Exception as e:
            logger.error(f"Error updating settings in Supabase: {e}")
            # Fall back to local file
            return update_settings_local(settings_data)
    else:
        return update_settings_local(settings_data)

def update_settings_local(settings_data):
    """Helper function to update settings in local file"""
    try:
        # Save settings data
        save_to_file(SETTINGS_FILE, settings_data)
        logger.info("Updated settings in local file")
        return True
    
    except Exception as e:
        logger.error(f"Error updating settings in local file: {e}")
        return False

def get_orders():
    """
    Get all orders from database
    """
    if USE_SUPABASE:
        try:
            # Get orders from Supabase
            response = supabase.table(ORDERS_TABLE).select("*").execute()
            return response.data or []
        
        except Exception as e:
            logger.error(f"Error getting orders from Supabase: {e}")
            # Fall back to local file
            return get_orders_local()
    else:
        return get_orders_local()

def get_orders_local():
    """Helper function to get orders from local file"""
    try:
        # Load orders data
        orders_data = load_from_file(ORDERS_FILE, [])
        return orders_data
    
    except Exception as e:
        logger.error(f"Error getting orders from local file: {e}")
        return []

def get_order(order_id):
    """
    Get an order by ID from database
    """
    if USE_SUPABASE:
        try:
            # Get order from Supabase
            response = supabase.table(ORDERS_TABLE).select("*").eq("id", order_id).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            else:
                return None
        
        except Exception as e:
            logger.error(f"Error getting order from Supabase: {e}")
            # Fall back to local file
            return get_order_local(order_id)
    else:
        return get_order_local(order_id)

def get_order_local(order_id):
    """Helper function to get order from local file"""
    try:
        # Load orders data
        orders_data = load_from_file(ORDERS_FILE, [])
        
        # Find order by ID
        for order in orders_data:
            if order["id"] == order_id:
                return order
        
        return None
    
    except Exception as e:
        logger.error(f"Error getting order from local file: {e}")
        return None

def add_payment(payment_data):
    """
    Add a new payment to the database
    """
    if USE_SUPABASE:
        try:
            # Insert payment into Supabase
            supabase.table(PAYMENTS_TABLE).insert(payment_data).execute()
            logger.info(f"Added payment {payment_data['id']} to Supabase")
            
            # Also add to local file
            payments_data = load_from_file(PAYMENTS_FILE, [])
            payments_data.append(payment_data)
            save_to_file(PAYMENTS_FILE, payments_data)
            return True
        
        except Exception as e:
            logger.error(f"Error adding payment to Supabase: {e}")
            # Fall back to local file
            return add_payment_local(payment_data)
    else:
        return add_payment_local(payment_data)

def add_payment_local(payment_data):
    """Helper function to add payment to local file"""
    try:
        # Load payments data
        payments_data = load_from_file(PAYMENTS_FILE, [])
        
        # Add payment
        payments_data.append(payment_data)
        
        # Save payments data
        save_to_file(PAYMENTS_FILE, payments_data)
        logger.info(f"Added payment {payment_data['id']} to local file")
        return True
    
    except Exception as e:
        logger.error(f"Error adding payment to local file: {e}")
        return False

def get_payments():
    """
    Get all payments from database
    """
    if USE_SUPABASE:
        try:
            # Get payments from Supabase
            response = supabase.table(PAYMENTS_TABLE).select("*").execute()
            return response.data or []
        
        except Exception as e:
            logger.error(f"Error getting payments from Supabase: {e}")
            # Fall back to local file
            return get_payments_local()
    else:
        return get_payments_local()

def get_payments_local():
    """Helper function to get payments from local file"""
    try:
        # Load payments data
        payments_data = load_from_file(PAYMENTS_FILE, [])
        return payments_data
    
    except Exception as e:
        logger.error(f"Error getting payments from local file: {e}")
        return []

def test_connection():
    """
    Test if the database connection is working
    """
    if not USE_SUPABASE:
        # Check if data directory exists
        if not os.path.exists(DATA_DIR):
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                return True
            except Exception as e:
                logger.error(f"Failed to create data directory: {e}")
                return False
        return True
    
    try:
        # Try to fetch a single row from the settings table
        result = supabase.table(SETTINGS_TABLE).select("*").limit(1).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")
        return False 
