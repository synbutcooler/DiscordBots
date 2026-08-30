"""Obfuscator access backed by the existing Vadrifts key-system flow.

The bot does not run a second LootLabs gateway.  It creates a normal shared
``guild_key_sessions`` record with ``purpose='obfuscator'`` and sends the user
to the website's existing ``/ks/gateway/<token>`` page.  The website performs
the same IP binding, timer, provider redirect, and completion checks used for keys;
for this purpose the website also encrypts a one-session destination through
LootLabs' official Redirect API. Discord grants the obfuscator window only
after that shared session is complete and the original user presses Claim
Access.

Configure a dedicated ad-link script profile in the owner guild, set its
LootLabs destination to the profile's normal ``/ks/done/...`` URL, then set
``OBF_KS_PROFILE_ID`` (preferred) or ``OBF_KS_PROFILE_NAME`` on the bot service.
"""

import hashlib
import logging
import os
import time
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)

DB_NAME = "vadrifts_bots"
UNLOCKS_COLLECTION = "obf_unlocks"
DEFAULT_BYPASS_IDS = "1323980404411334738"  # feariosz0
DEFAULT_PROFILE_NAME = "Obfuscator Access"
_LOOTLABS_HOSTS = ("lootdest.org", "lootlabs.gg", "loot-link.com", "loot-links.com")


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
            "source": "vadrifts_key_system",
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


def _resolve_profile(guild_id):
    from guild_key_system import (
        get_profile_by_name,
        get_script_profile,
        update_script_profile,
    )

    explicit_id = (os.environ.get("OBF_KS_PROFILE_ID") or "").strip()
    if explicit_id:
        profile = get_script_profile(explicit_id)
    else:
        name = (os.environ.get("OBF_KS_PROFILE_NAME") or DEFAULT_PROFILE_NAME).strip()
        profile = get_profile_by_name(guild_id, name)

    if not profile:
        selector = (
            f"ID `{explicit_id}`" if explicit_id else
            f"name `{os.environ.get('OBF_KS_PROFILE_NAME') or DEFAULT_PROFILE_NAME}`"
        )
        raise AccessError(
            f"The obfuscator verification profile ({selector}) does not exist. "
            "Create a dedicated Ad-Link profile with /ks setup, configure its "
            "LootLabs URL, then set OBF_KS_PROFILE_ID on the bot."
        )
    if str(profile.get("guild_id")) != str(guild_id):
        raise AccessError("OBF_KS_PROFILE_ID belongs to a different Discord server.")
    if not profile.get("enabled", True):
        raise AccessError("The obfuscator verification profile is disabled.")
    if profile.get("key_type") != "adlink":
        raise AccessError("The obfuscator verification profile must use Ad-Link mode.")
    reserved_for = profile.get("system_purpose")
    if reserved_for and reserved_for != "obfuscator":
        raise AccessError("That profile is reserved for another internal flow.")
    lootlabs_url = (profile.get("lootlabs_url") or "").strip()
    if not lootlabs_url:
        raise AccessError(
            "The obfuscator verification profile has no LootLabs URL. Configure "
            "one with /ks setlink first."
        )
    if any(char in lootlabs_url for char in "\r\n"):
        raise AccessError("The configured LootLabs URL is invalid.")
    try:
        parsed = urlsplit(lootlabs_url)
    except ValueError as exc:
        raise AccessError("The configured LootLabs URL is invalid.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not any(
        host == domain or host.endswith("." + domain)
        for domain in _LOOTLABS_HOSTS
    ):
        raise AccessError("The profile must contain a genuine HTTPS LootLabs URL.")
    if any(key.lower() == "data"
           for key, _value in parse_qsl(parsed.query, keep_blank_values=True)):
        raise AccessError("Remove the existing data parameter from the LootLabs URL.")
    if reserved_for != "obfuscator":
        if not update_script_profile(
            profile["profile_id"], {"system_purpose": "obfuscator"}
        ):
            raise AccessError("Could not reserve the obfuscator verification profile.")
        profile["system_purpose"] = "obfuscator"
    return profile


def profile_destination(guild_id, profile_id) -> str:
    from guild_key_system import get_destination_url
    return get_destination_url(guild_id, profile_id)


def create_verification_offer(discord_id, username, guild_id) -> dict:
    """Create/reuse an obfuscator-purpose session in the shared key-system DB."""
    from guild_key_system import (
        SERVER_BASE_URL,
        create_session,
        get_user_session,
    )

    profile = _resolve_profile(guild_id)
    profile_id = profile["profile_id"]

    # Prefer a completed unclaimed session, then an in-progress one. This is the
    # same resume behavior as normal key claims and avoids invalidating a link
    # whenever the user repeats .obf.
    session = get_user_session(
        discord_id, guild_id, profile_id,
        purpose="obfuscator", completed=True, claimed=False,
    )
    if not session:
        session = get_user_session(
            discord_id, guild_id, profile_id,
            purpose="obfuscator", completed=False,
        )

    if session:
        token = session["token"]
    else:
        token = create_session(
            guild_id,
            discord_id,
            username,
            profile_id,
            purpose="obfuscator",
            allowed_providers=["lootlabs"],
            min_completion_seconds=_minimum_seconds(),
            require_referrer=True,
        )
        if not token:
            raise AccessError("The website could not create a verification session.")
        session = {"token": token, "completed": False, "key_claimed": False}

    return {
        "session_token": token,
        "gateway_url": f"{SERVER_BASE_URL}/ks/gateway/{token}",
        "completed": bool(session.get("completed")),
        "claimed": bool(session.get("key_claimed")),
        "profile_id": profile_id,
        "profile_name": profile.get("name", DEFAULT_PROFILE_NAME),
        "destination_url": profile_destination(guild_id, profile_id),
    }


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
    if str(session.get("discord_id")) != str(discord_id):
        raise AccessError("This verification session belongs to another user.")
    if not session.get("completed"):
        raise AccessError(
            "Verification is not complete yet. Open the website, finish LootLabs, "
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
        # Let the user retry if the access DB had a transient failure after the
        # shared session was atomically claimed.
        update_session(session_token, {"key_claimed": False})
        raise
