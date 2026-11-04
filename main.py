import logging
import os
import threading
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in .env file")

import bot as discord_bot
from app import app

if FLASK_HOST in LOOPBACK_HOSTS:
    # Rejects requests with a foreign Host header (DNS rebinding protection).
    app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1", "[::1]"]


def run_flask():
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"Web UI running at http://localhost:{FLASK_PORT}")
    discord_bot.run_bot(DISCORD_TOKEN, log_level=logging.getLevelName(LOG_LEVEL))
