"""
SHUVO AI OFFICIAL — Link Bypass Bot
Only responds to registered commands.

User records, daily usage, and statistics are persisted in MongoDB.
Configuration is kept directly in this file for the requested deployment setup.
"""

import logging
import os
import threading
import time
from urllib.parse import urlparse

import pymongo
import requests
import telebot
from flask import Flask
from telebot import types


# ── Config ──────────────────────────────────────────────────
BOT_TOKEN = "8746241415:AAGf_HEP6Iy5GJXH0GFTTK-3nnwhytzRYEw"
MONGODB_URI = "mongodb+srv://playzarmc_db_user:vmfWz66SV3wrWUdV@cluster0.orj9xl9.mongodb.net"
BYPASS_API = os.environ.get(
    "BYPASS_API",
    "https://shuvo-bypasser-k8iw.onrender.com/bypass",
).strip()
ADMIN_IDS = [8600328303]
DAILY_LIMIT = 20
MONGODB_DATABASE = "shuvo_ai"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bypass-bot")


def create_mongodb():
    client = pymongo.MongoClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=15_000,
        connectTimeoutMS=15_000,
    )
    client.admin.command("ping")
    database = client[MONGODB_DATABASE]
    database["users"].create_index("user_id", unique=True)
    database["usage"].create_index("user_id", unique=True)
    return client, database


mongo_client, mongo_db = create_mongodb()
users_collection = mongo_db["users"]
usage_collection = mongo_db["usage"]
stats_collection = mongo_db["stats"]

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")


# ── Keep-alive HTTP server ──────────────────────────────────
web_app = Flask(__name__)


@web_app.route("/")
@web_app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    web_app.run(host="0.0.0.0", port=port)


# ── MongoDB-backed storage ─────────────────────────────────
def register_user(user_id: int):
    users_collection.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {"user_id": user_id, "created_at": time.time()}},
        upsert=True,
    )


def user_count() -> int:
    return users_collection.count_documents({})


def user_ids() -> list[int]:
    return [
        int(doc["user_id"])
        for doc in users_collection.find({}, {"_id": 0, "user_id": 1})
    ]


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def get_usage_count(user_id: int) -> int:
    entry = usage_collection.find_one({"user_id": user_id}, {"_id": 0})
    if not entry or entry.get("date") != today_str():
        return 0
    return int(entry.get("count", 0))


def increment_usage(user_id: int):
    today = today_str()
    usage_collection.update_one(
        {"user_id": user_id},
        [
            {
                "$set": {
                    "user_id": user_id,
                    "date": today,
                    "count": {
                        "$cond": [
                            {"$eq": ["$date", today]},
                            {"$add": [{"$ifNull": ["$count", 0]}, 1]},
                            1,
                        ]
                    },
                }
            }
        ],
        upsert=True,
    )


def remaining_quota(user_id: int) -> int:
    if is_admin(user_id):
        return -1
    return max(0, DAILY_LIMIT - get_usage_count(user_id))


def reset_usage(user_id: int):
    usage_collection.replace_one(
        {"user_id": user_id},
        {"user_id": user_id, "date": today_str(), "count": 0},
        upsert=True,
    )


def increment_stat(stat_name: str):
    stats_collection.update_one(
        {"_id": "global"},
        {"$inc": {stat_name: 1}},
        upsert=True,
    )


def get_stats() -> dict[str, int]:
    document = stats_collection.find_one({"_id": "global"}) or {}
    return {
        "total_requests": int(document.get("total_requests", 0)),
        "success": int(document.get("success", 0)),
        "failed": int(document.get("failed", 0)),
    }


# ── Branding ────────────────────────────────────────────────
DIVIDER = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
FOOTER = f"\n{DIVIDER}\n<i>⚡ SHUVO AI OFFICIAL</i>"


def brand(body: str) -> str:
    return f"{body}{FOOTER}"


# ── Styled button class ─────────────────────────────────────
class SBtn(types.InlineKeyboardButton):
    def __init__(self, text, style=None, **kwargs):
        super().__init__(text, **kwargs)
        self._style = style

    def to_dict(self):
        data = super().to_dict()
        if self._style:
            data["style"] = self._style
        return data


