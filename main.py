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
BOT_TOKEN = "8746241415:AAE2XgNB4t-aUEmFipB626rFJmdtPQ_ljEM"
MONGODB_URI = "mongodb+srv://playzarmc_db_user:vmfWz66SV3wrWUdV@cluster0.orj9xl9.mongodb.net"
BYPASS_API = os.environ.get(
    "BYPASS_API",
    "https://alexbypassapi.up.railway.app/bypass?url=",
).strip()
ADMIN_IDS = [8600328303]
DAILY_LIMIT = 20
MONGODB_DATABASE = "shuvo_ai"

# Bot username / channel / support group
BOT_USERNAME = "Shuvo_bypasserbot"          # without @
CHANNEL_LINK = "https://t.me/SHUVOMODS"
SUPPORT_LINK = "https://t.me/Shuvobhai"

CACHE_TTL_SECONDS = 3600  # how long a bypassed link stays cached (1 hour)


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
settings_collection = mongo_db["settings"]

MAX_FORCE_JOIN = 6

_cluster_host = MONGODB_URI.split("@", 1)[-1].split("/", 1)[0]
log.info(
    "MongoDB connected → cluster=%s database=%s existing_users=%d",
    _cluster_host,
    MONGODB_DATABASE,
    users_collection.count_documents({}),
)

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


def is_banned(user_id: int) -> bool:
    doc = users_collection.find_one({"user_id": user_id}, {"_id": 0, "banned": 1})
    return bool(doc and doc.get("banned"))


def ban_user(user_id: int):
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"banned": True}},
        upsert=True,
    )


def unban_user(user_id: int):
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"banned": False}},
        upsert=True,
    )


def banned_count() -> int:
    return users_collection.count_documents({"banned": True})


# ── Force Join (up to MAX_FORCE_JOIN channels/groups) ───────
def get_force_join_channels() -> list[dict]:
    doc = settings_collection.find_one({"_id": "force_join"}) or {}
    return doc.get("channels", [])


def add_force_join_channel(chat_id: str, title: str, link: str) -> bool:
    channels = get_force_join_channels()
    if len(channels) >= MAX_FORCE_JOIN:
        return False
    channels.append({"chat_id": chat_id, "title": title, "link": link})
    settings_collection.update_one(
        {"_id": "force_join"},
        {"$set": {"channels": channels}},
        upsert=True,
    )
    return True


def remove_force_join_channel(index: int) -> bool:
    channels = get_force_join_channels()
    if index < 0 or index >= len(channels):
        return False
    channels.pop(index)
    settings_collection.update_one(
        {"_id": "force_join"},
        {"$set": {"channels": channels}},
        upsert=True,
    )
    return True


def get_no_command_mode() -> bool:
    doc = settings_collection.find_one({"_id": "config"}) or {}
    return bool(doc.get("no_command_mode", False))


def set_no_command_mode(enabled: bool):
    settings_collection.update_one(
        {"_id": "config"},
        {"$set": {"no_command_mode": enabled}},
        upsert=True,
    )


def get_unjoined_channels(user_id: int) -> list[dict]:
    unjoined = []
    for ch in get_force_join_channels():
        try:
            member = bot.get_chat_member(ch["chat_id"], user_id)
            if member.status not in ("member", "administrator", "creator"):
                unjoined.append(ch)
        except Exception as error:
            log.warning("Force-join check failed for %s: %s", ch.get("chat_id"), error)
            unjoined.append(ch)
    return unjoined


def force_join_body(unjoined: list[dict]) -> str:
    lines = [f"🔸 {ch['title']}" for ch in unjoined]
    lines.append("")
    lines.append("Join all of the above, then tap ✅ I've Joined below.")
    return panel("JOIN TO CONTINUE", lines, icon="🔐")


def force_join_keyboard(unjoined: list[dict]) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for ch in unjoined:
        keyboard.add(SBtn(f"📢 {ch['title']}", url=ch["link"]))
    keyboard.add(SBtn("✅ I've Joined — Check Again", style="success", callback_data="fj_check"))
    return keyboard


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


