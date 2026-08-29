"""24-hour obfuscator access, unlocked by a LootLabs checkpoint.

Flow
----
1. User DMs `.obf` with no active access. The bot creates a one-task LootLabs
   locker whose destination is `{SELF_URL}/obf/unlock/{token}` and DMs the link.
2. User completes the checkpoint. LootLabs redirects them to that URL.
3. The Flask route in main.py calls `redeem(token)`, which marks the token used
   and grants that Discord ID `OBF_UNLOCK_HOURS` (default 24) of access.
4. `.obf` checks `has_access()` before running the engine.

Nothing is "charged" per obfuscation — one checkpoint buys a 24h window, so a
failed obfuscation never costs the user anything.

Two modes
---------
**Static link** (no API needed). Set `OBF_STATIC_LINK` to a link you made in the
LootLabs dashboard. Its destination must be `{SELF_URL}/obf/claim`. Because that
destination is fixed, it can't carry a per-user token, so the bot hands the user
a short claim code and the landing page asks for it.

**API link** (needs `LOOTLABS_API_TOKEN`). The bot creates a fresh locker per
user whose destination already embeds a one-time token — no code to type, and
nobody can claim without actually completing the checkpoint. Strictly better,
use it if you can get the API working.

`OBF_STATIC_LINK` wins if both are set.

Env vars
--------
  OBF_STATIC_LINK       a LootLabs link from the dashboard. Enables static mode.
  LOOTLABS_API_TOKEN    needed for API mode (same token as the website).
  LOOTLABS_OBF_TIER_ID  optional, 1-3 (default 3).
  LOOTLABS_OBF_THEME    optional, 1-5 (default 5).
  OBF_BYPASS_IDS        comma-separated Discord IDs that skip the checkpoint.
                        Defaults to feariosz0; the bot owner is always bypassed.
  OBF_UNLOCK_HOURS      optional, length of the access window (default 24).
  OBF_GATE_DISABLED     set to 1 to disable the gate entirely (emergency only).
  RENDER_EXTERNAL_URL   public base URL of this service.
"""

import logging
import os
import secrets
import time

import requests

logger = logging.getLogger(__name__)

LOOTLABS_API_URL = "https://creators.lootlabs.gg/api/public/content_locker"

DB_NAME = "vadrifts_bots"
UNLOCKS_COLLECTION = "obf_unlocks"
TOKENS_COLLECTION = "obf_unlock_tokens"
CLAIMS_COLLECTION = "obf_claim_codes"

# A pending link the user never completes shouldn't sit around forever.
TOKEN_TTL_SECONDS = 6 * 60 * 60

DEFAULT_BYPASS_IDS = "1323980404411334738"  # feariosz0


class AccessError(RuntimeError):
    """Something is misconfigured or LootLabs refused."""


# ---------------------------------------------------------------------------
# Mongo
# ---------------------------------------------------------------------------
_db = None
_unlocks = None
_tokens = None
_claims = None


def _init_db():
    global _db, _unlocks, _tokens, _claims
    try:
        from config import MONGODB_URI
    except Exception:
        MONGODB_URI = os.environ.get("MONGODB_URI")

    if not MONGODB_URI:
        logger.error("MONGODB_URI is not set — obfuscator access gate is off-line.")
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
        _db = client[DB_NAME]
        _unlocks = _db[UNLOCKS_COLLECTION]
        _tokens = _db[TOKENS_COLLECTION]
        _claims = _db[CLAIMS_COLLECTION]
        _tokens.create_index("expires_at")
        _claims.create_index("expires_at")
        logger.info("obf_access connected to MongoDB (%s)", DB_NAME)
        return True
    except Exception as exc:
        logger.error("obf_access MongoDB connection failed: %s", exc)
        _db = _unlocks = _tokens = _claims = None
        return False


_init_db()


def _require_db():
    if _unlocks is None or _tokens is None or _claims is None:
        raise AccessError(
            "The access database is unavailable right now — try again in a "
            "minute. If this keeps happening, tell the bot owner."
        )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _self_url() -> str:
    base = (os.environ.get("RENDER_EXTERNAL_URL")
            or "https://vadriftzbots.onrender.com").strip().rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    if not base.startswith("https://"):
        base = "https://" + base.lstrip("/")
    return base


def _unlock_hours() -> int:
    try:
        hours = int(os.environ.get("OBF_UNLOCK_HOURS", "24"))
    except ValueError:
        hours = 24
    return max(1, hours)


def bypass_ids() -> set:
    raw = os.environ.get("OBF_BYPASS_IDS", DEFAULT_BYPASS_IDS)
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def gate_disabled() -> bool:
    return os.environ.get("OBF_GATE_DISABLED", "").strip() in ("1", "true", "yes", "on")


