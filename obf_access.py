"""Obfuscator access through the existing Vadrifts key-system session flow.

The LootLabs locker is static and keeps its existing immutable destination:
``https://vadriftzbots.onrender.com/obf/claim``. Discord creates a shared
``guild_key_sessions`` record, sends the user through the existing Vadrifts
``/ks/gateway`` UI, and Vadrifts starts the IP-bound timer before redirecting to
``OBF_STATIC_LINK``. When LootLabs returns to the fixed bot callback, the bot
marks the matching shared session complete. Access is granted only when the
same Discord user subsequently presses Claim Access.
"""

import hashlib
import logging
import os
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DB_NAME = "vadrifts_bots"
UNLOCKS_COLLECTION = "obf_unlocks"
DEFAULT_BYPASS_IDS = "1323980404411334738"  # feariosz0
OBF_SESSION_PROFILE_ID = "__obfuscator_static__"
OBF_PROFILE_NAME = "Obfuscator Access"
_LOOTLABS_HOSTS = (
    "lootdest.org",
    "lootlabs.gg",
    "loot-link.com",
    "loot-links.com",
)


class AccessError(RuntimeError):
    pass


_unlocks = None


def _init_db():
    global _unlocks
    try:
        from config import MONGODB_URI
    except Exception:
        MONGODB_URI = os.environ.get("MONGODB_URI")

    if not MONGODB_URI:
        logger.error("MONGODB_URI is not set — obfuscator access is off-line.")
        return False
    try:
        from pymongo import MongoClient

        client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
        )
        client.admin.command("ping")
        _unlocks = client[DB_NAME][UNLOCKS_COLLECTION]
        logger.info("obf_access connected to MongoDB (%s)", DB_NAME)
        return True
    except Exception as exc:
        logger.error("obf_access MongoDB connection failed: %s", exc)
        _unlocks = None
        return False


_init_db()


def _require_db():
    if _unlocks is None:
        raise AccessError(
            "The access database is unavailable right now. Try again in a minute."
        )


def _unlock_hours() -> int:
    try:
        hours = int(os.environ.get("OBF_UNLOCK_HOURS", "24"))
    except ValueError:
        hours = 24
    return min(max(hours, 1), 24 * 30)


def _minimum_seconds() -> int:
    try:
        seconds = int(os.environ.get("OBF_MIN_COMPLETION_SECONDS", "25"))
    except ValueError:
        seconds = 25
    return min(max(seconds, 10), 15 * 60)


def _host_matches(host, domains):
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _static_link() -> str:
    link = (os.environ.get("OBF_STATIC_LINK") or "").strip()
    if not link:
        raise AccessError(
            "OBF_STATIC_LINK is not configured on the DiscordBots service."
        )
    if any(char in link for char in "\r\n"):
        raise AccessError("OBF_STATIC_LINK is invalid.")
    try:
        parsed = urlsplit(link)
    except ValueError as exc:
        raise AccessError("OBF_STATIC_LINK is invalid.") from exc
    if parsed.scheme != "https" or not _host_matches(parsed.hostname, _LOOTLABS_HOSTS):
        raise AccessError("OBF_STATIC_LINK must be a genuine HTTPS LootLabs URL.")
    return link


def is_valid_lootlabs_referrer(referer) -> bool:
    try:
        parsed = urlsplit(referer or "")
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and _host_matches(
        parsed.hostname, _LOOTLABS_HOSTS
    )


def bypass_ids() -> set:
    raw = os.environ.get("OBF_BYPASS_IDS", DEFAULT_BYPASS_IDS)
    return {int(part.strip()) for part in raw.split(",") if part.strip().isdigit()}