# ── Helpers ─────────────────────────────────────────────────
def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def call_bypass_api(url: str, timeout: int = 20):
    try:
        response = requests.get(BYPASS_API, params={"url": url}, timeout=timeout)
        if response.status_code == 200:
            return True, response.json()
        return False, f"API returned status {response.status_code}"
    except requests.exceptions.Timeout:
        return False, "Request timed out. The target site may be slow to respond."
    except requests.exceptions.RequestException as error:
        return False, f"Network error: {error}"
    except ValueError:
        return False, "API returned invalid response format."


def extract_result_link(data):
    if isinstance(data, dict):
        for key in ("bypassed", "result", "bypassed_url", "url", "link", "final_url"):
            if data.get(key):
                return data[key]
    return None


# ── Commands ────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    register_user(message.from_user.id)
    name = message.from_user.first_name or "there"
    body = (
        f"⚡ <b>Hey {name}, welcome aboard!</b>\n"
        f"{DIVIDER}\n"
        "🚀 <b>SHUVO LINK BYPASSER</b>\n"
        "<blockquote>Skip shortener &amp; ad-gate links instantly.\n"
        "Fast • Clean • Reliable</blockquote>\n\n"
        "📌 <b>How to use:</b>\n"
        "<code>/bypass &lt;link&gt;</code>\n\n"
        "💎 <b>Example:</b>\n"
        "<code>/bypass https://lksfy.com/wWAhrU5x</code>\n\n"
        "📋 Send /help for the full command list."
    )
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        SBtn(
            "🔗 Bypass a Link",
            style="primary",
            switch_inline_query_current_chat="/bypass ",
        )
    )
    keyboard.add(
        SBtn("📋 Help", style="success", callback_data="menu_help"),
        SBtn("📊 Stats", style="success", callback_data="menu_stats"),
    )
    bot.reply_to(message, brand(body), reply_markup=keyboard)


def help_body() -> str:
    return (
        "🛠 <b>COMMAND CENTER</b>\n"
        f"{DIVIDER}\n"
        "🔹 <code>/bypass &lt;link&gt;</code> or <code>/by &lt;link&gt;</code>\n"
        "   <i>↳ Bypass a shortener/ad-gate link</i>\n\n"
        "🔹 <code>/start</code>\n"
        "   <i>↳ Bot intro &amp; quick usage</i>\n\n"
        "🔹 <code>/help</code> or <code>/use</code>\n"
        "   <i>↳ This menu</i>\n\n"
        "🔹 <code>/stats</code>\n"
        "   <i>↳ Live usage statistics</i>\n\n"
        "🔹 <code>/admin</code>\n"
        "   <i>↳ Admin panel (admins only)</i>\n\n"
        "<blockquote>⚠️ Only commands get a reply — plain messages are silently ignored.</blockquote>"
    )


@bot.message_handler(commands=["help", "use"])
def cmd_help(message):
    register_user(message.from_user.id)
    bot.reply_to(message, brand(help_body()))


def make_bar(success: int, failed: int, length: int = 10) -> str:
    total = success + failed
    if total == 0:
        return "░" * length
    filled = round((success / total) * length)
    return "█" * filled + "░" * (length - filled)


def stats_body() -> str:
    stats = get_stats()
    bar = make_bar(stats["success"], stats["failed"])
    rate = (
        round((stats["success"] / stats["total_requests"]) * 100)
        if stats["total_requests"]
        else 0
    )
    return (
        "📊 <b>LIVE STATISTICS</b>\n"
        f"{DIVIDER}\n"
        f"🔸 Total Requests: <b>{stats['total_requests']}</b>\n"
        f"🔸 ✅ Success: <b>{stats['success']}</b>\n"
        f"🔸 ❌ Failed: <b>{stats['failed']}</b>\n"
        f"🔸 👥 Known Users: <b>{user_count()}</b>\n\n"
        f"<b>Success Rate:</b> {rate}%\n"
        f"<code>{bar}</code>"
    )


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    register_user(message.from_user.id)
    bot.reply_to(message, brand(stats_body()))


@bot.callback_query_handler(func=lambda call: call.data in ("menu_help", "menu_stats"))
def handle_menu_callbacks(call):
    register_user(call.from_user.id)
    bot.answer_callback_query(call.id)
    if call.data == "menu_help":
        bot.send_message(call.message.chat.id, brand(help_body()))
    else:
        bot.send_message(call.message.chat.id, brand(stats_body()))


