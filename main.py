"""
SHUVO AI OFFICIAL — Link Bypass Bot
Only responds to registered commands. No reply on plain text.
"""

import logging
import time
import requests
import telebot
from pymongo import MongoClient
from telebot import types
from urllib.parse import urlparse

# ── Hardcoded Config ─────────────────────────────────────
BOT_TOKEN = "8746241415:AAGf_HEP6Iy5GJXH0GFTTK-3nnwhytzRYEw"
BYPASS_API = "https://shuvo-bypasser-k8iw.onrender.com/bypass"
ADMIN_IDS = [8600328303]  # <-- replace with your real Telegram user id(s)

# MongoDB Atlas free cluster connection string (get from cloud.mongodb.com)
MONGO_URI = "mongodb+srv://sajidbbz55_db_user:shuvobhai@cluster0.ker9hya.mongodb.net/?appName=Cluster0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bypass-bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ── Persistent Storage (MongoDB Atlas — survives restarts/redeploys) ──
mongo = MongoClient(MONGO_URI)
db = mongo["shuvo_bypass_bot"]
users_col = db["users"]        # { _id: user_id }
usage_col = db["usage"]        # { _id: user_id, date: "YYYY-MM-DD", count: N }

stats = {"total_requests": 0, "success": 0, "failed": 0}

# ── Branding ──────────────────────────────────────────────
DIVIDER = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
FOOTER = f"\n{DIVIDER}\n<i>⚡ SHUVO AI OFFICIAL</i>"

def brand(body: str) -> str:
    return f"{body}{FOOTER}"


# ── Styled Button Class ───────────────────────────────────
# Telegram Bot API 9.4 (Feb 9, 2026) added a real "style" field for
# InlineKeyboardButton: "primary" (blue), "success" (green), "danger" (red).
# pyTelegramBotAPI doesn't expose this natively yet, so this subclass
# injects the field manually via to_dict() — this is a legitimate, working
# way to get colored buttons until the library adds native support.
class SBtn(types.InlineKeyboardButton):
    def __init__(self, text, style=None, **kwargs):
        super().__init__(text, **kwargs)
        self._style = style

    def to_dict(self):
        d = super().to_dict()
        if self._style:
            d["style"] = self._style
        return d


# ── User storage (MongoDB — persists across restarts/redeploys) ──
def load_users() -> set:
    try:
        return {doc["_id"] for doc in users_col.find({}, {"_id": 1})}
    except Exception as e:
        log.error(f"Failed to load users from MongoDB: {e}")
        return set()


known_users = load_users()


def register_user(user_id: int):
    if user_id not in known_users:
        known_users.add(user_id)
        try:
            users_col.update_one({"_id": user_id}, {"$set": {"_id": user_id}}, upsert=True)
        except Exception as e:
            log.error(f"Failed to register user {user_id} in MongoDB: {e}")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ── Daily bypass limit (non-admins only, MongoDB-backed) ─
DAILY_LIMIT = 20


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def get_usage_count(user_id: int) -> int:
    try:
        doc = usage_col.find_one({"_id": user_id})
    except Exception as e:
        log.error(f"Failed to read usage for {user_id}: {e}")
        return 0
    if not doc or doc.get("date") != today_str():
        return 0
    return doc.get("count", 0)


def increment_usage(user_id: int):
    today = today_str()
    try:
        doc = usage_col.find_one({"_id": user_id})
        if not doc or doc.get("date") != today:
            usage_col.update_one(
                {"_id": user_id},
                {"$set": {"_id": user_id, "date": today, "count": 1}},
                upsert=True,
            )
        else:
            usage_col.update_one({"_id": user_id}, {"$inc": {"count": 1}})
    except Exception as e:
        log.error(f"Failed to increment usage for {user_id}: {e}")


def remaining_quota(user_id: int) -> int:
    if is_admin(user_id):
        return -1  # unlimited
    return max(0, DAILY_LIMIT - get_usage_count(user_id))


def reset_usage(user_id: int):
    try:
        usage_col.update_one(
            {"_id": user_id},
            {"$set": {"_id": user_id, "date": today_str(), "count": 0}},
            upsert=True,
        )
    except Exception as e:
        log.error(f"Failed to reset usage for {user_id}: {e}")


# ── Helpers ───────────────────────────────────────────────
def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def call_bypass_api(url: str, timeout: int = 20):
    try:
        resp = requests.get(BYPASS_API, params={"url": url}, timeout=timeout)
        if resp.status_code == 200:
            return True, resp.json()
        return False, f"API returned status {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "Request timed out. The target site may be slow to respond."
    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"
    except ValueError:
        return False, "API returned invalid response format."


def extract_result_link(data):
    """Matches your API's JSON response shape: {"bypassed": "...", "status": "..."}"""
    if isinstance(data, dict):
        for key in ("bypassed", "result", "bypassed_url", "url", "link", "final_url"):
            if key in data and data[key]:
                return data[key]
    return None


