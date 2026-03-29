import os
import time
import secrets
import string
import logging
from pymongo import MongoClient, ASCENDING

logger = logging.getLogger(__name__)

MONGODB_URI = os.environ.get("MONGODB_URI")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
SERVER_BASE_URL = os.environ.get("SERVER_BASE_URL", "https://vadrifts.onrender.com")

guild_configs_collection = None
guild_sessions_collection = None
guild_keys_collection = None

if MONGODB_URI:
    try:
        _client = MongoClient(MONGODB_URI)
        _db = _client["vadrifts"]
        guild_configs_collection = _db["guild_key_configs"]
        guild_sessions_collection = _db["guild_key_sessions"]
        guild_keys_collection = _db["guild_keys"]

        guild_sessions_collection.create_index("expires_at", expireAfterSeconds=0)
        guild_sessions_collection.create_index([("guild_id", ASCENDING), ("discord_id", ASCENDING)])
        guild_sessions_collection.create_index([("guild_id", ASCENDING), ("ip", ASCENDING), ("completed", ASCENDING)])
        guild_keys_collection.create_index("expires_at")
        guild_keys_collection.create_index([("guild_id", ASCENDING), ("discord_id", ASCENDING)])
        guild_configs_collection.create_index("api_secret", unique=True, sparse=True)

        _client.admin.command('ping')
        logger.info("Connected to MongoDB for guild key system")
    except Exception as e:
        logger.error(f"MongoDB guild key system connection failed: {e}")
else:
    logger.warning("MONGODB_URI not set for guild key system")


# ─── Utility ───

def _generate_api_secret():
    return secrets.token_urlsafe(32)


def _generate_key_string():
    parts = []
    for _ in range(4):
        parts.append(''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5)))
    return '-'.join(parts)


def get_destination_url(guild_id):
    return f"{SERVER_BASE_URL}/ks/done/{guild_id}"


# ─── Guild Config ───

def get_guild_config(guild_id):
    if guild_configs_collection is None:
        return None
    try:
        doc = guild_configs_collection.find_one({"_id": str(guild_id)})
        if doc:
            config = {k: v for k, v in doc.items() if k != "_id"}
            config["guild_id"] = doc["_id"]
            return config
        return None
    except Exception as e:
        logger.error(f"Failed to get guild config: {e}")
        return None


