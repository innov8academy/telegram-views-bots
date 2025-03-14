import os
import zipfile
import shutil
import re
import sys

def setup_replit():
    """Set up the Telegram bot on Replit by extracting files and patching code."""
    print("=== Telegram Bot Setup for Replit ===")
    
    # Step 1: Extract the zip file if it exists
    if os.path.exists("replit_deployment.zip"):
        if extract_zip():
            print("✅ Extraction completed")
        else:
            print("❌ Extraction failed")
            return False
    else:
        print("⚠️ No zip file found, skipping extraction")
    
    # Step 2: Check if all required files exist
    required_files = ["viewsbot.py", "requirements.txt", ".replit", "replit.nix", "keep_alive.py"]
    missing_files = [file for file in required_files if not os.path.exists(file)]
    
    if missing_files:
        print(f"❌ Missing required files: {', '.join(missing_files)}")
        print("Please make sure all required files are uploaded to your Replit project.")
        return False
    
    # Step 3: Patch viewsbot.py for Replit compatibility
    if patch_viewsbot():
        print("✅ Patched viewsbot.py for Replit compatibility")
    else:
        print("❌ Failed to patch viewsbot.py")
        return False
    
    # Step 4: Create data directory if it doesn't exist
    if not os.path.exists("data"):
        os.makedirs("data")
        print("✅ Created data directory")
    
    # Step 5: Final instructions
    print("\n=== Setup Complete! ===")
    print("To finish setting up your bot:")
    print("1. Set up your environment variables in the Secrets tab (lock icon):")
    print("   - TELEGRAM_BOT_TOKEN: Your Telegram bot token")
    print("   - ADMIN_IDS: Comma-separated list of admin user IDs")
    print("2. Click 'Run' to start your bot")
    print("3. Set up an uptime monitor (like UptimeRobot) to keep your bot running 24/7")
    
    return True

def extract_zip():
    """Extract the replit_deployment.zip file."""
    try:
        print("\n--- Extracting replit_deployment.zip ---")
        
        # Extract the zip file
        with zipfile.ZipFile("replit_deployment.zip", 'r') as zip_ref:
            zip_ref.extractall("temp_extract")
        
        # Move files to the main directory
        for item in os.listdir("temp_extract"):
            item_path = os.path.join("temp_extract", item)
            dest_path = os.path.join(".", item)
            
            # Handle existing files/directories
            if os.path.exists(dest_path):
                if os.path.isdir(dest_path):
                    # For directories, merge contents
                    for subitem in os.listdir(item_path):
                        subitem_path = os.path.join(item_path, subitem)
                        subdest_path = os.path.join(dest_path, subitem)
                        if os.path.exists(subdest_path):
                            if os.path.isfile(subdest_path):
                                os.remove(subdest_path)
                            else:
                                shutil.rmtree(subdest_path)
                        shutil.move(subitem_path, dest_path)
                else:
                    # For files, replace them
                    os.remove(dest_path)
                    shutil.move(item_path, dest_path)
            else:
                # If destination doesn't exist, just move
                shutil.move(item_path, dest_path)
        
        # Clean up
        shutil.rmtree("temp_extract")
        
        return True
    
    except Exception as e:
        print(f"Error during extraction: {e}")
        if os.path.exists("temp_extract"):
            shutil.rmtree("temp_extract")
        return False

def patch_viewsbot():
    """Patch the viewsbot.py file to make it compatible with Replit."""
    try:
        print("\n--- Patching viewsbot.py ---")
        
        # Create a backup of the original file
        shutil.copy("viewsbot.py", "viewsbot.py.backup")
        
        # Read the file content
        with open("viewsbot.py", "r", encoding="utf-8") as f:
            content = f.read()
        
        # Add imports at the top
        if "from keep_alive import keep_alive" not in content:
            import_pattern = r"(import .*?\n|from .*?\n)"
            imports_match = re.search(import_pattern, content)
            if imports_match:
                # Find the last import statement
                last_import = None
                for match in re.finditer(import_pattern, content):
                    last_import = match
                
                if last_import:
                    # Insert after the last import
                    position = last_import.end()
                    content = content[:position] + "\nfrom keep_alive import keep_alive\nimport os\n" + content[position:]
            else:
                # No imports found, add at the beginning
                content = "from keep_alive import keep_alive\nimport os\n\n" + content
        
        # Add keep_alive() call before bot.polling()
        if "keep_alive()" not in content:
            polling_pattern = r"bot\.polling\("
            match = re.search(polling_pattern, content)
            if match:
                position = match.start()
                # Find the beginning of the line
                line_start = content.rfind("\n", 0, position)
                if line_start == -1:
                    line_start = 0
                else:
                    line_start += 1  # Skip the newline character
                
                # Insert keep_alive() call before bot.polling()
                indent = ""
                for i in range(line_start, position):
                    if content[i] in (" ", "\t"):
                        indent += content[i]
                    else:
                        break
                
                content = content[:line_start] + indent + "# Start the keep_alive web server\n" + indent + "keep_alive()\n\n" + indent + content[line_start:]
        
        # Replace hardcoded bot token with environment variable
        token_pattern = r"bot\s*=\s*telebot\.TeleBot\(['\"]([^'\"]+)['\"]\)"
        content = re.sub(token_pattern, r"bot = telebot.TeleBot(os.environ.get('TELEGRAM_BOT_TOKEN'))", content)
        
        # Replace hardcoded admin IDs with environment variable
        admin_pattern = r"ADMIN_IDS\s*=\s*\[([\d\s,]+)\]"
        if re.search(admin_pattern, content):
            content = re.sub(admin_pattern, r"ADMIN_IDS = [int(admin_id.strip()) for admin_id in os.environ.get('ADMIN_IDS', '').split(',') if admin_id.strip()]", content)
        
        # Make sure data directory exists
        data_dir_code = "\n# Make sure data directory exists\nos.makedirs('data', exist_ok=True)\n"
        if "os.makedirs('data', exist_ok=True)" not in content:
            # Find a good place to insert this code (after imports but before main code)
            if "from keep_alive import keep_alive" in content:
                position = content.find("from keep_alive import keep_alive")
                position = content.find("\n", position)
                if position != -1:
                    position += 1  # Skip the newline character
                    content = content[:position] + data_dir_code + content[position:]
            else:
                # Insert after the first few lines
                lines = content.split("\n", 10)
                content = "\n".join(lines[:5]) + data_dir_code + "\n".join(lines[5:])
        
        # Replace absolute file paths with relative paths
        content = re.sub(r'["\']\/[^"\']*\/data\/', r'"data/', content)
        content = re.sub(r'["\']C:\\[^"\']*\\data\\', r'"data/', content)
        
        # Write the modified content back to the file
        with open("viewsbot.py", "w", encoding="utf-8") as f:
            f.write(content)
        
        return True
    
    except Exception as e:
        print(f"Error during patching: {e}")
        if os.path.exists("viewsbot.py.backup"):
            shutil.copy("viewsbot.py.backup", "viewsbot.py")
        return False

if __name__ == "__main__":
    setup_replit() 