# ── Commands ──────────────────────────────────────────────
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
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        SBtn("🔗 Bypass a Link", style="primary", switch_inline_query_current_chat="/bypass "),
    )
    kb.add(
        SBtn("📋 Help", style="success", callback_data="menu_help"),
        SBtn("📊 Stats", style="success", callback_data="menu_stats"),
    )
    bot.reply_to(message, brand(body), reply_markup=kb)


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
    bar = make_bar(stats["success"], stats["failed"])
    rate = (
        round((stats["success"] / stats["total_requests"]) * 100)
        if stats["total_requests"] else 0
    )
    return (
        "📊 <b>LIVE STATISTICS</b>\n"
        f"{DIVIDER}\n"
        f"🔸 Total Requests: <b>{stats['total_requests']}</b>\n"
        f"🔸 ✅ Success: <b>{stats['success']}</b>\n"
        f"🔸 ❌ Failed: <b>{stats['failed']}</b>\n"
        f"🔸 👥 Known Users: <b>{len(known_users)}</b>\n\n"
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
    if call.data == "menu_help":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, brand(help_body()))
    elif call.data == "menu_stats":
        bot.answer_callback_query(call.id)
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
        unlock_kb = types.InlineKeyboardMarkup(row_width=1)
        unlock_kb.add(
            SBtn("🔓 Remove Limit (Admin Only)", style="danger", callback_data=f"unlock_{user_id}")
        )
        bot.reply_to(
            message,
            brand(
                "🚫 <b>Daily Limit Reached</b>\n"
                f"{DIVIDER}\n"
                f"<blockquote>You've used all <b>{DAILY_LIMIT}</b> bypasses for today. "
                "Your quota resets at midnight — come back tomorrow! ⏰</blockquote>"
            ),
            reply_markup=unlock_kb,
        )
        return

    stats["total_requests"] += 1

    # Animated loading sequence
    processing = bot.reply_to(message, "⚡ <b>Bypassing...</b>\n<code>[░░░░░░░░░░] 0%</code>")
    loading_frames = [
        "⚡ <b>Connecting...</b>\n<code>[███░░░░░░░] 30%</code>",
        "🔎 <b>Analyzing target...</b>\n<code>[██████░░░░] 60%</code>",
        "🔓 <b>Extracting link...</b>\n<code>[█████████░] 90%</code>",
    ]
    for frame in loading_frames:
        try:
            bot.edit_message_text(frame, chat_id=processing.chat.id, message_id=processing.message_id)
        except Exception:
            pass
        time.sleep(0.4)

    ok, data = call_bypass_api(url)

    if not ok:
        stats["failed"] += 1
        bot.edit_message_text(
            brand(f"❌ <b>Bypass Failed</b>\n{DIVIDER}\n<blockquote>{data}</blockquote>"),
            chat_id=processing.chat.id,
            message_id=processing.message_id,
        )
        return

    result_link = extract_result_link(data)

    if not result_link:
        stats["failed"] += 1
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

    stats["success"] += 1
    if not is_admin(user_id):
        increment_usage(user_id)

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(SBtn("🔗 OPEN LINK", style="primary", url=result_link))

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
        reply_markup=kb,
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


# ── Admin Panel ───────────────────────────────────────────
def admin_panel_body() -> str:
    return (
        "👑 <b>ADMIN PANEL</b>\n"
        f"{DIVIDER}\n"
        f"🔸 👥 Total Users: <b>{len(known_users)}</b>\n"
        f"🔸 📨 Requests: <b>{stats['total_requests']}</b>\n"
        f"🔸 ✅ Success: <b>{stats['success']}</b>  |  ❌ Failed: <b>{stats['failed']}</b>\n\n"
        "<blockquote>Choose an action below 👇</blockquote>"
    )


def admin_panel_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        SBtn("📊 Stats", style="success", callback_data="admin_stats"),
        SBtn("👥 Users", style="primary", callback_data="admin_users"),
    )
    kb.add(
        SBtn("📢 Broadcast Info", style="primary", callback_data="admin_bc_help"),
        SBtn("🔄 Refresh", style="success", callback_data="admin_refresh"),
    )
    return kb


@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    register_user(message.from_user.id)
    if not is_admin(message.from_user.id):
        bot.reply_to(message, brand("⛔ <b>Access Denied</b>\n<blockquote>This panel is for admins only.</blockquote>"))
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
            brand(f"👥 <b>USER COUNT</b>\n{DIVIDER}\n🔸 Known users: <b>{len(known_users)}</b>"),
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


# ── Broadcast (admin only, supports ALL message types) ───
@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, brand("⛔ <b>Access Denied</b>\n<blockquote>You are not authorized to use this command.</blockquote>"))
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
    total = len(known_users)
    sent, failed = 0, 0

    status = bot.reply_to(
        message, f"📢 <b>Broadcasting to {total} users...</b> ⏳"
    )

    for uid in list(known_users):
        try:
            bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=target.message_id)
            sent += 1
        except Exception as e:
            failed += 1
            log.warning(f"Broadcast failed for {uid}: {e}")
        time.sleep(0.05)  # gentle pacing to avoid hitting Telegram flood limits

    bot.edit_message_text(
        brand(
            "📢 <b>BROADCAST COMPLETE</b> ✅\n"
            f"{DIVIDER}\n"
            f"🔸 👥 Total users: <b>{total}</b>\n"
            f"🔸 ✅ Delivered: <b>{sent}</b>\n"
            f"🔸 ❌ Failed: <b>{failed}</b>"
        ),
        chat_id=status.chat.id,
        message_id=status.message_id,
    )


# ── Ignore everything that isn't a command ───────────────
@bot.message_handler(func=lambda m: True, content_types=[
    "text", "photo", "video", "document", "sticker", "voice",
    "audio", "animation", "video_note", "location", "contact",
])
def ignore_non_commands(message):
    register_user(message.from_user.id)
    return  # intentionally silent — bot only reacts to registered commands


# ── Run ───────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("SHUVO Link Bypass Bot starting...")
    bot.infinity_polling(skip_pending=True)
