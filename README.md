# Telegram Views Bot

A Telegram bot that helps users increase views on their Telegram posts.

## Features

- Add views to Telegram posts
- Multiple delivery speeds (Maximum, Slow, Drip Feed)
- Coin-based payment system
- Admin panel for management
- Support system

## Setup

1. Clone the repository:
```bash
git clone <your-repo-url>
cd telegram-views-bot
```

2. Create a virtual environment and activate it:
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Unix or MacOS:
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file with your configuration (copy from .env.example):
```bash
cp .env.example .env
# Then edit .env with your actual values
```

5. Create a `data` directory for storing bot data:
```bash
mkdir data
```

6. Run the bot:
```bash
python viewsbot.py
```

## Deployment to Replit

### Option 1: Deploy from GitHub (Recommended)

1. Fork this repository to your GitHub account
2. Create a new Replit project:
   - Go to [Replit](https://replit.com/new)
   - Choose "Import from GitHub"
   - Connect your GitHub account if needed
   - Select the forked repository
   - Click "Import from GitHub"

3. Set up environment variables in Replit:
   - Click on the "Secrets" tab (lock icon) in the left sidebar
   - Add the following secrets:
     - Key: `TELEGRAM_BOT_TOKEN`, Value: Your Telegram bot token
     - Key: `API_KEY`, Value: Your API key for the views service
     - Key: `ADMIN_IDS`, Value: Comma-separated list of admin user IDs

4. Run the setup script:
   - In the Replit Shell, type:
   ```
   python replit_setup.py
   ```

5. Run your bot:
   - Click the "Run" button at the top of the Replit interface

6. Keep your bot running 24/7 (Optional):
   - Go to [UptimeRobot](https://uptimerobot.com/) and create a free account
   - Add a new monitor of type "HTTP(s)"
   - Set the URL to your Replit project's web address (shown in the web view)
   - Set the monitoring interval to 5 minutes

### Option 2: Manual Deployment to Replit

1. Create a new Python Repl on Replit
2. Upload the following files:
   - viewsbot.py
   - requirements.txt
   - .replit
   - replit.nix
   - keep_alive.py
   - replit_setup.py

3. Follow steps 3-6 from Option 1

## Local Development

For local development, you need to:

1. Create a `.env` file with your configuration (see .env.example)
2. Install dependencies: `pip install -r requirements.txt`
3. Run the bot: `python viewsbot.py`

## Monitoring

- Check bot logs in the Replit console
- Monitor the data directory for order and user data

## Security Considerations

1. Keep your `.env` file secure and never commit it to version control
2. Regularly update dependencies for security patches
3. Use strong passwords and SSH keys for server access
4. Configure firewall rules to allow only necessary ports
5. Regularly backup the `data` directory

## Support

For support, contact the bot's support username configured in the settings. 