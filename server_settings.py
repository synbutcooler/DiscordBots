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
# Warm cache so /fun buttons can ACK Discord within 3s even if Mongo is slow.
_SETTINGS_CACHE = {}

if MONGODB_URI:
    try:
        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=1500,
            connectTimeoutMS=1500,
            socketTimeoutMS=2000,
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
    "fun_mommy": False,     # Gemini mommy persona (owner guild only)
}


def peek_settings(guild_id) -> dict:
    """Never hits Mongo. Cache if warm, otherwise defaults."""
    cached = _SETTINGS_CACHE.get(str(guild_id))
    return dict(cached) if cached is not None else dict(DEFAULTS)


def apply_settings_local(guild_id, updates: dict) -> dict:
    """Optimistic in-memory write so the UI can ACK before Mongo finishes."""
    merged = peek_settings(guild_id)
    merged.update(updates)
    _SETTINGS_CACHE[str(guild_id)] = merged
    return dict(merged)


def get_settings(guild_id) -> dict:
    cached = _SETTINGS_CACHE.get(str(guild_id))
    if settings_collection is None:
        return dict(cached) if cached is not None else dict(DEFAULTS)
    try:
        doc = settings_collection.find_one({"guild_id": str(guild_id)})
    except Exception as e:
        logger.error(f"Failed to load settings for {guild_id}: {e}")
        return dict(cached) if cached is not None else dict(DEFAULTS)
    if not doc:
        merged = dict(DEFAULTS)
    else:
        merged = dict(DEFAULTS)
        for k in DEFAULTS:
            if k in doc:
                merged[k] = doc[k]
    _SETTINGS_CACHE[str(guild_id)] = merged
    return dict(merged)


def update_settings(guild_id, updates: dict):
    apply_settings_local(guild_id, updates)
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
