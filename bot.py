from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import os
import sqlite3
from config import BOT_TOKEN

# Fixed bug 5: "group" collided with the g<chat_id> encoding scheme used for
# real group deep-links (the frontend treated anything starting with "g" as
# an encoded group chat id, so "group" itself parsed into a bogus id).
DIRECT_APP_LINK = "https://t.me/rosecap_nova_bot/split?startapp=personal"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect("nova.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


async def save_group_photo(chat_id: int, title: str):
    """Fetch the group's profile photo (if any) and store it on disk + groups table."""
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or title
        if chat.photo:
            small = chat.photo.small_file_id
            file_name = f"group_{chat_id}.jpg"
            dest = os.path.join(PHOTOS_DIR, file_name)
            await bot.download(small, destination=dest)
            conn = get_db()
            conn.cursor().execute(
                "INSERT INTO groups (chat_id, title, photo_file, member_count, updated_at) VALUES (?, ?, ?, 0, strftime('%s','now')) "
                "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, photo_file=excluded.photo_file, updated_at=strftime('%s','now')",
                (str(chat_id), title, file_name),
            )
            conn.commit()
            conn.close()
            return
        # No photo — still upsert title
        conn = get_db()
        conn.cursor().execute(
            "INSERT INTO groups (chat_id, title, photo_file, member_count, updated_at) VALUES (?, ?, NULL, 0, strftime('%s','now')) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=COALESCE(groups.title, excluded.title)",
            (str(chat_id), title),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"save_group_photo failed: {e}")


@dp.message(Command("start", "split"))
async def send_app_button(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        clean_chat_id = str(message.chat.id).replace("-", "g")
        dynamic_link = f"https://t.me/rosecap_nova_bot/split?startapp={clean_chat_id}"
        text = "Tap below to view or add expenses for this group☺️"
        asyncio.create_task(save_group_photo(message.chat.id, message.chat.title or "Group"))
    else:
        dynamic_link = DIRECT_APP_LINK
        text = "Tap below to open your personal expense tracker!"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Open Nova", url=dynamic_link)]
    ])

    await message.answer(text, reply_markup=markup)


async def main():
    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())