# ── Themes (3 colors, user-switchable) ──────────────────────
THEMES = {
    "blue": {"emoji": "🔵", "style": "primary", "name": "Blue"},
    "green": {"emoji": "🟢", "style": "success", "name": "Green"},
    "red": {"emoji": "🔴", "style": "danger", "name": "Red"},
}
DEFAULT_THEME = "blue"


def get_theme(user_id: int) -> str:
    doc = users_collection.find_one({"user_id": user_id}, {"_id": 0, "theme": 1})
    theme = doc.get("theme") if doc else None
    return theme if theme in THEMES else DEFAULT_THEME


def set_theme(user_id: int, theme: str):
    if theme not in THEMES:
        return
    users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"theme": theme}},
        upsert=True,
    )


def tbtn(theme: str, text: str, **kwargs) -> "SBtn":
    t = THEMES.get(theme, THEMES[DEFAULT_THEME])
    return SBtn(f"{t['emoji']} {text}", style=t["style"], **kwargs)


THEME_CYCLE = ["red", "blue", "green"]


def next_theme(current: str) -> str:
    if current not in THEME_CYCLE:
        return THEME_CYCLE[0]
    return THEME_CYCLE[(THEME_CYCLE.index(current) + 1) % len(THEME_CYCLE)]


# ── Branding ────────────────────────────────────────────────
DIVIDER = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
FOOTER = f"\n{DIVIDER}\n<i>⚡ SHUVO AI OFFICIAL</i>"


def brand(body: str) -> str:
    return f"{body}{FOOTER}"


def panel(title: str, lines, icon: str = "") -> str:
    """Bold header + divider + everything inside a single blockquote."""
    header = f"{icon} <b>{title}</b>" if icon else f"<b>{title}</b>"
    content = "\n".join(lines) if isinstance(lines, (list, tuple)) else lines
    return f"{header}\n{DIVIDER}\n<blockquote>{content}</blockquote>"


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


# ── Result cache (in-memory, per-URL) ───────────────────────
BYPASS_CACHE: dict[str, dict] = {}


def cache_get(url: str):
    entry = BYPASS_CACHE.get(url)
    if not entry:
        return None
    if time.time() - entry["cached_at"] > CACHE_TTL_SECONDS:
        BYPASS_CACHE.pop(url, None)
        return None
    return entry["result_link"]


def cache_set(url: str, result_link: str):
    BYPASS_CACHE[url] = {"result_link": result_link, "cached_at": time.time()}


def cache_clear():
    BYPASS_CACHE.clear()


# ── Commands ────────────────────────────────────────────────
def start_body(name: str) -> str:
    return panel(
        f"Hey {name}, welcome aboard!",
        [
            "🚀 <b>SHUVO LINK BYPASSER</b>",
            "Skip shortener &amp; ad-gate links instantly.",
            "Fast • Clean • Reliable",
            "",
            "📌 <b>How to use:</b>",
            "<code>/bypass &lt;link&gt;</code>",
            "",
            "💎 <b>Example:</b>",
            "<code>/bypass https://lksfy.com/wWAhrU5x</code>",
            "",
            "📋 Send /help for the full command list.",
        ],
        icon="⚡",
    )


def start_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    theme = get_theme(user_id)
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        tbtn(theme, "Bypass a Link", switch_inline_query_current_chat="/bypass ")
    )
    keyboard.add(
        tbtn(theme, "Add to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"),
    )
    keyboard.add(
        tbtn(theme, "Help", callback_data="menu_help"),
        tbtn(theme, "Support", url=SUPPORT_LINK),
    )
    return keyboard


@bot.message_handler(commands=["start"])
def cmd_start(message):
    register_user(message.from_user.id)
    name = message.from_user.first_name or "there"
    bot.reply_to(
        message,
        brand(start_body(name)),
        reply_markup=start_keyboard(message.from_user.id),
    )


