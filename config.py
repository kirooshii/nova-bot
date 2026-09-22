import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Bot username without @, used for deep links (t.me/<username>/...).
# Prod: rosecap_nova_bot, test: your test bot username from @BotFather.
BOT_USERNAME = os.environ.get("BOT_USERNAME", "rosecap_nova_bot")

# SQLite file. Prod: nova.db, test: e.g. nova.test.db.
# Same code runs in both places — only the local .env differs.
DB_PATH = os.environ.get("DB_PATH", "nova.db")

# FastAPI port. Prod: 8000, test: e.g. 8001.
PORT = int(os.environ.get("PORT", "8000"))

# Cached group photos dir. Test can use e.g. photos_test/ to avoid mixing.
PHOTOS_DIR = os.environ.get("PHOTOS_DIR", "photos")
