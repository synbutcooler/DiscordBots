import time
import secrets
import logging
from pymongo import MongoClient
from config import MONGODB_URI

logger = logging.getLogger(__name__)

GUILD_ID = 1241797935100989594

client = None
db = None
keys_collection = None

def init_db():
    global client, db, keys_collection
    try:
        if not MONGODB_URI:
            logger.error("MONGODB_URI is not set!")
            return False
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client["vadrifts_bots"]
        keys_collection = db["discord_keys"]
        keys_collection.create_index("key", unique=True)
        keys_collection.create_index("discord_id")
        keys_collection.create_index("expires_at")
        logger.info("MongoDB connected successfully")
        return True
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")
        return False

init_db()


def generate_key():
    return secrets.token_hex(16)


def create_key_for_user(discord_id, username, expiry_hours=24):
    if keys_collection is None:
        return None
    delete_keys_by_discord_id(discord_id)
    key = generate_key()
    keys_collection.insert_one({
        "key": key,
        "discord_id": str(discord_id),
        "username": username,
        "created_at": time.time(),
        "expires_at": time.time() + (expiry_hours * 3600),
        "hwid": None
    })
    return key


def get_key(key):
    if keys_collection is None:
        return None
    return keys_collection.find_one({"key": key})


def delete_key(key):
    if keys_collection is None:
        return 0
    result = keys_collection.delete_one({"key": key})
    return result.deleted_count


def delete_keys_by_discord_id(discord_id):
    if keys_collection is None:
        return 0
    discord_id = str(discord_id)
    result = keys_collection.delete_many({"discord_id": discord_id})
    return result.deleted_count


def lock_hwid(key, hwid):
    if keys_collection is None:
        return
    keys_collection.update_one({"key": key}, {"$set": {"hwid": hwid}})


def get_stats():
    if keys_collection is None:
        return {"total": 0, "active": 0, "expired": 0, "hwid_locked": 0}
    now = time.time()
    total = keys_collection.count_documents({})
    active = keys_collection.count_documents({"expires_at": {"$gt": now}})
    expired = total - active
    hwid_locked = keys_collection.count_documents({"hwid": {"$ne": None}})
    return {
        "total": total,
        "active": active,
        "expired": expired,
        "hwid_locked": hwid_locked
    }


def cleanup_expired():
    if keys_collection is None:
        return 0
    now = time.time()
    result = keys_collection.delete_many({"expires_at": {"$lt": now}})
    return result.deleted_count