def help_body() -> str:
    return panel(
        "COMMAND CENTER",
        [
            "🔹 <code>/bypass &lt;link&gt;</code> or <code>/by &lt;link&gt;</code>",
            "   ↳ Bypass a shortener/ad-gate link",
            "",
            "🔹 <code>/start</code>",
            "   ↳ Bot intro &amp; quick usage",
            "",
            "🔹 <code>/help</code> or <code>/use</code>",
            "   ↳ This menu",
            "",
            "🔹 <code>/stats</code>",
            "   ↳ Live usage statistics",
            "",
            "🔹 <code>/admin</code>",
            "   ↳ Admin panel (admins only)",
            "",
            "🔹 <code>/setting</code>",
            "   ↳ Color theme, stats &amp; preferences",
            "",
            "⚠️ Only commands get a reply — plain messages are silently ignored.",
        ],
        icon="🛠",
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
    return panel(
        "LIVE STATISTICS",
        [
            f"🔸 Total Requests: <b>{stats['total_requests']}</b>",
            f"🔸 ✅ Success: <b>{stats['success']}</b>",
            f"🔸 ❌ Failed: <b>{stats['failed']}</b>",
            f"🔸 👥 Known Users: <b>{user_count()}</b>",
            "",
            f"<b>Success Rate:</b> {rate}%",
            f"<code>{bar}</code>",
        ],
        icon="📊",
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


# ── /setting — stats, color cycle, channel, admin toggles ──
def setting_body(user_id: int) -> str:
    theme = get_theme(user_id)
    lines = [f"🎨 <b>Color:</b> {THEMES[theme]['emoji']} {THEMES[theme]['name']}"]
    if is_admin(user_id):
        mode = "ON ✅" if get_no_command_mode() else "OFF ❌"
        lines.append(f"🔗 <b>No-Command Mode (groups):</b> {mode}")
    return panel("SETTINGS", lines, icon="⚙️")


def setting_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    theme = get_theme(user_id)
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        tbtn(theme, "Stats", callback_data="menu_stats"),
        tbtn(theme, "Switch Color", callback_data="cycle_color"),
    )
    keyboard.add(tbtn(theme, "Channel", url=CHANNEL_LINK))
    if is_admin(user_id):
        mode_on = get_no_command_mode()
        keyboard.add(
            tbtn(theme, "Disable No-Command" if mode_on else "Enable No-Command", callback_data="toggle_nocmd")
        )
        keyboard.add(tbtn(theme, "Admin Panel", callback_data="admin_refresh"))
    return keyboard


@bot.message_handler(commands=["setting", "settings"])
def cmd_setting(message):
    register_user(message.from_user.id)
    bot.reply_to(
        message,
        brand(setting_body(message.from_user.id)),
        reply_markup=setting_keyboard(message.from_user.id),
    )


@bot.callback_query_handler(func=lambda call: call.data == "cycle_color")
def handle_cycle_color(call):
    user_id = call.from_user.id
    new_theme = next_theme(get_theme(user_id))
    set_theme(user_id, new_theme)
    bot.answer_callback_query(call.id, f"✅ Switched to {THEMES[new_theme]['name']}!")
    try:
        bot.edit_message_text(
            brand(setting_body(user_id)),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=setting_keyboard(user_id),
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data == "toggle_nocmd")
def handle_toggle_nocmd(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Admins only.", show_alert=True)
        return
    set_no_command_mode(not get_no_command_mode())
    bot.answer_callback_query(call.id, "✅ Updated!")
    user_id = call.from_user.id
    try:
        bot.edit_message_text(
            brand(setting_body(user_id)),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=setting_keyboard(user_id),
        )
    except Exception:
        pass


def build_result_body(url: str, result_link: str, user_id: int, elapsed: float, from_cache: bool) -> str:
    remaining = remaining_quota(user_id)
    quota_line = (
        "♾️ <b>Unlimited</b> (admin)"
        if remaining == -1
        else f"<b>{remaining}/{DAILY_LIMIT}</b> left today"
    )
    source_line = "📦 <b>Source:</b> Cache ⚡" if from_cache else "🌐 <b>Source:</b> Fresh"
    return panel(
        "BYPASS SUCCESSFUL 🎉",
        [
            "🔒 <b>Original:</b>",
            f'🌐 <a href="{url}">{url}</a>',
            "",
            "🔓 <b>Bypassed:</b>",
            f'🌐 <a href="{result_link}">{result_link}</a>',
            "",
            f"⏱️ <b>Response Time:</b> {elapsed:.2f}s",
            source_line,
            f"📊 <b>Quota:</b> {quota_line}",
        ],
        icon="✅",
    )


@bot.callback_query_handler(func=lambda call: call.data == "fj_check")
def handle_force_join_check(call):
    user_id = call.from_user.id
    unjoined = get_unjoined_channels(user_id)
    if unjoined:
        bot.answer_callback_query(call.id, "❌ You still haven't joined everything.", show_alert=True)
        try:
            bot.edit_message_text(
                brand(force_join_body(unjoined)),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=force_join_keyboard(unjoined),
            )
        except Exception:
            pass
        return
    bot.answer_callback_query(call.id, "✅ Verified! You're all set.")
    try:
        bot.edit_message_text(
            brand(panel("VERIFIED ✅", ["You've joined everything — send /bypass &lt;link&gt; again."], icon="🔓")),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass


def process_bypass(message, url: str):
    """Core bypass flow — used by /bypass command AND no-command auto-detect."""
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(
            message,
            brand(panel("You're Banned", ["Contact support if you think this is a mistake."], icon="⛔")),
        )
        return

    if not is_admin(user_id):
        unjoined = get_unjoined_channels(user_id)
        if unjoined:
            bot.reply_to(
                message,
                brand(force_join_body(unjoined)),
                reply_markup=force_join_keyboard(unjoined),
            )
            return

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
                panel(
                    "Daily Limit Reached",
                    [
                        f"You've used all <b>{DAILY_LIMIT}</b> bypasses for today.",
                        "Your quota resets at midnight — come back tomorrow! ⏰",
                    ],
                    icon="🚫",
                )
            ),
            reply_markup=unlock_keyboard,
        )
        return

    start_time = time.time()

    # ── Cache hit: reply almost instantly, skip the API + animation ──
    cached_link = cache_get(url)
    if cached_link:
        increment_stat("total_requests")
        increment_stat("success")
        if not is_admin(user_id):
            increment_usage(user_id)
        processing = bot.reply_to(
            message,
            panel("Fetching from cache...", ["<code>[██████████] 100%</code>"], icon="⚡"),
        )
        elapsed = time.time() - start_time
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        theme = get_theme(user_id)
        keyboard.add(tbtn(theme, "OPEN LINK", url=cached_link))
        bot.edit_message_text(
            brand(build_result_body(url, cached_link, user_id, elapsed, from_cache=True)),
            chat_id=processing.chat.id,
            message_id=processing.message_id,
            reply_markup=keyboard,
        )
        return

    increment_stat("total_requests")
    processing = bot.reply_to(
        message,
        panel("Bypassing...", ["<code>[░░░░░░░░░░] 0%</code>"], icon="⚡"),
    )
    loading_frames = [
        panel("Connecting...", ["<code>[███░░░░░░░] 30%</code>"], icon="⚡"),
        panel("Analyzing target...", ["<code>[██████░░░░] 60%</code>"], icon="🔎"),
        panel("Extracting link...", ["<code>[█████████░] 90%</code>"], icon="🔓"),
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
            brand(panel("Bypass Failed", [str(data)], icon="❌")),
            chat_id=processing.chat.id,
            message_id=processing.message_id,
        )
        return

    result_link = extract_result_link(data)
    if not result_link:
        increment_stat("failed")
        bot.edit_message_text(
            brand(
                panel(
                    "Extraction Failed",
                    ["⚠️ Couldn't find a direct link in the API response.", "Format may have changed."],
                    icon="❌",
                )
            ),
            chat_id=processing.chat.id,
            message_id=processing.message_id,
        )
        return

    increment_stat("success")
    if not is_admin(user_id):
        increment_usage(user_id)
    cache_set(url, result_link)
    elapsed = time.time() - start_time

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    theme = get_theme(user_id)
    keyboard.add(tbtn(theme, "OPEN LINK", url=result_link))
    bot.edit_message_text(
        brand(build_result_body(url, result_link, user_id, elapsed, from_cache=False)),
        chat_id=processing.chat.id,
        message_id=processing.message_id,
        reply_markup=keyboard,
    )


@bot.message_handler(commands=["bypass", "by"])
def cmd_bypass(message):
    register_user(message.from_user.id)
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            brand(panel("Missing Link", ["📌 Usage: <code>/bypass https://example.com/xyz</code>"], icon="⚠️")),
        )
        return

    url = parts[1].strip()
    if not is_valid_url(url):
        bot.reply_to(
            message,
            brand(panel("Invalid URL", ["🔸 Must start with <code>http://</code> or <code>https://</code>"], icon="❌")),
        )
        return

    process_bypass(message, url)


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
                panel(
                    "LIMIT REMOVED ✅",
                    [f"Daily quota reset — user can bypass <b>{DAILY_LIMIT}</b> more links today."],
                    icon="🔓",
                )
            ),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass


