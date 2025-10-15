import json
import os
import importlib
import config
import asyncio
from flask import Flask
import threading
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from config import TOKEN, ADMIN_ID, CHANNEL_ID

# ===== Flask server (ping uchun) =====
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot ishlayapti!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

# ===== Telegram bot qismi =====
CHANNEL_IDS = getattr(config, "CHANNEL_IDS", [])
MOVIES_FILE = "movies.json"
USERS_FILE = "users.json"

def ensure_file(file, default_data):
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)

def load_json(file):
    ensure_file(file, {} if "movie" in file else [])
    with open(file, "r", encoding="utf-8") as f:
        data = json.load(f)
        if "users" in file:
            return list(set(data))
        return data

def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["🎥 Kino qo‘shish", "🗑 Kino o‘chirish"],
            ["➕ Kanal qo‘shish", "➖ Kanal o‘chirish"],
            ["👥 Foydalanuvchilar sonini ko‘rish", "♻️ Botni qayta sozlash"],
        ],
        resize_keyboard=True
    )

async def get_non_joined_channels(context: ContextTypes.DEFAULT_TYPE, user_id: int, channel_ids: list):
    non_joined = []
    for ch in channel_ids:
        channel_id = ch["id"] if isinstance(ch, dict) else ch
        try:
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in ["left", "kicked"]:
                non_joined.append(ch)
        except Exception:
            non_joined.append(ch)
    return non_joined

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return False

    text = update.message.text.strip().upper()

    if text == "🎥 KINO QO‘SHISH":
        await update.message.reply_text("🎞 Kinoni video sifatida yuboring:")
        context.user_data["adding_movie"] = True
        return True

    elif text == "🗑 KINO O‘CHIRISH":
        await update.message.reply_text("❌ O‘chirmoqchi bo‘lgan kino kodini kiriting:")
        context.user_data["deleting_movie"] = True
        return True

    elif text == "➕ KANAL QO‘SHISH":
        await update.message.reply_text(
            "🔗 Yangi kanalni kiriting:\n➡️ @username yoki https://t.me/... formatida bo‘lishi kerak."
        )
        context.user_data["changing_channel"] = True
        return True

    elif text == "➖ KANAL O‘CHIRISH":
        await update.message.reply_text("❌ O‘chirmoqchi bo‘lgan kanal ID yoki username kiriting:")
        context.user_data["deleting_channel"] = True
        return True

    elif text == "👥 FOYDALANUVCHILAR SONINI KO‘RISH":
        users = load_json(USERS_FILE)
        await update.message.reply_text(f"👥 Foydalanuvchilar soni: {len(users)} ta", reply_markup=admin_menu())
        return True

    elif text == "♻️ BOTNI QAYTA SOZLASH":
        context.user_data.clear()
        await update.message.reply_text("✅ Bot qayta sozlandi.", reply_markup=admin_menu())
        return True

    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    users = load_json(USERS_FILE)
    if user_id not in users:
        users.append(user_id)
        save_json(USERS_FILE, users)

    if user_id == ADMIN_ID:
        await update.message.reply_text("🎬 Salom, admin!", reply_markup=admin_menu())
        return

    if CHANNEL_IDS:
        non_joined = await get_non_joined_channels(context, user_id, CHANNEL_IDS)
        if non_joined:
            buttons = [
                [InlineKeyboardButton("📢 Obuna bo‘lish", url=ch["link"])]
                for ch in non_joined if isinstance(ch, dict)
            ]
            buttons.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_membership")])
            await update.message.reply_text(
                "⚠️ Iltimos, quyidagi kanallarga obuna bo‘ling:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

    await update.message.reply_text("🎬 Kino olish uchun kod yuboring:")

async def check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if CHANNEL_IDS:
        non_joined = await get_non_joined_channels(context, user_id, CHANNEL_IDS)
        if non_joined:
            buttons = [
                [InlineKeyboardButton("📢 Obuna bo‘lish", url=ch["link"])]
                for ch in non_joined if isinstance(ch, dict)
            ]
            buttons.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_membership")])
            await query.edit_message_text("⚠️ Hali barcha kanallarga obuna bo‘lmadingiz.", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.edit_message_text("✅ Obuna bo‘ldingiz! Endi kod yuboring.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.user_data.get("adding_movie"):
        return

    video = update.message.video
    if not video:
        await update.message.reply_text("❌ Faqat video yuboring.")
        return

    context.user_data["temp_video_id"] = video.file_id
    context.user_data["adding_movie"] = False
    context.user_data["awaiting_code"] = True

    await update.message.reply_text("🔢 Shu kinoga kod kiriting (masalan: FAST10):")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHANNEL_IDS
    text = update.message.text.strip().upper()
    raw_text = update.message.text.strip()
    user_id = update.effective_user.id

    if user_id == ADMIN_ID and await handle_admin_buttons(update, context):
        return

    if user_id == ADMIN_ID and context.user_data.get("awaiting_code"):
        movies = load_json(MOVIES_FILE)
        if text in movies:
            await update.message.reply_text("⚠️ Bu kod allaqachon mavjud! Iltimos boshqa kod kiriting:", reply_markup=admin_menu())
            return

        file_id = context.user_data.get("temp_video_id")
        movies[text] = file_id
        save_json(MOVIES_FILE, movies)

        try:
            msg = await context.bot.send_video(
                chat_id=CHANNEL_ID,
                video=file_id,
                caption=f"🎬 Kino kodi: `{text}`",
                parse_mode="Markdown"
            )
            movies[text] = {"file_id": file_id, "msg_id": msg.message_id}
            save_json(MOVIES_FILE, movies)
            await update.message.reply_text(f"✅ Kino saqlandi va kanalga yuborildi.\nKod: {text}", reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"❌ Kanalga yuborishda xato: {e}", reply_markup=admin_menu())

        context.user_data.clear()
        return

    if user_id == ADMIN_ID and context.user_data.get("deleting_movie"):
        movies = load_json(MOVIES_FILE)
        if text not in movies:
            await update.message.reply_text("❌ Bunday kod topilmadi.", reply_markup=admin_menu())
            context.user_data.clear()
            return

        movie = movies.pop(text)
        save_json(MOVIES_FILE, movies)

        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=movie["msg_id"])
            await update.message.reply_text(f"🗑 Kino '{text}' o‘chirildi (kanaldan ham).", reply_markup=admin_menu())
        except Exception:
            await update.message.reply_text(f"⚠️ Kino '{text}' o‘chirildi, lekin kanaldan o‘chirilmadi.", reply_markup=admin_menu())

        context.user_data.clear()
        return

    movies = load_json(MOVIES_FILE)
    if text in movies:
        await update.message.reply_video(movies[text]["file_id"], caption=f"🎬 Kod: {text}")
    else:
        await update.message.reply_text("❌ Bunday kod topilmadi.")

# === Ishga tushirish ===
def main():
    app_bot = ApplicationBuilder().token(TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app_bot.add_handler(CallbackQueryHandler(check_callback, pattern="check_membership"))

    print("🤖 Bot va Flask server ishga tushdi...")
    threading.Thread(target=run_flask).start()
    app_bot.run_polling()

if __name__ == "__main__":
    main()
