"""Per-guild settings for bot features (anti-scam protection, etc.).

Stored in MongoDB so configuration survives restarts. The key system has its
own collections; this is purely for the bot's optional global features.
"""
import os
import logging
from pymongo import MongoClient

logger = logging.getLogger(__name__)

MONGODB_URI = os.environ.get("MONGODB_URI")

settings_collection = None

if MONGODB_URI:
    try:
        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )
        _db = _client["vadrifts_bots"]
        settings_collection = _db["server_settings"]
        settings_collection.create_index("guild_id", unique=True)
        logger.info("Connected to MongoDB for server settings")
    except Exception as e:
        logger.error(f"Server settings DB connection failed: {e}")

DEFAULTS = {
    "antispam_enabled": False,
    # Empty list = protect every channel; otherwise only the listed channels.
    "antispam_channels": [],
    # Fun features (per-guild toggles, configured via /fun).
    "fun_meow": True,       # bot replies to all-"meow" messages (on by default)
    "fun_goodboy": False,   # says "good boy" after a boost (off by default)
    "fun_mommy": False,
}


def get_settings(guild_id) -> dict:
    if settings_collection is None:
        return dict(DEFAULTS)
    try:
        doc = settings_collection.find_one({"guild_id": str(guild_id)})
    except Exception as e:
        logger.error(f"Failed to load settings for {guild_id}: {e}")
        return dict(DEFAULTS)
    if not doc:
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in doc:
            merged[k] = doc[k]
    return merged


def update_settings(guild_id, updates: dict):
    if settings_collection is None:
        return False
    try:
        settings_collection.update_one(
            {"guild_id": str(guild_id)},
            {"$set": updates},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update settings for {guild_id}: {e}")
        return False


def antispam_active(guild_id, channel_id) -> bool:
    """True if anti-scam deletion should run for this channel."""
    s = get_settings(guild_id)
    if not s.get("antispam_enabled"):
        return False
    channels = s.get("antispam_channels") or []
    if not channels:
        return True  # enabled server-wide
    return str(channel_id) in {str(c) for c in channels}