# ── Admin panel ─────────────────────────────────────────────
def admin_panel_body() -> str:
    stats = get_stats()
    return panel(
        "ADMIN CONTROL CENTER",
        [
            f"🔸 👥 Total Users: <b>{user_count()}</b>",
            f"🔸 🚫 Banned: <b>{banned_count()}</b>",
            f"🔸 📨 Requests: <b>{stats['total_requests']}</b>",
            f"🔸 ✅ Success: <b>{stats['success']}</b>  |  ❌ Failed: <b>{stats['failed']}</b>",
            f"🔸 📦 Cached Links: <b>{len(BYPASS_CACHE)}</b>",
            "",
            "Choose an action below 👇",
        ],
        icon="👑",
    )


def admin_panel_kb(user_id: int) -> types.InlineKeyboardMarkup:
    theme = get_theme(user_id)
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        tbtn(theme, "Stats", callback_data="admin_stats"),
        tbtn(theme, "Users", callback_data="admin_users"),
    )
    keyboard.add(
        tbtn(theme, "Global Broadcast", callback_data="admin_bc_start"),
    )
    keyboard.add(
        tbtn(theme, "Reset Limit", callback_data="admin_reset_limit"),
        tbtn(theme, "DM User", callback_data="admin_dm_user"),
    )
    keyboard.add(
        tbtn(theme, "Ban User", callback_data="admin_ban_user"),
        tbtn(theme, "Unban User", callback_data="admin_unban_user"),
    )
    keyboard.add(
        tbtn(theme, "Clear Cache", callback_data="admin_clear_cache"),
    )
    keyboard.add(
        tbtn(theme, "Force Join", callback_data="admin_fj_menu"),
    )
    keyboard.add(
        tbtn(theme, "Switch Color", callback_data="switch_color"),
        tbtn(theme, "Refresh", callback_data="admin_refresh"),
    )
    return keyboard