def _lootlabs_settings():
    token = (os.environ.get("LOOTLABS_API_TOKEN") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise AccessError(
            "LOOTLABS_API_TOKEN is not set, so checkpoint links can't be "
            "created. Add it in Render → Environment."
        )
    try:
        tier_id = int(os.environ.get("LOOTLABS_OBF_TIER_ID", "3"))
        theme = int(os.environ.get("LOOTLABS_OBF_THEME", "5"))
    except ValueError:
        raise AccessError("LOOTLABS_OBF_TIER_ID / LOOTLABS_OBF_THEME must be numbers.")
    # LootLabs docs: tiers 1-3, themes 1-5.
    return token, min(max(tier_id, 1), 3), min(max(theme, 1), 5)


# ---------------------------------------------------------------------------
# Access window
# ---------------------------------------------------------------------------
def has_access(discord_id) -> bool:
    """True if this user has an unexpired obfuscator window."""
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


def grant(discord_id, hours: int = None) -> float:
    """Open (or refresh) an access window. Returns the expiry timestamp."""
    _require_db()
    hours = _unlock_hours() if hours is None else hours
    expires_at = time.time() + hours * 3600
    _unlocks.update_one(
        {"_id": str(discord_id)},
        {"$set": {"granted_at": time.time(), "expires_at": expires_at}},
        upsert=True,
    )
    logger.info("obf access granted to %s until %s", discord_id, expires_at)
    return expires_at


def revoke(discord_id) -> None:
    _require_db()
    _unlocks.delete_one({"_id": str(discord_id)})


# ---------------------------------------------------------------------------
# LootLabs
# ---------------------------------------------------------------------------
def _extract_loot_url(data):
    """Pull a locker URL out of the several shapes LootLabs returns."""
    if data is None:
        return None
    if isinstance(data, str):
        text = data.strip()
        if text.startswith(("http://", "https://")):
            return text
        if text and " " not in text and 2 < len(text) < 80:
            return f"https://loot-link.com/s?{text}"
        return None
    if not isinstance(data, dict):
        return None
    for key in ("loot_url", "lootUrl", "locker_url", "short_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    short = data.get("short") or data.get("slug")
    if isinstance(short, str) and short.strip():
        short = short.strip()
        if short.startswith(("http://", "https://")):
            return short
        return f"https://loot-link.com/s?{short}"
    for key in ("message", "data", "result"):
        nested = data.get(key)
        if nested is not None and nested is not data:
            found = _extract_loot_url(nested)
            if found:
                return found
    return None


def _parse(response):
    body_text = (response.text or "")[:800]
    try:
        data = response.json()
    except ValueError:
        data = None
    loot_url = _extract_loot_url(data)
    ok = (
        response.status_code < 400
        and bool(loot_url)
        and not (isinstance(data, dict) and data.get("type") == "error")
    )
    return ok, loot_url, data, body_text


def _error_text(data, status_code, body_text=""):
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "reason"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = _error_text(value, status_code)
                if nested:
                    return nested
    return (body_text or "").strip() or f"HTTP {status_code}"


def _raise_failure(status_code, data, fallback):
    if status_code == 401:
        raise AccessError(
            "LootLabs rejected the API token. Check LOOTLABS_API_TOKEN."
        )
    if status_code == 429:
        raise AccessError("LootLabs rate-limited the request. Wait a minute and retry.")
    hint = _error_text(data, status_code, fallback) or fallback
    lowered = str(hint).lower()
    if "creator" in lowered or ("mandatory" in lowered and "detail" in lowered):
        raise AccessError(
            "LootLabs needs your creator profile filled in (name + avatar image) "
            "before it can create links."
        )
    raise AccessError(f"LootLabs could not create the checkpoint link. {hint}")


def _create_lootlabs_link(api_token, payload):
    """Create a locker link via POST, falling back to GET (mirrors the website)."""
    last_error = "No response from LootLabs."
    try:
        response = requests.post(
            LOOTLABS_API_URL,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=25,
        )
        ok, loot_url, data, body_text = _parse(response)
        logger.info("LootLabs POST status=%s body=%s", response.status_code, body_text)
        if ok:
            return loot_url
        last_error = _error_text(data, response.status_code, body_text)
        _raise_failure(response.status_code, data, last_error or body_text)
    except AccessError:
        raise
    except requests.RequestException as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        logger.error("LootLabs POST failed: %s", last_error)

    params = {
        "api_token": api_token,
        "title": payload["title"],
        "url": payload["url"],
        "tier_id": payload["tier_id"],
        "number_of_tasks": payload["number_of_tasks"],
        "theme": payload.get("theme", 1),
    }
    try:
        response = requests.get(LOOTLABS_API_URL, params=params, timeout=25)
        ok, loot_url, data, body_text = _parse(response)
        logger.info("LootLabs GET status=%s body=%s", response.status_code, body_text)
        if ok:
            return loot_url
        _raise_failure(response.status_code, data, last_error)
    except AccessError:
        raise
    except requests.RequestException as exc:
        raise AccessError(
            f"LootLabs could not create the checkpoint link. "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def create_checkpoint_link(discord_id, username: str = "") -> str:
    """Create a fresh one-task checkpoint link bound to this Discord user.

    Stores a single-use token so the redirect can be tied back to them.
    Returns the loot-link URL to send the user.
    """
    _require_db()

    # Reuse a still-valid pending link if we already made one for this user,
    # so repeatedly typing .obf doesn't hammer the LootLabs API (it 429s).
    existing = _tokens.find_one({
        "discord_id": str(discord_id),
        "used": False,
        "expires_at": {"$gt": time.time()},
        "loot_url": {"$exists": True},
    })
    if existing and existing.get("loot_url"):
        return existing["loot_url"]

    api_token, tier_id, theme = _lootlabs_settings()

    token = secrets.token_urlsafe(24)
    destination = f"{_self_url()}/obf/unlock/{token}"

    payload = {
        "title": "Unlock the Lua obfuscator"[:30],
        "url": destination,
        "tier_id": tier_id,
        "number_of_tasks": 1,
        "theme": theme,
    }
    loot_url = _create_lootlabs_link(api_token, payload)

    _tokens.update_one(
        {"_id": token},
        {"$set": {
            "discord_id": str(discord_id),
            "username": username or "",
            "created_at": time.time(),
            "expires_at": time.time() + TOKEN_TTL_SECONDS,
            "used": False,
            "loot_url": loot_url,
        }},
        upsert=True,
    )
    # Opportunistic cleanup; ignore failures.
    try:
        _tokens.delete_many({"expires_at": {"$lt": time.time() - 86400}})
    except Exception:
        pass

    logger.info("obf checkpoint link created for %s", discord_id)
    return loot_url


def redeem(token: str):
    """Consume a redirect token. Returns (discord_id, expires_at) or (None, None).

    Single-use: a second visit with the same token gets nothing.
    """
    _require_db()
    now = time.time()
    result = _tokens.find_one_and_update(
        {"_id": token, "used": False, "expires_at": {"$gt": now}},
        {"$set": {"used": True, "used_at": now}},
    )
    if not result:
        return None, None
    discord_id = result.get("discord_id")
    expires_at = grant(discord_id)
    return discord_id, expires_at


# ---------------------------------------------------------------------------
# Static-link mode (no LootLabs API token required)
# ---------------------------------------------------------------------------
# No 0/O, 1/I/L — users type this by hand off a phone screen.
_CLAIM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CLAIM_CODE_LEN = 6
CLAIM_TTL_SECONDS = 2 * 60 * 60


def static_link() -> str:
    return (os.environ.get("OBF_STATIC_LINK") or "").strip()


def mode() -> str:
    """'static' if a dashboard link is configured, else 'api'."""
    return "static" if static_link() else "api"


def create_claim_code(discord_id) -> str:
    """Hand out a short single-use code for the static-link landing page."""
    _require_db()

    # Reuse an unused, unexpired code so re-running .obf doesn't churn codes.
    existing = _claims.find_one({
        "discord_id": str(discord_id),
        "used": False,
        "expires_at": {"$gt": time.time()},
    })
    if existing:
        return existing["_id"]

    for _ in range(10):
        code = "".join(secrets.choice(_CLAIM_ALPHABET) for _ in range(CLAIM_CODE_LEN))
        if _claims.find_one({"_id": code}):
            continue
        _claims.update_one(
            {"_id": code},
            {"$set": {
                "discord_id": str(discord_id),
                "created_at": time.time(),
                "expires_at": time.time() + CLAIM_TTL_SECONDS,
                "used": False,
            }},
            upsert=True,
        )
        logger.info("obf claim code issued for %s", discord_id)
        return code
    raise AccessError("Could not allocate a claim code. Try again in a minute.")


def redeem_claim_code(code: str):
    """Consume a claim code. Returns (discord_id, expires_at) or (None, None)."""
    _require_db()
    if not code:
        return None, None
    code = code.strip().upper()
    now = time.time()
    result = _claims.find_one_and_update(
        {"_id": code, "used": False, "expires_at": {"$gt": now}},
        {"$set": {"used": True, "used_at": now}},
    )
    if not result:
        return None, None
    discord_id = result.get("discord_id")
    return discord_id, grant(discord_id)


def claim_page_url() -> str:
    """The URL to set as the destination of the static LootLabs link."""
    return f"{_self_url()}/obf/claim"


def unlock_offer(discord_id, username: str = ""):
    """Return (link, claim_code_or_None) for whichever mode is configured.

    Static mode returns a claim code; API mode returns None because the
    destination URL already carries the one-time token.
    """
    if static_link():
        return static_link(), create_claim_code(discord_id)
    return create_checkpoint_link(discord_id, username), None
