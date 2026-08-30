import html
import os
import time
import logging
import threading
import requests
from flask import Flask, request, jsonify
from config import DISCORD_TOKEN, DISCORD_KEY_API_SECRET
from key_store import get_key, delete_key, lock_hwid, GUILD_ID
import obf_access
from discord_bot import start_bot
from stickied_message_bot import start_stickied_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def get_client_ip():
    """Render puts the real client first in X-Forwarded-For."""
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",", 1)[0].strip()
    return client_ip


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

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/')
def index():
    return jsonify({"status": "Bot server running"}), 200

_OBF_RESULT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:system-ui;background:#1e1f22;color:#dbdee1;display:grid;place-items:center;
min-height:100vh;margin:0;padding:24px}}.card{{max-width:500px;background:#2b2d31;border-radius:12px;
padding:30px;text-align:center;box-shadow:0 8px 24px #0006}}h1{{font-size:22px;color:{color}}}
p{{line-height:1.55}}code{{background:#1e1f22;padding:3px 6px;border-radius:5px}}
</style></head><body><div class="card"><h1>{title}</h1><p>{message}</p>
</div></body></html>"""


def _obf_result(title, message, success=False):
    return _OBF_RESULT_PAGE.format(
        title=html.escape(title),
        message=html.escape(message),
        color="#57f287" if success else "#ed4245",
    )


@app.route('/obf/claim', methods=['GET'])
def obf_claim_callback():
    """Immutable destination of OBF_STATIC_LINK; confirms but never grants."""
    try:
        session = obf_access.complete_static_callback(
            get_client_ip(), request.headers.get("Referer", "")
        )
    except obf_access.AccessError as exc:
        logger.warning("Static obfuscator callback rejected: %s", exc)
        return _obf_result("Verification rejected", str(exc)), 403
    except Exception:
        logger.exception("Static obfuscator callback failed")
        return _obf_result(
            "Verification unavailable",
            "The verification could not be saved. Return to Discord and try again.",
        ), 503

    logger.info(
        "Static obfuscator callback confirmed session=%s",
        session["token"][:8],
    )
    return _obf_result(
        "Verification complete",
        "Return to Discord and press Claim Access. This page does not grant access by itself.",
        success=True,
    ), 200


@app.route('/obf/claim', methods=['POST'])
def obf_claim_legacy_post():
    return _obf_result(
        "Old code form retired",
        "Return to Discord, run .obfunlock, and use the Vadrifts verification page.",
    ), 410


@app.route('/obf/unlock/<token>')
def obf_unlock_retired(token):
    return _obf_result(
        "Old verification link retired",
        "Return to Discord and run .obfunlock for a current verification session.",
    ), 410


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
        logger.info("Starting main bot...")
        bot_thread = threading.Thread(target=start_bot, daemon=True)
        bot_thread.start()
        time.sleep(10)
        logger.info("Starting stickied message bot...")
        stickied_bot_thread = threading.Thread(target=start_stickied_bot, daemon=True)
        stickied_bot_thread.start()

    bots_thread = threading.Thread(target=start_bots_delayed, daemon=True)
    bots_thread.start()

    keep_alive_thread = threading.Thread(target=keep_alive_loop, daemon=True)
    keep_alive_thread.start()

    logger.info(f"Bot server starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