def fj_menu_body() -> str:
    channels = get_force_join_channels()
    if not channels:
        lines = ["No channels/groups added yet.", f"You can add up to <b>{MAX_FORCE_JOIN}</b>."]
    else:
        lines = [f"{i+1}. {ch['title']}" for i, ch in enumerate(channels)]
        lines.append("")
        lines.append(f"<b>{len(channels)}/{MAX_FORCE_JOIN}</b> slots used.")
    return panel("FORCE JOIN CHANNELS", lines, icon="🔐")


def fj_menu_kb(theme: str) -> types.InlineKeyboardMarkup:
    channels = get_force_join_channels()
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for i, ch in enumerate(channels):
        keyboard.add(SBtn(f"❌ Remove: {ch['title']}", style="danger", callback_data=f"admin_fj_remove_{i}"))
    if len(channels) < MAX_FORCE_JOIN:
        keyboard.add(tbtn(theme, "Add Channel/Group", callback_data="admin_fj_add"))
    keyboard.add(tbtn(theme, "Back to Admin Panel", callback_data="admin_refresh"))
    return keyboard


@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    register_user(message.from_user.id)
    if not is_admin(message.from_user.id):
        bot.reply_to(
            message,
            brand(panel("Access Denied", ["This panel is for admins only."], icon="⛔")),
        )
        return
    bot.reply_to(message, brand(admin_panel_body()), reply_markup=admin_panel_kb(message.from_user.id))


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
            brand(
                panel(
                    "USER COUNT",
                    [
                        f"🔸 Known users: <b>{user_count()}</b>",
                        f"🔸 Banned: <b>{banned_count()}</b>",
                        "",
                        f"🗄 DB: <code>{MONGODB_DATABASE}</code> @ <code>{_cluster_host}</code>",
                    ],
                    icon="👥",
                )
            ),
        )
    elif call.data == "admin_bc_start":
        bot.answer_callback_query(call.id)
        prompt = bot.send_message(
            call.message.chat.id,
            brand(
                panel(
                    "GLOBAL BROADCAST",
                    [
                        "Send the message you want to broadcast now",
                        "(text, photo, video, document — anything).",
                        "Send /cancel to abort.",
                    ],
                    icon="📢",
                )
            ),
            reply_markup=types.ForceReply(selective=False),
        )
        bot.register_next_step_handler(prompt, step_broadcast_content)
    elif call.data == "admin_reset_limit":
        bot.answer_callback_query(call.id)
        prompt = bot.send_message(
            call.message.chat.id,
            brand(panel("RESET LIMIT", ["Send the user ID to reset their daily quota."], icon="🔓")),
            reply_markup=types.ForceReply(selective=False),
        )
        bot.register_next_step_handler(prompt, step_reset_limit)
    elif call.data == "admin_dm_user":
        bot.answer_callback_query(call.id)
        prompt = bot.send_message(
            call.message.chat.id,
            brand(panel("DM USER", ["Send in format: <code>user_id message text</code>"], icon="💬")),
            reply_markup=types.ForceReply(selective=False),
        )
        bot.register_next_step_handler(prompt, step_dm_user)
    elif call.data == "admin_ban_user":
        bot.answer_callback_query(call.id)
        prompt = bot.send_message(
            call.message.chat.id,
            brand(panel("BAN USER", ["Send the user ID to ban."], icon="🔨")),
            reply_markup=types.ForceReply(selective=False),
        )
        bot.register_next_step_handler(prompt, step_ban_user)
    elif call.data == "admin_unban_user":
        bot.answer_callback_query(call.id)
        prompt = bot.send_message(
            call.message.chat.id,
            brand(panel("UNBAN USER", ["Send the user ID to unban."], icon="♻️")),
            reply_markup=types.ForceReply(selective=False),
        )
        bot.register_next_step_handler(prompt, step_unban_user)
    elif call.data == "admin_fj_menu":
        bot.answer_callback_query(call.id)
        theme = get_theme(call.from_user.id)
        try:
            bot.edit_message_text(
                brand(fj_menu_body()),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=fj_menu_kb(theme),
            )
        except Exception:
            bot.send_message(call.message.chat.id, brand(fj_menu_body()), reply_markup=fj_menu_kb(theme))
    elif call.data == "admin_fj_add":
        bot.answer_callback_query(call.id)
        prompt = bot.send_message(
            call.message.chat.id,
            brand(
                panel(
                    "ADD CHANNEL / GROUP",
                    [
                        "Send in this format (pipe-separated):",
                        "<code>chat_id_or_@username | Title | join_link</code>",
                        "",
                        "Example:",
                        "<code>@shuvo_channel | SHUVO Channel | https://t.me/shuvo_channel</code>",
                        "",
                        "⚠️ Bot must be an <b>admin</b> in that channel/group to verify membership.",
                        "Send /cancel to abort.",
                    ],
                    icon="➕",
                )
            ),
            reply_markup=types.ForceReply(selective=False),
        )
        bot.register_next_step_handler(prompt, step_fj_add)
    elif call.data.startswith("admin_fj_remove_"):
        index = int(call.data.rsplit("_", 1)[1])
        removed = remove_force_join_channel(index)
        bot.answer_callback_query(call.id, "✅ Removed!" if removed else "❌ Not found.")
        theme = get_theme(call.from_user.id)
        try:
            bot.edit_message_text(
                brand(fj_menu_body()),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=fj_menu_kb(theme),
            )
        except Exception:
            pass
    elif call.data == "admin_clear_cache":
        count = len(BYPASS_CACHE)
        cache_clear()
        bot.answer_callback_query(call.id, "🗑 Cache cleared!")
        bot.send_message(
            call.message.chat.id,
            brand(panel("CACHE CLEARED", [f"Removed <b>{count}</b> cached link(s)."], icon="🗑")),
        )
    elif call.data == "admin_refresh":
        bot.answer_callback_query(call.id, "Refreshed ✅")
        try:
            bot.edit_message_text(
                brand(admin_panel_body()),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=admin_panel_kb(call.from_user.id),
            )
        except Exception:
            pass


