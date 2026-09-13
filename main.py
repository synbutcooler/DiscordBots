import os
import time
import logging
import threading
import requests
from flask import Flask, request, jsonify
from config import DISCORD_TOKEN, DISCORD_KEY_API_SECRET
from key_store import get_key, delete_key, lock_hwid, GUILD_ID
from discord_bot import start_bot
from stickied_message_bot import start_stickied_bot
# Imported for the /health gateway probe only.
from discord_bot import bot as main_bot
from stickied_message_bot import bot as stickied_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://vadriftzbots.onrender.com")

def keep_alive_loop():
    time.sleep(30)
    while True:
        try:
            resp = requests.get(f"{SELF_URL}/health", timeout=15)
            logger.info(f"Keep-alive ping: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")
        time.sleep(600)

def _run_forever(label, target):
    """Keep a bot alive. Without this, one exception kills the daemon thread and
    Flask goes on serving /health 200 while the bot is permanently offline."""
    backoff = 5
    while True:
        try:
            target()
        except Exception:
            logger.exception("%s stopped unexpectedly", label)
        logger.warning("%s is down; restarting in %ss", label, backoff)
        time.sleep(backoff)
        backoff = min(backoff * 2, 300)   # cap at 5 minutes
        # A successful long run would ideally reset backoff, but bot.run() only
        # returns when the client has already died, so we never see that here.

@app.route('/health')
def health():
    """Report real gateway state. A hardcoded 200 hides a dead bot from Render,
    from uptime monitors, and from you."""
    def probe(b):
        """Gateway round-trip in ms, or None if this bot is not connected.
        latency is NaN until the first heartbeat completes, and jsonify(nan)
        emits invalid JSON, so NaN is mapped to None."""
        try:
            ws = getattr(b, "ws", None)
            if ws is None or ws.is_closed():
                return None
            ms = b.latency * 1000
            if ms != ms or ms in (float("inf"), float("-inf")):   # NaN / inf
                return None
            return round(ms, 1)
        except Exception:
            return None

    main_ms, stickied_ms = probe(main_bot), probe(stickied_bot)
    ok = main_ms is not None and stickied_ms is not None
    return jsonify({
        "status": "healthy" if ok else "degraded",
        "main_bot_gateway_ms": main_ms,
        "stickied_bot_gateway_ms": stickied_ms,
    }), (200 if ok else 503)

@app.route('/')
def index():
    return jsonify({"status": "Bot server running"}), 200


@app.route('/api/validate-discord-key', methods=['POST'])
def validate_discord_key():
    data = request.get_json()
    if not data:
        return jsonify({"valid": False, "message": "No data provided"})

    secret = data.get("secret", "")
    key_value = data.get("key", "")
    hwid = data.get("hwid", "")

    if secret != DISCORD_KEY_API_SECRET:
        return jsonify({"valid": False, "message": "Unauthorized"})

    if not key_value or not hwid:
        return jsonify({"valid": False, "message": "Missing key or HWID"})

    key_data = get_key(key_value)
    if not key_data:
        return jsonify({"valid": False, "message": "Invalid key"})

    if time.time() > key_data.get("expires_at", 0):
        delete_key(key_value)
        return jsonify({"valid": False, "message": "Key expired. Run /getkey in Discord."})

    discord_id = key_data.get("discord_id")

    try:
        headers = {
            "Authorization": f"Bot {DISCORD_TOKEN}"
        }
        membership_url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{discord_id}"
        resp = requests.get(membership_url, headers=headers, timeout=10)
        logger.info(f"Discord membership check for {discord_id}: status {resp.status_code}")

        if resp.status_code == 404:
            delete_key(key_value)
            return jsonify({"valid": False, "message": "You must be in the Discord server."})
        elif resp.status_code != 200:
            logger.warning(f"Discord API returned {resp.status_code}, not deleting key")
            return jsonify({"valid": False, "message": "Verification error. Try again later."})
    except Exception as e:
        logger.error(f"Discord API error: {e}")
        return jsonify({"valid": False, "message": "Verification error. Try again later."})

    if key_data.get("hwid") and key_data["hwid"] != hwid:
        return jsonify({"valid": False, "message": "Key is locked to a different device. Use /resetkey in Discord."})

    if not key_data.get("hwid"):
        lock_hwid(key_value, hwid)

    return jsonify({"valid": True, "message": "Authenticated"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))

    def start_bots_delayed():
        time.sleep(5)
        main_present = bool((DISCORD_TOKEN or "").strip())
        sticky_present = bool((os.environ.get("STICKIED_TOKEN") or "").strip())
        same_token = main_present and sticky_present and DISCORD_TOKEN == os.environ.get("STICKIED_TOKEN")
        logger.info(
            "Discord bot token check: main=%s stickied=%s distinct=%s",
            "set" if main_present else "MISSING",
            "set" if sticky_present else "MISSING",
            "no" if same_token else "yes",
        )
        if same_token:
            logger.error(
                "DISCORD_TOKEN and STICKIED_TOKEN are identical. They must be two different Discord bot tokens."
            )
        logger.info("Starting main bot...")
        bot_thread = threading.Thread(
            target=_run_forever, args=("Main bot", start_bot),
            daemon=True, name="main-discord-bot",
        )
        bot_thread.start()
        time.sleep(10)
        logger.info("Starting stickied message bot...")
        stickied_bot_thread = threading.Thread(
            target=_run_forever, args=("Stickied bot", start_stickied_bot),
            daemon=True, name="stickied-discord-bot",
        )
        stickied_bot_thread.start()

    bots_thread = threading.Thread(target=start_bots_delayed, daemon=True)
    bots_thread.start()

    keep_alive_thread = threading.Thread(target=keep_alive_loop, daemon=True)
    keep_alive_thread.start()

    logger.info(f"Bot server starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