def save_guild_config(guild_id, config_data):
    if guild_configs_collection is None:
        return False
    try:
        doc = dict(config_data)
        doc.pop("guild_id", None)
        guild_configs_collection.update_one(
            {"_id": str(guild_id)},
            {"$set": doc},
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save guild config: {e}")
        return False


def init_guild_config(guild_id, guild_name, admin_id, key_duration_hours=24, min_completion_seconds=25):
    existing = get_guild_config(guild_id)
    if existing and existing.get("api_secret"):
        api_secret = existing["api_secret"]
    else:
        api_secret = _generate_api_secret()

    config = {
        "guild_name": guild_name,
        "admin_id": str(admin_id),
        "api_secret": api_secret,
        "enabled": True,
        "key_duration_hours": key_duration_hours,
        "min_completion_seconds": min_completion_seconds,
        "require_membership": True,
        "workink_url": existing.get("workink_url", "") if existing else "",
        "lootlabs_url": existing.get("lootlabs_url", "") if existing else "",
        "linkvertise_url": existing.get("linkvertise_url", "") if existing else "",
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time()
    }

    success = save_guild_config(guild_id, config)
    if success:
        config["guild_id"] = str(guild_id)
        return config
    return None


def delete_guild_config(guild_id):
    if guild_configs_collection is None:
        return False
    try:
        guild_configs_collection.delete_one({"_id": str(guild_id)})
        return True
    except Exception as e:
        logger.error(f"Failed to delete guild config: {e}")
        return False


def get_guild_by_secret(api_secret):
    if guild_configs_collection is None:
        return None
    try:
        doc = guild_configs_collection.find_one({"api_secret": api_secret})
        if doc:
            config = {k: v for k, v in doc.items() if k != "_id"}
            config["guild_id"] = doc["_id"]
            return config
        return None
    except Exception as e:
        logger.error(f"Failed to lookup guild by secret: {e}")
        return None


# ─── Sessions ───

def create_session(guild_id, discord_id, discord_name):
    if guild_sessions_collection is None:
        return None
    try:
        # Kill any existing active sessions for this user in this guild
        guild_sessions_collection.delete_many({
            "guild_id": str(guild_id),
            "discord_id": str(discord_id),
            "completed": False
        })

        token = secrets.token_urlsafe(32)
        now = time.time()
        session = {
            "_id": token,
            "guild_id": str(guild_id),
            "discord_id": str(discord_id),
            "discord_name": discord_name,
            "ip": None,
            "timer_started": False,
            "timer_started_at": None,
            "completed": False,
            "completed_at": None,
            "key_claimed": False,
            "created_at": now,
            "expires_at": now + 1800  # 30 min session
        }
        guild_sessions_collection.insert_one(session)
        return token
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        return None


def get_session(token):
    if guild_sessions_collection is None:
        return None
    try:
        doc = guild_sessions_collection.find_one({"_id": token})
        if not doc:
            return None
        if time.time() > doc.get("expires_at", 0):
            guild_sessions_collection.delete_one({"_id": token})
            return None
        session = {k: v for k, v in doc.items() if k != "_id"}
        session["token"] = doc["_id"]
        return session
    except Exception as e:
        logger.error(f"Failed to get session: {e}")
        return None


def update_session(token, updates):
    if guild_sessions_collection is None:
        return False
    try:
        guild_sessions_collection.update_one(
            {"_id": token},
            {"$set": updates}
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update session: {e}")
        return False


def find_session_by_ip_and_guild(ip, guild_id):
    """Find the most recent uncompleted session for this IP + guild."""
    if guild_sessions_collection is None:
        return None
    try:
        doc = guild_sessions_collection.find_one(
            {
                "guild_id": str(guild_id),
                "ip": ip,
                "completed": False,
                "timer_started": True,
                "expires_at": {"$gt": time.time()}
            },
            sort=[("created_at", -1)]
        )
        if doc:
            session = {k: v for k, v in doc.items() if k != "_id"}
            session["token"] = doc["_id"]
            return session
        return None
    except Exception as e:
        logger.error(f"Failed to find session by IP: {e}")
        return None


def get_pending_session(discord_id, guild_id):
    """Get unclaimed completed session for a user."""
    if guild_sessions_collection is None:
        return None
    try:
        doc = guild_sessions_collection.find_one(
            {
                "guild_id": str(guild_id),
                "discord_id": str(discord_id),
                "completed": True,
                "key_claimed": False,
                "expires_at": {"$gt": time.time()}
            },
            sort=[("completed_at", -1)]
        )
        if doc:
            session = {k: v for k, v in doc.items() if k != "_id"}
            session["token"] = doc["_id"]
            return session
        return None
    except Exception as e:
        logger.error(f"Failed to get pending session: {e}")
        return None


def get_active_session(discord_id, guild_id):
    """Get any active (not expired) session for a user."""
    if guild_sessions_collection is None:
        return None
    try:
        doc = guild_sessions_collection.find_one(
            {
                "guild_id": str(guild_id),
                "discord_id": str(discord_id),
                "key_claimed": False,
                "expires_at": {"$gt": time.time()}
            },
            sort=[("created_at", -1)]
        )
        if doc:
            session = {k: v for k, v in doc.items() if k != "_id"}
            session["token"] = doc["_id"]
            return session
        return None
    except Exception as e:
        logger.error(f"Failed to get active session: {e}")
        return None


# ─── Keys ───

def create_guild_key(guild_id, discord_id, discord_name, duration_hours):
    if guild_keys_collection is None:
        return None
    try:
        # Delete old keys for this user in this guild
        guild_keys_collection.delete_many({
            "guild_id": str(guild_id),
            "discord_id": str(discord_id)
        })

        key = _generate_key_string()
        now = time.time()
        key_doc = {
            "_id": key,
            "guild_id": str(guild_id),
            "discord_id": str(discord_id),
            "discord_name": discord_name,
            "hwid": None,
            "created_at": now,
            "expires_at": now + (duration_hours * 3600)
        }
        guild_keys_collection.insert_one(key_doc)
        return key
    except Exception as e:
        logger.error(f"Failed to create guild key: {e}")
        return None


def validate_guild_key(key, hwid, api_secret):
    """Validate a key. Returns (valid: bool, message: str)."""
    if guild_keys_collection is None:
        return False, "Key system unavailable"

    try:
        guild_config = get_guild_by_secret(api_secret)
        if not guild_config:
            return False, "Invalid API secret"

        guild_id = guild_config["guild_id"]

        key_doc = guild_keys_collection.find_one({"_id": key})
        if not key_doc:
            return False, "Invalid key"

        if key_doc.get("guild_id") != guild_id:
            return False, "Invalid key"

        if time.time() > key_doc.get("expires_at", 0):
            guild_keys_collection.delete_one({"_id": key})
            return False, "Key expired. Get a new one from Discord."

        # HWID lock
        stored_hwid = key_doc.get("hwid")
        if stored_hwid and stored_hwid != hwid:
            return False, "Key locked to another device."

        if not stored_hwid:
            guild_keys_collection.update_one(
                {"_id": key},
                {"$set": {"hwid": hwid}}
            )

        # Membership check
        if guild_config.get("require_membership", True):
            discord_id = key_doc.get("discord_id")
            if discord_id and DISCORD_TOKEN:
                try:
                    import requests as req
                    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
                    url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{discord_id}"
                    resp = req.get(url, headers=headers, timeout=10)
                    if resp.status_code != 200:
                        guild_keys_collection.delete_one({"_id": key})
                        return False, "You must be in the Discord server."
                except Exception:
                    pass  # fail open on network errors

        return True, "Authenticated"

    except Exception as e:
        logger.error(f"Key validation error: {e}")
        return False, "Validation error"


def delete_guild_keys_by_user(guild_id, discord_id):
    if guild_keys_collection is None:
        return 0
    try:
        result = guild_keys_collection.delete_many({
            "guild_id": str(guild_id),
            "discord_id": str(discord_id)
        })
        return result.deleted_count
    except Exception as e:
        logger.error(f"Failed to delete keys: {e}")
        return 0


def get_guild_key_stats(guild_id):
    if guild_keys_collection is None:
        return {"total": 0, "active": 0, "expired": 0, "hwid_locked": 0}
    try:
        now = time.time()
        pipeline = [
            {"$match": {"guild_id": str(guild_id)}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "active": {"$sum": {"$cond": [{"$gt": ["$expires_at", now]}, 1, 0]}},
                "expired": {"$sum": {"$cond": [{"$lte": ["$expires_at", now]}, 1, 0]}},
                "hwid_locked": {"$sum": {"$cond": [{"$ne": ["$hwid", None]}, 1, 0]}}
            }}
        ]
        result = list(guild_keys_collection.aggregate(pipeline))
        if result:
            r = result[0]
            return {
                "total": r.get("total", 0),
                "active": r.get("active", 0),
                "expired": r.get("expired", 0),
                "hwid_locked": r.get("hwid_locked", 0)
            }
        return {"total": 0, "active": 0, "expired": 0, "hwid_locked": 0}
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return {"total": 0, "active": 0, "expired": 0, "hwid_locked": 0}


def cleanup_expired_guild_keys():
    if guild_keys_collection is None:
        return 0
    try:
        result = guild_keys_collection.delete_many({
            "expires_at": {"$lte": time.time()}
        })
        return result.deleted_count
    except Exception as e:
        logger.error(f"Failed to cleanup keys: {e}")
        return 0
