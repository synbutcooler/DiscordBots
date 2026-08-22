import os

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
STICKIED_TOKEN = os.environ.get("STICKIED_TOKEN")
DISCORD_KEY_API_SECRET = os.environ.get("DISCORD_KEY_API_SECRET")
MONGODB_URI = os.environ.get("MONGODB_URI")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