# ── Broadcast ───────────────────────────────────────────────
def run_broadcast(source_chat_id: int, source_message_id: int, status_chat_id: int):
    recipients = user_ids()
    sent, failed = 0, 0
    status = bot.send_message(
        status_chat_id,
        panel("Broadcasting...", [f"To <b>{len(recipients)}</b> users ⏳"], icon="📢"),
    )

    for uid in recipients:
        try:
            bot.copy_message(
                chat_id=uid,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            sent += 1
        except Exception as error:
            failed += 1
            log.warning("Broadcast failed for %s: %s", uid, error)
        time.sleep(0.05)

    bot.edit_message_text(
        brand(
            panel(
                "BROADCAST COMPLETE ✅",
                [
                    f"🔸 👥 Total users: <b>{len(recipients)}</b>",
                    f"🔸 ✅ Delivered: <b>{sent}</b>",
                    f"🔸 ❌ Failed: <b>{failed}</b>",
                ],
                icon="📢",
            )
        ),
        chat_id=status.chat.id,
        message_id=status.message_id,
    )


@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(
            message,
            brand(panel("Access Denied", ["You are not authorized to use this command."], icon="⛔")),
        )
        return

    if not message.reply_to_message:
        bot.reply_to(
            message,
            brand(
                panel(
                    "HOW TO BROADCAST",
                    [
                        "Reply to any message (text, photo, video, document,",
                        "sticker, voice — anything) with <code>/broadcast</code>,",
                        "or use 👑 Admin Panel → 📢 Global Broadcast.",
                    ],
                    icon="📢",
                )
            ),
        )
        return

    run_broadcast(message.chat.id, message.reply_to_message.message_id, message.chat.id)


# ── Admin panel wizards (triggered via buttons) ─────────────
def step_fj_add(message):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        bot.reply_to(message, brand(panel("Cancelled", ["No channel was added."], icon="❌")))
        return

    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3 or not all(parts):
        bot.reply_to(
            message,
            brand(
                panel(
                    "Invalid Format",
                    ["Use: <code>chat_id_or_@username | Title | join_link</code>"],
                    icon="❌",
                )
            ),
        )
        return

    chat_id, title, link = parts
    if len(get_force_join_channels()) >= MAX_FORCE_JOIN:
        bot.reply_to(
            message,
            brand(panel("Limit Reached", [f"Max {MAX_FORCE_JOIN} channels/groups allowed."], icon="🚫")),
        )
        return

    try:
        bot.get_chat(chat_id)
    except Exception as error:
        bot.reply_to(
            message,
            brand(
                panel(
                    "Couldn't Verify Channel",
                    [
                        f"Error: {error}",
                        "Make sure the bot is added as an admin there, then try again.",
                    ],
                    icon="⚠️",
                )
            ),
        )
        return

    add_force_join_channel(chat_id, title, link)
    bot.reply_to(
        message,
        brand(panel("CHANNEL ADDED ✅", [f"<b>{title}</b> is now required to use the bot."], icon="🔐")),
    )


def step_broadcast_content(message):
    if not is_admin(message.from_user.id):
        return
    if message.text and message.text.strip().lower() == "/cancel":
        bot.reply_to(message, brand(panel("Broadcast Cancelled", ["No message was sent."], icon="❌")))
        return
    run_broadcast(message.chat.id, message.message_id, message.chat.id)


def step_reset_limit(message):
    if not is_admin(message.from_user.id):
        return
    try:
        target_id = int(message.text.strip())
    except (ValueError, AttributeError):
        bot.reply_to(message, brand(panel("Invalid ID", ["Send a numeric Telegram user ID."], icon="❌")))
        return
    reset_usage(target_id)
    bot.reply_to(
        message,
        brand(
            panel(
                "LIMIT RESET",
                [f"User <code>{target_id}</code> can bypass {DAILY_LIMIT} more links today."],
                icon="🔓",
            )
        ),
    )


def step_dm_user(message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, brand(panel("Invalid Format", ["Use: <code>user_id message text</code>"], icon="❌")))
        return
    try:
        target_id = int(parts[0])
    except ValueError:
        bot.reply_to(message, brand(panel("Invalid ID", ["First word must be a numeric user ID."], icon="❌")))
        return
    try:
        bot.send_message(target_id, brand(panel("Message from Admin", [parts[1]], icon="💬")))
        bot.reply_to(message, brand(panel("Sent", [f"Delivered to <code>{target_id}</code>"], icon="✅")))
    except Exception as error:
        bot.reply_to(message, brand(panel("Failed to Send", [str(error)], icon="❌")))


def step_ban_user(message):
    if not is_admin(message.from_user.id):
        return
    try:
        target_id = int(message.text.strip())
    except (ValueError, AttributeError):
        bot.reply_to(message, brand(panel("Invalid ID", ["Send a numeric Telegram user ID."], icon="❌")))
        return
    ban_user(target_id)
    bot.reply_to(
        message,
        brand(panel("BANNED", [f"User <code>{target_id}</code> can no longer use the bot."], icon="🔨")),
    )


def step_unban_user(message):
    if not is_admin(message.from_user.id):
        return
    try:
        target_id = int(message.text.strip())
    except (ValueError, AttributeError):
        bot.reply_to(message, brand(panel("Invalid ID", ["Send a numeric Telegram user ID."], icon="❌")))
        return
    unban_user(target_id)
    bot.reply_to(
        message,
        brand(panel("UNBANNED", [f"User <code>{target_id}</code> can use the bot again."], icon="♻️")),
    )


# ── Auto-detect a raw URL (no command needed) ───────────────
# DM: always on. Groups/Channels: only when No-Command Mode is enabled via /setting.
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
def auto_handle_message(message):
    if not message.from_user:
        return  # anonymous channel posts have no from_user to attribute usage to
    register_user(message.from_user.id)

    text = (message.text or "").strip()
    if not text or " " in text or not is_valid_url(text):
        return  # not a bare, single URL — ignore silently (keeps spam down)

    is_private = message.chat.type == "private"
    if not is_private and not get_no_command_mode():
        return  # group/channel auto-bypass disabled — admin can enable it via /setting

    process_bypass(message, text)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("SHUVO Link Bypass Bot starting with MongoDB persistence...")
    bot.infinity_polling(skip_pending=True)
