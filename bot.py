from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import asyncio
import os
import sqlite3
from config import BOT_TOKEN

DIRECT_APP_LINK = "https://t.me/rosecap_nova_bot/split?startapp=personal"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

BOT_STRINGS = {
    "en": {
        "group_text": "Tap below to view or add expenses for this group☺️",
        "personal_text": "Tap below to open your personal expense tracker!",
        "open_btn": "Open Nova",
        "lang_btn": "🇷🇺 Switch to Russian",
        "lang_switched": "Bot language set to English.",
    },
    "ru": {
        "group_text": "Нажмите ниже, чтобы посмотреть или добавить расходы для этой группы☺️",
        "personal_text": "Нажмите ниже, чтобы открыть свой личный учёт расходов!",
        "open_btn": "Открыть Nova",
        "lang_btn": "🇬🇧 Switch to English",
        "lang_switched": "Язык бота переключён на русский.",
    },
}


def get_db():
    conn = sqlite3.connect("nova.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_chat_lang(chat_id: int) -> str:
    conn = get_db()
    row = conn.execute("SELECT bot_lang FROM groups WHERE chat_id=?", (str(chat_id),)).fetchone()
    conn.close()
    lang = row["bot_lang"] if row and row["bot_lang"] else "en"
    return lang if lang in BOT_STRINGS else "en"


def set_chat_lang(chat_id: int, lang: str, title: str = None):
    conn = get_db()
    conn.execute(
        "INSERT INTO groups (chat_id, title, photo_file, member_count, bot_lang, updated_at) VALUES (?, ?, NULL, 0, ?, strftime('%s','now')) "
        "ON CONFLICT(chat_id) DO UPDATE SET bot_lang=excluded.bot_lang, updated_at=strftime('%s','now')",
        (str(chat_id), title, lang),
    )
    conn.commit()
    conn.close()


def ensure_chat_row(chat_id: int, title: str = None):
    conn = get_db()
    row = conn.execute("SELECT chat_id FROM groups WHERE chat_id=?", (str(chat_id),)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO groups (chat_id, title, photo_file, member_count, bot_lang, updated_at) VALUES (?, ?, NULL, 0, 'en', strftime('%s','now'))",
            (str(chat_id), title),
        )
        conn.commit()
    conn.close()


async def save_group_photo(chat_id: int, title: str):
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or title
        if chat.photo:
            small = chat.photo.small_file_id
            file_name = f"group_{chat_id}.jpg"
            dest = os.path.join(PHOTOS_DIR, file_name)
            await bot.download(small, destination=dest)
            conn = get_db()
            conn.execute(
                "INSERT INTO groups (chat_id, title, photo_file, member_count, bot_lang, updated_at) VALUES (?, ?, ?, 0, 'en', strftime('%s','now')) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, photo_file=excluded.photo_file, updated_at=strftime('%s','now')",
                (str(chat_id), title, file_name),
            )
            conn.commit()
            conn.close()
            return
        conn = get_db()
        conn.execute(
            "INSERT INTO groups (chat_id, title, photo_file, member_count, bot_lang, updated_at) VALUES (?, ?, NULL, 0, 'en', strftime('%s','now')) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=COALESCE(groups.title, excluded.title)",
            (str(chat_id), title),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"save_group_photo failed: {e}")


def build_keyboard(chat, lang):
    if chat.type in ["group", "supergroup"]:
        clean_chat_id = str(chat.id).replace("-", "g")
        open_link = f"https://t.me/rosecap_nova_bot/split?startapp={clean_chat_id}"
    else:
        open_link = DIRECT_APP_LINK
    s = BOT_STRINGS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=s["open_btn"], url=open_link)],
        [InlineKeyboardButton(text=s["lang_btn"], callback_data="lang_toggle")],
    ])


@dp.message(Command("start", "split"))
async def send_app_button(message: types.Message):
    ensure_chat_row(message.chat.id, message.chat.title or None)
    lang = get_chat_lang(message.chat.id)
    s = BOT_STRINGS[lang]
    if message.chat.type in ["group", "supergroup"]:
        text = s["group_text"]
        asyncio.create_task(save_group_photo(message.chat.id, message.chat.title or "Group"))
    else:
        text = s["personal_text"]
    await message.answer(text, reply_markup=build_keyboard(message.chat, lang))


@dp.callback_query(F.data == "lang_toggle")
async def toggle_language(cb: CallbackQuery):
    chat = cb.message.chat
    current = get_chat_lang(chat.id)
    new_lang = "ru" if current == "en" else "en"
    set_chat_lang(chat.id, new_lang, chat.title or None)
    s = BOT_STRINGS[new_lang]
    new_text = s["group_text"] if chat.type in ["group", "supergroup"] else s["personal_text"]
    try:
        await cb.message.edit_text(new_text, reply_markup=build_keyboard(chat, new_lang))
    except Exception:
        pass
    await cb.answer(s["lang_switched"])


async def main():
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