@bot.message_handler(commands=["bypass", "by"])
def cmd_bypass(message):
    register_user(message.from_user.id)
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            brand(
                "⚠️ <b>Missing Link</b>\n"
                f"{DIVIDER}\n"
                "<blockquote>📌 Usage: <code>/bypass https://example.com/xyz</code></blockquote>"
            ),
        )
        return

    url = parts[1].strip()
    if not is_valid_url(url):
        bot.reply_to(
            message,
            brand(
                "❌ <b>Invalid URL</b>\n"
                f"{DIVIDER}\n"
                "<blockquote>🔸 Must start with <code>http://</code> or <code>https://</code></blockquote>"
            ),
        )
        return

    user_id = message.from_user.id
    if not is_admin(user_id) and get_usage_count(user_id) >= DAILY_LIMIT:
        unlock_keyboard = types.InlineKeyboardMarkup(row_width=1)
        unlock_keyboard.add(
            SBtn(
                "🔓 Remove Limit (Admin Only)",
                style="danger",
                callback_data=f"unlock_{user_id}",
            )
        )
        bot.reply_to(
            message,
            brand(
                "🚫 <b>Daily Limit Reached</b>\n"
                f"{DIVIDER}\n"
                f"<blockquote>You've used all <b>{DAILY_LIMIT}</b> bypasses for today. "
                "Your quota resets at midnight — come back tomorrow! ⏰</blockquote>"
            ),
            reply_markup=unlock_keyboard,
        )
        return

    increment_stat("total_requests")
    processing = bot.reply_to(
        message, "⚡ <b>Bypassing...</b>\n<code>[░░░░░░░░░░] 0%</code>"
    )
    loading_frames = [
        "⚡ <b>Connecting...</b>\n<code>[███░░░░░░░] 30%</code>",
        "🔎 <b>Analyzing target...</b>\n<code>[██████░░░░] 60%</code>",
        "🔓 <b>Extracting link...</b>\n<code>[█████████░] 90%</code>",
    ]
    for frame in loading_frames:
        try:
            bot.edit_message_text(
                frame,
                chat_id=processing.chat.id,
                message_id=processing.message_id,
            )
        except Exception:
            pass
        time.sleep(0.4)

    ok, data = call_bypass_api(url)
    if not ok:
        increment_stat("failed")
        bot.edit_message_text(
            brand(f"❌ <b>Bypass Failed</b>\n{DIVIDER}\n<blockquote>{data}</blockquote>"),
            chat_id=processing.chat.id,
            message_id=processing.message_id,
        )
        return

    result_link = extract_result_link(data)
    if not result_link:
        increment_stat("failed")
        bot.edit_message_text(
            brand(
                "❌ <b>Extraction Failed</b>\n"
                f"{DIVIDER}\n"
                "<blockquote>⚠️ Couldn't find a direct link in the API response. "
                "Format may have changed.</blockquote>"
            ),
            chat_id=processing.chat.id,
            message_id=processing.message_id,
        )
        return

    increment_stat("success")
    if not is_admin(user_id):
        increment_usage(user_id)

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(SBtn("🔗 OPEN LINK", style="primary", url=result_link))
    remaining = remaining_quota(user_id)
    quota_line = (
        "♾️ <b>Unlimited</b> (admin)"
        if remaining == -1
        else f"<b>{remaining}/{DAILY_LIMIT}</b> left today"
    )
    body = (
        "✅ <b>BYPASS SUCCESSFUL</b> 🎉\n"
        f"{DIVIDER}\n"
        "🔒 <b>Original:</b>\n"
        f"<blockquote>🌐 <a href=\"{url}\">{url}</a></blockquote>\n"
        "🔓 <b>Bypassed:</b>\n"
        f"<blockquote>🌐 <a href=\"{result_link}\">{result_link}</a></blockquote>\n"
        f"📦 <b>Quota:</b> {quota_line}"
    )
    bot.edit_message_text(
        brand(body),
        chat_id=processing.chat.id,
        message_id=processing.message_id,
        reply_markup=keyboard,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("unlock_"))
