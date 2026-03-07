import os
import time
import threading
import requests
import logging

from discord_bot import start_bot
from stickied_message_bot import start_stickied_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

PING_URL = "https://vadrifts.onrender.com/health"

def server_pinger():
    while True:
        try:
            r = requests.get(PING_URL, timeout=10)
            logger.info(f"Ping {PING_URL} -> {r.status_code}")
        except Exception as e:
            logger.warning(f"Ping failed: {e}")
        time.sleep(300)

if __name__ == '__main__':
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    time.sleep(10)

    stickied_bot_thread = threading.Thread(target=start_stickied_bot, daemon=True)
    stickied_bot_thread.start()

    ping_thread = threading.Thread(target=server_pinger, daemon=True)
    ping_thread.start()

    logger.info("All bots started. Running...")

    while True:
        time.sleep(60)