def gate_disabled() -> bool:
    return os.environ.get("OBF_GATE_DISABLED", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def has_access(discord_id) -> bool:
    if gate_disabled():
        return True
    _require_db()
    doc = _unlocks.find_one({"_id": str(discord_id)})
    return bool(doc and doc.get("expires_at", 0) > time.time())


def seconds_left(discord_id) -> int:
    _require_db()
    doc = _unlocks.find_one({"_id": str(discord_id)})
    if not doc:
        return 0
    return max(0, int(doc.get("expires_at", 0) - time.time()))


def grant(discord_id, hours=None, session_token="") -> float:
    _require_db()
    hours = _unlock_hours() if hours is None else max(1, int(hours))
    now = time.time()
    expires_at = now + hours * 3600
    _unlocks.update_one(
        {"_id": str(discord_id)},
        {"$set": {
            "granted_at": now,
            "expires_at": expires_at,
            "source": "vadrifts_static_lootlabs",
            "verification_session_hash": hashlib.sha256(
                str(session_token or "").encode("utf-8")
            ).hexdigest(),
        }},
        upsert=True,
    )
    logger.info("obfuscator access granted to discord_id=%s", discord_id)
    return expires_at


def revoke(discord_id) -> None:
    _require_db()
    _unlocks.delete_one({"_id": str(discord_id)})


def create_verification_offer(discord_id, username, guild_id) -> dict:
    """Create or resume a shared session for the immutable static locker."""
    from guild_key_system import (
        SERVER_BASE_URL,
        create_session,
        get_user_session,
        update_session,
    )

    static_link = _static_link()

    # Resume a completed unclaimed session first, then an in-progress session.
    session = get_user_session(
        discord_id,
        guild_id,
        OBF_SESSION_PROFILE_ID,
        purpose="obfuscator",
        completed=True,
        claimed=False,
    )
    if not session:
        session = get_user_session(
            discord_id,
            guild_id,
            OBF_SESSION_PROFILE_ID,
            purpose="obfuscator",
            completed=False,
        )

    if session:
        token = session["token"]
    else:
        token = create_session(
            guild_id,
            discord_id,
            username,
            OBF_SESSION_PROFILE_ID,
            purpose="obfuscator",
            allowed_providers=["lootlabs"],
            min_completion_seconds=_minimum_seconds(),
            require_referrer=True,
        )
        if not token:
            raise AccessError("The website could not create a verification session.")
        session = {"token": token, "completed": False, "key_claimed": False}

    if not session.get("completed"):
        if not update_session(token, {
            "provider_url": static_link,
            "static_callback": True,
            "flow_name": OBF_PROFILE_NAME,
        }):
            raise AccessError("The static verification session could not be prepared.")

    return {
        "session_token": token,
        "gateway_url": f"{SERVER_BASE_URL}/ks/gateway/{token}",
        "completed": bool(session.get("completed")),
        "claimed": bool(session.get("key_claimed")),
        "profile_id": OBF_SESSION_PROFILE_ID,
        "profile_name": OBF_PROFILE_NAME,
    }


def complete_static_callback(client_ip, referer) -> dict:
    """Validate the fixed /obf/claim return and mark its shared session complete."""
    from guild_key_system import (
        complete_static_obfuscator_session,
        get_static_obfuscator_session_by_ip,
    )

    if not client_ip:
        raise AccessError("Your network could not be identified. Start again in Discord.")
    if not is_valid_lootlabs_referrer(referer):
        raise AccessError(
            "Open the Vadrifts verification page and complete LootLabs first."
        )

    session = get_static_obfuscator_session_by_ip(client_ip)
    if not session:
        raise AccessError(
            "No active verification was found for this browser/network. "
            "Start again with .obfunlock in Discord."
        )

    timer_started = session.get("timer_started_at")
    provider_started = session.get("provider_started_at")
    try:
        started_at = max(float(timer_started), float(provider_started))
        required = int(
            session.get("min_completion_seconds") or _minimum_seconds()
        )
    except (TypeError, ValueError):
        raise AccessError("The verification timer is invalid. Start again in Discord.")
    required = min(max(required, _minimum_seconds(), 10), 15 * 60)
    elapsed = time.time() - started_at
    if elapsed < required:
        wait = int(required - elapsed) + 1
        raise AccessError(
            f"LootLabs returned too quickly. Complete the task and try again in {wait}s."
        )

    completed = complete_static_obfuscator_session(session["token"], client_ip)
    if not completed:
        raise AccessError("This verification was already used or changed. Start again.")

    logger.info(
        "static obfuscator verification completed: session=%s user=%s elapsed=%.1fs",
        session["token"][:8],
        session.get("discord_id"),
        elapsed,
    )
    return completed


def claim_verification(session_token, discord_id) -> float:
    """Claim a completed shared session and grant the obfuscator window."""
    from guild_key_system import (
        claim_completed_session,
        get_session,
        update_session,
    )

    session = get_session(session_token)
    if not session:
        raise AccessError("This verification session expired. Run .obfunlock again.")
    if session.get("purpose", "key") != "obfuscator":
        raise AccessError("This is not an obfuscator verification session.")
    if not session.get("static_callback"):
        raise AccessError("This is not a current static LootLabs session.")
    if str(session.get("discord_id")) != str(discord_id):
        raise AccessError("This verification session belongs to another user.")
    if not session.get("completed"):
        raise AccessError(
            "Verification is not complete yet. Open Vadrifts, finish LootLabs, "
            "then press Claim Access again."
        )
    if session.get("key_claimed"):
        if has_access(discord_id):
            return time.time() + seconds_left(discord_id)
        raise AccessError("This verification session was already claimed.")

    claimed = claim_completed_session(
        session_token, discord_id, purpose="obfuscator"
    )
    if not claimed:
        current = get_session(session_token)
        if current and current.get("key_claimed") and has_access(discord_id):
            return time.time() + seconds_left(discord_id)
        raise AccessError("This verification session was already claimed or changed.")

    try:
        return grant(discord_id, session_token=session_token)
    except Exception:
        # Let the user retry if access storage failed after the atomic claim.
        update_session(session_token, {"key_claimed": False})
        raise


# Keeps an older obfuscator.py from crashing during a staggered deployment.
def unlock_offer(discord_id, username="", guild_id=None):
    if guild_id is None:
        guild_id = os.environ.get("DISCORD_GUILD_ID", "1241797935100989594")
    offer = create_verification_offer(discord_id, username, guild_id)
    return offer["gateway_url"], None
