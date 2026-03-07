import os

STICKIED_TOKEN = os.environ.get('STICKIED_TOKEN')
MONGODB_URI = os.environ.get('MONGODB_URI')
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')

DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
