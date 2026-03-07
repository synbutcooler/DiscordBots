import time
import secrets
from pymongo import MongoClient
from config import MANGODB_URI

GUILD_ID = 1241797935100989594

client = MongoClient(MANGODB_URI)
db = client["vadrifts_bots"]
keys_collection = db["discord_keys"]

keys_collection.create_index("key", unique=True)
keys_collection.create_index("discord_id")
keys_collection.create_index("expires_at")


def generate_key():
    return secrets.token_hex(16)


def create_key_for_user(discord_id, username, expiry_hours=24):
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
    return keys_collection.find_one({"key": key})


def delete_key(key):
    result = keys_collection.delete_one({"key": key})
    return result.deleted_count


def delete_keys_by_discord_id(discord_id):
    discord_id = str(discord_id)
    result = keys_collection.delete_many({"discord_id": discord_id})
    return result.deleted_count


def lock_hwid(key, hwid):
    keys_collection.update_one({"key": key}, {"$set": {"hwid": hwid}})


def get_stats():
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
    now = time.time()
    result = keys_collection.delete_many({"expires_at": {"$lt": now}})
    return result.deleted_count