def handle_unlock_callback(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(
            call.id,
            "⛔ This button is for admins only.",
            show_alert=True,
        )
        return

    target_id = int(call.data.split("_", 1)[1])
    reset_usage(target_id)
    bot.answer_callback_query(call.id, "✅ Limit removed for this user!")
    try:
        bot.edit_message_text(
            brand(
                "🔓 <b>LIMIT REMOVED</b> ✅\n"
                f"{DIVIDER}\n"
                f"<blockquote>Daily quota reset — user can bypass "
                f"<b>{DAILY_LIMIT}</b> more links today.</blockquote>"
            ),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass


# ── Admin panel ─────────────────────────────────────────────
def admin_panel_body() -> str:
    stats = get_stats()
    return (
        "👑 <b>ADMIN PANEL</b>\n"
        f"{DIVIDER}\n"
        f"🔸 👥 Total Users: <b>{user_count()}</b>\n"
        f"🔸 📨 Requests: <b>{stats['total_requests']}</b>\n"
        f"🔸 ✅ Success: <b>{stats['success']}</b>  |  ❌ Failed: <b>{stats['failed']}</b>\n\n"
        "<blockquote>Choose an action below 👇</blockquote>"
    )


def admin_panel_kb() -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        SBtn("📊 Stats", style="success", callback_data="admin_stats"),
        SBtn("👥 Users", style="primary", callback_data="admin_users"),
    )
    keyboard.add(
        SBtn("📢 Broadcast Info", style="primary", callback_data="admin_bc_help"),
        SBtn("🔄 Refresh", style="success", callback_data="admin_refresh"),
    )
    return keyboard


@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    register_user(message.from_user.id)
    if not is_admin(message.from_user.id):
        bot.reply_to(
            message,
            brand("⛔ <b>Access Denied</b>\n<blockquote>This panel is for admins only.</blockquote>"),
        )
        return
    bot.reply_to(message, brand(admin_panel_body()), reply_markup=admin_panel_kb())


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def handle_admin_callbacks(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Admins only.", show_alert=True)
        return

    if call.data == "admin_stats":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, brand(stats_body()))
    elif call.data == "admin_users":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            brand(f"👥 <b>USER COUNT</b>\n{DIVIDER}\n🔸 Known users: <b>{user_count()}</b>"),
        )
    elif call.data == "admin_bc_help":
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            brand(
                "📢 <b>HOW TO BROADCAST</b>\n"
                f"{DIVIDER}\n"
                "<blockquote>Reply to any message with <code>/broadcast</code> "
                "to copy it to every known user.</blockquote>"
            ),
        )
    elif call.data == "admin_refresh":
        bot.answer_callback_query(call.id, "Refreshed ✅")
        try:
            bot.edit_message_text(
                brand(admin_panel_body()),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=admin_panel_kb(),
            )
        except Exception:
            pass


# ── Broadcast ───────────────────────────────────────────────
@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(
            message,
            brand("⛔ <b>Access Denied</b>\n<blockquote>You are not authorized to use this command.</blockquote>"),
        )
        return

    if not message.reply_to_message:
        bot.reply_to(
            message,
            brand(
                "📢 <b>HOW TO BROADCAST</b>\n"
                f"{DIVIDER}\n"
                "<blockquote>Reply to any message (text, photo, video, document, "
                "sticker, voice — anything) with <code>/broadcast</code> "
                "and it's copied to every known user.</blockquote>"
            ),
        )
        return

    target = message.reply_to_message
    recipients = user_ids()
    sent, failed = 0, 0
    status = bot.reply_to(
        message,
        f"📢 <b>Broadcasting to {len(recipients)} users...</b> ⏳",
    )

    for user_id in recipients:
        try:
            bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=target.message_id,
            )
            sent += 1
        except Exception as error:
            failed += 1
            log.warning("Broadcast failed for %s: %s", user_id, error)
        time.sleep(0.05)

    bot.edit_message_text(
        brand(
            "📢 <b>BROADCAST COMPLETE</b> ✅\n"
            f"{DIVIDER}\n"
            f"🔸 👥 Total users: <b>{len(recipients)}</b>\n"
            f"🔸 ✅ Delivered: <b>{sent}</b>\n"
            f"🔸 ❌ Failed: <b>{failed}</b>"
        ),
        chat_id=status.chat.id,
        message_id=status.message_id,
    )


# ── Ignore everything that isn't a command ─────────────────
@bot.message_handler(
    func=lambda message: True,
    content_types=[
        "text",
        "photo",
        "video",
        "document",
        "sticker",
        "voice",
        "audio",
        "animation",
        "video_note",
        "location",
        "contact",
    ],
)
def ignore_non_commands(message):
    register_user(message.from_user.id)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("SHUVO Link Bypass Bot starting with MongoDB persistence...")
    bot.infinity_polling(skip_pending=True)
