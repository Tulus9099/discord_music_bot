import os
import threading
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in .env file")

import bot as discord_bot
from app import app


def run_flask():
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"Web UI running at http://localhost:{FLASK_PORT}")
    discord_bot.run_bot(DISCORD_TOKEN)
