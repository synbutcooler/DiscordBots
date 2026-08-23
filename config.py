import os

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
STICKIED_TOKEN = os.environ.get("STICKIED_TOKEN")
DISCORD_KEY_API_SECRET = os.environ.get("DISCORD_KEY_API_SECRET")
MONGODB_URI = os.environ.get("MONGODB_URI")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# gemini-2.5-flash-lite 404s on a lot of keys now. Prefer current lite models.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Discord user ID of the real 25ms. Set this so the bot always recognizes her,
# even if she changes nickname. Leave unset to fall back to name matching.
def _optional_snowflake(name):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


TWENTYFIVE_MS_USER_ID = _optional_snowflake("TWENTYFIVE_MS_USER_ID")
