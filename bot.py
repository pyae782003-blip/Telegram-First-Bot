import asyncio
import contextvars
import logging
import os
import re
import socket
import urllib.parse
from datetime import datetime
from typing import List, Set, Optional, Tuple, Dict, Any
import telegram
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

import database
from translations import t

def get_user_lang(user_id: int) -> str:
    """Helper to get user's selected language ('my' or 'en')."""
    return database.get_user_language(user_id)

# Ensure IPv4 resolution to prevent IPv6 routing latency/timeouts on Windows networks
_original_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_getaddrinfo

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def clean_bot_token(token: Optional[str]) -> str:
    """Cleans and sanitizes a Telegram bot token by stripping whitespace, newlines, tabs, and non-printable characters."""
    if not token:
        return ""
    s = str(token).strip().strip("'\"`")
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]", "", s)

# Bot Token (Configurable via TELEGRAM_BOT_TOKEN env var)
TOKEN = clean_bot_token(
    os.getenv("TELEGRAM_BOT_TOKEN", "8640565830:AAEpb_PCt75QqG6-jUNbtLZFc8yBAMWupkU")
)

# SaaS Super Admin IDs (The master SaaS provider / landlord who controls licensing & kill switch)
DEFAULT_SUPER_ADMINS: Set[int] = {1920334929}
SUPER_ADMIN_IDS: Set[int] = DEFAULT_SUPER_ADMINS | {
    int(x.strip())
    for x in os.getenv("SAAS_SUPER_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}


def is_super_admin(user_id: Optional[int]) -> bool:
    """Checks if a user is the Master SaaS Super Admin."""
    return bool(user_id and user_id in SUPER_ADMIN_IDS)


# Context variable for thread-safe/task-safe multi-tenant store context
current_store_id_ctx: contextvars.ContextVar[int] = contextvars.ContextVar("current_store_id_ctx", default=1)


def get_store_name(store_id: Optional[int] = None) -> str:
    """Dynamically returns the store name for the current store context or specified store_id."""
    if store_id is None:
        try:
            store_id = current_store_id_ctx.get()
        except Exception:
            store_id = 1
    return database.get_store_name(store_id=store_id)


def get_admin_username(store_id: Optional[int] = None) -> str:
    """Dynamically returns the admin Telegram username for the store."""
    if store_id is None:
        try:
            store_id = current_store_id_ctx.get()
        except Exception:
            store_id = 1
    custom_contact = database.get_store_setting(store_id, "admin_username", "")
    if custom_contact:
        return custom_contact.lstrip("@")
    store = database.get_store(store_id)
    if store and store.get("bot_username"):
        return store["bot_username"].lstrip("@")
    return database.get_admin_username()


def get_admin_url(store_id: Optional[int] = None) -> str:
    """Dynamically returns the admin Telegram URL for the store."""
    return f"https://t.me/{get_admin_username(store_id)}"


def get_admin_contact(store_id: Optional[int] = None) -> str:
    """Dynamically returns the markdown link to admin contact for the store."""
    return f"[Admin]({get_admin_url(store_id)})"


# Dynamic string wrappers for backwards compatibility across templates
class _DynamicStoreName(str):
    def __str__(self):
        return get_store_name()
    def __repr__(self):
        return get_store_name()
    def __format__(self, format_spec):
        return get_store_name().__format__(format_spec)


class _DynamicAdminContact(str):
    def __str__(self):
        return get_admin_contact()
    def __repr__(self):
        return get_admin_contact()
    def __format__(self, format_spec):
        return get_admin_contact().__format__(format_spec)


class _DynamicAdminUrl(str):
    def __str__(self):
        return get_admin_url()
    def __repr__(self):
        return get_admin_url()
    def __format__(self, format_spec):
        return get_admin_url().__format__(format_spec)


STORE_NAME = _DynamicStoreName("Store")
ADMIN_CONTACT = _DynamicAdminContact("[Admin](https://t.me/latt_swal2026)")
ADMIN_URL = _DynamicAdminUrl("https://t.me/latt_swal2026")

# Payment Accounts configuration defaults (Configurable via environment variables)
KPAY_PHONE = os.getenv("KPAY_PHONE", "09758511925")
KPAY_NAME = os.getenv("KPAY_NAME", "Pyae Phyo Kyaw")
WPAY_PHONE = os.getenv("WPAY_PHONE", "09779737113")
WPAY_NAME = os.getenv("WPAY_NAME", "Sandar Kyaw")
PROMPTPAY_PHONE = os.getenv("PROMPTPAY_PHONE", "09758511925")
PROMPTPAY_NAME = os.getenv("PROMPTPAY_NAME", "Pyae Phyo Kyaw")
THAI_BANK_INFO = os.getenv("THAI_BANK_INFO", "Kasikorn Bank (KBank) - 123-4-56789-0 (Pyae Phyo Kyaw)")

# Pre-configured Admin IDs
DEFAULT_ADMINS: Set[int] = DEFAULT_SUPER_ADMINS
ENV_ADMIN_IDS: Set[int] = DEFAULT_ADMINS | {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}


def get_all_admins() -> Set[int]:
    """Returns combined set of admins from environment and database."""
    admins = set(ENV_ADMIN_IDS)
    try:
        admins.update(database.get_all_admin_ids())
    except Exception as e:
        logger.warning(f"Error fetching admin IDs from DB - {e}")
    return admins


def is_admin_user(user_id: int) -> bool:
    """Checks if a user is an authorized admin."""
    return user_id in get_all_admins() or is_super_admin(user_id)


def is_admin_access_allowed(user_id: Optional[int], store_id: Optional[int] = None) -> bool:
    """
    Checks if a user is an admin AND their store license is active.
    Super Admins (1920334929) always have access.
    For store admins, if their SaaS subscription has expired or is suspended,
    returns False so Admin Panel buttons are hidden and access is restricted until renewed.
    """
    if not user_id:
        return False
    if is_super_admin(user_id):
        return True
    if not is_admin_user(user_id):
        return False

    s_id = store_id
    if s_id is None or s_id == 1:
        store = database.get_store_by_owner(user_id)
        if store:
            s_id = store.get("id", 1)
        else:
            s_id = 1

    if s_id != 1:
        lic = database.get_store_license(s_id)
        if not lic["is_valid"]:
            return False

    return True


def get_admin_active_store(user_id: Optional[int]) -> Dict[str, Any]:
    """Returns the store owned by the admin, or Store #1 for Super Admin or fallback."""
    if user_id:
        store = database.get_store_by_owner(user_id)
        if store:
            current_store_id_ctx.set(store["id"])
            return store
    s1 = database.get_store(1)
    s1_res = s1 or {"id": 1, "store_name": "Digital Store", "owner_id": 1920334929}
    current_store_id_ctx.set(s1_res.get("id", 1))
    return s1_res


def get_effective_store_id(update: Optional[Update] = None, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> int:
    """Returns the effective store ID for the current context or update and updates current_store_id_ctx."""
    store_id: Optional[int] = None
    if context and hasattr(context, "bot_data") and "store_id" in context.bot_data:
        store_id = int(context.bot_data["store_id"])

    if store_id is None and context and hasattr(context, "bot") and context.bot:
        try:
            bot_uname = getattr(context.bot, "username", None)
            if bot_uname:
                s = database.get_store_by_bot_username(bot_uname)
                if s:
                    store_id = int(s["id"])
            if store_id is None and hasattr(context.bot, "token"):
                s = database.get_store_by_token(context.bot.token)
                if s:
                    store_id = int(s["id"])
        except Exception:
            pass

    if store_id is None:
        user_id = update.effective_user.id if (update and update.effective_user) else None
        if user_id:
            store = database.get_store_by_owner(user_id)
            if store:
                store_id = int(store["id"])
            else:
                user_store = database.get_user_store_id(user_id)
                if user_store:
                    store_id = int(user_store)

    if store_id is None:
        store_id = 1

    current_store_id_ctx.set(store_id)
    return store_id


def check_license_access(user_id: Optional[int], store_id: int = 1) -> Tuple[bool, str, Optional[InlineKeyboardMarkup]]:
    """
    Checks if the store instance license is active.
    Super Admins always have access.
    Returns:
        (is_allowed, lock_message, reply_markup)
    """
    if is_super_admin(user_id):
        return True, "", None

    lic = database.get_store_license(store_id)
    if lic["is_valid"]:
        return True, "", None

    store_name = lic.get("store_name") or get_store_name()
    status_label = "သက်တမ်းကုန်ဆုံးသွားပါပြီ (Expired)"
    if lic["status"] == "suspended":
        status_label = "ဝန်ဆောင်မှု ခေတ္တပိတ်ထားပါသည် (Suspended by Super Admin)"

    lock_message = (
        "🔒 *[ဆိုင်ဝန်ဆောင်မှု ခေတ္တရပ်ဆိုင်းထားပါသည်]*\n"
        "*(Store Operations Suspended / License Expired)*\n\n"
        "⚠️ ဤ Telegram Bot ၏ လစဉ်အသုံးပြုခ သက်တမ်းကုန်ဆုံးသွားပြီ ဖြစ်သောကြောင့် ဝန်ဆောင်မှုများကို ခေတ္တရပ်နားထားပါသည်။\n\n"
        f"🏪 *ဆိုင်အမည် -* {store_name}\n"
        f"📅 *သက်တမ်းကုန်ဆုံးရက် -* `{lic['expiry_str']}`\n"
        f"📌 *အခြေအနေ -* ❌ {status_label}\n\n"
        "📢 *ဆိုင်ပိုင်ရှင်နှင့် ဝယ်ယူသူများ သိရှိရန် -*\n"
        "စနစ်ပြန်လည်ဖွင့်လှစ် အသုံးပြုနိုင်ရန်အတွက် SaaS Bot Provider / Super Admin ထံ ဆက်သွယ်၍ လစဉ်ကြေး ပေးသွင်းပြီး သက်တမ်းတိုးမြှင့်နိုင်ပါသည်။\n\n"
        "📞 *ဆက်သွယ်ရန် Super Admin -*\n"
        "👉 [Peter (SaaS Super Admin)](https://t.me/latt_swal2026)"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Super Admin ထံ ဆက်သွယ်ရန်", url="https://t.me/latt_swal2026")],
        [InlineKeyboardButton("🔄 Refresh စစ်ဆေးမည်", callback_data="check_license_again")],
    ])

    return False, lock_message, markup


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, message: str, store_id: Optional[int] = None) -> None:
    """Broadcasts a notification message to appropriate admins for the store."""
    admins_to_notify: Set[int] = set()
    if store_id is not None and store_id != 1:
        store = database.get_store(store_id)
        if store and store.get("owner_id"):
            admins_to_notify.add(int(store["owner_id"]))
        admins_to_notify.update(SUPER_ADMIN_IDS)
    else:
        admins_to_notify = get_all_admins()

    for admin_id in admins_to_notify:
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=message, parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send admin notification to {admin_id} - {e}")


async def safe_edit_message(
    query: telegram.CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = "Markdown",
) -> None:
    """Safely edits a query message, handling 'Message is not modified', photo messages, and markdown fallback."""
    try:
        if query.message and query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.chat.send_message(
                text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
            return

        await query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode=parse_mode
        )
    except telegram.error.BadRequest as e:
        err_msg = str(e).lower()
        if "message is not modified" in err_msg:
            return
        elif "can't parse entities" in err_msg:
            logger.warning(f"Markdown parse error, falling back to clean text - {e}")
            try:
                clean_text = text.replace("*", "").replace("`", "").replace("_", "")
                await query.edit_message_text(
                    text=clean_text, reply_markup=reply_markup, parse_mode=None
                )
            except Exception as e2:
                logger.error(f"Fallback plain text edit failed - {e2}")
        elif "there is no text in the message to edit" in err_msg or "message to edit not found" in err_msg:
            try:
                if query.message:
                    await query.message.delete()
            except Exception:
                pass
            if query.message and query.message.chat:
                await query.message.chat.send_message(
                    text=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
        else:
            logger.error(f"BadRequest in safe_edit_message - {e}")
            try:
                if query.message and query.message.chat:
                    await query.message.chat.send_message(
                        text=text, reply_markup=reply_markup, parse_mode=parse_mode
                    )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Unexpected error in safe_edit_message - {e}")
        try:
            if query.message and query.message.chat:
                await query.message.chat.send_message(
                    text=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
        except Exception:
            pass


def clean_single_icon_name(name: str) -> str:
    """Ensures each item has only one clean icon without duplicate generic icons."""
    name = name.strip()
    # If starts with double icons like '📦 🎬', remove the outer '📦 '
    if name.startswith("📦 ") and len(name) > 3 and name[2] != " ":
        name = name[2:].strip()
    return name


def get_main_menu_keyboard(is_admin: bool = False, lang: str = "my") -> ReplyKeyboardMarkup:
    """Builds the persistent bottom reply keyboard with strictly only the 4 core buttons."""
    buttons = [
        [KeyboardButton(t("btn_products", lang)), KeyboardButton(t("btn_topup", lang))],
        [KeyboardButton(t("btn_status", lang)), KeyboardButton(t("btn_support", lang))],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_main_inline_keyboard(user_id: int, lang: str = "my", is_admin: bool = False, store_id: int = 1) -> InlineKeyboardMarkup:
    """Builds neat inline keyboard for the main store dashboard including SaaS Bot purchase flow."""
    keyboard = [
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
            InlineKeyboardButton(t("btn_topup", lang), callback_data="nav_topup"),
        ],
        [
            InlineKeyboardButton(t("btn_referral", lang), callback_data="nav_referral"),
            InlineKeyboardButton(t("btn_history", lang), callback_data="nav_history"),
        ],
        [
            InlineKeyboardButton(t("btn_status", lang), callback_data="nav_status"),
            InlineKeyboardButton(t("btn_guide", lang), callback_data="nav_guide"),
        ],
        [
            InlineKeyboardButton(t("btn_language", lang), callback_data="nav_language"),
            InlineKeyboardButton(t("btn_support", lang), callback_data="nav_support"),
        ],
        [
            InlineKeyboardButton("🤖 ကိုယ့်ပိုင်လုပ်ငန်းအတွက် Bot ဝယ်ယူအသုံးပြုရန်", callback_data="saas_buy_bot"),
        ],
    ]
    if is_admin and is_admin_access_allowed(user_id, store_id=store_id):
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data="admin_home")])
    return InlineKeyboardMarkup(keyboard)


def get_catalog_keyboard(lang: str = "my", store_id: int = 1) -> InlineKeyboardMarkup:
    """Builds clean inline keyboard list for available products for a specific store."""
    products = database.get_products_with_stock(store_id=store_id)
    keyboard = []
    for prod in products:
        stock = prod["stock_count"]
        stock_badge = f"({stock} ကျန်)" if lang == "my" else f"({stock} left)"
        if stock == 0:
            stock_badge = "(ကုန်ပြီ)" if lang == "my" else "(Sold Out)"
        clean_name = clean_single_icon_name(prod["name"])
        # Format single icon and clean price display
        btn_text = f"{clean_name} • {prod['price']:,} K {stock_badge}"
        keyboard.append(
            [InlineKeyboardButton(btn_text, callback_data=f"prod_{prod['id']}")]
        )

    keyboard.append(
        [
            InlineKeyboardButton(t("btn_topup", lang), callback_data="nav_topup"),
            InlineKeyboardButton(t("btn_refresh", lang), callback_data="refresh_catalog"),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def build_admin_panel_text(user_id: Optional[int] = None) -> str:
    """Builds clean, comprehensive main Admin Control Panel message text scoped to the active store."""
    active_store = get_admin_active_store(user_id)
    store_id = active_store.get("id", 1)
    store_name = database.get_store_name(store_id=store_id)

    stats = database.get_store_stats(store_id=store_id)
    rate = database.get_exchange_rate(store_id=store_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lic = database.get_store_license(store_id=store_id)
    status_badge = "Active ✅" if lic["is_valid"] else f"Suspended/Expired ❌ ({lic['status']})"

    super_section = ""
    if is_super_admin(user_id):
        super_section = (
            "\n👑 *Super Admin Master Control -*\n"
            "• `/stores` - Multi-tenant စတိုးဆိုင်များ အားလုံး စာရင်း\n"
            "• `/editbalance <user_id> <amount>` - မည်သည့် Admin/User မဆို Balance ပြင်ဆင်ရန်\n"
            "• `/removebalance <user_id>` - မည်သည့် Admin/User မဆို Balance ဖျက်ရန်\n"
            "• `/deductbalance <user_id> <amount>` - Balance နှုတ်ယူရန်\n"
            "• `/linkbot <store_id> <token>` - Bot Token ချိတ်ဆက်ရန်\n"
            "• `/extendstore <store_id> <days>` - ဆိုင်လိုင်စင် သက်တမ်းတိုးရန်\n"
            "• `/suspendstore <store_id>` - ဆိုင်ပိတ်သိမ်းရန် (Kill Switch)\n"
            "• `/activatestore <store_id>` - ဆိုင်ပြန်ဖွင့်ရန် (Activate)\n"
            "• `/storeinfo <store_id>` - ဆိုင်အသေးစိတ် စစ်ဆေးရန်\n"
        )

    return (
        f"👑 *{store_name} Admin Control Panel*\n"
        f"🆔 *Store ID -* `#{store_id}`\n\n"
        "📊 *ဆိုင်အခြေအနေ အကျဉ်းချုပ် -*\n"
        f"• 👥 အသုံးပြုသူ - `{stats['total_users']} ယောက်`\n"
        f"• 🛒 ပြီးမြောက်ခဲ့သော အော်ဒါ - `{stats['total_orders']} ခု`\n"
        f"• 💰 စုစုပေါင်း အရောင်းရငွေ - `{stats['total_revenue']:,} MMK`\n"
        f"• 📦 ပစ္စည်းအမျိုးအစား - `{stats['total_products']} မျိုး`\n"
        f"• 🔑 ရောင်းရန်ကျန်ရှိသော Stock - `{stats['total_stock']} ခု`\n"
        f"• 💱 လက်ရှိ THB ငွေလဲနှုန်း - `1 THB = {rate:,} MMK`\n"
        f"• 🔐 SaaS License - `{lic['expiry_str']}` ({lic['days_remaining']} ရက်ကျန်) [{status_badge}]\n\n"
        f"🕒 *အချိန် (Live Update) -* `{now_str}`\n\n"
        "🛠️ *Quick Commands -*\n"
        "• `/setstorename <name>` - ဆိုင်အမည် ပြောင်းလဲရန်\n"
        "• `/setcontact <username>` - Admin Contact ပြောင်းရန်\n"
        "• `/license` - SaaS လိုင်စင် အချက်အလက် ကြည့်ရန်\n"
        "• `/setrate <rate>` - THB ငွေလဲနှုန်းပြင်ရန် (ဥပမာ `/setrate 138`)\n"
        "• `/editstock <id> <new_mail_pass>` - Stock Mail/Pass ပြင်ရန်\n"
        "• `/deletestock <id>` - Stock ဖျက်ရန်\n"
        "• `/editprice <id> <new_price>` - စျေးနှုန်းပြင်ရန်\n"
        "• `/editname <id> <new_name>` - အမည်ပြင်ရန်\n"
        "• `/deleteproduct <id>` - Product ဖျက်ရန်\n"
        "• `/addstock <id> <data>` - Stock ထည့်ရန်\n"
        "• `/editbalance <user_id> <new_bal>` - User Balance ပြင်ဆင်ရန်\n"
        "• `/removebalance <user_id>` - User Balance ဖျက်ရန် (0 K သို့)\n"
        "• `/deductbalance <user_id> <amount>` - User Balance နှုတ်ယူရန်\n"
        "• `/addbalance <user_id> <amount>` - User ထံ ငွေဖြည့်ရန်\n"
        f"{super_section}"
    )


def get_admin_panel_markup(is_super: bool = False) -> InlineKeyboardMarkup:
    """Builds clean Admin Panel dashboard inline keyboard with easy interactive product & stock actions."""
    keyboard = [
        [
            InlineKeyboardButton("➕ Product အသစ်ထည့်မည်", callback_data="admin_interactive_add_product"),
            InlineKeyboardButton("📥 Stock အသစ်ထည့်မည်", callback_data="admin_interactive_add_stock"),
        ],
        [
            InlineKeyboardButton("📊 စာရင်းဇယား (Stats)", callback_data="admin_stats"),
            InlineKeyboardButton("📦 Stock အခြေအနေ", callback_data="admin_stocks"),
        ],
        [
            InlineKeyboardButton("🔑 Stock အားလုံး ပြင်/ကြည့်မည် (Mail & Pass)", callback_data="admin_view_all_credentials"),
        ],
        [
            InlineKeyboardButton("⚙️ Product စီမံရန် (Edit & Delete)", callback_data="admin_manage_prods"),
            InlineKeyboardButton("💱 THB ငွေလဲနှုန်း ပြင်မည်", callback_data="admin_rate_menu"),
        ],
        [
            InlineKeyboardButton("💳 ငွေဖြည့်နည်း & QR စီမံမည်", callback_data="admin_payment_settings"),
            InlineKeyboardButton("👥 အသုံးပြုသူများ", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("💰 Balance စီမံ/ဖျက်မည်", callback_data="admin_bal_panel"),
            InlineKeyboardButton("🔐 SaaS License", callback_data="admin_license_info"),
        ],
    ]
    if is_super:
        keyboard.append([
            InlineKeyboardButton("🏢 Multi-tenant ဆိုင်များ စီမံရန် (/stores)", callback_data="super_admin_stores")
        ])
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_refresh"),
        InlineKeyboardButton("🔙 ထွက်မည် (Close)", callback_data="admin_close"),
    ])
    return InlineKeyboardMarkup(keyboard)


def get_admin_back_markup() -> InlineKeyboardMarkup:
    """Builds clean back button markup for Admin Panel sub-views."""
    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh"),
            InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def render_admin_payment_settings_view(
    target: Any, store_id: int
) -> None:
    """Renders the top-up text, payment accounts, and QR customization management view for a store admin."""
    s_name = database.get_store_name(store_id=store_id)
    topup_text = database.get_store_topup_instructions(store_id=store_id)
    qr_file = database.get_store_qr_file(store_id=store_id)
    rate = database.get_exchange_rate(store_id=store_id)

    kpay_phone = database.get_store_setting(store_id, "kpay_phone", KPAY_PHONE if store_id == 1 else "မသတ်မှတ်ရသေးပါ")
    kpay_name = database.get_store_setting(store_id, "kpay_name", KPAY_NAME if store_id == 1 else "")
    wpay_phone = database.get_store_setting(store_id, "wpay_phone", WPAY_PHONE if store_id == 1 else "မသတ်မှတ်ရသေးပါ")
    wpay_name = database.get_store_setting(store_id, "wpay_name", WPAY_NAME if store_id == 1 else "")
    promptpay_phone = database.get_store_setting(store_id, "promptpay_phone", "09758511925" if store_id == 1 else "မသတ်မှတ်ရသေးပါ")
    promptpay_name = database.get_store_setting(store_id, "promptpay_name", "Pyae Phyo Kyaw" if store_id == 1 else "")
    thai_bank = database.get_store_setting(store_id, "thai_bank_info", "Kasikorn Bank (KBank) - 123-4-56789-0 (Pyae Phyo Kyaw)" if store_id == 1 else "မသတ်မှတ်ရသေးပါ")

    if topup_text and topup_text.strip():
        topup_status = f"✅ *စိတ်ကြိုက်သတ်မှတ်ထားပါသည်* -\n_{topup_text[:140]}..._"
    else:
        topup_status = "⚪ *မူလ Standard Template သုံးထားပါသည်* (KPay, WavePay, PromptPay, Thai Bank စာရင်း)"

    if qr_file == "none":
        qr_status = "❌ *ဖယ်ရှားထားပါသည်* (Customer များထံ QR ခလုတ်နှင့် ပုံ မပြသပါ)"
    elif qr_file:
        qr_status = "✅ *သီးသန့် QR ပုံ တင်ထားပါသည်* (Customer များထံ ဤ QR ပုံကို ပြသပါမည်)"
    else:
        if store_id == 1:
            qr_status = "⚪ *မူလ Master Thai QR သုံးထားပါသည်* (thai_qr.jpg)"
        else:
            qr_status = "⚪ *QR ပုံ မတင်ရသေးပါ* (PromptPay & Bank စာသားသာ ပြသပါမည်)"

    msg_text = (
        f"💳 *[{s_name} - ငွေဖြည့်နည်း & QR စီမံခန့်ခွဲမှု]*\n"
        f"🆔 *Store ID -* `#{store_id}`\n\n"
        f"📝 *ငွေဖြည့်ညွှန်ကြားချက် စာသား (Top-up Text) -*\n"
        f"{topup_status}\n\n"
        f"🖼️ *ငွေလက်ခံ QR Code (PromptPay / SCB / KPay) -*\n"
        f"{qr_status}\n\n"
        f"🏦 *လက်ရှိ ငွေလက်ခံ အကောင့်များ -*\n"
        f"• KPay: `{kpay_phone}` ({kpay_name})\n"
        f"• WavePay: `{wpay_phone}` ({wpay_name})\n"
        f"• PromptPay: `{promptpay_phone}` ({promptpay_name})\n"
        f"• Thai Bank: `{thai_bank}`\n"
        f"• လက်ရှိ THB နှုန်း - `1 THB = {rate:,} MMK`\n\n"
        "👇 *ပြုလုပ်လိုသော ပြင်ဆင်မှုကို အောက်ပါခလုတ်များဖြင့် ရွေးချယ်ပါ -*"
    )

    buttons = [
        [
            InlineKeyboardButton("✏️ ငွေဖြည့်စာ ပြင်ဆင်မည်", callback_data=f"adm_pay_edit_text_{store_id}"),
            InlineKeyboardButton("🔄 မူလစာသို့ ပြန်ထားမည်", callback_data=f"adm_pay_reset_text_{store_id}"),
        ],
        [
            InlineKeyboardButton("📸 QR ပုံသစ် တင်မည်/ပြောင်းမည်", callback_data=f"adm_pay_upload_qr_{store_id}"),
            InlineKeyboardButton("🗑️ QR ပုံ ဖယ်ရှားမည်", callback_data=f"adm_pay_remove_qr_{store_id}"),
        ],
        [
            InlineKeyboardButton("🏦 ငွေလက်ခံ အကောင့်များ ပြင်မည်", callback_data=f"adm_pay_edit_acc_{store_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"adm_pay_settings_{store_id}"),
            InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
        ],
    ]
    await _send_or_edit(target, msg_text, reply_markup=InlineKeyboardMarkup(buttons))


def get_admin_rate_view(rate: int, store_name: Optional[str] = None) -> tuple[str, InlineKeyboardMarkup]:
    """Generates the exchange rate admin view text and interactive editing buttons."""
    if not store_name:
        store_name = get_store_name()
    rate_text = (
        f"👑 *[{store_name} Admin - THB ငွေလဲနှုန်း ပြင်ဆင်ရန်]*\n\n"
        f"💱 *လက်ရှိ ထိုင်းဘတ် (THB) ငွေလဲနှုန်း -*\n"
        f"👉 *`1 THB (ဘတ်) = {rate:,} MMK (ကျပ်)`*\n\n"
        f"• 100 THB = `{100 * rate:,} MMK`\n"
        f"• 300 THB = `{300 * rate:,} MMK`\n"
        f"• 500 THB = `{500 * rate:,} MMK`\n"
        f"• 1,000 THB = `{1000 * rate:,} MMK`\n\n"
        "👇 *ငွေလဲနှုန်း ပြင်ဆင်ရန် -*\n"
        "• '✏️ စိတ်ကြိုက်နှုန်းထား ရိုက်ထည့်မည်' နှိပ်၍ ဂဏန်း (ဥပမာ - 135 သို့မဟုတ် 136) တိုက်ရိုက် ရိုက်ပို့နိုင်ပါသည်\n"
        "• သို့မဟုတ် အောက်ပါ Quick Button များကို အသုံးပြုနိုင်ပါသည် -"
    )
    rate_keyboard = [
        [
            InlineKeyboardButton("✏️ စိတ်ကြိုက်နှုန်းထား ရိုက်ထည့်မည် (Edit Rate)", callback_data="admin_rate_input_prompt"),
        ],
        [
            InlineKeyboardButton("➖ 5 K", callback_data="admin_rate_dec5"),
            InlineKeyboardButton(f"⭐ {rate} K", callback_data="admin_rate_menu"),
            InlineKeyboardButton("➕ 5 K", callback_data="admin_rate_inc5"),
        ],
        [
            InlineKeyboardButton("130 K", callback_data="admin_rate_set_130"),
            InlineKeyboardButton("135 K", callback_data="admin_rate_set_135"),
            InlineKeyboardButton("140 K", callback_data="admin_rate_set_140"),
            InlineKeyboardButton("145 K", callback_data="admin_rate_set_145"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_rate_menu"),
            InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
        ],
    ]
    return rate_text, InlineKeyboardMarkup(rate_keyboard)


# ==============================================================================
# User Command & Menu Handlers
# ==============================================================================

async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the main store dashboard with neat inline buttons."""
    user = update.effective_user
    if not user:
        return

    # SaaS License Check
    store_id = get_effective_store_id(update, context)
    allowed, lock_text, lock_markup = check_license_access(user.id, store_id=store_id)
    if not allowed:
        if update.message:
            await update.message.reply_text(lock_text, reply_markup=lock_markup, parse_mode="Markdown")
        elif update.callback_query:
            await safe_edit_message(update.callback_query, lock_text, reply_markup=lock_markup)
        return

    lang = get_user_lang(user.id)
    db_user = database.get_or_create_user(user.id, user.username, user.first_name, store_id=store_id)
    admin_access = is_admin_access_allowed(user.id, store_id=store_id)
    s_name = database.get_store_name(store_id=store_id)
    exchange_rate = database.get_exchange_rate(store_id=store_id)
    referral_earnings = db_user.get("referral_earnings", 0)

    admin_tag = ""
    if admin_access:
        admin_tag = "\n👑 *Admin အဆင့် သတ်မှတ်ထားပါသည်!* (/admin ဖြင့် ဝင်ရောက်နိုင်ပါသည်)\n" if lang == "my" else "\n👑 *Super Admin Account!* (Access via /admin)\n"

    welcome_msg = t(
        "welcome_message",
        lang,
        store_name=s_name,
        name=user.first_name or "Valued Customer",
        user_id=user.id,
        balance=db_user["balance"],
        referral_earnings=referral_earnings,
        exchange_rate=exchange_rate,
        admin_tag=admin_tag,
    )

    markup = get_main_inline_keyboard(user.id, lang=lang, is_admin=admin_access, store_id=store_id)

    if update.message:
        await update.message.reply_text(
            f"✨ *{s_name}*",
            reply_markup=get_main_menu_keyboard(is_admin=admin_access, lang=lang),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            welcome_msg,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    elif update.callback_query:
        await safe_edit_message(update.callback_query, welcome_msg, reply_markup=markup)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command, registers user, supports referral link, and displays main menu."""
    user = update.effective_user
    if not user:
        return

    # SaaS License Check
    store_id = get_effective_store_id(update, context)
    allowed, lock_text, lock_markup = check_license_access(user.id, store_id=store_id)
    if not allowed:
        if update.message:
            await update.message.reply_text(lock_text, reply_markup=lock_markup, parse_mode="Markdown")
        return

    referrer_id = None
    if context.args:
        arg = context.args[0].strip()
        if arg.startswith("ref_") and arg[4:].isdigit():
            possible_ref = int(arg[4:])
            if possible_ref != user.id:
                referrer_id = possible_ref

    existing_user = database.get_user(user.id)
    db_user = database.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        referrer_id=referrer_id,
        store_id=store_id,
    )

    # Notify referrer if a new user just registered via referral link
    if not existing_user and referrer_id:
        try:
            ref_lang = get_user_lang(referrer_id)
            if ref_lang == "en":
                notif_text = f"🎉 *[New Referral!]*\n\n*{user.first_name}* has joined using your referral link!"
            else:
                notif_text = f"🎉 *[မိတ်ဆက်ဆုလာဘ် အသိပေးချက်]*\n\n*{user.first_name}* သည် သင့်မိတ်ဆက် Link မှတစ်ဆင့် Bot ကို စတင်အသုံးပြုခဲ့ပါသည်!"
            await context.bot.send_message(chat_id=referrer_id, text=notif_text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Failed to notify referrer {referrer_id} - {e}")

    admin_access = is_admin_access_allowed(user.id, store_id=store_id)
    lang = db_user.get("language") or "my"
    s_name = database.get_store_name(store_id=store_id)
    exchange_rate = database.get_exchange_rate(store_id=store_id)
    referral_earnings = db_user.get("referral_earnings", 0)

    admin_tag = ""
    if admin_access:
        admin_tag = "\n👑 *Admin အဆင့် သတ်မှတ်ထားပါသည်!* (/admin ဖြင့် ဝင်ရောက်နိုင်ပါသည်)\n" if lang == "my" else "\n👑 *Super Admin Account!* (Access via /admin)\n"

    welcome_msg = t(
        "welcome_message",
        lang,
        store_name=s_name,
        name=user.first_name or "Valued Customer",
        user_id=user.id,
        balance=db_user["balance"],
        referral_earnings=referral_earnings,
        exchange_rate=exchange_rate,
        admin_tag=admin_tag,
    )

    if update.message:
        await update.message.reply_text(
            f"✨ *{s_name}*",
            reply_markup=get_main_menu_keyboard(is_admin=admin_access, lang=lang),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            welcome_msg,
            reply_markup=get_main_inline_keyboard(user.id, lang=lang, is_admin=admin_access, store_id=store_id),
            parse_mode="Markdown",
        )


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the product catalog with inline buttons scoped to the store."""
    user = update.effective_user
    lang = get_user_lang(user.id) if user else "my"
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    text = t("catalog_title", lang, store_name=s_name)
    markup = get_catalog_keyboard(lang=lang, store_id=store_id)

    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=markup)


async def show_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays top-up payment accounts (MMK & THB) and exchange rate scoped to effective store."""
    user = update.effective_user
    if not user:
        return

    lang = get_user_lang(user.id)
    db_user = database.get_user(user.id)
    balance = db_user["balance"] if db_user else 0
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    exchange_rate = database.get_exchange_rate(store_id=store_id)
    admin_contact_link = get_admin_contact(store_id=store_id)
    admin_contact_url = get_admin_url(store_id=store_id)

    custom_instructions = database.get_store_topup_instructions(store_id=store_id)
    qr_file = database.get_store_qr_file(store_id=store_id)

    kpay_phone = database.get_store_setting(store_id, "kpay_phone", KPAY_PHONE if store_id == 1 else "မသတ်မှတ်ရသေးပါ")
    kpay_name = database.get_store_setting(store_id, "kpay_name", KPAY_NAME if store_id == 1 else "")
    wpay_phone = database.get_store_setting(store_id, "wpay_phone", WPAY_PHONE if store_id == 1 else "မသတ်မှတ်ရသေးပါ")
    wpay_name = database.get_store_setting(store_id, "wpay_name", WPAY_NAME if store_id == 1 else "")
    thai_bank_info = database.get_store_setting(
        store_id,
        "thai_bank_info",
        "Kasikorn Bank (KBank) - 123-4-56789-0 (Pyae Phyo Kyaw)" if store_id == 1 else "PromptPay / Direct Bank Transfer",
    )
    promptpay_phone = database.get_store_setting(store_id, "promptpay_phone", "09758511925" if store_id == 1 else "")
    promptpay_name = database.get_store_setting(store_id, "promptpay_name", "Pyae Phyo Kyaw" if store_id == 1 else "")

    if custom_instructions and custom_instructions.strip():
        base_inst = custom_instructions.strip()
        if "PromptPay" not in base_inst and "SCB" not in base_inst:
            base_inst += (
                f"\n\n🇹🇭 *ထိုင်းဘတ်ငွေဖြင့် လွှဲရန် (THB Payments) -*\n"
                f"🏦 *Bank -* SCB (Siam Commercial Bank)\n"
                f"📱 *PromptPay -* `XXX-XXX-9883` / `{promptpay_phone}`\n"
                f"👤 *Name -* `{promptpay_name or 'Store Admin'}`"
            )
        text = (
            f"💳 *[{s_name} - ငွေဖြည့်ရန် အချက်အလက်များ]*\n\n"
            f"💰 *လက်ရှိ သင့်လက်ကျန်ငွေ -* `{balance:,} MMK`\n"
            f"💱 *လက်ရှိ THB ငွေလဲနှုန်း -* `1 THB = {exchange_rate:,} MMK`\n\n"
            f"{base_inst}\n\n"
            f"⚠️ *ငွေလွှဲပြီးပါက ပြေစာ (Slip) ဓာတ်ပုံကို ဤ Chat ထဲသို့ တိုက်ရိုက် ပို့ပေးပါ*\n"
            f"မရှင်းလင်းပါက {admin_contact_link} ထံ ဆက်သွယ်နိုင်ပါသည်။"
        )
    else:
        text = t(
            "topup_info",
            lang,
            store_name=s_name,
            balance=balance,
            exchange_rate=exchange_rate,
            calc_100=100 * exchange_rate,
            calc_300=300 * exchange_rate,
            calc_500=500 * exchange_rate,
            calc_1000=1000 * exchange_rate,
            kpay_phone=kpay_phone,
            kpay_name=kpay_name,
            wpay_phone=wpay_phone,
            wpay_name=wpay_name,
            thai_bank_info=thai_bank_info,
            promptpay_phone=promptpay_phone,
            promptpay_name=promptpay_name,
            admin_contact=admin_contact_link,
        )

    keyboard = []
    if qr_file != "none":
        keyboard.append([InlineKeyboardButton(t("btn_thai_qr", lang), callback_data="show_thai_qr")])

    keyboard.extend([
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
            InlineKeyboardButton(t("btn_history", lang), callback_data="nav_history"),
        ],
        [
            InlineKeyboardButton(t("btn_contact_admin", lang), url=admin_contact_url),
        ],
        [
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ],
    ])
    markup = InlineKeyboardMarkup(keyboard)

    custom_photo_id = qr_file if (qr_file and qr_file not in ("none", "removed")) else None
    thai_qr_file = os.path.join(os.path.dirname(__file__), "thai_qr.jpg")
    local_qr_path = thai_qr_file if (os.path.exists(thai_qr_file) and qr_file not in ("none", "removed")) else None
    has_qr_image = bool(custom_photo_id or local_qr_path)

    qr_caption = (
        f"🇹🇭 *[{s_name} - Thai QR PromptPay / SCB Bank]*\n\n"
        f"🏦 *Bank -* SCB (Siam Commercial Bank)\n"
        f"📱 *PromptPay ID -* `XXX-XXX-9883` / `{promptpay_phone}`\n"
        f"👤 *Account Name -* `{promptpay_name or 'Store Admin'}`\n"
        f"💱 *လက်ရှိ ငွေလဲနှုန်း -* `1 THB = {exchange_rate:,} MMK`\n\n"
        f"👉 မည်သည့် Thai Mobile Banking App (SCB, KBank, KTB, etc.) ဖြင့်မဆို Scan ဖတ်၍ တိုက်ရိုက် ငွေလွှဲနိုင်ပါသည်။\n"
        f"ငွေလွှဲပြီးပါက ပြေစာ (Slip) ဓာတ်ပုံကို ဤနေရာတွင် ပေးပို့ပေးပါ။"
    )

    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass

        photo_sent = False
        if has_qr_image:
            if custom_photo_id:
                try:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=custom_photo_id,
                        caption=qr_caption,
                        parse_mode="Markdown",
                    )
                    photo_sent = True
                except Exception as pe:
                    logger.warning(f"Failed to send custom QR photo for store #{store_id}: {pe}")

            if not photo_sent and local_qr_path and os.path.exists(local_qr_path):
                try:
                    with open(local_qr_path, "rb") as photo:
                        await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=photo,
                            caption=qr_caption,
                            parse_mode="Markdown",
                        )
                        photo_sent = True
                except Exception as pe:
                    logger.warning(f"Failed to send local QR photo for store #{store_id}: {pe}")

        if photo_sent:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=markup,
                parse_mode="Markdown",
            )
        else:
            await safe_edit_message(query, text, reply_markup=markup)
    elif update.message:
        photo_sent = False
        if has_qr_image:
            if custom_photo_id:
                try:
                    await update.message.reply_photo(
                        photo=custom_photo_id,
                        caption=qr_caption,
                        parse_mode="Markdown",
                    )
                    photo_sent = True
                except Exception as pe:
                    logger.warning(f"Failed to send custom QR photo for store #{store_id}: {pe}")

            if not photo_sent and local_qr_path and os.path.exists(local_qr_path):
                try:
                    with open(local_qr_path, "rb") as photo:
                        await update.message.reply_photo(
                            photo=photo,
                            caption=qr_caption,
                            parse_mode="Markdown",
                        )
                        photo_sent = True
                except Exception as pe:
                    logger.warning(f"Failed to send local QR photo for store #{store_id}: {pe}")

        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def show_thai_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the Thai QR PromptPay photo or custom store payment QR for payments."""
    user = update.effective_user
    if not user:
        return
    lang = get_user_lang(user.id)
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    rate = database.get_exchange_rate(store_id=store_id)
    admin_contact_link = get_admin_contact(store_id=store_id)
    admin_contact_url = get_admin_url(store_id=store_id)

    qr_file = database.get_store_qr_file(store_id=store_id)

    keyboard = [
        [
            InlineKeyboardButton(t("btn_contact_admin", lang), url=admin_contact_url),
        ],
        [
            InlineKeyboardButton("💳 ငွေဖြည့်နည်းများ" if lang == "my" else "💳 Payment Options", callback_data="nav_topup"),
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if qr_file in ("none", "removed"):
        no_qr_text = (
            f"ℹ️ *[{s_name}]*\n\n"
            f"ဤဆိုင်အတွက် QR Code သီးသန့် သတ်မှတ်မထားပါ။\n"
            f"ငွေလွှဲအကောင့် အသေးစိတ်အတွက် အောက်ပါ '💳 ငွေဖြည့်နည်းများ' ကို နှိပ်ပါ သို့မဟုတ် {admin_contact_link} ထံ ဆက်သွယ်ပါ။"
        )
        if update.callback_query:
            await safe_edit_message(update.callback_query, no_qr_text, reply_markup=markup)
        elif update.message:
            await update.message.reply_text(no_qr_text, reply_markup=markup, parse_mode="Markdown")
        return

    caption = t(
        "thai_qr_caption",
        lang,
        exchange_rate=rate,
        calc_100=100 * rate,
        calc_300=300 * rate,
        calc_500=500 * rate,
        calc_1000=1000 * rate,
        admin_contact=admin_contact_link,
    )

    custom_photo_id = qr_file if (qr_file and qr_file not in ("none", "removed")) else None
    thai_qr_file = os.path.join(os.path.dirname(__file__), "thai_qr.jpg")
    local_qr_path = thai_qr_file if os.path.exists(thai_qr_file) else None

    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass

        photo_sent = False
        if custom_photo_id:
            try:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=custom_photo_id,
                    caption=caption,
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
                photo_sent = True
            except Exception as pe:
                logger.warning(f"Failed to send custom QR photo in show_thai_qr: {pe}")

        if not photo_sent and local_qr_path and os.path.exists(local_qr_path):
            try:
                with open(local_qr_path, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=photo,
                        caption=caption,
                        reply_markup=markup,
                        parse_mode="Markdown",
                    )
                photo_sent = True
            except Exception as pe:
                logger.warning(f"Failed to send local QR photo in show_thai_qr: {pe}")

        if photo_sent:
            try:
                await query.message.delete()
            except Exception:
                pass
        else:
            await safe_edit_message(query, caption, reply_markup=markup)
    elif update.message:
        photo_sent = False
        if custom_photo_id:
            try:
                await update.message.reply_photo(
                    photo=custom_photo_id,
                    caption=caption,
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
                photo_sent = True
            except Exception as pe:
                logger.warning(f"Failed to send custom QR photo in show_thai_qr: {pe}")

        if not photo_sent and local_qr_path and os.path.exists(local_qr_path):
            try:
                with open(local_qr_path, "rb") as photo:
                    await update.message.reply_photo(
                        photo=photo,
                        caption=caption,
                        reply_markup=markup,
                        parse_mode="Markdown",
                    )
                photo_sent = True
            except Exception as pe:
                logger.warning(f"Failed to send local QR photo in show_thai_qr: {pe}")

        if not photo_sent:
            await update.message.reply_text(caption, reply_markup=markup, parse_mode="Markdown")


async def slip_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming payment slip screenshot photos sent by users and forwards them to admins."""
    user = update.effective_user
    if not user or not update.message or not update.message.photo:
        return

    photo_file_id = update.message.photo[-1].file_id
    user_caption = update.message.caption or ""
    lang = get_user_lang(user.id)

    # 0. Store Admin QR Code Image Upload Flow
    admin_state = str(context.user_data.get("admin_state", ""))
    if admin_state.startswith("waiting_store_qr_photo_"):
        store_id = int(admin_state.replace("waiting_store_qr_photo_", ""))
        context.user_data.pop("admin_state", None)
        database.set_store_qr_file(store_id, photo_file_id)
        s_name = database.get_store_name(store_id=store_id)
        ack = (
            f"✅ *[{s_name} - Payment QR ပုံ အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ]*\n\n"
            f"🖼️ Customer များ ငွေဖြည့် QR ကြည့်ရှုသည့်အခါ ဤ QR ပုံကို တိုက်ရိုက် ပြသပေးမည် ဖြစ်သည်။"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 ငွေဖြည့်နည်း & QR စီမံမှုသို့", callback_data=f"adm_pay_settings_{store_id}")],
            [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
        ])
        await update.message.reply_text(ack, reply_markup=markup, parse_mode="Markdown")
        return

    # 1. SaaS Store Rental / Application Slip Flow
    if context.user_data.get("saas_state") == "waiting_saas_slip":
        store_name = context.user_data.get("saas_store_name", f"{user.first_name}'s Store")
        plan = context.user_data.get("saas_plan", "Pro Business (25,000 MMK)")
        context.user_data.pop("saas_state", None)

        req_id = database.create_store_request(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            store_name=store_name,
            plan=plan,
            slip_file_id=photo_file_id,
        )

        super_caption = (
            f"👑 *[SaaS စတိုးဆိုင်အသစ် လျှောက်ထားလွှာ (New Store Request)]*\n\n"
            f"🆔 *Request ID -* `#{req_id}`\n"
            f"👤 *လျှောက်ထားသူ -* {user.first_name} (@{user.username or 'NoUsername'})\n"
            f"🔢 *User ID -* `{user.id}`\n"
            f"🏪 *ဆိုင်အမည် -* *{store_name}*\n"
            f"📦 *ရွေးချယ်သော Plan -* *{plan}*\n"
            f"📅 *ရက်စွဲ -* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n"
            "👇 စလစ်ကို စစ်ဆေး၍ အတည်ပြု/ငြင်းပယ်ရန် ရွေးချယ်ပါ -"
        )
        super_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ အတည်ပြုပြီး စတိုးဖွင့်ပေးမည် (30 ရက်)", callback_data=f"saas_appr_{req_id}"),
            ],
            [
                InlineKeyboardButton("❌ ငြင်းပယ်မည် (Reject)", callback_data=f"saas_rejc_{req_id}"),
            ],
        ])

        for sa_id in SUPER_ADMIN_IDS:
            try:
                await context.bot.send_photo(
                    chat_id=sa_id,
                    photo=photo_file_id,
                    caption=super_caption,
                    reply_markup=super_markup,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Failed to forward store request to super admin {sa_id} - {e}")

        ack_text = (
            f"🎉 *[စတိုးဖွင့် လျှောက်ထားမှု ပေးပို့ပြီးပါပြီ!]*\n\n"
            f"• Request ID - `#{req_id}`\n"
            f"• ဆိုင်အမည် - *{store_name}*\n"
            f"• Plan - *{plan}*\n\n"
            f"သင့်ငွေလွှဲပြေစာကို Super Admin ([Peter](https://t.me/latt_swal2026)) ထံသို့ တိုက်ရိုက် ပေးပို့လိုက်ပါပြီ။\n"
            f"Super Admin မှ စစ်ဆေးအတည်ပြုပြီးသည်နှင့် သင့်အကောင့်အား စတိုးဆိုင် Admin Access ဖွင့်လှစ်ပေးပြီး ချက်ချင်း အသိပေးစာ ပေးပို့ပါမည်။\n\n"
            f"မေးမြန်းလိုပါက Super Admin [Peter](https://t.me/latt_swal2026) ထံသို့ တိုက်ရိုက် ဆက်သွယ်နိုင်ပါသည်။"
        )
        await update.message.reply_text(ack_text, parse_mode="Markdown")
        return

    # 2. Standard Top-up Payment Slip Flow
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    rate = database.get_exchange_rate(store_id=store_id)
    thb_100_mmk = 100 * rate
    thb_300_mmk = 300 * rate
    thb_500_mmk = 500 * rate

    db_user = database.get_or_create_user(user.id, user.username, user.first_name, store_id=store_id)
    current_bal = db_user["balance"]
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build Admin Forward Message
    admin_caption = (
        f"💳 *[{s_name} - ငွေဖြည့်သွင်းမှု ပြေစာအသစ် / Top-up Slip]*\n\n"
        f"• ဝယ်ယူသူ - *{user.first_name}* (@{user.username or 'N/A'})\n"
        f"• User ID - `{user.id}`\n"
        f"• လက်ရှိ Balance - `{current_bal:,} MMK`\n"
        f"• လက်ရှိ THB Rate - `1 THB = {rate:,} MMK`\n"
        f"• အချိန် - {time_str}\n"
    )
    if user_caption:
        admin_caption += f"• User မှတ်ချက် - {user_caption}\n"

    admin_caption += "\n👇 စစ်ဆေးအတည်ပြုရန် ပမာဏရွေးချယ်ပါ သို့မဟုတ် ငြင်းပယ်ပါ -"

    # Inline approval buttons for Admins (with MMK presets & THB converted presets)
    keyboard = [
        [
            InlineKeyboardButton("✅ +5,000 K", callback_data=f"slip_app_{user.id}_5000"),
            InlineKeyboardButton("✅ +10,000 K", callback_data=f"slip_app_{user.id}_10000"),
            InlineKeyboardButton("✅ +20,000 K", callback_data=f"slip_app_{user.id}_20000"),
        ],
        [
            InlineKeyboardButton(f"✅ +100 THB ({thb_100_mmk:,} K)", callback_data=f"slip_app_{user.id}_{thb_100_mmk}"),
            InlineKeyboardButton(f"✅ +300 THB ({thb_300_mmk:,} K)", callback_data=f"slip_app_{user.id}_{thb_300_mmk}"),
            InlineKeyboardButton(f"✅ +500 THB ({thb_500_mmk:,} K)", callback_data=f"slip_app_{user.id}_{thb_500_mmk}"),
        ],
        [
            InlineKeyboardButton("✏️ တခြားပမာဏ ဖြည့်ရန်", callback_data=f"slip_custom_{user.id}"),
            InlineKeyboardButton("❌ Reject (ငြင်းပယ်မည်)", callback_data=f"slip_rej_{user.id}"),
        ],
    ]

    admins_to_notify: Set[int] = set()
    if store_id != 1:
        store = database.get_store(store_id)
        if store and store.get("owner_id"):
            admins_to_notify.add(int(store["owner_id"]))
        admins_to_notify.update(SUPER_ADMIN_IDS)
    else:
        admins_to_notify = get_all_admins()

    for admin_id in admins_to_notify:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=photo_file_id,
                caption=admin_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Failed to forward slip to admin {admin_id} - {e}")

    # Acknowledge to user in user's selected language
    ack_text = t("slip_received", lang)
    await update.message.reply_text(ack_text, parse_mode="Markdown")


async def show_member_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays user status, balance, language, and purchase history."""
    user = update.effective_user
    if not user:
        return

    lang = get_user_lang(user.id)
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    db_user = database.get_or_create_user(user.id, user.username, user.first_name, store_id=store_id)
    orders = database.get_user_orders(user.id, limit=5, store_id=store_id)

    orders_text = ""
    if orders:
        orders_header = "📜 *မကြာသေးမီက ဝယ်ယူခဲ့သော ပစ္စည်းများ -*\n" if lang == "my" else "📜 *Recent Purchases -*\n"
        orders_text = "\n" + orders_header
        for i, order in enumerate(orders, 1):
            date_str = str(order["purchased_at"])[:16]
            orders_text += (
                f"{i}. *{order['product_name']}*\n"
                f"   💰 `{order['price']:,} MMK` • 📅 {date_str}\n"
                f"   🔑 `{order['delivered_data']}`\n\n"
            )
    else:
        orders_text = "\n" + ("📜 *ဝယ်ယူမှုမှတ်တမ်း -* မရှိသေးပါ\n" if lang == "my" else "📜 *Purchase History -* None\n")

    lang_display = "🇲🇲 မြန်မာစာ" if lang == "my" else "🇬🇧 English"
    status_msg = t(
        "member_status",
        lang,
        store_name=s_name,
        user_id=user.id,
        name=user.first_name or "N/A",
        username=user.username if user.username else "N/A",
        balance=db_user["balance"],
        lang_name=lang_display,
        orders_section=orders_text,
    )

    keyboard = [
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
            InlineKeyboardButton(t("btn_topup", lang), callback_data="nav_topup"),
        ],
        [
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ],
    ]

    markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(status_msg, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, status_msg, reply_markup=markup)


async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays referral program details, user link, and stats."""
    user = update.effective_user
    if not user:
        return

    lang = get_user_lang(user.id)
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    bot_me = await context.bot.get_me()
    bot_username = bot_me.username or "pyae_test_bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    stats = database.get_referral_stats(user.id)
    total_referred = stats["total_referred"]
    referral_earnings = stats.get("referral_earnings", 0)

    text = t(
        "referral_info",
        lang,
        store_name=s_name,
        ref_link=ref_link,
        total_referred=total_referred,
        referral_earnings=referral_earnings,
    )

    share_text = urllib.parse.quote(f"{s_name} - Best Digital Accounts")
    share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"

    keyboard = [
        [
            InlineKeyboardButton("📤 Share Link", url=share_url),
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=markup)


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays user purchase / order history."""
    user = update.effective_user
    if not user:
        return

    lang = get_user_lang(user.id)
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    orders = database.get_user_orders(user.id, limit=10, store_id=store_id)

    if not orders:
        empty_text = (
            f"📜 *{s_name} - " + ("ဝယ်ယူမှုမှတ်တမ်း*" if lang == "my" else "Order History*") + "\n\n"
            + t("history_empty", lang)
        )
        keyboard = [
            [
                InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
                InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
            ]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        if update.message:
            await update.message.reply_text(empty_text, reply_markup=markup, parse_mode="Markdown")
        elif update.callback_query:
            await safe_edit_message(update.callback_query, empty_text, reply_markup=markup)
        return

    title = t("history_title", lang, store_name=s_name)
    lines = [title]
    for i, o in enumerate(orders, 1):
        dt = str(o["purchased_at"])[:16]
        lines.append(
            f"{i}. *{o['product_name']}*\n"
            f"   💰 `{o['price']:,} MMK` • 📅 `{dt}`\n"
            f"   🔑 `{o['delivered_data']}`\n"
        )

    full_text = "\n".join(lines)
    keyboard = [
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(full_text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, full_text, reply_markup=markup)


async def show_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays comprehensive user guide."""
    user = update.effective_user
    if not user:
        return

    lang = get_user_lang(user.id)
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    text = t("guide_info", lang, store_name=s_name)

    keyboard = [
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
            InlineKeyboardButton(t("btn_topup", lang), callback_data="nav_topup"),
        ],
        [
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=markup)


async def show_language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays language toggle menu (English & Myanmar)."""
    user = update.effective_user
    if not user:
        return

    lang = get_user_lang(user.id)
    text = t("language_menu", lang)

    keyboard = [
        [
            InlineKeyboardButton("🇲🇲 မြန်မာစာ (Myanmar)", callback_data="setlang_my"),
            InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
        ],
        [
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=markup)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays help and support instructions with clickable admin link and direct button."""
    user = update.effective_user
    lang = get_user_lang(user.id) if user else "my"
    store_id = get_effective_store_id(update, context)
    s_name = database.get_store_name(store_id=store_id)
    admin_contact_link = get_admin_contact(store_id=store_id)
    admin_contact_url = get_admin_url(store_id=store_id)
    help_text = t(
        "support_info",
        lang,
        store_name=s_name,
        admin_contact=admin_contact_link,
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_contact_admin", lang), url=admin_contact_url)],
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="nav_products"),
            InlineKeyboardButton(t("btn_back_home", lang), callback_data="nav_home"),
        ],
    ])

    if update.message:
        await update.message.reply_text(help_text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, help_text, reply_markup=markup, parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels current action, clears user session data, and returns to main menu."""
    if context.user_data:
        context.user_data.clear()
    user = update.effective_user
    lang = get_user_lang(user.id) if user else "my"
    msg = "❌ လက်ရှိလုပ်ဆောင်ချက်ကို ပယ်ဖျက်လိုက်ပါပြီ။" if lang == "my" else "❌ Current action cancelled."
    if update.message:
        await update.message.reply_text(msg)
    await show_home(update, context)


async def set_rate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to view/edit THB exchange rate: /setrate <rate>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။ (Unauthorized)")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not context.args or not context.args[0].isdigit():
        current_rate = database.get_exchange_rate(store_id=store_id)
        await update.message.reply_text(
            f"💱 *{active_store['store_name']} - လက်ရှိ THB ငွေလဲနှုန်း -* `1 THB = {current_rate:,} MMK`\n\n"
            "အသုံးပြုပုံ - `/setrate <ငွေလဲနှုန်း>`\n"
            "ဥပမာ - `/setrate 138`",
            parse_mode="Markdown",
        )
        return

    new_rate = int(context.args[0])
    if new_rate <= 0:
        await update.message.reply_text("❌ ငွေလဲနှုန်းသည် 0 ထက် ကြီးရပါမည်။")
        return

    database.set_exchange_rate(new_rate, store_id=store_id)
    await update.message.reply_text(
        f"✅ *THB ငွေလဲနှုန်း အောင်မြင်စွာ ပြောင်းလဲပြီးပါပြီ!*\n\n"
        f"🏪 *ဆိုင် -* {active_store['store_name']}\n"
        f"👉 *`1 THB = {new_rate:,} MMK`*\n\n"
        f"သင့်ဆိုင်ရှိ ငွေဖြည့်သွင်းသည့် Menu တွင် ချက်ချင်း ပြောင်းလဲသွားပါမည်။",
        parse_mode="Markdown",
    )


# ==============================================================================
# Admin Panel & Commands
# ==============================================================================

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the Admin Control Panel scoped to the active store."""
    user = update.effective_user
    if not user:
        return

    if not is_admin_user(user.id):
        if len(get_all_admins()) == 0:
            await update.message.reply_text(
                "⚠️ *လက်ရှိတွင် မည်သည့် Admin မှ မရှိသေးပါ။*\n"
                "သင်ကိုယ်တိုင် Super Admin အဖြစ် ရယူလိုပါက `/claimadmin` ဟု ရိုက်ပို့ပါ။",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text("⛔ *ခွင့်ပြုချက်မရှိပါ!* Admin များသာ ဝင်ရောက်နိုင်ပါသည်။", parse_mode="Markdown")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    # SaaS License Check for Store Admins (Super Admin is exempted)
    if not is_super_admin(user.id):
        lic = database.get_store_license(store_id)
        if not lic["is_valid"]:
            _, lock_text, lock_markup = check_license_access(user.id, store_id=store_id)
            if update.message:
                await update.message.reply_text(lock_text, reply_markup=lock_markup, parse_mode="Markdown")
            elif update.callback_query:
                await safe_edit_message(update.callback_query, lock_text, reply_markup=lock_markup)
            return

    text = build_admin_panel_text(user.id)
    markup = get_admin_panel_markup(is_super=is_super_admin(user.id))
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=markup)


# ==============================================================================
# SaaS Multi-tenant Store Customization & Licensing Commands
# ==============================================================================

async def set_store_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Changes the store name dynamically: /setstorename <new_name>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not context.args:
        current_name = database.get_store_name(store_id=store_id)
        await update.message.reply_text(
            f"🏪 *လက်ရှိ ဆိုင်အမည် -* `{current_name}` (Store #{store_id})\n\n"
            "အသုံးပြုပုံ - `/setstorename <ဆိုင်အမည် အသစ်>`\n"
            "ဥပမာ - `/setstorename Gaming Zone Burma`",
            parse_mode="Markdown",
        )
        return

    new_name = " ".join(context.args).strip()
    database.set_store_name(new_name, store_id=store_id)
    await update.message.reply_text(
        f"✅ *ဆိုင်အမည်ကို အောင်မြင်စွာ ပြောင်းလဲပြီးပါပြီ!*\n\n"
        f"🆔 *Store ID -* `#{store_id}`\n"
        f"👉 *ဆိုင်အမည်သစ် -* `{new_name}`\n\n"
        "သင့်ဆိုင်ရှိ စာသားများနှင့် Menu များတွင် ချက်ချင်း အလိုအလျောက် ပြောင်းလဲသွားပါမည်။ (အခြားဆိုင်များအပေါ် သက်ရောက်မှုမရှိပါ)",
        parse_mode="Markdown",
    )


async def set_contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Updates admin contact username: /setcontact <telegram_username>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not context.args:
        current_user = get_admin_username(store_id=store_id)
        await update.message.reply_text(
            f"👤 *လက်ရှိ Admin Username -* `@{current_user}` (Store #{store_id})\n\n"
            "အသုံးပြုပုံ - `/setcontact <Telegram Username>`\n"
            "ဥပမာ - `/setcontact my_shop_admin`",
            parse_mode="Markdown",
        )
        return

    new_contact = context.args[0].strip().lstrip("@")
    database.set_store_setting(store_id, "admin_username", new_contact)
    if store_id == 1:
        database.set_admin_username(new_contact)

    await update.message.reply_text(
        f"✅ *Admin Contact ကို အောင်မြင်စွာ ပြောင်းလဲပြီးပါပြီ!*\n\n"
        f"🆔 *Store ID -* `#{store_id}`\n"
        f"👉 *Admin Username -* `@{new_contact}`\n\n"
        f"User များ အကူအညီတောင်းရာတွင် `https://t.me/{new_contact}` သို့ ချိတ်ဆက်သွားပါမည်။",
        parse_mode="Markdown",
    )


async def _send_or_edit(
    target: Any,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = "Markdown",
) -> None:
    """Helper to send a new message or edit existing message depending on whether target is CallbackQuery or Update."""
    if isinstance(target, telegram.CallbackQuery):
        await safe_edit_message(target, text, reply_markup=reply_markup, parse_mode=parse_mode)
    elif isinstance(target, Update):
        if target.callback_query:
            await safe_edit_message(target.callback_query, text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif target.message:
            await target.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    elif hasattr(target, "edit_message_text"):
        await safe_edit_message(target, text, reply_markup=reply_markup, parse_mode=parse_mode)
    elif hasattr(target, "reply_text"):
        await target.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


def clean_bot_token(token: Optional[str]) -> str:
    """Cleans and sanitizes a Telegram bot token by stripping whitespace, newlines (\\n, \\r), tabs, and non-printable ASCII characters."""
    if not token:
        return ""
    s = str(token).strip().strip("'\"`")
    # Remove all whitespace characters (\\r, \\n, \\t, spaces) and non-printable control characters
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]", "", s)


async def validate_and_bind_bot_token(
    token: str,
    store_id: int,
    bot_manager: Any,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Validates Telegram bot token via getMe and launches client polling instance."""
    token = clean_bot_token(token)
    if not token or ":" not in token:
        return False, "❌ Bot Token ပုံစံ မမှန်ကန်ပါ။ (ဥပမာ - `7123456789:ABCdefGhIJKlmNo...`)", {}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
    except Exception as e:
        return False, f"❌ Telegram API နှင့် ချိတ်ဆက်မှု မအောင်မြင်ပါ -\n`{e}`", {}

    if not data.get("ok"):
        err_desc = data.get("description", "Invalid bot token")
        return False, f"❌ Bot Token မမှန်ကန်ပါ (BotFather မှ Token ကို ပြန်လည်စစ်ဆေးပါ) -\n`{err_desc}`", {}

    bot_res = data["result"]
    bot_username = "@" + bot_res.get("username", "")
    bot_first_name = bot_res.get("first_name", "")

    # Save to database
    database.update_store(store_id, bot_token=token, bot_username=bot_username)

    # Start instance
    started = await bot_manager.start_instance(store_id, token)

    return True, "Success", {
        "bot_username": bot_username,
        "bot_first_name": bot_first_name,
        "started": started,
    }


async def render_super_admin_stores_view(target: Any, user_id: int) -> None:
    """Super Admin dedicated view: lists all client stores, assigned bot tokens, expiry dates, and management controls."""
    stores = database.get_all_stores()
    lines = [
        "🏢 *[SaaS Multi-tenant Client စတိုးဆိုင်များ စီမံခန့်ခွဲရေး Panel]*\n",
        f"👑 *Super Admin -* [Peter](https://t.me/latt_swal2026)",
        f"📊 *စုစုပေါင်း ဆိုင်အရေအတွက် -* `{len(stores)} ဆိုင်`\n",
        "────────────────────"
    ]

    keyboard = []

    for s in stores:
        s_id = s["id"]
        lic = database.get_store_license(s_id)
        status_icon = "🟢" if lic["is_valid"] else ("🔴" if lic["status"] == "suspended" else "⚠️")
        status_text = "Active ✅" if lic["is_valid"] else f"{lic['status'].capitalize()} ❌"
        bot_uname = s.get("bot_username") or "Token မချိတ်ရသေးပါ"
        owner_name = s.get("owner_first_name") or f"ID {s['owner_id']}"
        owner_handle = f" (@{s['owner_username']})" if s.get("owner_username") else ""
        is_running = "⚡ Online" if s_id in client_bot_manager.running_apps else "⚪ Standby"

        lines.append(
            f"{status_icon} *Store #{s_id} - {s['store_name']}*\n"
            f"   👤 Owner - `{s['owner_id']}` ({owner_name}{owner_handle})\n"
            f"   🤖 Bot - `{bot_uname}` [{is_running}]\n"
            f"   📦 Plan - `{s.get('plan', 'Standard')}`\n"
            f"   📅 Expiry - `{lic['expiry_str']}` ({lic['days_remaining']} ရက်ကျန်) [{status_text}]\n"
        )

        btn_label = f"🏪 #{s_id} {s['store_name']} ({status_icon})"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"saas_manage_{s_id}")])

    lines.append("────────────────────\n💡 *ဆိုင်တစ်ခုစီကို အသေးစိတ် စီမံရန် သက်ဆိုင်ရာ Button ကို နှိပ်ပါ -*")

    keyboard.append([
        InlineKeyboardButton("➕ Client ဆိုင်အသစ် ထည့်သွင်းမည်", callback_data="saas_add_store_prompt"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_license_info"),
        InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
    ])

    await _send_or_edit(target, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def render_store_detail_manage_view(target: Any, store_id: int) -> None:
    """Super Admin dedicated view for an individual store: Kill switch, extend license, bot token, name edit."""
    store = database.get_store(store_id)
    if not store:
        await _send_or_edit(
            target,
            f"❌ Store ID `#{store_id}` ကို ရှာမတွေ့ပါ။",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ဆိုင်များ စာရင်းသို့", callback_data="admin_license_info")]]),
        )
        return

    lic = database.get_store_license(store_id)
    stats = database.get_store_stats(store_id=store_id)
    rate = database.get_exchange_rate(store_id=store_id)
    token = (store.get("bot_token") or "").strip()
    masked_token = f"`{token[:8]}...{token[-5:]}`" if len(token) > 15 else ("`မချိတ်ရသေးပါ`" if not token else f"`{token}`")
    bot_uname = store.get("bot_username") or "ချိတ်ဆက်မထားပါ"
    is_online = store_id in client_bot_manager.running_apps
    online_badge = "🟢 Polling Online" if is_online else "⚪ Offline / Stopped"
    status_badge = "🟢 Active" if lic["is_valid"] else f"🔴 {lic['status'].capitalize()}"
    owner_info = f"`{store['owner_id']}`"
    if store.get("owner_first_name"):
        owner_info += f" ({store['owner_first_name']})"
    if store.get("owner_username"):
        owner_info += f" @{store['owner_username']}"

    text = (
        f"🏪 *[Store #{store_id} - {store['store_name']} စီမံခန့်ခွဲခြင်း]*\n\n"
        f"👤 *ဆိုင်ပိုင်ရှင် Admin -* {owner_info}\n"
        f"🤖 *Bot Username -* `{bot_uname}`\n"
        f"🔑 *Bot Token -* {masked_token}\n"
        f"⚡ *Bot Instance -* `{online_badge}`\n"
        f"📦 *Subscription Plan -* `{store.get('plan', 'Pro Business')}`\n"
        f"📅 *လိုင်စင်ကုန်ဆုံးရက် -* `{lic['expiry_str']}` ({lic['days_remaining']} ရက်ကျန်)\n"
        f"📌 *လိုင်စင်အခြေအနေ -* {status_badge}\n"
        f"💱 *THB ငွေလဲနှုန်း -* `1 THB = {rate:,} MMK`\n\n"
        f"📊 *ဆိုင်အရောင်းစာရင်း -*\n"
        f"• ပြီးမြောက်ခဲ့သော အော်ဒါ - `{stats['total_orders']} ခု`\n"
        f"• စုစုပေါင်း အရောင်းရငွေ - `{stats['total_revenue']:,} MMK`\n"
        f"• ပစ္စည်းအမျိုးအစား - `{stats['total_products']} မျိုး`\n"
        f"• လက်ကျန် Stock - `{stats['total_stock']} ခု`\n\n"
        "👇 *အောက်ပါ ခလုတ်များဖြင့် ချက်ချင်း စီမံခန့်ခွဲနိုင်ပါသည် -*"
    )

    keyboard = []

    # Row 1: Kill switch or Activate
    if lic["is_valid"]:
        keyboard.append([
            InlineKeyboardButton("🛑 ဆိုင်ပိတ်မည် (Kill Switch)", callback_data=f"saas_kill_{store_id}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("✅ ဆိုင်ပြန်ဖွင့်မည် (Activate)", callback_data=f"saas_act_{store_id}")
        ])

    # Row 2: Extend license
    keyboard.append([
        InlineKeyboardButton("➕ ၃၀ ရက် တိုးမည်", callback_data=f"saas_ext_{store_id}_30"),
        InlineKeyboardButton("➕ ၉၀ ရက် တိုးမည်", callback_data=f"saas_ext_{store_id}_90"),
    ])

    # Row 3: Edit Name & Bot Token
    keyboard.append([
        InlineKeyboardButton("✏️ ဆိုင်အမည် ပြင်မည်", callback_data=f"saas_name_prompt_{store_id}"),
        InlineKeyboardButton("🤖 Bot Token ချိတ်/ပြင်", callback_data=f"saas_tkn_prompt_{store_id}"),
    ])

    # Row 4: Unlink token & Delete store (store_id != 1)
    sub_row = []
    if token:
        sub_row.append(InlineKeyboardButton("🔌 Token ဖြုတ်မည်", callback_data=f"saas_untkn_{store_id}"))
    if store_id != 1:
        sub_row.append(InlineKeyboardButton("🗑️ ဆိုင်ဖျက်မည်", callback_data=f"saas_del_confirm_{store_id}"))
    if sub_row:
        keyboard.append(sub_row)

    # Row 5: Refresh & Back
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"saas_manage_{store_id}"),
        InlineKeyboardButton("🔙 ဆိုင်များ စာရင်းသို့", callback_data="admin_license_info"),
    ])

    await _send_or_edit(target, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def render_store_admin_license_view(target: Any, user_id: int) -> None:
    """Store Admin dedicated view: displays their store's license status, assigned bot token, and bot binding options."""
    store = database.get_store_by_owner(user_id)
    if not store:
        store = database.get_store(1)

    store_id = store.get("id", 1) if store else 1
    store_name = store.get("store_name", "Latt Swal Store") if store else "Latt Swal Store"
    lic = database.get_store_license(store_id)
    token = (store.get("bot_token") or "").strip() if store else ""
    bot_uname = (store.get("bot_username") or "").strip() if store else ""
    is_online = store_id in client_bot_manager.running_apps
    online_badge = "🟢 Polling Online" if is_online else "⚪ Offline / Not Running"
    status_badge = "✅ Active" if lic["is_valid"] else f"❌ {lic['status'].capitalize()}"

    bot_section = ""
    if bot_uname:
        bot_section = (
            f"🤖 *ချိတ်ဆက်ထားသော Bot -* `{bot_uname}`\n"
            f"⚡ *Bot Status -* `{online_badge}`\n"
            f"👉 သင့် Customers များသည် {bot_uname} သို့ တိုက်ရိုက်ဝင်ရောက်၍ စျေးဝယ်ယူနိုင်ပါသည်။\n"
        )
    else:
        bot_section = (
            "🤖 *ချိတ်ဆက်ထားသော Bot -* `မချိတ်ဆက်ရသေးပါ`\n"
            "⚠️ သင့်ဆိုင်အတွက် သီးသန့် Telegram Bot မချိတ်ရသေးပါ။\n"
            "အောက်ပါ '🤖 ကိုယ်ပိုင် Bot Token ချိတ်မည်' ကို နှိပ်၍ BotFather Token ချိတ်ဆက်နိုင်ပါသည်။\n"
        )

    text = (
        f"🔐 *[သင့်စတိုးဆိုင် SaaS License & Bot အချက်အလက်]*\n\n"
        f"🏪 *ဆိုင်အမည် -* `{store_name}`\n"
        f"🆔 *Store ID -* `#{store_id}`\n"
        f"📦 *Subscription Plan -* `{lic['plan']}`\n"
        f"📌 *လိုင်စင်အခြေအနေ -* {status_badge}\n"
        f"📅 *သက်တမ်းကုန်ဆုံးရက် -* `{lic['expiry_str']}`\n"
        f"⏳ *ကျန်ရှိရက် -* `{lic['days_remaining']} ရက်`\n\n"
        f"{bot_section}\n"
        "💡 *သက်တမ်းတိုးမြှင့်လိုပါက -*\n"
        "SaaS Master Super Admin ([Peter](https://t.me/latt_swal2026)) ထံ ဆက်သွယ်၍ လစဉ်ကြေး ပေးသွင်းနိုင်ပါသည်။"
    )

    keyboard = [
        [
            InlineKeyboardButton("🤖 ကိုယ်ပိုင် Bot Token ချိတ်/ပြင်မည်", callback_data="store_admin_bind_token_prompt"),
            InlineKeyboardButton("📖 Bot ဖန်တီးနည်း လမ်းညွှန်", callback_data="saas_botfather_guide"),
        ]
    ]
    if token:
        keyboard.append([
            InlineKeyboardButton("🔌 Bot Token ဖြုတ်မည် (Unlink)", callback_data="store_admin_unlink_token"),
        ])
    keyboard.append([
        InlineKeyboardButton("💬 Super Admin ထံ ဆက်သွယ်ရန်", url="https://t.me/latt_swal2026"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="admin_license_info"),
        InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
    ])

    await _send_or_edit(target, text, reply_markup=InlineKeyboardMarkup(keyboard))


WAITING_BOT_TOKEN = 1


async def render_botfather_guide_view(target: Any, user_id: int) -> None:
    """Displays comprehensive BotFather step-by-step setup guide for store admins."""
    guide_text = (
        "📖 *[Telegram BotFather ဖြင့် ကိုယ်ပိုင် Bot ဖန်တီးနည်း လမ်းညွှန်]*\n"
        "*(Step-by-Step Telegram Bot Setup Guide)*\n\n"
        "သင့်စတိုးဆိုင်အတွက် သီးသန့် Telegram Bot တစ်ခု ဖန်တီး၍ ဤ SaaS စနစ်နှင့် ချိတ်ဆက်ရန် အောက်ပါ အဆင့် ၄ ဆင့်ကို လုပ်ဆောင်ပါ -\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ *BotFather ထံ သွားပါ -*\n"
        "👉 [@BotFather](https://t.me/BotFather) Link ကို နှိပ်ပြီး Telegram တွင် ဖွင့်ပါ၊ ထို့နောက် `/start` ကို နှိပ်ပါ။\n\n"
        "2️⃣ *Bot အသစ် စတင်ဖန်တီးပါ -*\n"
        "• BotFather ထံသို့ `/newbot` ဟု စာရိုက်ပို့ပါ။\n"
        "• BotFather မှ သင့် Bot ၏ **အမည် (Display Name)** ကို မေးပါမည် (ဥပမာ - `Ko Aung Game Shop`)။\n"
        "• ထို့နောက် Bot ၏ **Username** ကို မေးပါမည်။ Username သည် အဆုံးတွင် `bot` သို့မဟုတ် `_bot` ဖြင့် ဆုံးရပါမည် (ဥပမာ - `koaung_gameshop_bot` သို့မဟုတ် `aungshopbot`)။\n\n"
        "3️⃣ *HTTP API Token ကို ရယူပါ -*\n"
        "• ဖန်တီးပြီးသည်နှင့် BotFather မှ အောက်ပါပုံစံအတိုင်း **HTTP API Token** ကို ပေးပို့ပါမည် -\n"
        "`7123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`\n"
        "• ထို Token ကုဒ် အပြည့်အစုံကို Copy (ကူးယူ) လုပ်ပါ။\n\n"
        "4️⃣ *ဤစနစ်တွင် လာရောက် ချိတ်ဆက်ပါ -*\n"
        "• ဤနေရာသို့ ပြန်လာပြီး အောက်ပါ **'🔑 Token ချိတ်ဆက်မည်'** ခလုတ်ကို နှိပ်ပါ။\n"
        "• ကူးယူထားသော Token ကို Message အဖြစ် တိုက်ရိုက် Paste ချ၍ ရိုက်ပို့လိုက်ပါ။\n"
        "• စနစ်မှ Token မှန်ကန်မှုကို စစ်ဆေးပြီး သင့်ဆိုင်နှင့် အလိုအလျောက် ချက်ချင်း ချိတ်ဆက်ပေးသွားပါမည်!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⭐ *ကိုယ်ပိုင် Bot ချိတ်ဆက်ခြင်း၏ အကျိုးကျေးဇူးများ -*\n"
        "✅ Customer များသည် သင့် Bot ထဲတွင်သာ သီးသန့် ဝင်ရောက် စျေးဝယ်ယူနိုင်ပါမည်။\n"
        "✅ သင့်ဆိုင်၏ Product များ၊ Stock များ၊ ဈေးနှုန်းများနှင့် Payment အကောင့်များသာ သီးသန့် ပေါ်နေမည်။\n"
        "✅ သင့် Bot ထဲတွင်လည်း /admin ဖြင့် Admin Control Panel ကို တိုက်ရိုက် အသုံးပြုနိုင်ပါသည်။"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 @BotFather သို့ သွားမည်", url="https://t.me/BotFather"),
            InlineKeyboardButton("🔑 Token ချိတ်ဆက်မည်", callback_data="store_admin_bind_token_prompt"),
        ],
        [
            InlineKeyboardButton("🔙 SaaS License သို့", callback_data="admin_license_info"),
            InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
        ],
    ])

    await _send_or_edit(target, guide_text, reply_markup=markup)


async def start_token_binding_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for Bot Token linking ConversationHandler."""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        if update.callback_query:
            try:
                await update.callback_query.answer("⛔ Admin သာ အသုံးပြုခွင့်ရှိပါသည်။", show_alert=True)
            except Exception:
                pass
        return ConversationHandler.END

    store = database.get_store_by_owner(user.id) or database.get_store(1)
    store_name = store.get("store_name", "Latt Swal Store") if store else "Latt Swal Store"
    store_id = store.get("id", 1) if store else 1

    context.user_data["admin_state"] = "waiting_store_admin_token"
    context.user_data["binding_store_id"] = store_id

    prompt_text = (
        f"🤖 *[သင့်ဆိုင်အတွက် သီးသန့် Telegram Bot ချိတ်ဆက်ရန်]*\n"
        f"*(Connect Your Telegram Bot Token)*\n\n"
        f"🏪 *ဆိုင်အမည် -* `{store_name}`\n"
        f"🆔 *Store ID -* `#{store_id}`\n\n"
        "👉 @BotFather မှ ရရှိထားသော **HTTP API Token** ကို ဤနေရာတွင် စာရိုက်ပို့ပေးပါ -\n\n"
        "📌 *Token နမူနာ ပုံစံ -*\n"
        "`7123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`\n\n"
        "💡 _Bot အသစ် ဖန်တီးနည်း မသိသေးပါက အောက်ပါ '📖 Bot ဖန်တီးနည်း လမ်းညွှန်' ကို နှိပ်၍ အဆင့်ဆင့် ဖတ်ရှုနိုင်ပါသည်။_\n"
        "_(မချိတ်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ သို့မဟုတ် Cancel ခလုတ်ကို နှိပ်ပါ)_"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Bot ဖန်တီးနည်း လမ်းညွှန် (Guide)", callback_data="saas_botfather_guide"),
            InlineKeyboardButton("🤖 @BotFather သို့", url="https://t.me/BotFather"),
        ],
        [
            InlineKeyboardButton("❌ မချိတ်တော့ပါ (Cancel)", callback_data="cancel_bind_token"),
            InlineKeyboardButton("🔙 SaaS License သို့", callback_data="admin_license_info"),
        ],
    ])

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await safe_edit_message(update.callback_query, prompt_text, reply_markup=markup)
    elif update.message:
        await update.message.reply_text(prompt_text, reply_markup=markup, parse_mode="Markdown")

    return WAITING_BOT_TOKEN


async def receive_bot_token_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives, verifies with Telegram API, and binds the BotFather token."""
    if not update.message or not update.message.text:
        return WAITING_BOT_TOKEN

    raw_text = update.message.text.strip()
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    if raw_text.startswith("/cancel") or raw_text.lower() == "cancel":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("binding_store_id", None)
        await update.message.reply_text("❌ Bot Token ချိတ်ဆက်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
        await render_store_admin_license_view(update, user.id)
        return ConversationHandler.END

    cleaned_token = clean_bot_token(raw_text)
    store_id = context.user_data.get("binding_store_id")
    if not store_id:
        store = database.get_store_by_owner(user.id)
        store_id = store["id"] if store else 1

    status_msg = await update.message.reply_text("🔄 Bot Token ကို စစ်ဆေးပြီး Telegram API နှင့် ချိတ်ဆက်နေပါသည်...")
    ok, err_msg, info = await validate_and_bind_bot_token(cleaned_token, store_id, client_bot_manager)
    if not ok:
        retry_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Bot ဖန်တီးနည်း လမ်းညွှန်", callback_data="saas_botfather_guide"),
                InlineKeyboardButton("🤖 @BotFather သို့", url="https://t.me/BotFather"),
            ],
            [
                InlineKeyboardButton("❌ Cancel (မချိတ်တော့ပါ)", callback_data="cancel_bind_token")
            ]
        ])
        await status_msg.edit_text(
            f"{err_msg}\n\n"
            f"⚠️ Token ကို သေချာစွာ စစ်ဆေးပြီး ပြန်လည်ရိုက်ပို့ပေးပါ (သို့မဟုတ် /cancel) -",
            parse_mode="Markdown",
            reply_markup=retry_markup,
        )
        return WAITING_BOT_TOKEN

    context.user_data.pop("admin_state", None)
    context.user_data.pop("binding_store_id", None)

    bot_uname = info["bot_username"]
    bot_name = info["bot_first_name"]
    store = database.get_store(store_id)
    store_name = store["store_name"] if store else f"Store #{store_id}"

    success_text = (
        f"🎉 *[ဂုဏ်ယူပါသည်! သင့်စတိုးဆိုင် Bot ချိတ်ဆက်မှု အောင်မြင်ပါသည်]*\n\n"
        f"🏪 *ဆိုင်အမည် -* {store_name}\n"
        f"🤖 *Bot Username -* {bot_uname}\n"
        f"📛 *Bot Name -* {bot_name}\n\n"
        f"🚀 ယခုအခါ Customer များသည် {bot_uname} သို့ တိုက်ရိုက်ဝင်ရောက်၍ သင့်ဆိုင်၏ ပစ္စည်းများကို သီးသန့် ဝယ်ယူနိုင်ပါပြီ!\n\n"
        f"👑 ထို Bot ထဲတွင်လည်း /admin ဖြင့် Admin Control Panel ကို တိုက်ရိုက် အသုံးပြုနိုင်ပါသည်။"
    )

    success_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🤖 {bot_uname} သို့ သွားမည်", url=f"https://t.me/{bot_uname[1:]}")],
        [InlineKeyboardButton("🔙 SaaS License သို့", callback_data="admin_license_info")],
    ])

    await status_msg.edit_text(success_text, parse_mode="Markdown", reply_markup=success_markup)

    # Notify Super Admin
    try:
        await context.bot.send_message(
            chat_id=1920334929,
            text=(
                f"📢 *[Store Admin Bot Token ချိတ်ဆက်မှု အသိပေးချက်]*\n\n"
                f"🏪 *Store #{store_id} -* {store_name}\n"
                f"👤 *Admin ID -* `{user.id}` ({user.first_name})\n"
                f"🤖 *Linked Bot -* {bot_uname}\n"
                f"⚡ Polling status: Active"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Failed to notify super admin: {e}")

    return ConversationHandler.END


async def cancel_token_binding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the Bot Token linking conversation."""
    context.user_data.pop("admin_state", None)
    context.user_data.pop("binding_store_id", None)
    user = update.effective_user
    user_id = user.id if user else 0

    if update.callback_query:
        try:
            await update.callback_query.answer("❌ ပယ်ဖျက်လိုက်ပါပြီ!")
        except Exception:
            pass
        await render_store_admin_license_view(update.callback_query, user_id)
    elif update.message:
        await update.message.reply_text("❌ Bot Token ချိတ်ဆက်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
        await render_store_admin_license_view(update, user_id)

    return ConversationHandler.END


def create_bot_token_conv_handler() -> ConversationHandler:
    """Builds and returns the ConversationHandler for Store Admin Bot Token linking."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_token_binding_conv, pattern="^(store_admin_bind_token_prompt|admin_bind_bot_token)$"),
            CommandHandler("bindbot", bind_bot_command),
        ],
        states={
            WAITING_BOT_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bot_token_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_token_binding),
            CallbackQueryHandler(cancel_token_binding, pattern="^(cancel_bind_token|admin_license_info)$"),
        ],
        per_chat=True,
        per_user=True,
        per_message=False,
    )


async def list_stores_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists all tenant stores: /stores (Super Admin only)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။")
        return
    await render_super_admin_stores_view(update, user.id)


async def link_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Links and launches a client bot token to a store: /linkbot <store_id> <bot_token> (Super Admin only)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။")
        return

    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            "အသုံးပြုပုံ -\n"
            "`/linkbot <store_id> <bot_token>`\n\n"
            "ဥပမာ -\n"
            "`/linkbot 2 7123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`",
            parse_mode="Markdown",
        )
        return

    store_id = int(context.args[0])
    token = clean_bot_token(context.args[1])

    store = database.get_store(store_id)
    if not store:
        await update.message.reply_text(f"❌ Store ID `#{store_id}` ကို ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    status_msg = await update.message.reply_text(f"🔄 Store #{store_id} အတွက် Bot Token ကို စစ်ဆေးနေပါသည်...")
    ok, err_msg, info = await validate_and_bind_bot_token(token, store_id, client_bot_manager)
    if not ok:
        await status_msg.edit_text(err_msg, parse_mode="Markdown")
        return

    bot_username = info["bot_username"]
    launch_status = "🚀 Bot Instance ကို background တွင် အောင်မြင်စွာ စတင်လည်ပတ်ပေးလိုက်ပါပြီ!" if info["started"] else "ℹ️ Bot Token ကို သိမ်းဆည်းလိုက်ပါပြီ။"

    await status_msg.edit_text(
        f"✅ *[Bot Instance ချိတ်ဆက်မှု အောင်မြင်ပါသည်!]*\n\n"
        f"🏪 *ဆိုင် ID -* `#{store_id}` ({store['store_name']})\n"
        f"🤖 *Bot Username -* {bot_username}\n"
        f"📛 *Bot Name -* {info['bot_first_name']}\n"
        f"👤 *ဆိုင်ပိုင်ရှင် Admin ID -* `{store['owner_id']}`\n\n"
        f"{launch_status}\n\n"
        f"ယခုအခါ {bot_username} သို့ Customer များ ဝင်ရောက် /start ပြုလုပ်ပါက {store['store_name']} ၏ ပစ္စည်းများနှင့် စျေးနှုန်းများကို သီးသန့် အသုံးပြုနိုင်ပါပြီ!",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=store["owner_id"],
            text=(
                f"🎉 *[ဂုဏ်ယူပါသည်! သင့်ဆိုင်အတွက် သီးသန့် Bot ချိတ်ဆက်ပြီးပါပြီ]*\n\n"
                f"Super Admin မှ သင့်စတိုးဆိုင် *{store['store_name']}* အတွက် သီးသန့် Bot ကို ချိတ်ဆက်ပေးလိုက်ပါပြီ!\n\n"
                f"🤖 *သင့် Bot Username -* {bot_username}\n"
                f"👉 Customer များကို {bot_username} Link ပေး၍ ရောင်းချနိုင်ပါပြီ။\n"
                f"👑 /admin ဖြင့် Product, Price, Stock များကို စီမံခန့်ခွဲနိုင်ပါသည်။"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Could not notify owner {store['owner_id']} of linked bot - {e}")


async def bind_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store Admin command to link their BotFather token: /bindbot [token]"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ Admin သာ အသုံးပြုခွင့်ရှိပါသည်။")
        return ConversationHandler.END

    store = database.get_store_by_owner(user.id)
    if not store:
        if is_super_admin(user.id):
            await update.message.reply_text(
                "👑 Super Admin သည် မည်သည့်ဆိုင်မဆို Bot Token ချိတ်ဆက်ရန် `/linkbot <store_id> <token>` ကို အသုံးပြုနိုင်ပါသည်။"
            )
            return ConversationHandler.END
        await update.message.reply_text("❌ သင့်အကောင့်နှင့် ချိတ်ဆက်ထားသော စတိုးဆိုင် ရှာမတွေ့ပါ။")
        return ConversationHandler.END

    if context.args:
        token = clean_bot_token(context.args[0])
        status_msg = await update.message.reply_text("🔄 Bot Token ကို စစ်ဆေးနေပါသည်...")

        ok, err_or_msg, info = await validate_and_bind_bot_token(token, store["id"], client_bot_manager)
        if not ok:
            await status_msg.edit_text(err_or_msg, parse_mode="Markdown")
            return ConversationHandler.END

        bot_username = info["bot_username"]
        await status_msg.edit_text(
            f"🎉 *[ဂုဏ်ယူပါသည်! သင့်စတိုးဆိုင် Bot ချိတ်ဆက်မှု အောင်မြင်ပါသည်]*\n\n"
            f"🏪 *ဆိုင်အမည် -* {store['store_name']}\n"
            f"🤖 *Bot Username -* {bot_username}\n"
            f"📛 *Bot Name -* {info['bot_first_name']}\n\n"
            f"🚀 ယခုအခါ Customer များသည် {bot_username} သို့ တိုက်ရိုက်ဝင်ရောက်၍ သင့်ဆိုင်၏ ပစ္စည်းများကို သီးသန့် ဝယ်ယူနိုင်ပါပြီ!\n"
            f"Admin Panel အား ထို bot ထဲတွင်လည်း /admin ဖြင့် အသုံးပြုနိုင်ပါသည်။",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🤖 {bot_username} သို့ သွားမည်", url=f"https://t.me/{bot_username[1:]}")],
                [InlineKeyboardButton("🔙 SaaS License သို့", callback_data="admin_license_info")],
            ])
        )
        return ConversationHandler.END

    return await start_token_binding_conv(update, context)


async def unlink_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Store Admin command to unlink their BotFather token: /unlinkbot"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ Admin သာ အသုံးပြုခွင့်ရှိပါသည်။")
        return

    store = database.get_store_by_owner(user.id)
    if not store:
        await update.message.reply_text("❌ သင့်အကောင့်နှင့် ချိတ်ဆက်ထားသော စတိုးဆိုင် ရှာမတွေ့ပါ။")
        return

    s_id = store["id"]
    await client_bot_manager.stop_instance(s_id)
    database.update_store(s_id, bot_token="", bot_username="")
    await update.message.reply_text(
        f"🔌 *[Bot Token ဖြုတ်လိုက်ပါပြီ]*\n\n"
        f"🏪 *ဆိုင်အမည် -* {store['store_name']}\n"
        f"သီးသန့် Bot Polling ကို ရပ်နားလိုက်ပါပြီ။ Bot အသစ် ပြန်လည်ချိတ်ဆက်လိုပါက `/bindbot <token>` ဖြင့် ပြန်လည် ချိတ်နိုင်ပါသည်။",
        parse_mode="Markdown",
    )


async def extend_store_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Extends license for a specific store: /extendstore <store_id> <days> (Super Admin only)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။")
        return

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/extendstore <store_id> <ရက်ပေါင်း>`\nဥပမာ - `/extendstore 2 30`", parse_mode="Markdown")
        return

    store_id = int(context.args[0])
    days = int(context.args[1])
    res = database.extend_store_license(store_id, days)
    if not res:
        await update.message.reply_text(f"❌ Store ID `#{store_id}` ရှာမတွေ့ပါ။")
        return

    await update.message.reply_text(
        f"✅ *[Store #{store_id} သက်တမ်းတိုးပြီးပါပြီ]*\n\n"
        f"🏪 *ဆိုင် -* {res['store_name']}\n"
        f"📅 *သက်တမ်းကုန်ဆုံးရက် -* `{res['expiry_str']}` ({res['days_remaining']} ရက်ကျန်)\n"
        f"📌 *Status -* ✅ {res['status'].capitalize()}",
        parse_mode="Markdown",
    )


async def suspend_store_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instantly suspends a client store (Kill Switch): /suspendstore <store_id> (Super Admin only)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/suspendstore <store_id>`\nဥပမာ - `/suspendstore 2`", parse_mode="Markdown")
        return

    store_id = int(context.args[0])
    database.set_store_license_status(store_id, "suspended")
    await client_bot_manager.stop_instance(store_id)
    res = database.get_store_license(store_id)
    await update.message.reply_text(
        f"🚨 *[Store #{store_id} အား ဝန်ဆောင်မှု ပိတ်သိမ်းလိုက်ပါပြီ (Kill Switch Activated)]*\n\n"
        f"🏪 *ဆိုင် -* {res['store_name']}\n"
        f"👤 *Owner ID -* `{res['owner_id']}`\n"
        f"📌 *Status -* ❌ Suspended\n\n"
        f"ထိုဆိုင်၏ Bot သို့ ဝင်ရောက်သူတိုင်း ဆိုင်ပိတ်ထားကြောင်းနှင့် Super Admin ထံ ဆက်သွယ်ရန် သတိပေးချက် ပြသမည် ဖြစ်သည်။",
        parse_mode="Markdown",
    )


async def activate_store_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reactivates a suspended client store: /activatestore <store_id> (Super Admin only)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/activatestore <store_id>`\nဥပမာ - `/activatestore 2`", parse_mode="Markdown")
        return

    store_id = int(context.args[0])
    database.set_store_license_status(store_id, "active")
    store = database.get_store(store_id)
    if store and store.get("bot_token"):
        await client_bot_manager.start_instance(store_id, store["bot_token"])
    res = database.get_store_license(store_id)
    await update.message.reply_text(
        f"✅ *[Store #{store_id} အား ပြန်လည်ဖွင့်လှစ်ပေးလိုက်ပါပြီ (Activated)]*\n\n"
        f"🏪 *ဆိုင် -* {res['store_name']}\n"
        f"👤 *Owner ID -* `{res['owner_id']}`\n"
        f"📅 *Expiry -* `{res['expiry_str']}` ({res['days_remaining']} ရက်ကျန်)\n"
        f"📌 *Status -* 🟢 Active",
        parse_mode="Markdown",
    )


async def store_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows complete details of a store: /storeinfo <store_id> (Super Admin only)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/storeinfo <store_id>`\nဥပမာ - `/storeinfo 2`", parse_mode="Markdown")
        return

    store_id = int(context.args[0])
    await render_store_detail_manage_view(update, store_id)


async def license_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays SaaS multi-tenant licensing details or super admin store management."""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    if is_super_admin(user.id):
        await render_super_admin_stores_view(update, user.id)
    else:
        await render_store_admin_license_view(update, user.id)


async def renew_license_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Super Admin command to extend the SaaS license: /renew <days>"""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ ဤ Command ကို Master SaaS Super Admin သာလျှင် အသုံးပြုခွင့်ရှိပါသည်။")
        return

    days = 30
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])

    lic = database.extend_license(days)
    store_name = database.get_store_name()

    await update.message.reply_text(
        f"🎉 *[SaaS License သက်တမ်းတိုးမြှင့်ပြီးပါပြီ]*\n\n"
        f"🏪 *ဆိုင်အမည် -* `{store_name}`\n"
        f"➕ *တိုးမြှင့်သည့်ရက် -* `+{days} ရက်`\n"
        f"📅 *သက်တမ်းကုန်ဆုံးရက် အသစ် -* `{lic['expiry_str']}`\n"
        f"⏳ *လက်ကျန်ရက် စုစုပေါင်း -* `{lic['days_remaining']} ရက်`\n"
        f"📌 *Status -* ✅ `{lic['status']}`\n\n"
        "စနစ်လည်ပတ်မှု အားလုံးကို အပြည့်အဝ ပြန်လည်အသုံးပြုနိုင်ပါပြီ။",
        parse_mode="Markdown",
    )


async def set_license_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Super Admin command to set a specific expiration date: /setlicense <YYYY-MM-DD>"""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ ဤ Command ကို Master SaaS Super Admin သာလျှင် အသုံးပြုခွင့်ရှိပါသည်။")
        return

    if not context.args:
        await update.message.reply_text(
            "အသုံးပြုပုံ - `/setlicense <YYYY-MM-DD>`\nဥပမာ - `/setlicense 2026-12-31`",
            parse_mode="Markdown",
        )
        return

    date_str = context.args[0].strip()
    try:
        lic = database.set_license_expiry(date_str)
        await update.message.reply_text(
            f"✅ *License သက်တမ်းကုန်ဆုံးရက် သတ်မှတ်ပြီးပါပြီ!*\n\n"
            f"📅 *သက်တမ်းကုန်ဆုံးရက် -* `{lic['expiry_str']}`\n"
            f"⏳ *ကျန်ရှိရက် -* `{lic['days_remaining']} ရက်`\n"
            f"📌 *Status -* ✅ `{lic['status']}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ ရက်စွဲ သတ်မှတ်မှု မှားယွင်းနေပါသည်: {e}")


async def kill_switch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Super Admin command to instantly suspend the bot instance (Kill Switch)."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ ဤ Command ကို Master SaaS Super Admin သာလျှင် အသုံးပြုခွင့်ရှိပါသည်။")
        return

    lic = database.kill_switch()
    store_name = database.get_store_name()
    await update.message.reply_text(
        f"🚨 *[KILL SWITCH ACTIVATED / BOT DEACTIVATED]*\n\n"
        f"⚠️ `{store_name}` ၏ စနစ်လည်ပတ်မှုကို ချက်ချင်း ရပ်ဆိုင်းလိုက်ပါပြီ!\n\n"
        f"📌 *License Status -* ❌ `{lic['status']}`\n"
        "ဝယ်ယူသူများနှင့် ဆိုင်ပိုင်ရှင်အားလုံး ထံသို့ Service Suspended Message သာ ပြသတော့မည်ဖြစ်ပါသည်။\n\n"
        "ပြန်လည်ဖွင့်လှစ်လိုပါက `/activatelicense` သို့မဟုတ် `/renew 30` ဟု ရိုက်ပို့ပါ။",
        parse_mode="Markdown",
    )


async def activate_license_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Super Admin command to reactivate suspended bot."""
    user = update.effective_user
    if not user or not is_super_admin(user.id):
        await update.message.reply_text("⛔ ဤ Command ကို Master SaaS Super Admin သာလျှင် အသုံးပြုခွင့်ရှိပါသည်။")
        return

    lic = database.activate_license(30)
    store_name = database.get_store_name()
    await update.message.reply_text(
        f"✅ *[BOT REACTIVATED / LICENSE ACTIVATED]*\n\n"
        f"🎉 `{store_name}` စနစ်လည်ပတ်မှုကို အောင်မြင်စွာ ပြန်လည်ဖွင့်လှစ်လိုက်ပါပြီ!\n\n"
        f"📅 *သက်တမ်းကုန်ဆုံးရက် -* `{lic['expiry_str']}`\n"
        f"⏳ *ကျန်ရှိရက် -* `{lic['days_remaining']} ရက်`\n"
        f"📌 *Status -* ✅ `{lic['status']}`",
        parse_mode="Markdown",
    )


async def claim_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows the first user to claim admin privileges if none exist."""
    user = update.effective_user
    if not user:
        return

    existing_admins = get_all_admins()
    if len(existing_admins) > 0 and user.id not in existing_admins:
        await update.message.reply_text("⛔ Admin ခွင့်ပြုချက် ရှိနှင့်ပြီးဖြစ်သဖြင့် ထပ်မံ claim ၍ မရပါ။")
        return

    database.add_admin(user.id)
    await update.message.reply_text(
        f"🎉 *ဂုဏ်ယူပါသည် {user.first_name}!* သင့်ကို Super Admin အဖြစ် အောင်မြင်စွာ သတ်မှတ်လိုက်ပါပြီ!\n\n"
        "ယခုအခါ `/admin` သို့မဟုတ် Menu ရှိ '👑 Admin Panel' ခလုတ်ဖြင့် စတင်အသုံးပြုနိုင်ပါပြီ။",
        reply_markup=get_main_menu_keyboard(is_admin=True),
        parse_mode="Markdown",
    )


async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Promotes another user to admin: /addadmin <user_id>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/addadmin <user_id>`", parse_mode="Markdown")
        return

    target_id = int(context.args[0])
    database.add_admin(target_id)
    await update.message.reply_text(f"✅ User ID `{target_id}` ကို Admin အဖြစ် အောင်မြင်စွာ ခန့်အပ်လိုက်ပါပြီ။", parse_mode="Markdown")


def can_admin_manage_user_balance(
    admin_id: int, target_user_id: int, store_id: Optional[int] = None
) -> Tuple[bool, str]:
    """
    Validates whether the admin can edit/remove the target user's balance.
    Rules:
    - Super Admin: Master privilege to edit/update/remove wallet balances for anyone (store admins & end-users platform-wide).
    - Store Admin:
      - Can ONLY manage customers of their own specific store (checked via database.is_user_in_store).
      - CANNOT manage Super Admin (1920334929) or other store admins.
      - License must be active.
    """
    if is_super_admin(admin_id):
        return True, ""

    if not is_admin_user(admin_id):
        return False, "⛔ သင့်တွင် Admin ခွင့်ပြုချက် မရှိပါ။"

    if store_id is None:
        active_store = get_admin_active_store(admin_id)
        store_id = active_store.get("id", 1)

    if not is_admin_access_allowed(admin_id, store_id):
        return False, "⛔ သင့်ဆိုင်လိုင်စင် သက်တမ်းကုန်ဆုံး/ရပ်ဆိုင်းထားသဖြင့် စီမံခွင့်မရှိသေးပါ။"

    # Store admin cannot modify Super Admin or other admins
    if is_super_admin(target_user_id):
        return False, "⛔ Super Admin ၏ Balance ကို ပြင်ဆင်ခွင့်မရှိပါ။"
    if is_admin_user(target_user_id) and target_user_id != admin_id:
        return False, "⛔ အခြား Store Admin များ၏ Balance ကို ပြင်ဆင်ခွင့်မရှိပါ။"

    # Store admin must only manage customers belonging to their store
    if not database.is_user_in_store(target_user_id, store_id):
        return False, f"⛔ User ID `{target_user_id}` သည် သင့်စတိုးဆိုင်၏ Customer မဟုတ်သဖြင့် Balance ပြင်ဆင်/ဖျက်ပိုင်ခွင့် မရှိပါ။"

    return True, ""


async def render_user_balance_view(
    target: Any, target_user_id: int, admin_id: int, context: ContextTypes.DEFAULT_TYPE, notice: str = ""
) -> None:
    """Renders the interactive balance management card for a specific user."""
    active_store = get_admin_active_store(admin_id)
    store_id = active_store.get("id", 1)
    s_name = database.get_store_name(store_id=store_id)

    can_manage, err_msg = can_admin_manage_user_balance(admin_id, target_user_id, store_id)
    target_user = database.get_user(target_user_id)
    if not target_user:
        await _send_or_edit(
            target,
            f"❌ User ID `{target_user_id}` ကို Database တွင် ရှာမတွေ့ပါ။",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 အသုံးပြုသူများ စာရင်း", callback_data="admin_users")]]),
        )
        return

    if not can_manage:
        await _send_or_edit(
            target,
            f"{err_msg}\n\n🆔 User ID: `{target_user_id}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 အသုံးပြုသူများ စာရင်း", callback_data="admin_users")]]),
        )
        return

    username_str = f"@{target_user['username']}" if target_user.get("username") else "မရှိပါ"
    name_str = target_user.get("first_name") or "User"
    curr_balance = target_user.get("balance", 0)

    notice_part = f"\n{notice}\n" if notice else ""

    text = (
        f"💰 *[{s_name} - User Balance စီမံခန့်ခွဲခြင်း]*\n"
        f"{notice_part}\n"
        f"🆔 *User ID -* `{target_user_id}`\n"
        f"👤 *အမည် -* {name_str} ({username_str})\n"
        f"💵 *လက်ရှိ Balance -* `{curr_balance:,} MMK`\n\n"
        "👇 *ပြုလုပ်လိုသော လုပ်ဆောင်ချက်ကို ရွေးချယ်ပါ -*\n"
        "• *Balance တိုက်ရိုက်ပြင်မည်* - Balance ပမာဏကို တိုက်ရိုက်သတ်မှတ်ပါမည်\n"
        "• *ငွေဖြည့်မည် (+)* - လက်ရှိငွေထဲသို့ ပေါင်းထည့်ပါမည်\n"
        "• *ငွေနှုတ်မည် (-)* - လက်ရှိငွေထဲမှ နှုတ်ယူပါမည်\n"
        "• *Balance ဖျက်မည် (0 K)* - Balance အားလုံးကို 0 K သို့ ရှင်းလင်းဖျက်သိမ်းပါမည်"
    )

    buttons = [
        [
            InlineKeyboardButton("✏️ Balance တိုက်ရိုက်ပြင်မည်", callback_data=f"adm_bal_edit_{target_user_id}"),
            InlineKeyboardButton("❌ Balance ဖျက်မည် (0 K)", callback_data=f"adm_bal_zero_{target_user_id}"),
        ],
        [
            InlineKeyboardButton("➕ ငွေဖြည့်မည် (+)", callback_data=f"adm_bal_add_{target_user_id}"),
            InlineKeyboardButton("➖ ငွေနုတ်မည် (-)", callback_data=f"adm_bal_sub_{target_user_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"adm_bal_view_{target_user_id}"),
            InlineKeyboardButton("👥 အသုံးပြုသူများ", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
        ],
    ]
    await _send_or_edit(target, text, reply_markup=InlineKeyboardMarkup(buttons))


async def render_balance_manager_panel(target: Any, admin_id: int) -> None:
    """Renders the main Balance Management Panel showing quick user list and command guide."""
    active_store = get_admin_active_store(admin_id)
    store_id = active_store.get("id", 1)
    s_name = database.get_store_name(store_id=store_id)
    users = database.get_all_users(store_id=store_id)

    super_tag = "👑 [Super Admin Master Mode]\n" if is_super_admin(admin_id) else ""

    text = (
        f"💰 *[{s_name} - Balance စီမံခန့်ခွဲမှု Panel]*\n"
        f"{super_tag}\n"
        f"👥 ဆိုင်တွင်း စုစုပေါင်း အသုံးပြုသူ - `{len(users)} ယောက်`\n\n"
        "🛠️ *အသုံးပြုနိုင်သော Commands များ -*\n"
        "• `/editbalance <user_id> <amount>` - Balance တိုက်ရိုက်သတ်မှတ်ရန်\n"
        "• `/removebalance <user_id>` - Balance ကို 0 MMK သို့ ဖျက်သိမ်း/ရှင်းလင်းရန်\n"
        "• `/deductbalance <user_id> <amount>` - Balance နှုတ်ယူရန်\n"
        "• `/addbalance <user_id> <amount>` - ငွေဖြည့်ပေးရန်\n\n"
        "👇 *User ID ရိုက်ထည့်၍ စီမံလိုပါက အောက်ပါခလုတ်ကို နှိပ်ပါ -*"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔍 User ID ရိုက်ထည့်၍ Balance စီမံမည်", callback_data="adm_bal_search_prompt"),
        ],
        [
            InlineKeyboardButton("👥 အသုံးပြုသူများ စာရင်းမှ ရွေးချယ်မည်", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
        ],
    ]
    await _send_or_edit(target, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def edit_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets a user's balance directly: /editbalance <user_id> <new_balance>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    store_id = get_effective_store_id(update, context)

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text(
            "အသုံးပြုပုံ - `/editbalance <user_id> <new_balance>`\n"
            "ဥပမာ - `/editbalance 12345678 50000` (သို့မဟုတ် `/editbalance 12345678 0`)",
            parse_mode="Markdown",
        )
        return

    target_id = int(context.args[0])
    new_balance = max(0, int(context.args[1]))

    can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
    if not can_manage:
        await update.message.reply_text(err_msg, parse_mode="Markdown")
        return

    target_user = database.get_user(target_id)
    if not target_user:
        await update.message.reply_text(f"❌ User ID `{target_id}` ကို Database တွင် ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    updated_bal = database.set_user_balance(target_id, new_balance)
    s_name = database.get_store_name(store_id=store_id)

    await update.message.reply_text(
        f"✅ *[Balance ပြင်ဆင်ပြီးပါပြီ]*\n\n"
        f"🏪 ဆိုင် - `{s_name}`\n"
        f"👤 User ID - `{target_id}`\n"
        f"💰 သတ်မှတ်လိုက်သော Balance - `{updated_bal:,} MMK`",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🔔 *[{s_name} - Wallet Balance ပြင်ဆင်သတ်မှတ်ခြင်း]*\n\n"
                 f"Admin မှ သင့် Wallet Balance ကို `{updated_bal:,} MMK` အဖြစ် ပြင်ဆင်သတ်မှတ်လိုက်ပါသည်။\n"
                 f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{updated_bal:,} MMK`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id} of edit balance - {e}")


async def remove_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removes/resets a user's balance to 0: /removebalance <user_id>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    store_id = get_effective_store_id(update, context)

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "အသုံးပြုပုံ - `/removebalance <user_id>`\n"
            "ဥပမာ - `/removebalance 12345678` (Balance ကို 0 MMK သို့ ဖျက်သိမ်း/ရှင်းလင်းပါမည်)",
            parse_mode="Markdown",
        )
        return

    target_id = int(context.args[0])
    can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
    if not can_manage:
        await update.message.reply_text(err_msg, parse_mode="Markdown")
        return

    target_user = database.get_user(target_id)
    if not target_user:
        await update.message.reply_text(f"❌ User ID `{target_id}` ကို Database တွင် ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    old_bal = target_user.get("balance", 0)
    database.set_user_balance(target_id, 0)
    s_name = database.get_store_name(store_id=store_id)

    await update.message.reply_text(
        f"🗑️ *[Balance ဖျက်သိမ်း/ရှင်းလင်းပြီးပါပြီ]*\n\n"
        f"🏪 ဆိုင် - `{s_name}`\n"
        f"👤 User ID - `{target_id}`\n"
        f"💸 ယခင် Balance - `{old_bal:,} MMK`\n"
        f"💰 ယခု Balance - `0 MMK`",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🔔 *[{s_name} - Wallet Balance ရှင်းလင်းခြင်း]*\n\n"
                 f"Admin မှ သင့် Wallet Balance ကို `0 MMK` သို့ ဖျက်သိမ်း/ရှင်းလင်းလိုက်ပါသည်။\n"
                 f"💰 *လက်ရှိလက်ကျန်ငွေ -* `0 MMK`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id} of remove balance - {e}")


async def deduct_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deducts an amount from user balance: /deductbalance <user_id> <amount>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    store_id = get_effective_store_id(update, context)

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text(
            "အသုံးပြုပုံ - `/deductbalance <user_id> <amount>`\n"
            "ဥပမာ - `/deductbalance 12345678 5000`",
            parse_mode="Markdown",
        )
        return

    target_id = int(context.args[0])
    amount = int(context.args[1])
    can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
    if not can_manage:
        await update.message.reply_text(err_msg, parse_mode="Markdown")
        return

    target_user = database.get_user(target_id)
    if not target_user:
        await update.message.reply_text(f"❌ User ID `{target_id}` ကို Database တွင် ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    new_bal = database.deduct_user_balance(target_id, amount)
    s_name = database.get_store_name(store_id=store_id)

    await update.message.reply_text(
        f"✅ *[Balance နှုတ်ယူပြီးပါပြီ]*\n\n"
        f"🏪 ဆိုင် - `{s_name}`\n"
        f"👤 User ID - `{target_id}`\n"
        f"➖ နှုတ်ယူငွေ - `-{amount:,} MMK`\n"
        f"💵 ယခုလက်ကျန်ငွေ - `{new_bal:,} MMK`",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🔔 *[{s_name} - Wallet Balance နှုတ်ယူခြင်း]*\n\n"
                 f"Admin မှ သင့် Wallet ထဲမှ `-{amount:,} MMK` နှုတ်ယူလိုက်ပါသည်။\n"
                 f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{new_bal:,} MMK`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id} of deduct balance - {e}")


async def add_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds balance to user: /addbalance <user_id> <amount>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    store_id = get_effective_store_id(update, context)

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].lstrip('-').isdigit():
        await update.message.reply_text(
            "အသုံးပြုပုံ - `/addbalance <user_id> <amount>`\nဥပမာ - `/addbalance 12345678 10000`",
            parse_mode="Markdown",
        )
        return

    target_id = int(context.args[0])
    amount = int(context.args[1])

    can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
    if not can_manage:
        await update.message.reply_text(err_msg, parse_mode="Markdown")
        return

    target_user = database.get_user(target_id)
    if not target_user:
        await update.message.reply_text(f"❌ User ID `{target_id}` ကို Database တွင် ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    new_bal = database.add_user_balance(target_id, amount)
    s_name = database.get_store_name(store_id=store_id)

    await update.message.reply_text(
        f"✅ *[ငွေဖြည့်သွင်းပြီးပါပြီ]*\n\n"
        f"🏪 ဆိုင် - `{s_name}`\n"
        f"👤 User ID - `{target_id}` ထံသို့ `+{amount:,} MMK` ထည့်သွင်းပေးပြီးပါပြီ!\n"
        f"💵 ယခုလက်ကျန်ငွေ - `{new_bal:,} MMK`",
        parse_mode="Markdown",
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🔔 *[{s_name} - ငွေဖြည့်သွင်းမှု အတည်ပြုချက်]*\n\n"
                 f"Admin မှ သင့်အကောင့်ထဲသို့ `+{amount:,} MMK` ထည့်သွင်းပေးလိုက်ပါပြီ!\n"
                 f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{new_bal:,} MMK`\n\n"
                 f"ကျေးဇူးတင်ပါသည်! ပစ္စည်းများကို ချက်ချင်း ဝယ်ယူနိုင်ပါပြီ။",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id} - {e}")


# ==============================================================================
# User-Friendly Interactive Product & Stock Addition (No Complex Syntax)
# ==============================================================================

async def start_interactive_add_product(target: Any, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts step-by-step interactive product creation flow without needing complex command syntax."""
    active_store = get_admin_active_store(user_id)
    store_id = active_store["id"]
    store_name = database.get_store_name(store_id=store_id)

    context.user_data["admin_state"] = "interactive_add_prod_name"
    context.user_data.pop("interactive_prod_name", None)
    context.user_data.pop("interactive_prod_category", None)
    context.user_data.pop("interactive_prod_price", None)
    context.user_data.pop("interactive_prod_desc", None)

    prompt = (
        f"📦 *[Product အသစ် ထည့်သွင်းခြင်း - အဆင့် ၁/၄]*\n"
        f"*(Interactive Product Creator)*\n\n"
        f"🏪 *ဆိုင် -* `{store_name}`\n\n"
        f"👉 ထည့်သွင်းလိုသော **Product အမည်** ကို စာရိုက်ပို့ပေးပါ -\n\n"
        f"📌 _နမူနာ - `YouTube Premium 1 Month` သို့မဟုတ် `Netflix 4K UHD`_\n\n"
        f"_(မထည့်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ သို့မဟုတ် Cancel ခလုတ်ကို နှိပ်ပါ)_"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ မထည့်တော့ပါ (Cancel)", callback_data="cancel_interactive_add")],
        [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
    ])
    await _send_or_edit(target, prompt, reply_markup=markup)


async def render_interactive_category_select(target: Any, prod_name: str) -> None:
    """Step 2: Category selection via inline buttons or typing."""
    prompt = (
        f"🏷️ *[Product အသစ် ထည့်သွင်းခြင်း - အဆင့် ၂/၄]*\n\n"
        f"📦 *Product အမည် -* *{prod_name}*\n\n"
        f"👉 Product ၏ **အုပ်စု (Category)** ကို အောက်ပါ ခလုတ်များမှ ရွေးချယ်ပါ (သို့မဟုတ် စာရိုက်ပို့ပေးပါ) -\n\n"
        f"_(မထည့်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ)_"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Streaming", callback_data="adm_cat_sel_Streaming"),
            InlineKeyboardButton("🎵 Music", callback_data="adm_cat_sel_Music"),
        ],
        [
            InlineKeyboardButton("🎮 Gaming", callback_data="adm_cat_sel_Gaming"),
            InlineKeyboardButton("⚡ VPN / Tools", callback_data="adm_cat_sel_VPN"),
        ],
        [
            InlineKeyboardButton("📱 Software / AI", callback_data="adm_cat_sel_Software"),
            InlineKeyboardButton("📦 အထွေထွေ (Other)", callback_data="adm_cat_sel_General"),
        ],
        [
            InlineKeyboardButton("❌ မထည့်တော့ပါ (Cancel)", callback_data="cancel_interactive_add"),
        ],
    ])
    await _send_or_edit(target, prompt, reply_markup=markup)


async def render_interactive_price_prompt(target: Any, prod_name: str, category: str) -> None:
    """Step 3: Price prompt in MMK."""
    prompt = (
        f"💰 *[Product အသစ် ထည့်သွင်းခြင်း - အဆင့် ၃/၄]*\n\n"
        f"📦 *Product အမည် -* *{prod_name}*\n"
        f"🏷️ *အုပ်စု -* `{category}`\n\n"
        f"👉 ရောင်းချလိုသော **စျေးနှုန်း (MMK မြန်မာကျပ်ငွေ)** ကို ဂဏန်းသီးသန့် စာရိုက်ပို့ပေးပါ -\n\n"
        f"📌 _နမူနာ - `4500` သို့မဟုတ် `12000`_\n\n"
        f"_(မထည့်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ)_"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ မထည့်တော့ပါ (Cancel)", callback_data="cancel_interactive_add")],
    ])
    await _send_or_edit(target, prompt, reply_markup=markup)


async def render_interactive_desc_prompt(target: Any, prod_name: str, category: str, price: int) -> None:
    """Step 4: Description prompt (optional skip)."""
    prompt = (
        f"📝 *[Product အသစ် ထည့်သွင်းခြင်း - အဆင့် ၄/၄]*\n\n"
        f"📦 *Product အမည် -* *{prod_name}*\n"
        f"🏷️ *အုပ်စု -* `{category}`\n"
        f"💰 *စျေးနှုန်း -* `{price:,} MMK`\n\n"
        f"👉 Product ၏ **အသေးစိတ် အချက်အလက် (Description / Warranty)** ကို ရိုက်ပို့ပေးပါ -\n\n"
        f"📌 _နမူနာ - `1 Month Private Account | 30 Days Full Warranty`_\n"
        f"💡 _အသေးစိတ် မထည့်လိုပါက အောက်ပါ '⏭️ Skip (မထည့်ပါ)' ခလုတ်ကို နှိပ်ပါ သို့မဟုတ် `skip` ဟု ရိုက်ပို့ပါ_\n\n"
        f"_(မထည့်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ)_"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ Skip (အသေးစိတ် မထည့်ပါ)", callback_data="adm_prod_skip_desc")],
        [InlineKeyboardButton("❌ မထည့်တော့ပါ (Cancel)", callback_data="cancel_interactive_add")],
    ])
    await _send_or_edit(target, prompt, reply_markup=markup)


async def render_interactive_add_stock_select(target: Any, user_id: int, store_id: int) -> None:
    """Step 1 of interactive stock addition: Select product from list."""
    store_name = database.get_store_name(store_id=store_id)
    products = database.get_products_with_stock(store_id=store_id)

    if not products:
        text = (
            f"⚠️ *[{store_name} - Product မရှိသေးပါ]*\n\n"
            f"Stock မထည့်သွင်းမီ ပထမဦးစွာ Product အသစ်တစ်ခု ဖန်တီးပေးရန် လိုအပ်ပါသည်။\n"
            f"အောက်ပါ '➕ Product အသစ် ထည့်မည်' ကို နှိပ်၍ အဆင့်ဆင့် ထည့်သွင်းနိုင်ပါသည်။"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Product အသစ် ထည့်မည်", callback_data="admin_interactive_add_product")],
            [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
        ])
        await _send_or_edit(target, text, reply_markup=markup)
        return

    text = (
        f"📥 *[Stock အသစ် ထည့်သွင်းခြင်း - အဆင့် ၁/၂]*\n"
        f"*(Interactive Stock Addition)*\n\n"
        f"🏪 *ဆိုင် -* `{store_name}`\n"
        f"👉 Stock (Account / Key / Code) ထည့်သွင်းလိုသော **Product** ကို အောက်ပါစာရင်းမှ ရွေးချယ်ပါ -\n"
    )
    keyboard = []
    for p in products:
        stock_cnt = p.get("stock_count", 0)
        badge = f"({stock_cnt} ကျန်)" if stock_cnt > 0 else "(လက်ကျန်မရှိ ❌)"
        clean_name = clean_single_icon_name(p["name"])
        btn_text = f"📦 {clean_name} • {badge}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"adm_istk_sel_{p['id']}")])

    keyboard.append([
        InlineKeyboardButton("➕ Product အသစ် ထည့်မည်", callback_data="admin_interactive_add_product"),
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
    ])
    await _send_or_edit(target, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def render_interactive_stock_input_prompt(target: Any, user_id: int, prod_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 2 of interactive stock addition: Prompts admin to send credentials (plain text / multi-line)."""
    product = database.get_product(prod_id)
    if not product:
        await _send_or_edit(target, "❌ Product ရှာမတွေ့ပါ။", reply_markup=get_admin_back_markup())
        return

    active_store = get_admin_active_store(user_id)
    store_name = database.get_store_name(store_id=active_store["id"])

    context.user_data["admin_state"] = f"interactive_add_stock_{prod_id}"

    prompt = (
        f"📥 *[Stock ထည့်သွင်းခြင်း - အဆင့် ၂/၂]*\n\n"
        f"🏪 *ဆိုင် -* `{store_name}`\n"
        f"📦 *ရွေးချယ်ထားသော ပစ္စည်း -* *{product['name']}*\n"
        f"💰 *စျေးနှုန်း -* `{product['price']:,} MMK`\n"
        f"🔢 *လက်ရှိ Stock -* `{product['stock_count']} ခု`\n\n"
        f"👉 ဝယ်ယူသူထံ အလိုအလျောက် ပေးပို့ပေးမည့် **Account (Email / Password) သို့မဟုတ် Code / Link / Key** ကို ဤနေရာတွင် စာရိုက်ပို့ပေးပါ -\n\n"
        f"💡 *အကောင့်များစွာ တစ်ပြိုင်နက် ထည့်လိုပါက -*\n"
        f"တစ်ကြောင်းလျှင် အကောင့် ၁ ခုနှုန်းဖြင့် စာကြောင်းခွဲ (Enter ခေါက်) ၍ တစ်ပြိုင်နက် ရိုက်ပို့နိုင်ပါသည်!\n\n"
        f"📌 *နမူနာ ပုံစံ -*\n"
        f"`user1@gmail.com | pass123 | profile 1 pin 4321`\n"
        f"`user2@gmail.com | pass456 | profile 2 pin 5678`\n\n"
        f"_(မထည့်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ သို့မဟုတ် Cancel ခလုတ်ကို နှိပ်ပါ)_"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 အခြား Product ရွေးမည်", callback_data="admin_interactive_add_stock")],
        [InlineKeyboardButton("❌ မထည့်တော့ပါ (Cancel)", callback_data="cancel_interactive_add")],
    ])
    await _send_or_edit(target, prompt, reply_markup=markup)


async def add_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds stock item to a product: /addstock [product_id] [credentials/key] (Interactive prompt fallback)"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not is_admin_access_allowed(user.id, store_id=store_id):
        await update.message.reply_text("⛔ သင့်ဆိုင်၏ SaaS License သက်တမ်းကုန်ဆုံးနေသောကြောင့် Stock ထည့်သွင်းခွင့်ကို ပိတ်ထားပါသည်။")
        return

    if len(context.args) < 2 or not context.args[0].isdigit():
        await render_interactive_add_stock_select(update, user.id, store_id)
        return

    product_id = int(context.args[0])
    item_data = " ".join(context.args[1:])

    product = database.get_product(product_id)
    if not product:
        await update.message.reply_text(f"❌ Product ID `{product_id}` ကို ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    if not is_super_admin(user.id) and product.get("store_id") and product["store_id"] != store_id:
        await update.message.reply_text("⛔ ဤ Product သည် သင့်ဆိုင်ပိုင် မဟုတ်ပါ။")
        return

    database.add_stock(product_id, item_data, store_id=store_id)
    updated_prod = database.get_product(product_id)
    stock_count = updated_prod["stock_count"] if updated_prod else 1

    await update.message.reply_text(
        f"✅ *Stock အသစ် ထည့်သွင်းပြီးပါပြီ!*\n\n"
        f"🏪 *ဆိုင် -* {active_store['store_name']}\n"
        f"📦 *ပစ္စည်း -* {product['name']}\n"
        f"🔢 *လက်ရှိ Stock စုစုပေါင်း -* `{stock_count} ခု`\n"
        f"🔑 *ထည့်သွင်းလိုက်သော အချက်အလက် -*\n`{item_data}`",
        parse_mode="Markdown",
    )


async def edit_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Updates an existing stock item's credentials: /editstock <stock_id> <new_mail_pass>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            "အသုံးပြုပုံ -\n"
            "`/editstock <stock_id> <အချက်အလက်သစ်>`\n\n"
            "ဥပမာ -\n"
            "`/editstock 1 Email - updated@mail.com | Pass - Secret#2026`",
            parse_mode="Markdown",
        )
        return

    stock_id = int(context.args[0])
    new_data = " ".join(context.args[1:])

    stock_item = database.get_stock_by_id(stock_id)
    if not stock_item:
        await update.message.reply_text(f"❌ Stock ID `#{stock_id}` ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    if not is_super_admin(user.id) and stock_item.get("store_id") and stock_item["store_id"] != store_id:
        await update.message.reply_text("⛔ ဤ Stock သည် သင့်ဆိုင်ပိုင် မဟုတ်ပါ။")
        return

    database.update_stock_data(stock_id, new_data)

    await update.message.reply_text(
        f"✅ *Stock Credentials ပြင်ဆင်ပြီးပါပြီ!*\n\n"
        f"🔢 *Stock ID -* `#{stock_id}`\n"
        f"📦 *Product -* {stock_item['product_name']}\n\n"
        f"🔑 *အချက်အလက်သစ် (Updated Mail/Pass) -*\n"
        f"```\n{new_data}\n```",
        parse_mode="Markdown",
    )


async def delete_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deletes an unsold stock item: /deletestock <stock_id>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/deletestock <stock_id>`\nဥပမာ - `/deletestock 1`", parse_mode="Markdown")
        return

    stock_id = int(context.args[0])
    stock_item = database.get_stock_by_id(stock_id)
    if not stock_item:
        await update.message.reply_text(f"❌ Stock ID `#{stock_id}` ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    if not is_super_admin(user.id) and stock_item.get("store_id") and stock_item["store_id"] != store_id:
        await update.message.reply_text("⛔ ဤ Stock သည် သင့်ဆိုင်ပိုင် မဟုတ်ပါ။")
        return

    database.delete_stock(stock_id)
    await update.message.reply_text(
        f"🗑️ *Stock ဖျက်ခြင်း ပြီးမြောက်ပါပြီ!*\n\n"
        f"Stock ID `#{stock_id}` ({stock_item['product_name']}) ကို စာရင်းထဲမှ ဖျက်လိုက်ပါပြီ။",
        parse_mode="Markdown",
    )


async def edit_price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Updates product price: /editprice <product_id> <new_price>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/editprice <product_id> <new_price>`\nဥပမာ - `/editprice 1 9000`", parse_mode="Markdown")
        return

    product_id = int(context.args[0])
    new_price = int(context.args[1])

    product = database.get_product(product_id)
    if not product:
        await update.message.reply_text(f"❌ Product ID `{product_id}` ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    if not is_super_admin(user.id) and product.get("store_id") and product["store_id"] != store_id:
        await update.message.reply_text("⛔ ဤ Product သည် သင့်ဆိုင်ပိုင် မဟုတ်ပါ။")
        return

    old_price = product["price"]
    database.update_product_price(product_id, new_price)

    await update.message.reply_text(
        f"✅ *စျေးနှုန်း ပြင်ဆင်ပြီးပါပြီ!*\n\n"
        f"🏪 *ဆိုင် -* {active_store['store_name']}\n"
        f"📦 *ပစ္စည်း -* {product['name']}\n"
        f"💵 *စျေးနှုန်းဟောင်း -* `{old_price:,} MMK`\n"
        f"💰 *စျေးနှုန်းအသစ် -* `{new_price:,} MMK`",
        parse_mode="Markdown",
    )


async def edit_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Updates product name: /editname <product_id> <new_name>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/editname <product_id> <new_name>`\nဥပမာ - `/editname 1 Netflix 4K UHD (1 Month)`", parse_mode="Markdown")
        return

    product_id = int(context.args[0])
    new_name = " ".join(context.args[1:])

    product = database.get_product(product_id)
    if not product:
        await update.message.reply_text(f"❌ Product ID `{product_id}` ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    if not is_super_admin(user.id) and product.get("store_id") and product["store_id"] != store_id:
        await update.message.reply_text("⛔ ဤ Product သည် သင့်ဆိုင်ပိုင် မဟုတ်ပါ။")
        return

    old_name = product["name"]
    database.update_product_name(product_id, new_name)

    await update.message.reply_text(
        f"✅ *ပစ္စည်းအမည် ပြင်ဆင်ပြီးပါပြီ!*\n\n"
        f"• အမည်ဟောင်း - {old_name}\n"
        f"• အမည်သစ် - *{new_name}*",
        parse_mode="Markdown",
    )


async def delete_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deletes a product: /deleteproduct <product_id>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/deleteproduct <product_id>`\nဥပမာ - `/deleteproduct 1`", parse_mode="Markdown")
        return

    product_id = int(context.args[0])
    product = database.get_product(product_id)
    if not product:
        await update.message.reply_text(f"❌ Product ID `{product_id}` ရှာမတွေ့ပါ။", parse_mode="Markdown")
        return

    if not is_super_admin(user.id) and product.get("store_id") and product["store_id"] != store_id:
        await update.message.reply_text("⛔ ဤ Product သည် သင့်ဆိုင်ပိုင် မဟုတ်ပါ။")
        return

    database.delete_product(product_id)
    await update.message.reply_text(
        f"🗑️ *Product ဖျက်ခြင်း ပြီးမြောက်ပါပြီ!*\n\n"
        f"*{product['name']}* (ID - `{product_id}`) ကို စာရင်းထဲမှ အောင်မြင်စွာ ဖျက်လိုက်ပါပြီ။",
        parse_mode="Markdown",
    )


async def add_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a new product: /addproduct [name] | [price] | [category] | [description] (Interactive prompt fallback)"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    active_store = get_admin_active_store(user.id)
    store_id = active_store["id"]

    if not is_admin_access_allowed(user.id, store_id=store_id):
        await update.message.reply_text("⛔ သင့်ဆိုင်၏ SaaS License သက်တမ်းကုန်ဆုံးနေသောကြောင့် Product ထည့်သွင်းခွင့်ကို ပိတ်ထားပါသည်။")
        return

    full_text = " ".join(context.args).strip()
    if not full_text:
        await start_interactive_add_product(update, user.id, context)
        return

    parts = [p.strip() for p in full_text.split("|")]
    if len(parts) < 4 or not parts[1].isdigit():
        await start_interactive_add_product(update, user.id, context)
        return

    name = parts[0]
    price = int(parts[1])
    category = parts[2]
    desc = parts[3]

    new_id = database.add_product(name, desc, price, category, store_id=store_id)
    await update.message.reply_text(
        f"✅ *Product အသစ် ထည့်သွင်းပြီးပါပြီ!*\n\n"
        f"🏪 *ဆိုင် -* {active_store['store_name']} (Store #{store_id})\n"
        f"🆔 ID - `{new_id}`\n"
        f"📦 အမည် - *{name}*\n"
        f"💰 စျေးနှုန်း - `{price:,} MMK`\n"
        f"🏷️ အုပ်စု - {category}\n"
        f"📝 အသေးစိတ် - {desc}\n\n"
        f"💡 Stock ထည့်ရန် - `/addstock {new_id} <credentials>`",
        parse_mode="Markdown",
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcasts a message to all registered users: /broadcast <text>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ - `/broadcast <ပေးပို့လိုသော ကြေညာချက်>`", parse_mode="Markdown")
        return

    broadcast_text = " ".join(context.args)
    users = database.get_all_users()
    total = len(users)
    success = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📢 စာပို့ခြင်း စတင်နေပါသည်... (စုစုပေါင်း {total} ဦး)")

    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u["user_id"],
                text=f"📢 *[{STORE_NAME} - အသိပေးကြေညာချက်]*\n\n{broadcast_text}",
                parse_mode="Markdown",
            )
            success += 1
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"✅ *Broadcast ပေးပို့ပြီးပါပြီ!*\n\n"
        f"• အောင်မြင် - `{success} ဦး`\n"
        f"• မအောင်မြင် - `{failed} ဦး`\n"
        f"• စုစုပေါင်း - `{total} ဦး`",
        parse_mode="Markdown",
    )


async def check_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checks details of a user: /checkuser <user_id>"""
    user = update.effective_user
    if not user or not is_admin_user(user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("အသုံးပြုပုံ - `/checkuser <user_id>`", parse_mode="Markdown")
        return

    target_id = int(context.args[0])
    target_user = database.get_user(target_id)
    if not target_user:
        await update.message.reply_text("❌ အသုံးပြုသူ ရှာမတွေ့ပါ။")
        return

    orders = database.get_user_orders(target_id, limit=5)
    order_info = f"{len(orders)} ခု" if orders else "မရှိသေးပါ"

    await update.message.reply_text(
        f"👤 *အသုံးပြုသူ အချက်အလက် -*\n\n"
        f"• ID - `{target_user['user_id']}`\n"
        f"• အမည် - {target_user['first_name']}\n"
        f"• Username - @{target_user['username'] or 'N/A'}\n"
        f"• လက်ကျန်ငွေ - `{target_user['balance']:,} MMK`\n"
        f"• စတင်ရက် - {str(target_user['created_at'])[:10]}\n"
        f"• ဝယ်ယူခဲ့မှု - {order_info}",
        parse_mode="Markdown",
    )


# ==============================================================================
# Text Message & Reply Keyboard Router
# ==============================================================================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Routes reply keyboard button clicks or general text messages in Myanmar & English."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user = update.effective_user
    lang = get_user_lang(user.id) if user else "my"

    # SaaS License Check (Super Admins are exempted)
    if user:
        store_id = get_effective_store_id(update, context)
        allowed, lock_text, lock_markup = check_license_access(user.id, store_id=store_id)
        if not allowed:
            await update.message.reply_text(lock_text, reply_markup=lock_markup, parse_mode="Markdown")
            return

    # User Onboarding: Store Name Input for SaaS Bot Purchase Flow
    if user and context.user_data.get("saas_state") == "waiting_saas_store_name":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("saas_state", None)
            context.user_data.pop("saas_store_name", None)
            context.user_data.pop("saas_plan", None)
            await update.message.reply_text("❌ စတိုးဆိုင် Bot လျှောက်ထားမှုကို ပယ်ဖျက်လိုက်ပါပြီ။")
            return

        store_name_input = text.strip()
        context.user_data["saas_store_name"] = store_name_input
        context.user_data["saas_state"] = "waiting_saas_slip"

        prompt_slip = (
            f"📸 *[ငွေလွှဲပြေစာ (Payment Slip) ပေးပို့ပါ]*\n\n"
            f"🏪 *လျှောက်ထားသော ဆိုင်အမည် -* `{store_name_input}`\n"
            f"👤 *လျှောက်ထားသူ User ID -* `{user.id}`\n\n"
            f"💳 *ငွေပေးချေမှု အကောင့်များ -*\n"
            f"• KPay: `{KPAY_PHONE}` ({KPAY_NAME})\n"
            f"• WavePay: `{WPAY_PHONE}` ({WPAY_NAME})\n"
            f"• Thai Bank: Kasikorn Bank (KBank) - `123-4-56789-0`\n"
            f"• PromptPay: `{KPAY_PHONE}`\n\n"
            f"👉 လစဉ်ကြေး ငွေလွှဲပြီးပါက ပြေစာ (Slip) ဓာတ်ပုံကို ဤနေရာတွင် ပေးပို့ပေးပါ (Photo upload ပြုလုပ်ပါ)။\n"
            f"Super Admin ([Peter](https://t.me/latt_swal2026)) ထံသို့ တိုက်ရိုက် ရောက်ရှိသွားမည် ဖြစ်ပြီး အတည်ပြုပြီးပါက အကြောင်းကြားပေးပါမည်!\n\n"
            f"_(ပယ်ဖျက်လိုပါက /cancel ဟု ရိုက်ပို့ပါ)_"
        )
        await update.message.reply_text(prompt_slip, parse_mode="Markdown")
        return

    # Admin interactive exchange rate input check
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_exchange_rate"):
        curr_state = context.user_data.get("admin_state", "")
        if curr_state.startswith("waiting_exchange_rate_"):
            target_store_id = int(curr_state.replace("waiting_exchange_rate_", ""))
        else:
            target_store_id = get_admin_active_store(user.id)["id"]

        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ငွေလဲနှုန်း ပြင်ဆင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        cleaned_text = text.strip().replace("K", "").replace("k", "").replace("MMK", "").replace("mmk", "").strip()
        if cleaned_text.isdigit():
            new_rate = int(cleaned_text)
            if new_rate > 0:
                database.set_exchange_rate(new_rate, store_id=target_store_id)
                context.user_data.pop("admin_state", None)
                s_name = database.get_store_name(store_id=target_store_id)
                rate_text, markup = get_admin_rate_view(new_rate, store_name=s_name)
                success_msg = (
                    f"✅ *[{s_name} - THB ငွေလဲနှုန်း အောင်မြင်စွာ ပြောင်းလဲပြီးပါပြီ]*\n\n"
                    f"👉 *`1 THB = {new_rate:,} MMK`*\n\n"
                    f"• 100 THB = `{100 * new_rate:,} MMK`\n"
                    f"• 300 THB = `{300 * new_rate:,} MMK`\n"
                    f"• 500 THB = `{500 * new_rate:,} MMK`\n"
                    f"• 1,000 THB = `{1000 * new_rate:,} MMK`\n\n"
                    f"ဆိုင်ရှိ ငွေဖြည့် Menu နှင့် စနစ်တွက်ချက်မှုများတွင် ချက်ချင်း update ဖြစ်သွားပါပြီ။"
                )
                await update.message.reply_text(success_msg, reply_markup=markup, parse_mode="Markdown")
                return
            else:
                await update.message.reply_text("❌ ငွေလဲနှုန်းသည် 0 ထက် ကြီးရပါမည်။ ကျေးဇူးပြု၍ ဂဏန်းအမှန် ပြန်ရိုက်ပေးပါ (ဥပမာ - `135` သို့မဟုတ် `136`) -")
                return
        else:
            await update.message.reply_text(
                "❌ ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ (ဥပမာ - `135` သို့မဟုတ် `136`) -\n"
                "_မပြောင်းလိုပါက /cancel ကို နှိပ်ပါ_"
            )
            return

    # Admin interactive custom top-up slip amount input check
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_custom_slip_"):
        target_user_id = context.user_data.get("custom_slip_target_user")
        slip_msg_id = context.user_data.get("custom_slip_msg_id")
        slip_chat_id = context.user_data.get("custom_slip_chat_id")

        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("custom_slip_target_user", None)
            context.user_data.pop("custom_slip_msg_id", None)
            context.user_data.pop("custom_slip_chat_id", None)
            await update.message.reply_text("❌ စိတ်ကြိုက်ငွေဖြည့်သွင်းမှုကို ပယ်ဖျက်လိုက်ပါပြီ။")
            return

        cleaned_amount = text.strip().replace(",", "").replace("K", "").replace("k", "").replace("MMK", "").replace("mmk", "").strip()
        if cleaned_amount.isdigit():
            amount = int(cleaned_amount)
            if amount > 0:
                new_bal = database.add_user_balance(target_user_id, amount)
                admin_name = user.first_name or "Admin"
                time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Clear admin state
                context.user_data.pop("admin_state", None)
                context.user_data.pop("custom_slip_target_user", None)
                context.user_data.pop("custom_slip_msg_id", None)
                context.user_data.pop("custom_slip_chat_id", None)

                # 1. Update the original slip photo caption if possible
                if slip_msg_id and slip_chat_id:
                    try:
                        new_caption = (
                            f"✅ *[စိတ်ကြိုက် ငွေဖြည့်သွင်းမှု အတည်ပြုပြီးပါပြီ / Approved]*\n\n"
                            f"• User ID - `{target_user_id}` ထံသို့ `+{amount:,} MMK` ထည့်သွင်းပေးပြီးပါပြီ!\n"
                            f"• ယခု Balance - `{new_bal:,} MMK`\n"
                            f"• အတည်ပြုသူ Admin - {admin_name}\n"
                            f"• အချိန် - {time_now}"
                        )
                        await context.bot.edit_message_caption(
                            chat_id=slip_chat_id,
                            message_id=slip_msg_id,
                            caption=new_caption,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Could not update original slip caption: {e}")

                # 2. Reply confirmation to the admin
                await update.message.reply_text(
                    f"✅ *ငွေဖြည့်သွင်းမှု အောင်မြင်ပါသည်!*\n\n"
                    f"• User ID - `{target_user_id}`\n"
                    f"• ထည့်သွင်းငွေ - `+{amount:,} MMK`\n"
                    f"• အသစ် Balance - `{new_bal:,} MMK`\n"
                    f"• အချိန် - {time_now}\n\n"
                    f"User ထံသို့လည်း အတည်ပြုချက် အသိပေးစာ ပေးပို့ပြီးပါပြီ။",
                    parse_mode="Markdown"
                )

                # 3. Notify the user directly
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=(
                            f"🎉 *[ငွေဖြည့်သွင်းမှု အတည်ပြုပြီးပါပြီ]*\n\n"
                            f"Admin မှ သင်၏ ငွေလွှဲပြေစာကို စစ်ဆေးအတည်ပြုပြီး အကောင့်ထဲသို့ `+{amount:,} MMK` ထည့်သွင်းပေးလိုက်ပါပြီ!\n"
                            f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{new_bal:,} MMK`\n\n"
                            f"ကျေးဇူးတင်ပါသည်! ပစ္စည်းများကို ချက်ချင်း စျေးဝယ်ယူနိုင်ပါပြီ။"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning(f"Could not notify user {target_user_id} - {e}")
                return
            else:
                await update.message.reply_text("❌ ငွေပမာဏသည် 0 ထက် ကြီးရပါမည်။ ကျေးဇူးပြု၍ ဂဏန်းအမှန် ပြန်ရိုက်ပေးပါ (ဥပမာ - `15000`) -")
                return
        else:
            await update.message.reply_text(
                "❌ ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ (ဥပမာ - `15000` သို့မဟုတ် `7500`) -\n"
                "_မပြောင်းလိုပါက /cancel ကို နှိပ်ပါ_"
            )
            return

    # Admin interactive User ID search for Balance Management
    if user and is_admin_user(user.id) and context.user_data.get("admin_state") == "waiting_bal_search_uid":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ User ID ရှာဖွေခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        cleaned_uid = text.strip()
        if cleaned_uid.isdigit():
            target_id = int(cleaned_uid)
            context.user_data.pop("admin_state", None)
            await render_user_balance_view(update, target_id, user.id, context)
            return
        else:
            await update.message.reply_text(
                "❌ ဂဏန်းသီးသန့် User ID ကိုသာ ရိုက်ထည့်ပေးပါ (ဥပမာ - `12345678`) -\n"
                "_မလုပ်ဆောင်တော့ပါက /cancel ကို နှိပ်ပါ_"
            )
            return

    # Admin interactive balance direct edit (set balance)
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_edit_bal_"):
        curr_state = context.user_data.get("admin_state", "")
        target_id = int(curr_state.replace("waiting_edit_bal_", ""))
        store_id = get_effective_store_id(update, context)

        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ Balance ပြင်ဆင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_user_balance_view(update, target_id, user.id, context)
            return

        cleaned = text.strip().replace(",", "").replace("K", "").replace("k", "").replace("MMK", "").replace("mmk", "").strip()
        if cleaned.isdigit():
            new_bal = int(cleaned)
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
            if not can_manage:
                context.user_data.pop("admin_state", None)
                await update.message.reply_text(err_msg, parse_mode="Markdown")
                return

            updated = database.set_user_balance(target_id, new_bal)
            context.user_data.pop("admin_state", None)
            s_name = database.get_store_name(store_id=store_id)

            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🔔 *[{s_name} - Wallet Balance ပြင်ဆင်သတ်မှတ်ခြင်း]*\n\n"
                         f"Admin မှ သင့် Wallet Balance ကို `{updated:,} MMK` အဖြစ် ပြင်ဆင်သတ်မှတ်လိုက်ပါသည်။\n"
                         f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{updated:,} MMK`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_id} - {e}")

            await render_user_balance_view(
                update,
                target_id,
                user.id,
                context,
                notice=f"✅ User ID `{target_id}` ၏ Balance ကို `{updated:,} MMK` သို့ အောင်မြင်စွာ ပြင်ဆင်သတ်မှတ်ပြီးပါပြီ!"
            )
            return
        else:
            await update.message.reply_text(
                "❌ ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ (ဥပမာ - `50000` သို့မဟုတ် `0`) -\n"
                "_မပြင်ဆင်လိုပါက /cancel ကို နှိပ်ပါ_"
            )
            return

    # Admin interactive add balance (+)
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_add_bal_"):
        curr_state = context.user_data.get("admin_state", "")
        target_id = int(curr_state.replace("waiting_add_bal_", ""))
        store_id = get_effective_store_id(update, context)

        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ငွေဖြည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_user_balance_view(update, target_id, user.id, context)
            return

        cleaned = text.strip().replace(",", "").replace("K", "").replace("k", "").replace("MMK", "").replace("mmk", "").strip()
        if cleaned.isdigit() and int(cleaned) > 0:
            amount = int(cleaned)
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
            if not can_manage:
                context.user_data.pop("admin_state", None)
                await update.message.reply_text(err_msg, parse_mode="Markdown")
                return

            updated = database.add_user_balance(target_id, amount)
            context.user_data.pop("admin_state", None)
            s_name = database.get_store_name(store_id=store_id)

            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🔔 *[{s_name} - ငွေဖြည့်သွင်းမှု အတည်ပြုချက်]*\n\n"
                         f"Admin မှ သင့်အကောင့်ထဲသို့ `+{amount:,} MMK` ထည့်သွင်းပေးလိုက်ပါပြီ!\n"
                         f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{updated:,} MMK`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_id} - {e}")

            await render_user_balance_view(
                update,
                target_id,
                user.id,
                context,
                notice=f"✅ User ID `{target_id}` ထံ `+{amount:,} MMK` ထည့်သွင်းပြီးပါပြီ!\n(ယခုလက်ကျန်ငွေ: `{updated:,} MMK`)"
            )
            return
        else:
            await update.message.reply_text(
                "❌ 0 ထက်ကြီးသော ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ (ဥပမာ - `10000`) -\n"
                "_မဖြည့်လိုပါက /cancel ကို နှိပ်ပါ_"
            )
            return

    # Admin interactive sub balance (-)
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_sub_bal_"):
        curr_state = context.user_data.get("admin_state", "")
        target_id = int(curr_state.replace("waiting_sub_bal_", ""))
        store_id = get_effective_store_id(update, context)

        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ငွေနှုတ်ယူခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_user_balance_view(update, target_id, user.id, context)
            return

        cleaned = text.strip().replace(",", "").replace("K", "").replace("k", "").replace("MMK", "").replace("mmk", "").strip()
        if cleaned.isdigit() and int(cleaned) > 0:
            amount = int(cleaned)
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, store_id)
            if not can_manage:
                context.user_data.pop("admin_state", None)
                await update.message.reply_text(err_msg, parse_mode="Markdown")
                return

            updated = database.deduct_user_balance(target_id, amount)
            context.user_data.pop("admin_state", None)
            s_name = database.get_store_name(store_id=store_id)

            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🔔 *[{s_name} - Wallet Balance နှုတ်ယူခြင်း]*\n\n"
                         f"Admin မှ သင့် Wallet ထဲမှ `-{amount:,} MMK` နှုတ်ယူလိုက်ပါသည်။\n"
                         f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{updated:,} MMK`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_id} - {e}")

            await render_user_balance_view(
                update,
                target_id,
                user.id,
                context,
                notice=f"✅ User ID `{target_id}` ထံမှ `-{amount:,} MMK` နှုတ်ယူပြီးပါပြီ!\n(ယခုလက်ကျန်ငွေ: `{updated:,} MMK`)"
            )
            return
        else:
            await update.message.reply_text(
                "❌ 0 ထက်ကြီးသော ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ (ဥပမာ - `5000`) -\n"
                "_မနှုတ်လိုပါက /cancel ကို နှိပ်ပါ_"
            )
            return

    # Store Admin interactive bot token input
    if user and is_admin_user(user.id) and context.user_data.get("admin_state") == "waiting_store_admin_token":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ Bot Token ချိတ်ဆက်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_store_admin_license_view(update, user.id)
            return

        store = database.get_store_by_owner(user.id)
        if not store:
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ သင့်အကောင့်နှင့် ချိတ်ဆက်ထားသော စတိုးဆိုင် ရှာမတွေ့ပါ။ Super Admin ထံ ဆက်သွယ်ပါ။")
            return

        token_input = clean_bot_token(text)
        status_msg = await update.message.reply_text("🔄 Bot Token ကို စစ်ဆေးနေပါသည်...")
        ok, err_msg, info = await validate_and_bind_bot_token(token_input, store["id"], client_bot_manager)
        if not ok:
            await status_msg.edit_text(
                f"{err_msg}\n\nပြန်လည်စစ်ဆေးပြီး Token ကို ပြန်ရိုက်ပို့ပါ (သို့မဟုတ် /cancel) -",
                parse_mode="Markdown"
            )
            return

        context.user_data.pop("admin_state", None)
        bot_uname = info["bot_username"]
        await status_msg.edit_text(
            f"🎉 *[ဂုဏ်ယူပါသည်! သင့်စတိုးဆိုင် Bot ချိတ်ဆက်မှု အောင်မြင်ပါသည်]*\n\n"
            f"🏪 *ဆိုင်အမည် -* {store['store_name']}\n"
            f"🤖 *Bot Username -* {bot_uname}\n"
            f"📛 *Bot Name -* {info['bot_first_name']}\n\n"
            f"🚀 ယခုအခါ Customer များသည် {bot_uname} သို့ တိုက်ရိုက်ဝင်ရောက်၍ သင့်ဆိုင်၏ ပစ္စည်းများကို သီးသန့် ဝယ်ယူနိုင်ပါပြီ!\n\n"
            f"👑 ထို Bot ထဲတွင်လည်း /admin ဖြင့် Admin Control Panel ကို တိုက်ရိုက် အသုံးပြုနိုင်ပါသည်။",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🤖 {bot_uname} သို့ သွားမည်", url=f"https://t.me/{bot_uname[1:]}")],
                [InlineKeyboardButton("🔙 SaaS License သို့", callback_data="admin_license_info")],
            ])
        )
        try:
            await context.bot.send_message(
                chat_id=1920334929,
                text=(
                    f"📢 *[Store Admin Bot Token ချိတ်ဆက်မှု အသိပေးချက်]*\n\n"
                    f"🏪 *Store #{store['id']} -* {store['store_name']}\n"
                    f"👤 *Admin ID -* `{user.id}` ({user.first_name})\n"
                    f"🤖 *Linked Bot -* {bot_uname}\n"
                    f"⚡ Polling status: Active"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Failed to notify super admin: {e}")
        return

    # Super Admin interactive bot token input for store
    if user and is_super_admin(user.id) and context.user_data.get("admin_state", "").startswith("waiting_store_token_"):
        s_id = int(context.user_data.get("admin_state").replace("waiting_store_token_", ""))
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_store_detail_manage_view(update, s_id)
            return

        token_input = clean_bot_token(text)
        status_msg = await update.message.reply_text(f"🔄 Store #{s_id} အတွက် Token စစ်ဆေးနေပါသည်...")
        ok, err_msg, info = await validate_and_bind_bot_token(token_input, s_id, client_bot_manager)
        if not ok:
            await status_msg.edit_text(f"{err_msg}\n\nပြန်လည်ရိုက်ပို့ပါ (သို့မဟုတ် /cancel) -", parse_mode="Markdown")
            return

        context.user_data.pop("admin_state", None)
        bot_uname = info["bot_username"]
        store = database.get_store(s_id)
        s_name = store["store_name"] if store else f"Store #{s_id}"
        await status_msg.edit_text(
            f"✅ *[Store #{s_id} Bot Token ချိတ်ဆက်မှု အောင်မြင်ပါသည်]*\n\n"
            f"🏪 *ဆိုင် -* {s_name}\n"
            f"🤖 *Bot -* {bot_uname}\n"
            f"⚡ Polling Launched Successfully!",
            parse_mode="Markdown"
        )
        await render_store_detail_manage_view(update, s_id)
        return

    # Super Admin interactive store name edit
    if user and is_super_admin(user.id) and context.user_data.get("admin_state", "").startswith("waiting_store_name_"):
        s_id = int(context.user_data.get("admin_state").replace("waiting_store_name_", ""))
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_store_detail_manage_view(update, s_id)
            return

        new_name = text.strip()
        database.set_store_name(new_name, store_id=s_id)
        context.user_data.pop("admin_state", None)
        await update.message.reply_text(f"✅ Store #{s_id} ၏ အမည်ကို *{new_name}* သို့ ပြောင်းလဲလိုက်ပါပြီ!", parse_mode="Markdown")
        await render_store_detail_manage_view(update, s_id)
        return

    # Super Admin interactive add store
    if user and is_super_admin(user.id) and context.user_data.get("admin_state") == "waiting_super_add_store":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ဆိုင်ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_super_admin_stores_view(update, user.id)
            return

        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2 or not parts[0].isdigit():
            await update.message.reply_text(
                "❌ Format မမှန်ကန်ပါ။ ကျေးဇူးပြု၍ အောက်ပါ Format အတိုင်း ရိုက်ပို့ပေးပါ -\n"
                "`<Owner_Telegram_ID> | <Store_Name> | <Days>`\n\n"
                "ဥပမာ -\n"
                "`987654321 | Ko Aung Game Shop | 30`",
                parse_mode="Markdown"
            )
            return

        owner_id = int(parts[0])
        store_name = parts[1]
        days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30

        new_s_id = database.create_store(owner_id=owner_id, store_name=store_name, days=days)
        context.user_data.pop("admin_state", None)
        await update.message.reply_text(
            f"✅ *[Client ဆိုင်အသစ် ထည့်သွင်းပြီးပါပြီ]*\n\n"
            f"🏪 *Store ID -* `#{new_s_id}`\n"
            f"📛 *ဆိုင်အမည် -* {store_name}\n"
            f"👤 *Owner ID -* `{owner_id}`\n"
            f"📅 *ရက်ပေါင်း -* `{days} ရက်`\n\n"
            f"အောက်ပါ Panel မှတစ်ဆင့် Bot Token ချိတ်ဆက်နိုင်ပါပြီ -",
            parse_mode="Markdown"
        )
        await render_store_detail_manage_view(update, new_s_id)
        return

    # Admin interactive top-up instructions text edit
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_store_topup_text_"):
        s_id = int(context.user_data.get("admin_state").replace("waiting_store_topup_text_", ""))
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ငွေဖြည့်စာသား ပြင်ဆင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_admin_payment_settings_view(update, s_id)
            return

        new_instructions = text.strip()
        database.set_store_topup_instructions(s_id, new_instructions)
        context.user_data.pop("admin_state", None)
        s_name = database.get_store_name(store_id=s_id)
        await update.message.reply_text(
            f"✅ *[{s_name} - ငွေဖြည့်နည်း စာသား အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ]*\n\n"
            f"သင့် customer များ /topup နှိပ်သည့်အခါ ဤစိတ်ကြိုက် ညွှန်ကြားချက်ကို ပြသပေးမည် ဖြစ်သည်။",
            parse_mode="Markdown"
        )
        await render_admin_payment_settings_view(update, s_id)
        return

    # Admin interactive payment accounts update
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("waiting_store_accs_"):
        s_id = int(context.user_data.get("admin_state").replace("waiting_store_accs_", ""))
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ ငွေလွှဲအကောင့် ပြင်ဆင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await render_admin_payment_settings_view(update, s_id)
            return

        parts = [p.strip() for p in text.split("|")]
        if len(parts) >= 1 and parts[0]:
            database.set_store_setting(s_id, "kpay_phone", parts[0])
            database.set_store_setting(s_id, "promptpay_phone", parts[0])
        if len(parts) >= 2 and parts[1]:
            database.set_store_setting(s_id, "kpay_name", parts[1])
            database.set_store_setting(s_id, "promptpay_name", parts[1])
        if len(parts) >= 3 and parts[2]:
            database.set_store_setting(s_id, "wpay_phone", parts[2])
        if len(parts) >= 4 and parts[3]:
            database.set_store_setting(s_id, "wpay_name", parts[3])
        if len(parts) >= 5 and parts[4]:
            database.set_store_setting(s_id, "thai_bank_info", parts[4])

        context.user_data.pop("admin_state", None)
        s_name = database.get_store_name(store_id=s_id)
        await update.message.reply_text(
            f"✅ *[{s_name} - ငွေလွှဲအကောင့်များ အောင်မြင်စွာ ပြင်ဆင်ပြီးပါပြီ]*",
            parse_mode="Markdown"
        )
        await render_admin_payment_settings_view(update, s_id)
        return

    # Admin Interactive Product Creation Steps
    if user and is_admin_user(user.id) and context.user_data.get("admin_state") == "interactive_add_prod_name":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("interactive_prod_name", None)
            await update.message.reply_text("❌ Product ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        prod_name = text.strip()
        context.user_data["interactive_prod_name"] = prod_name
        context.user_data["admin_state"] = "interactive_add_prod_category"
        await render_interactive_category_select(update, prod_name)
        return

    if user and is_admin_user(user.id) and context.user_data.get("admin_state") == "interactive_add_prod_category":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("interactive_prod_name", None)
            context.user_data.pop("interactive_prod_category", None)
            await update.message.reply_text("❌ Product ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        cat = text.strip()
        context.user_data["interactive_prod_category"] = cat
        prod_name = context.user_data.get("interactive_prod_name", "Product")
        context.user_data["admin_state"] = "interactive_add_prod_price"
        await render_interactive_price_prompt(update, prod_name, cat)
        return

    if user and is_admin_user(user.id) and context.user_data.get("admin_state") == "interactive_add_prod_price":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("interactive_prod_name", None)
            context.user_data.pop("interactive_prod_category", None)
            context.user_data.pop("interactive_prod_price", None)
            await update.message.reply_text("❌ Product ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        digits = re.sub(r"[^\d]", "", text)
        if not digits or int(digits) <= 0:
            await update.message.reply_text("❌ စျေးနှုန်းကို ဂဏန်းသီးသန့် ရိုက်ထည့်ပေးပါ (ဥပမာ - `4500` သို့မဟုတ် `12000`) -\n_(မထည့်တော့ပါက /cancel ကို နှိပ်ပါ)_", parse_mode="Markdown")
            return

        price = int(digits)
        context.user_data["interactive_prod_price"] = price
        context.user_data["admin_state"] = "interactive_add_prod_desc"
        prod_name = context.user_data.get("interactive_prod_name", "Product")
        cat = context.user_data.get("interactive_prod_category", "General")
        await render_interactive_desc_prompt(update, prod_name, cat, price)
        return

    if user and is_admin_user(user.id) and context.user_data.get("admin_state") == "interactive_add_prod_desc":
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("interactive_prod_name", None)
            context.user_data.pop("interactive_prod_category", None)
            context.user_data.pop("interactive_prod_price", None)
            context.user_data.pop("interactive_prod_desc", None)
            await update.message.reply_text("❌ Product ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        desc = "" if text.strip().lower() == "skip" else text.strip()
        prod_name = context.user_data.get("interactive_prod_name", "Product")
        cat = context.user_data.get("interactive_prod_category", "General")
        price = context.user_data.get("interactive_prod_price", 0)

        active_store = get_admin_active_store(user.id)
        store_id = active_store["id"]
        new_id = database.add_product(prod_name, desc, price, cat, store_id=store_id)

        context.user_data.pop("admin_state", None)
        context.user_data.pop("interactive_prod_name", None)
        context.user_data.pop("interactive_prod_category", None)
        context.user_data.pop("interactive_prod_price", None)
        context.user_data.pop("interactive_prod_desc", None)

        success_text = (
            f"🎉 *[Product အသစ် အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီ]*\n\n"
            f"🏪 *ဆိုင် -* {active_store['store_name']}\n"
            f"🆔 *Product ID -* `#{new_id}`\n"
            f"📦 *အမည် -* *{prod_name}*\n"
            f"💰 *စျေးနှုန်း -* `{price:,} MMK`\n"
            f"🏷️ *အုပ်စု -* `{cat}`\n"
            f"📝 *အသေးစိတ် -* {desc or 'မရှိပါ'}\n\n"
            "💡 _ယခု Product အတွက် Customer များ ချက်ချင်း ဝယ်ယူနိုင်ရန် အောက်ပါ ခလုတ်ကို နှိပ်၍ Stock (Email/Pass/Link) ထည့်သွင်းနိုင်ပါပြီ -_"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 ဤ Product အတွက် Stock ချက်ချင်းထည့်မည်", callback_data=f"adm_istk_sel_{new_id}")],
            [InlineKeyboardButton("➕ နောက်ထပ် Product တစ်ခု ထပ်ထည့်မည်", callback_data="admin_interactive_add_product")],
            [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
        ])
        await update.message.reply_text(success_text, reply_markup=markup, parse_mode="Markdown")
        return

    # Admin Interactive Stock Addition (Single line or Multi-line input)
    if user and is_admin_user(user.id) and context.user_data.get("admin_state", "").startswith("interactive_add_stock_"):
        if text.startswith("/cancel") or text.lower() == "cancel":
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ Stock ထည့်သွင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
            await admin_panel_command(update, context)
            return

        prod_id = int(context.user_data.get("admin_state").replace("interactive_add_stock_", ""))
        product = database.get_product(prod_id)
        if not product:
            context.user_data.pop("admin_state", None)
            await update.message.reply_text("❌ Product ရှာမတွေ့ပါ။")
            return

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            await update.message.reply_text("❌ ကျေးဇူးပြု၍ ထည့်သွင်းလိုသော Account / Password / Key ကို ရိုက်ပို့ပေးပါ -")
            return

        active_store = get_admin_active_store(user.id)
        store_id = active_store["id"]

        added_cnt = 0
        for line in lines:
            database.add_stock(prod_id, line, store_id=store_id)
            added_cnt += 1

        context.user_data.pop("admin_state", None)
        updated_prod = database.get_product(prod_id)
        new_total = updated_prod["stock_count"] if updated_prod else added_cnt

        success_text = (
            f"✅ *[Stock ထည့်သွင်းခြင်း အောင်မြင်ပါသည်]*\n\n"
            f"🏪 *ဆိုင် -* {active_store['store_name']}\n"
            f"📦 *ပစ္စည်း -* *{product['name']}*\n"
            f"📥 *ယခု ထည့်သွင်းခဲ့သော အရေအတွက် -* `+{added_cnt} ခု`\n"
            f"🔢 *လက်ကျန် Stock စုစုပေါင်း -* `{new_total} ခု`\n\n"
            "Customer များသည် ယခုပစ္စည်းကို စတင်ဝယ်ယူနိုင်ပါပြီ!"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 ဤ Product အတွက် ထပ်ထည့်မည်", callback_data=f"adm_istk_sel_{prod_id}")],
            [InlineKeyboardButton("📦 အခြား Product ရွေးမည်", callback_data="admin_interactive_add_stock")],
            [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
        ])
        await update.message.reply_text(success_text, reply_markup=markup, parse_mode="Markdown")
        return

    if text in ("🛒 ထုတ်ကုန်များ", "ထုတ်ကုန်များ", "🛒 Products", "Products", "🛒 ပစ္စည်းဝယ်ရန်", "ပစ္စည်းဝယ်ရန်", "🟩 🛒 ပစ္စည်းဝယ်ရန်"):
        await show_products(update, context)
    elif text in ("💳 ငွေဖြည့်ရန်", "ငွေဖြည့်ရန်", "💳 Top-up", "Top-up", "🟦 💳 ငွေဖြည့်ရန်"):
        await show_topup(update, context)
    elif text in ("👥 မိတ်ဆက်", "👥 Referral"):
        await show_referral(update, context)
    elif text in ("📜 မှတ်တမ်း", "📜 History"):
        await show_history(update, context)
    elif text in ("👤 Member Status", "Member Status", "member status", "🟦 👤 Member Status"):
        await show_member_status(update, context)
    elif text in ("📖 အသုံးပြုနည်း", "📖 User Guide"):
        await show_guide(update, context)
    elif text in ("🌐 ဘာသာစကား", "🌐 Language"):
        await show_language_menu(update, context)
    elif text in ("❓ အကူအညီ", "အကူအညီ", "❓ Support", "Support"):
        await show_help(update, context)
    elif text == "👑 Admin Panel":
        await admin_panel_command(update, context)
    else:
        is_adm = is_admin_user(user.id) if user else False
        hint_text = (
            f"📩 သင်ပို့သောစာ - \"{text}\"\n\nအောက်ပါ Menu ခလုတ်များကို အသုံးပြု၍ စျေးဝယ်ယူနိုင်ပါသည် -"
            if lang == "my"
            else f"📩 Received message - \"{text}\"\n\nPlease use the menu buttons below to navigate -"
        )
        await update.message.reply_text(
            hint_text,
            reply_markup=get_main_menu_keyboard(is_admin=is_adm, lang=lang),
        )


# ==============================================================================
# Inline Callback Query Handler
# ==============================================================================

async def callback_query_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handles all inline button clicks for catalog, purchases, topup approval, and admin panel."""
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    user = update.effective_user
    if not user:
        return

    # Check if user clicked refresh license check
    if data == "check_license_again":
        store_id = get_effective_store_id(update, context)
        lic = database.get_store_license(store_id)
        if lic["is_valid"]:
            try:
                await query.answer("✅ License သက်တမ်း အတည်ပြုပြီးပါပြီ!", show_alert=True)
            except Exception:
                pass
            await show_home(update, context)
        else:
            try:
                await query.answer("❌ License သက်တမ်း ကုန်ဆုံးနေဆဲဖြစ်ပါသည်။ Super Admin ထံ ဆက်သွယ်ပါ။", show_alert=True)
            except Exception:
                pass
        return

    # SaaS Bot Purchase Flow for New Users
    if data == "saas_buy_bot":
        buy_text = (
            "🤖 *[ကိုယ့်ပိုင်လုပ်ငန်းအတွက် Telegram Bot ဝယ်ယူအသုံးပြုရန်]*\n"
            "*(Start Your Own Automated Telegram Store)*\n\n"
            "သင်ကိုယ်တိုင် Game Topup၊ Digital Accounts (Netflix, YouTube, Spotify, VPN, Key/Codes) "
            "ရောင်းချသော လုပ်ငန်းတစ်ခုကို 24 နာရီ Auto ပို့ပေးသည့် Telegram Bot ဖြင့် စတင်လိုက်ပါ!\n\n"
            "⭐ *ပါဝင်သော ထူးခြားချက်များ (Key Features) -*\n"
            "• ⚡ *24/7 Auto Stock Delivery* - Account / Key များကို Customer ဝယ်ယူသည်နှင့် ချက်ချင်း Auto ပို့ပေးခြင်း\n"
            "• 💳 *စိတ်ကြိုက် ငွေပေးချေမှုစနစ်* - KPay, WavePay, Thai Bank QR, PromptPay စိတ်ကြိုက်ထည့်သွင်းနိုင်ခြင်း\n"
            "• 📦 *သီးသန့် စတိုးဆိုင်စီမံမှု (Multi-Tenant)* - မိမိဆိုင်၏ Product, Price, Stock များကို အခြားဆိုင်များနှင့် မရောဘဲ သီးသန့် စီမံခန့်ခွဲနိုင်ခြင်း\n"
            "• 📊 *လစဉ် အရောင်းစာရင်းဇယား (Analytics)* - လစဉ် ရောင်းအားနှင့် Order Count စာရင်းများ တိကျစွာ ထွက်ရှိခြင်း\n"
            "• 👥 *Referral & Slip Approval* - မိတ်ဆက်ဆုလာဘ်နှင့် ငွေလွှဲပြေစာ စစ်ဆေးအတည်ပြုမှု စနစ် အပြည့်အစုံ ပါဝင်ခြင်း\n\n"
            "💎 *လစဉ်ကြေး ဈေးနှုန်း အစီအစဉ်များ (Pricing Plans) -*\n"
            "1️⃣ 🌟 *Starter Plan - `15,000 MMK / လ`* (သို့မဟုတ် 120 THB / လ)\n"
            "   • Product ၁၀ မျိုး ထည့်သွင်းနိုင်ခြင်း\n"
            "   • Auto Stock Delivery စနစ်\n"
            "   • Admin Control Panel အပြည့်အစုံ\n\n"
            "2️⃣ 🚀 *Pro Business Plan - `25,000 MMK / လ`* (သို့မဟုတ် 200 THB / လ)\n"
            "   • Product အကန့်အသတ်မရှိ ထည့်သွင်းနိုင်ခြင်း\n"
            "   • Unlimited Stock ထည့်သွင်းနိုင်ခြင်း\n"
            "   • ကိုယ်ပိုင် Bot Token (@your_shop_bot) တိုက်ရိုက် ချိတ်ဆက်နိုင်ခြင်း\n"
            "   • 24/7 Priority Support\n\n"
            "📝 *ဘယ်လို စတင်အသုံးပြုရမလဲ (How it works) -*\n"
            "၁။ အောက်ပါ '💳 စလစ်ပို့၍ စတိုးဖွင့်မည်' ခလုတ်ကို နှိပ်ပါ\n"
            "၂။ မိမိဆိုင်အမည် (Store Name) ကို ရိုက်ပို့ပါ\n"
            "၃။ လစဉ်ကြေး ငွေလွှဲပြေစာ (Slip) ကို ဓာတ်ပုံ ပို့ပေးပါ\n"
            "၄။ Super Admin ([Peter](https://t.me/latt_swal2026)) မှ စစ်ဆေးအတည်ပြုပြီးသည်နှင့် သင့်အကောင့်အား Store Admin အဖြစ် ချက်ချင်း စတင်ဖွင့်လှစ်ပေးပါမည်!"
        )
        buy_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💳 စလစ်ပို့၍ စတိုးဖွင့်မည် (Apply Now)", callback_data="saas_apply_bot"),
            ],
            [
                InlineKeyboardButton("💬 Super Admin (Peter) ထံ ဆက်သွယ်ရန်", url="https://t.me/latt_swal2026"),
            ],
            [
                InlineKeyboardButton("🔙 ပင်မစာမျက်နှာ သို့", callback_data="nav_home"),
            ],
        ])
        await safe_edit_message(query, buy_text, reply_markup=buy_markup)
        return

    if data == "saas_apply_bot":
        context.user_data["saas_state"] = "waiting_saas_store_name"
        context.user_data["saas_plan"] = "Pro Business (25,000 MMK)"
        prompt_text = (
            "📝 *[ကိုယ့်ပိုင် စတိုးဆိုင် Bot လျှောက်ထားရန်]*\n\n"
            f"👤 *လျှောက်ထားသူ User ID -* `{user.id}`\n\n"
            "ကျေးဇူးပြု၍ သင်ဖွင့်လှစ်လိုသော **ဆိုင်အမည် (Store Name)** ကို စာရိုက်ပို့ပေးပါ -\n\n"
            "ဥပမာ -\n"
            "`Gaming Zone Myanmar`\n\n"
            "_(မလျှောက်ထားလိုပါက /cancel ကို နှိပ်ပါ)_"
        )
        cancel_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ မလုပ်တော့ပါ (Cancel)", callback_data="saas_buy_bot")]
        ])
        await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
        return

    # Super Admin Approval/Rejection for SaaS Store Applications
    if data.startswith("saas_appr_"):
        if not is_super_admin(user.id):
            await query.answer("⛔ Super Admin သာ ခွင့်ပြုနိုင်ပါသည်။", show_alert=True)
            return
        req_id = int(data.split("_")[2])
        ok, store = database.approve_store_request(req_id, days=30)
        if not ok or not store:
            await query.answer("❌ လျှောက်ထားမှု ရှာမတွေ့ပါ သို့မဟုတ် ပြီးဆုံးပြီး ဖြစ်ပါသည်။", show_alert=True)
            return

        appr_caption = (
            f"✅ *[စတိုးဆိုင်ဖွင့်လှစ်မှု အတည်ပြုပြီးပါပြီ / Store Activated]*\n\n"
            f"• Request ID - `#{req_id}`\n"
            f"• ဆိုင် ID - `#{store['id']}`\n"
            f"• ဆိုင်အမည် - *{store['store_name']}*\n"
            f"• ပိုင်ရှင် User ID - `{store['owner_id']}`\n"
            f"• Plan - *{store['plan']}*\n"
            f"• လိုင်စင်သက်တမ်း - `{store['license_expiry'][:10]}` (၃၀ ရက်)\n"
            f"• အတည်ပြုသူ Super Admin - {user.first_name}\n"
            f"• အချိန် - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            await query.edit_message_caption(caption=appr_caption, parse_mode="Markdown")
        except Exception:
            pass

        await query.answer(f"✅ Store #{store['id']} အောင်မြင်စွာ ဖွင့်လှစ်ပေးလိုက်ပါပြီ!", show_alert=True)

        try:
            await context.bot.send_message(
                chat_id=store["owner_id"],
                text=(
                    f"🎉 *[ဂုဏ်ယူပါသည်! သင့်စတိုးဆိုင် ဖွင့်လှစ်ပြီးပါပြီ]*\n\n"
                    f"Super Admin မှ သင့်ငွေလွှဲပြေစာကို အတည်ပြုပြီး စတိုးဆိုင် Admin Access ကို ဖွင့်ပေးလိုက်ပါပြီ!\n\n"
                    f"🏪 *ဆိုင်အမည် -* *{store['store_name']}*\n"
                    f"🆔 *Store ID -* `#{store['id']}`\n"
                    f"📦 *Subscription Plan -* *{store['plan']}*\n"
                    f"📅 *လိုင်စင်သက်တမ်း -* `{store['license_expiry'][:10]}` (၃၀ ရက်)\n"
                    f"👑 *အဆင့် -* `Store Admin`\n\n"
                    f"👉 ယခုပင် /admin ဟု ရိုက်ပို့ပြီး သင့်ကိုယ်ပိုင်ဆိုင်၏ Product, Price, Stock များကို စတင်ထည့်သွင်း စီမံခန့်ခွဲနိုင်ပါပြီ!\n\n"
                    f"💡 သင့်ကိုယ်ပိုင် Bot Token ချိတ်ဆက်လိုပါက Super Admin ([Peter](https://t.me/latt_swal2026)) ထံ ဆက်သွယ်၍ ချိတ်ဆက်နိုင်ပါသည်။"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Could not notify client {store['owner_id']} - {e}")
        return

    if data.startswith("saas_rejc_"):
        if not is_super_admin(user.id):
            await query.answer("⛔ Super Admin သာ ခွင့်ပြုနိုင်ပါသည်။", show_alert=True)
            return
        req_id = int(data.split("_")[2])
        req = database.get_store_request(req_id)
        database.reject_store_request(req_id)

        rejc_caption = (
            f"❌ *[စတိုးဆိုင် လျှောက်ထားမှု ငြင်းပယ်လိုက်ပါပြီ / Rejected]*\n\n"
            f"• Request ID - `#{req_id}`\n"
            f"• အချိန် - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• ငြင်းပယ်သူ Super Admin - {user.first_name}"
        )
        try:
            await query.edit_message_caption(caption=rejc_caption, parse_mode="Markdown")
        except Exception:
            pass

        await query.answer("❌ လျှောက်ထားမှုကို ငြင်းပယ်လိုက်ပါပြီ!", show_alert=True)

        if req:
            try:
                await context.bot.send_message(
                    chat_id=req["user_id"],
                    text=(
                        f"⚠️ *[စတိုးဖွင့် လျှောက်ထားမှု အတည်မပြုနိုင်ပါ]*\n\n"
                        f"သင်ပေးပို့ထားသော ငွေလွှဲပြေစာသည် အချက်အလက် မကိုက်ညီသဖြင့် Super Admin မှ အတည်မပြုနိုင်ပါ။\n"
                        f"အသေးစိတ်သိရှိလိုပါက Super Admin ([Peter](https://t.me/latt_swal2026)) သို့ ဆက်သွယ်မေးမြန်းနိုင်ပါသည်။"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify rejected applicant - {e}")
        return

    if data == "super_admin_stores":
        if not is_super_admin(user.id):
            await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
            return
        await render_super_admin_stores_view(query, user.id)
        return

    # SaaS License Check for all other callbacks (Super Admins are exempted)
    store_id = get_effective_store_id(update, context)
    allowed, lock_text, lock_markup = check_license_access(user.id, store_id=store_id)
    if not allowed:
        try:
            await query.answer("🔒 ဆိုင်ဝန်ဆောင်မှု သက်တမ်းကုန်ဆုံးနေပါသည်", show_alert=True)
        except Exception:
            pass
        await safe_edit_message(query, lock_text, reply_markup=lock_markup)
        return

    # Navigation callbacks
    if data == "nav_home":
        await show_home(update, context)
        return

    if data in ("nav_products", "refresh_catalog"):
        await show_products(update, context)
        return

    if data in ("nav_topup", "topup_menu"):
        await show_topup(update, context)
        return

    if data == "show_thai_qr":
        await show_thai_qr(update, context)
        return

    if data in ("nav_status", "view_status"):
        await show_member_status(update, context)
        return

    if data == "nav_referral":
        await show_referral(update, context)
        return

    if data == "nav_history":
        await show_history(update, context)
        return

    if data == "nav_guide":
        await show_guide(update, context)
        return

    if data == "nav_language":
        await show_language_menu(update, context)
        return

    if data == "nav_support":
        await show_help(update, context)
        return

    # Language selection callbacks
    if data == "setlang_my":
        database.set_user_language(user.id, "my")
        try:
            await query.answer("🇲🇲 မြန်မာစာသို့ ပြောင်းလဲပြီးပါပြီ!", show_alert=True)
        except Exception:
            pass
        await show_home(update, context)
        return

    if data == "setlang_en":
        database.set_user_language(user.id, "en")
        try:
            await query.answer("🇬🇧 Language changed to English!", show_alert=True)
        except Exception:
            pass
        await show_home(update, context)
        return

    # View specific product
    if data.startswith("prod_"):
        product_id = int(data.split("_")[1])
        product = database.get_product(product_id)
        if not product:
            await safe_edit_message(
                query,
                "⚠️ ပစ္စည်းရှာမတွေ့ပါ။",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 နောက်သို့", callback_data="refresh_catalog")]]
                ),
            )
            return

        stock = product["stock_count"]
        stock_status = f"✅ ပစ္စည်းလက်ကျန် - {stock} ခု" if stock > 0 else "❌ ပစ္စည်းလက်ကျန် - ကုန်သွားပါပြီ"
        clean_name = clean_single_icon_name(product["name"])

        product_detail = (
            f"*{clean_name}*\n"
            f"🏷️ အုပ်စု - {product['category']}\n\n"
            f"📝 *အသေးစိတ် -* \n{product['description']}\n\n"
            f"💰 *စျေးနှုန်း -* `{product['price']:,} MMK`\n"
            f"{stock_status}\n"
            f"──────────────────────"
        )

        buttons = []
        if stock > 0:
            buttons.append(
                [InlineKeyboardButton("🛒 ဝယ်ယူမည် (Buy Now)", callback_data=f"buy_{product['id']}")]
            )
        buttons.append(
            [InlineKeyboardButton("🔙 စာရင်းသို့ ပြန်သွားမည်", callback_data="refresh_catalog")]
        )

        await safe_edit_message(
            query,
            product_detail,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # Auto-Delivery Purchase execution
    if data.startswith("buy_"):
        product_id = int(data.split("_")[1])
        product = database.get_product(product_id)

        if not product:
            await safe_edit_message(
                query,
                "⚠️ ပစ္စည်းရှာမတွေ့ပါ။",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 စာရင်းသို့", callback_data="refresh_catalog")]]
                ),
            )
            return

        success, message, delivered_data, remaining_stock, prod_name, price, *extra = database.purchase_product(
            user_id=user.id, product_id=product_id
        )
        referrer_id_to_notify = extra[0] if extra else None

        if success:
            db_user = database.get_user(user.id)
            current_balance = db_user["balance"] if db_user else 0

            delivery_message = (
                f"🎉 *ဝယ်ယူမှု အောင်မြင်ပါသည်! (Payment Successful)*\n\n"
                f"📦 *ပစ္စည်း -* {prod_name}\n"
                f"💰 *ကျသင့်ငွေ -* `{price:,} MMK`\n"
                f"💳 *လက်ကျန်ငွေ -* `{current_balance:,} MMK`\n\n"
                f"🔑 *သင်၏ ပစ္စည်းအချက်အလက် (Auto-Delivered) -*\n"
                f"```\n{delivered_data}\n```\n\n"
                f"⚠️ _လုံခြုံရေးအတွက် သင်၏ အချက်အလက်များကို သိမ်းဆည်းထားပါ။ "
                f"Member Status တွင်လည်း ပြန်လည်ကြည့်ရှုနိုင်ပါသည်။_"
            )

            buttons = [
                [
                    InlineKeyboardButton("🛒 ထပ်မံဝယ်ယူရန်", callback_data="refresh_catalog"),
                    InlineKeyboardButton("👤 Member Status", callback_data="view_status"),
                ]
            ]

            await safe_edit_message(
                query,
                delivery_message,
                reply_markup=InlineKeyboardMarkup(buttons),
            )

            # Awarded referral bonus notification to referrer
            if referrer_id_to_notify:
                try:
                    ref_lang = get_user_lang(referrer_id_to_notify)
                    if ref_lang == "en":
                        ref_bonus_msg = (
                            f"🎁 *[Referral Bonus Earned!]*\n\n"
                            f"Your referred friend *{user.first_name}* completed a purchase!\n"
                            f"You have received *+1,000 MMK* referral bonus in your balance."
                        )
                    else:
                        ref_bonus_msg = (
                            f"🎁 *[မိတ်ဆက်ဆုကြေး ရရှိပါသည်!]*\n\n"
                            f"သင့်မိတ်ဆွေ *{user.first_name}* သည် ပစ္စည်းစတင်ဝယ်ယူလိုက်သဖြင့် "
                            f"မိတ်ဆက်ဆုကြေး *+1,000 MMK* ကို သင့်လက်ကျန်ငွေထဲသို့ ထည့်သွင်းပေးလိုက်ပါပြီ!"
                        )
                    await context.bot.send_message(
                        chat_id=referrer_id_to_notify,
                        text=ref_bonus_msg,
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning(f"Failed to notify referrer {referrer_id_to_notify} of bonus: {e}")

            prod_store_id = product.get("store_id", 1) if (product and "store_id" in product.keys()) else 1
            s_name = database.get_store_name(store_id=prod_store_id)
            purchase_alert = (
                f"🔔 *[{s_name} - ဝယ်ယူမှုအသစ် သတိပေးချက်]*\n\n"
                f"• ဝယ်ယူသူ - {user.first_name} (@{user.username or 'N/A'})\n"
                f"• User ID - `{user.id}`\n"
                f"• ပစ္စည်း - {prod_name}\n"
                f"• တန်ဖိုး - `{price:,} MMK`\n"
                f"• အချိန် - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"• လက်ကျန် Stock - `{remaining_stock} ခု`"
            )
            await notify_admins(context, purchase_alert, store_id=prod_store_id)

            if remaining_stock == 0:
                out_of_stock_alert = (
                    f"🚨 *[{s_name} - ပစ္စည်းကုန်သွားပါပြီ / OUT OF STOCK!]*\n\n"
                    f"⚠️ *{prod_name}* ၏ Stock ကုန်သွားပါပြီ!\n"
                    f"ကျေးဇူးပြု၍ `/addstock {product_id} <credentials>` ဖြင့် Stock အသစ် ထပ်ဖြည့်ပေးပါ။"
                )
                await notify_admins(context, out_of_stock_alert, store_id=prod_store_id)
            elif remaining_stock == 1:
                low_stock_alert = (
                    f"⚠️ *[{s_name} - Stock နည်းနေပါသည် / LOW STOCK]*\n\n"
                    f"*{prod_name}* တွင် လက်ကျန် `1` ခုသာ ကျန်ရှိပါတော့သည်။"
                )
                await notify_admins(context, low_stock_alert, store_id=prod_store_id)

        else:
            buttons = [
                [InlineKeyboardButton("💳 ငွေဖြည့်ရန် (Top-up)", callback_data="topup_menu")],
                [InlineKeyboardButton("🔙 စာရင်းသို့", callback_data="refresh_catalog")],
            ]
            await safe_edit_message(
                query,
                message,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    # =========================================================================
    # Admin Manual Top-up Slip Approval / Rejection Callbacks
    # =========================================================================

    if data.startswith("slip_"):
        if not is_admin_user(user.id):
            try:
                await query.answer("⛔ Admin ခွင့်ပြုချက်မရှိပါ။", show_alert=True)
            except Exception:
                pass
            return

        # 1. Approve Slip with Specific Amount
        if data.startswith("slip_app_"):
            parts = data.split("_")
            target_user_id = int(parts[2])
            amount = int(parts[3])

            new_bal = database.add_user_balance(target_user_id, amount)
            admin_name = user.first_name or "Admin"
            time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Update Admin Photo Message
            new_caption = (
                f"✅ *[ငွေဖြည့်သွင်းမှု အတည်ပြုပြီးပါပြီ / Approved]*\n\n"
                f"• User ID - `{target_user_id}` ထံသို့ `+{amount:,} MMK` ထည့်သွင်းပေးပြီးပါပြီ!\n"
                f"• ယခု Balance - `{new_bal:,} MMK`\n"
                f"• အတည်ပြုသူ Admin - {admin_name}\n"
                f"• အချိန် - {time_now}"
            )
            try:
                await query.edit_message_caption(caption=new_caption, parse_mode="Markdown")
            except Exception:
                pass

            try:
                await query.answer(f"✅ +{amount:,} MMK ထည့်သွင်းပေးလိုက်ပါပြီ!", show_alert=True)
            except Exception:
                pass

            # Notify the user directly
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=(
                        f"🎉 *[ငွေဖြည့်သွင်းမှု အတည်ပြုပြီးပါပြီ]*\n\n"
                        f"Admin မှ သင်၏ ငွေလွှဲပြေစာကို စစ်ဆေးအတည်ပြုပြီး အကောင့်ထဲသို့ `+{amount:,} MMK` ထည့်သွင်းပေးလိုက်ပါပြီ!\n"
                        f"💰 *လက်ရှိလက်ကျန်ငွေ -* `{new_bal:,} MMK`\n\n"
                        f"ကျေးဇူးတင်ပါသည်! ပစ္စည်းများကို ချက်ချင်း စျေးဝယ်ယူနိုင်ပါပြီ။"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_user_id} - {e}")
            return

        # 2. Reject Slip
        if data.startswith("slip_rej_"):
            target_user_id = int(data.split("_")[2])
            admin_name = user.first_name or "Admin"
            time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            new_caption = (
                f"❌ *[ငွေဖြည့်သွင်းမှု ငြင်းပယ်လိုက်ပါပြီ / Rejected]*\n\n"
                f"• User ID - `{target_user_id}` ၏ ငွေလွှဲပြေစာကို ငြင်းပယ်လိုက်ပါသည်။\n"
                f"• ငြင်းပယ်သူ Admin - {admin_name}\n"
                f"• အချိန် - {time_now}"
            )
            try:
                await query.edit_message_caption(caption=new_caption, parse_mode="Markdown")
            except Exception:
                pass

            try:
                await query.answer("❌ ငွေလွှဲပြေစာကို ငြင်းပယ်လိုက်ပါပြီ!", show_alert=True)
            except Exception:
                pass

            # Notify the user
            try:
                rej_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Admin ထံ စာပို့ရန်", url=ADMIN_URL)]
                ])
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=(
                        f"⚠️ *[ငွေဖြည့်သွင်းမှု ငြင်းပယ်ခံရပါသည်]*\n\n"
                        f"သင်ပေးပို့ထားသော ငွေလွှဲပြေစာသည် အချက်အလက် မကိုက်ညီသဖြင့် Admin မှ ငြင်းပယ်လိုက်ပါသည်။\n"
                        f"အသေးစိတ်သိရှိလိုပါက {ADMIN_CONTACT} သို့ ဆက်သွယ်မေးမြန်းနိုင်ပါသည်။"
                    ),
                    reply_markup=rej_markup,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_user_id} - {e}")
            return

        # 3. Custom Amount Input State for Admin
        if data.startswith("slip_custom_"):
            target_user_id = int(data.split("_")[2])
            context.user_data["admin_state"] = f"waiting_custom_slip_{target_user_id}"
            context.user_data["custom_slip_target_user"] = target_user_id
            if query.message:
                context.user_data["custom_slip_msg_id"] = query.message.message_id
                context.user_data["custom_slip_chat_id"] = query.message.chat_id

            target_user = database.get_user(target_user_id)
            target_name = target_user["first_name"] if target_user else f"User {target_user_id}"
            target_bal = target_user["balance"] if target_user else 0

            try:
                await query.answer("✏️ ထည့်သွင်းလိုသော ပမာဏကို ရိုက်ပို့ပေးပါ")
            except Exception:
                pass

            prompt_msg = (
                f"✏️ *[စိတ်ကြိုက် ငွေဖြည့်သွင်းမှု ပမာဏ ရိုက်ထည့်ပါ]*\n\n"
                f"• ဝယ်ယူသူ - *{target_name}*\n"
                f"• User ID - `{target_user_id}`\n"
                f"• လက်ရှိ Balance - `{target_bal:,} MMK`\n\n"
                f"👉 ကျေးဇူးပြု၍ ထည့်သွင်းပေးလိုသော ငွေပမာဏ (MMK ကျပ်) ကို ရိုက်ပို့ပေးပါ -\n"
                f"*(ဥပမာ - `15000` သို့မဟုတ် `7500`)*\n\n"
                f"_(မဖြည့်တော့ပါက /cancel ကို နှိပ်ပါ)_"
            )
            await query.message.reply_text(prompt_msg, parse_mode="Markdown")
            return

    # =========================================================================
    # Admin Panel Sub-views & Stock Credentials Callbacks
    # =========================================================================

    if (
        data.startswith("admin_")
        or data.startswith("adm_")
        or data.startswith("saas_")
        or data.startswith("store_admin_")
        or data == "cancel_bind_token"
    ):
        if not is_admin_user(user.id):
            try:
                await query.answer("⛔ Admin ခွင့်ပြုချက်မရှိပါ။", show_alert=True)
            except Exception:
                pass
            return

        active_store = get_admin_active_store(user.id)
        admin_store_id = active_store["id"]

        # Restrict access if store license is expired or suspended (Super Admin is exempted)
        if not is_super_admin(user.id) and data not in ("admin_license_info", "store_admin_bind_token_prompt", "saas_botfather_guide", "store_admin_unlink_token", "cancel_bind_token"):
            if not is_admin_access_allowed(user.id, store_id=admin_store_id):
                try:
                    await query.answer("⛔ သင့်ဆိုင်၏ SaaS License သက်တမ်းကုန်ဆုံးနေသောကြောင့် Admin Panel အသုံးပြုခွင့်ကို ပိတ်ထားပါသည်။", show_alert=True)
                except Exception:
                    pass
                allowed, lock_text, lock_markup = check_license_access(user.id, store_id=admin_store_id)
                await safe_edit_message(query, lock_text, reply_markup=lock_markup)
                return

        # 1. Admin Refresh / Home Panel
        if data in ("admin_refresh", "admin_home"):
            context.user_data.pop("admin_state", None)
            panel_text = build_admin_panel_text(user.id)
            markup = get_admin_panel_markup(is_super=is_super_admin(user.id))
            if data == "admin_refresh":
                try:
                    await query.answer("🔄 Admin Panel ကို Refresh ပြုလုပ်ပြီးပါပြီ!")
                except Exception:
                    pass
            await safe_edit_message(
                query,
                panel_text,
                reply_markup=markup,
            )
            return

        # Interactive Add Product
        if data == "admin_interactive_add_product":
            await start_interactive_add_product(query, user.id, context)
            return

        # Interactive Category Selected
        if data.startswith("adm_cat_sel_"):
            cat = data.replace("adm_cat_sel_", "")
            context.user_data["interactive_prod_category"] = cat
            prod_name = context.user_data.get("interactive_prod_name", "Product")
            context.user_data["admin_state"] = "interactive_add_prod_price"
            await render_interactive_price_prompt(query, prod_name, cat)
            return

        # Interactive Skip Description
        if data == "adm_prod_skip_desc":
            prod_name = context.user_data.get("interactive_prod_name", "Product")
            cat = context.user_data.get("interactive_prod_category", "General")
            price = context.user_data.get("interactive_prod_price", 0)
            new_id = database.add_product(prod_name, "", price, cat, store_id=admin_store_id)

            context.user_data.pop("admin_state", None)
            context.user_data.pop("interactive_prod_name", None)
            context.user_data.pop("interactive_prod_category", None)
            context.user_data.pop("interactive_prod_price", None)
            context.user_data.pop("interactive_prod_desc", None)

            success_text = (
                f"🎉 *[Product အသစ် အောင်မြင်စွာ ဖန်တီးပြီးပါပြီ]*\n\n"
                f"🏪 *ဆိုင် -* {active_store['store_name']}\n"
                f"🆔 *ID -* `#{new_id}`\n"
                f"📦 *အမည် -* *{prod_name}*\n"
                f"💰 *စျေးနှုန်း -* `{price:,} MMK`\n"
                f"🏷️ *အုပ်စု -* `{cat}`\n\n"
                "💡 _ယခု Product အတွက် Customer များ ချက်ချင်း ဝယ်ယူနိုင်ရန် အောက်ပါ ခလုတ်ကို နှိပ်၍ Stock (Email/Pass/Link) ထည့်သွင်းနိုင်ပါပြီ -_"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 ဤ Product အတွက် Stock ချက်ချင်းထည့်မည်", callback_data=f"adm_istk_sel_{new_id}")],
                [InlineKeyboardButton("➕ နောက်ထပ် Product တစ်ခု ထပ်ထည့်မည်", callback_data="admin_interactive_add_product")],
                [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
            ])
            await safe_edit_message(query, success_text, reply_markup=markup)
            return

        # Interactive Add Stock
        if data == "admin_interactive_add_stock":
            await render_interactive_add_stock_select(query, user.id, admin_store_id)
            return

        # Interactive Select Product for Stock
        if data.startswith("adm_istk_sel_"):
            prod_id = int(data.replace("adm_istk_sel_", ""))
            await render_interactive_stock_input_prompt(query, user.id, prod_id, context)
            return

        # Cancel Interactive Add
        if data == "cancel_interactive_add":
            context.user_data.pop("admin_state", None)
            context.user_data.pop("interactive_prod_name", None)
            context.user_data.pop("interactive_prod_category", None)
            context.user_data.pop("interactive_prod_price", None)
            context.user_data.pop("interactive_prod_desc", None)
            try:
                await query.answer("❌ ပယ်ဖျက်လိုက်ပါပြီ!")
            except Exception:
                pass
            panel_text = build_admin_panel_text(user.id)
            markup = get_admin_panel_markup(is_super=is_super_admin(user.id))
            await safe_edit_message(query, panel_text, reply_markup=markup)
            return

        # 2. Monthly Sales & Statistics Report (စာရင်းဇယား)
        if data == "admin_stats":
            context.user_data.pop("admin_state", None)
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            stats = database.get_store_stats(store_id=admin_store_id)
            monthly_data = database.get_monthly_sales_stats(store_id=admin_store_id)
            rate = database.get_exchange_rate(store_id=admin_store_id)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            report_lines = [
                f"📊 *[{store_name} - အရောင်းနှင့် စာရင်းဇယား အစီရင်ခံစာ]*\n",
                f"🆔 *Store ID -* `#{admin_store_id}`\n",
                "🏪 *ဆိုင်အရောင်း စုစုပေါင်း အကျဉ်းချုပ် -*",
                f"• 💰 စုစုပေါင်း အရောင်းရငွေ - `{stats['total_revenue']:,} MMK`",
                f"• 🛒 ပြီးမြောက်ခဲ့သော အော်ဒါ - `{stats['total_orders']} ခု`",
                f"• 👥 အသုံးပြုသူ (Users) - `{stats['total_users']} ယောက်`",
                f"• 📦 ရောင်းချနေသော ပစ္စည်း - `{stats['total_products']} မျိုး`",
                f"• 🔑 ရောင်းရန်ကျန် Stock - `{stats['total_stock']} ခု`",
                f"• 💱 လက်ရှိ THB ငွေလဲနှုန်း - `1 THB = {rate:,} MMK`\n",
                "━━━━━━━━━━━━━━━━━━━━",
                "📅 *လအလိုက် အရောင်းစာရင်း (Monthly Breakdown) -*\n",
            ]

            if not monthly_data:
                report_lines.append("• မှတ်တမ်းတင်ထားသော အရောင်းစာရင်း မရှိသေးပါ။")
            else:
                for row in monthly_data:
                    m_str = str(row["month_str"]) if row["month_str"] else "N/A"
                    count = row["order_count"]
                    sales = row["total_sales"]
                    avg_val = int(sales / count) if count > 0 else 0
                    try:
                        y, m = m_str.split("-")
                        month_label = f"🗓️ *{y} ခုနှစ် {int(m)} လပိုင်း ({m_str})*"
                    except Exception:
                        month_label = f"🗓️ *{m_str}*"

                    report_lines.append(
                        f"{month_label}\n"
                        f"  • 🛒 အော်ဒါအရေအတွက် - `{count} ခု`\n"
                        f"  • 💵 အရောင်းရငွေ - `{sales:,} MMK`\n"
                        f"  • 📈 ပျမ်းမျှ အော်ဒါတန်ဖိုး - `{avg_val:,} MMK`\n"
                    )

            report_lines.append("━━━━━━━━━━━━━━━━━━━━")
            report_lines.append(f"🕒 *အစီရင်ခံစာ အချိန် -* `{now_str}`")

            report_text = "\n".join(report_lines)
            report_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 Refresh စာရင်းဇယား", callback_data="admin_stats"),
                    InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
                ]
            ])
            try:
                await query.answer("📊 စာရင်းဇယား အစီရင်ခံစာကို ရယူလိုက်ပါပြီ!")
            except Exception:
                pass
            await safe_edit_message(query, report_text, reply_markup=report_markup)
            return

        # Store Admin Payment Settings (Top-up Text, QR & Accounts)
        if data in ("admin_payment_settings", "adm_pay_menu") or data.startswith("adm_pay_settings_"):
            context.user_data.pop("admin_state", None)
            if data.startswith("adm_pay_settings_"):
                s_id = int(data.split("_")[3])
            else:
                active_store = get_admin_active_store(user.id)
                s_id = active_store["id"]
            await render_admin_payment_settings_view(query, s_id)
            return

        if data.startswith("adm_pay_edit_text_"):
            s_id = int(data.split("_")[4])
            context.user_data["admin_state"] = f"waiting_store_topup_text_{s_id}"
            s_name = database.get_store_name(store_id=s_id)
            prompt_text = (
                f"✏️ *[{s_name} - ငွေဖြည့်နည်း ညွှန်ကြားချက် စာသားသစ် ထည့်သွင်းရန်]*\n\n"
                f"သင့် customer များ /topup နှိပ်သည့်အခါ ပြသမည့် စာသား၊ KPay / Wave / ဘဏ်အကောင့် အချက်အလက်များကို ဤ Chat ထဲသို့ စာရိုက်ပို့ပေးပါ -\n\n"
                f"_(မပြောင်းလဲလိုပါက /cancel ဟု ရိုက်ပို့ပါ သို့မဟုတ် Cancel ကို နှိပ်ပါ)_"
            )
            cancel_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"adm_pay_settings_{s_id}")]
            ])
            await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
            return

        if data.startswith("adm_pay_reset_text_"):
            s_id = int(data.split("_")[4])
            database.set_store_topup_instructions(s_id, "")
            try:
                await query.answer("✅ မူလ Standard ငွေဖြည့်နည်း ပုံစံသို့ ပြန်လည်ထားရှိလိုက်ပါပြီ!", show_alert=True)
            except Exception:
                pass
            await render_admin_payment_settings_view(query, s_id)
            return

        if data.startswith("adm_pay_upload_qr_"):
            s_id = int(data.split("_")[4])
            context.user_data["admin_state"] = f"waiting_store_qr_photo_{s_id}"
            s_name = database.get_store_name(store_id=s_id)
            prompt_text = (
                f"📸 *[{s_name} - Payment QR Code ပုံ အသစ်တင်ရန်]*\n\n"
                f"SCB, PromptPay, KPay သို့မဟုတ် မိမိ ငွေလက်ခံလိုသော QR Code ဓာတ်ပုံ (Photo) ကို ဤ Chat ထဲသို့ တိုက်ရိုက် ပို့ပေးပါ -\n\n"
                f"_(မတင်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ သို့မဟုတ် Cancel ကို နှိပ်ပါ)_"
            )
            cancel_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"adm_pay_settings_{s_id}")]
            ])
            await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
            return

        if data.startswith("adm_pay_remove_qr_"):
            s_id = int(data.split("_")[4])
            database.set_store_qr_file(s_id, "none")
            try:
                await query.answer("🗑️ Payment QR ပုံကို ဖယ်ရှားလိုက်ပါပြီ! Customer များထံ QR ပြသတော့မည် မဟုတ်ပါ။", show_alert=True)
            except Exception:
                pass
            await render_admin_payment_settings_view(query, s_id)
            return

        if data.startswith("adm_pay_edit_acc_"):
            s_id = int(data.split("_")[4])
            context.user_data["admin_state"] = f"waiting_store_accs_{s_id}"
            s_name = database.get_store_name(store_id=s_id)
            prompt_text = (
                f"🏦 *[{s_name} - ငွေလက်ခံ အကောင့်များ ပြင်ဆင်ရန်]*\n\n"
                f"ကျေးဇူးပြု၍ အောက်ပါ Format အတိုင်း ရိုက်ပို့ပေးပါ -\n"
                f"`<KPay_Phone> | <KPay_Name> | <Wave_Phone> | <Wave_Name> | <Thai_Bank_Info>`\n\n"
                f"ဥပမာ -\n"
                f"`09123456789 | Ko Aung | 09987654321 | Ko Aung | SCB 123-456-7890 (Ko Aung)`\n\n"
                f"_(မပြောင်းလဲလိုပါက /cancel ဟု ရိုက်ပို့ပါ သို့မဟုတ် Cancel ကို နှိပ်ပါ)_"
            )
            cancel_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"adm_pay_settings_{s_id}")]
            ])
            await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
            return

        # SaaS License / Multi-tenant Client Management
        if data in ("admin_license_info", "super_admin_stores"):
            context.user_data.pop("admin_state", None)
            if is_super_admin(user.id):
                await render_super_admin_stores_view(query, user.id)
            else:
                await render_store_admin_license_view(query, user.id)
            return

        # Super Admin Store Detail View
        if data.startswith("saas_manage_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[2])
            await render_store_detail_manage_view(query, s_id)
            return

        # Super Admin Kill Switch for Store
        if data.startswith("saas_kill_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[2])
            database.set_store_license_status(s_id, "suspended")
            await client_bot_manager.stop_instance(s_id)
            try:
                await query.answer("🚨 ဆိုင်ဝန်ဆောင်မှု ပိတ်သိမ်းလိုက်ပါပြီ (Kill Switch Activated)!", show_alert=True)
            except Exception:
                pass
            await render_store_detail_manage_view(query, s_id)
            return

        # Super Admin Activate Store
        if data.startswith("saas_act_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[2])
            database.set_store_license_status(s_id, "active")
            store = database.get_store(s_id)
            if store and store.get("bot_token"):
                await client_bot_manager.start_instance(s_id, store["bot_token"])
            try:
                await query.answer("✅ ဆိုင်ပြန်လည်ဖွင့်လှစ်ပေးလိုက်ပါပြီ (Activated)!", show_alert=True)
            except Exception:
                pass
            await render_store_detail_manage_view(query, s_id)
            return

        # Super Admin Extend Store License
        if data.startswith("saas_ext_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            parts = data.split("_")
            s_id = int(parts[2])
            days = int(parts[3])
            database.extend_store_license(s_id, days)
            try:
                await query.answer(f"✅ သက်တမ်း +{days} ရက် တိုးမြှင့်ပြီးပါပြီ!", show_alert=True)
            except Exception:
                pass
            await render_store_detail_manage_view(query, s_id)
            return

        # Super Admin Unlink Bot Token
        if data.startswith("saas_untkn_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[2])
            await client_bot_manager.stop_instance(s_id)
            database.update_store(s_id, bot_token="", bot_username="")
            try:
                await query.answer("🔌 Bot Token ဖြုတ်လိုက်ပါပြီ!", show_alert=True)
            except Exception:
                pass
            await render_store_detail_manage_view(query, s_id)
            return

        # Super Admin Delete Store Confirmation & Execution
        if data.startswith("saas_del_confirm_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[3])
            store = database.get_store(s_id)
            store_name = store["store_name"] if store else f"Store #{s_id}"
            del_text = (
                f"⚠️ *[သတိပေးချက် - Store #{s_id} ({store_name}) ကို အပြီးဖျက်မည်]*\n\n"
                f"ဤဆိုင်နှင့် သက်ဆိုင်သော Products များနှင့် Stocks များကိုပါ အပြီးဖျက်ပစ်မည် ဖြစ်သည်။\n"
                f"ဆက်လက်လုပ်ဆောင်ရန် သေချာပါသလား?"
            )
            del_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ ဟုတ်ကဲ့၊ အပြီးဖျက်မည်", callback_data=f"saas_del_do_{s_id}")],
                [InlineKeyboardButton("❌ မဖျက်တော့ပါ (Cancel)", callback_data=f"saas_manage_{s_id}")],
            ])
            await safe_edit_message(query, del_text, reply_markup=del_markup)
            return

        if data.startswith("saas_del_do_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[3])
            await client_bot_manager.stop_instance(s_id)
            database.delete_store(s_id)
            try:
                await query.answer(f"🗑️ Store #{s_id} ကို အပြီးဖျက်ပြီးပါပြီ!", show_alert=True)
            except Exception:
                pass
            await render_super_admin_stores_view(query, user.id)
            return

        # Super Admin Interactive Prompt Callbacks
        if data.startswith("saas_tkn_prompt_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[3])
            context.user_data["admin_state"] = f"waiting_store_token_{s_id}"
            prompt_text = (
                f"🤖 *[Store #{s_id} အတွက် Bot Token ထည့်သွင်းပါ]*\n\n"
                f"👉 @BotFather မှ ရရှိသော HTTP API Token (ဥပမာ - `7123456789:ABCdefGhIJKlmNo...`) ကို ရိုက်ပို့ပေးပါ -\n\n"
                f"_(မပြောင်းလဲလိုပါက /cancel ဟု ရိုက်ပို့ပါ)_"
            )
            cancel_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"saas_manage_{s_id}")]
            ])
            await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
            return

        if data.startswith("saas_name_prompt_"):
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            s_id = int(data.split("_")[3])
            context.user_data["admin_state"] = f"waiting_store_name_{s_id}"
            store = database.get_store(s_id)
            old_name = store["store_name"] if store else f"Store #{s_id}"
            prompt_text = (
                f"✏️ *[Store #{s_id} အတွက် ဆိုင်အမည် အသစ် ရိုက်ထည့်ပါ]*\n\n"
                f"🏪 လက်ရှိဆိုင်အမည် - `{old_name}`\n\n"
                f"👉 ပြောင်းလဲလိုသော ဆိုင်အမည်ကို ရိုက်ပို့ပေးပါ -\n\n"
                f"_(မပြောင်းလဲလိုပါက /cancel ဟု ရိုက်ပို့ပါ)_"
            )
            cancel_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"saas_manage_{s_id}")]
            ])
            await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
            return

        if data == "saas_add_store_prompt":
            if not is_super_admin(user.id):
                await query.answer("⛔ Super Admin သာ ခွင့်ပြုချက်ရှိပါသည်။", show_alert=True)
                return
            context.user_data["admin_state"] = "waiting_super_add_store"
            prompt_text = (
                "➕ *[Client ဆိုင်အသစ် ထည့်သွင်းရန်]*\n\n"
                "ကျေးဇူးပြု၍ အောက်ပါ Format အတိုင်း ရိုက်ပို့ပေးပါ -\n"
                "`<Owner_Telegram_ID> | <Store_Name> | <Days>`\n\n"
                "ဥပမာ -\n"
                "`987654321 | Ko Aung Game Shop | 30`\n\n"
                "_(မလုပ်တော့ပါက /cancel ဟု ရိုက်ပို့ပါ)_"
            )
            cancel_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="admin_license_info")]
            ])
            await safe_edit_message(query, prompt_text, reply_markup=cancel_markup)
            return

        # BotFather Setup Guide & Token Binding Handlers
        if data == "saas_botfather_guide":
            await render_botfather_guide_view(query, user.id)
            return

        if data in ("store_admin_bind_token_prompt", "admin_bind_bot_token"):
            await start_token_binding_conv(update, context)
            return

        if data == "cancel_bind_token":
            await cancel_token_binding(update, context)
            return

        if data == "store_admin_unlink_token":
            store = database.get_store_by_owner(user.id)
            if store:
                s_id = store["id"]
                await client_bot_manager.stop_instance(s_id)
                database.update_store(s_id, bot_token="", bot_username="")
                try:
                    await query.answer("🔌 သီးသန့် Bot Token ကို ဖြုတ်လိုက်ပါပြီ!", show_alert=True)
                except Exception:
                    pass
            await render_store_admin_license_view(query, user.id)
            return

        # Admin Exchange Rate Management
        if data == "admin_rate_menu":
            context.user_data.pop("admin_state", None)
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            rate = database.get_exchange_rate(store_id=admin_store_id)
            rate_text, markup = get_admin_rate_view(rate, store_name=store_name)
            await safe_edit_message(query, rate_text, reply_markup=markup)
            return

        if data == "admin_rate_input_prompt":
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            context.user_data["admin_state"] = f"waiting_exchange_rate_{admin_store_id}"
            rate = database.get_exchange_rate(store_id=admin_store_id)
            prompt_text = (
                f"✏️ *[THB ငွေလဲနှုန်း အသစ် ရိုက်ထည့်ပါ]*\n\n"
                f"💱 လက်ရှိနှုန်းထား - `1 THB = {rate:,} MMK`\n\n"
                f"👉 ကျေးဇူးပြု၍ သတ်မှတ်လိုသော THB ငွေလဲနှုန်း ဂဏန်းကို ရိုက်ပို့ပေးပါ -\n"
                f"*(ဥပမာ - `135` သို့မဟုတ် `136`)*\n\n"
                f"_(မပြောင်းလဲလိုပါက အောက်ပါ Cancel ခလုတ်ကို နှိပ်ပါ သို့မဟုတ် /cancel)_"
            )
            buttons = [
                [InlineKeyboardButton("❌ မပြောင်းတော့ပါ (Cancel)", callback_data="admin_rate_menu")],
                [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
            ]
            await safe_edit_message(query, prompt_text, reply_markup=InlineKeyboardMarkup(buttons))
            return

        if data == "admin_rate_inc5":
            context.user_data.pop("admin_state", None)
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            current_rate = database.get_exchange_rate(store_id=admin_store_id) + 5
            database.set_exchange_rate(current_rate, store_id=admin_store_id)
            try:
                await query.answer(f"✅ 1 THB = {current_rate} MMK")
            except Exception:
                pass
            rate_text, markup = get_admin_rate_view(current_rate, store_name=store_name)
            await safe_edit_message(query, rate_text, reply_markup=markup)
            return

        if data == "admin_rate_dec5":
            context.user_data.pop("admin_state", None)
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            current_rate = max(1, database.get_exchange_rate(store_id=admin_store_id) - 5)
            database.set_exchange_rate(current_rate, store_id=admin_store_id)
            try:
                await query.answer(f"✅ 1 THB = {current_rate} MMK")
            except Exception:
                pass
            rate_text, markup = get_admin_rate_view(current_rate, store_name=store_name)
            await safe_edit_message(query, rate_text, reply_markup=markup)
            return

        if data.startswith("admin_rate_set_"):
            context.user_data.pop("admin_state", None)
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            new_r = int(data.split("_")[3])
            database.set_exchange_rate(new_r, store_id=admin_store_id)
            try:
                await query.answer(f"✅ 1 THB = {new_r} MMK")
            except Exception:
                pass
            rate_text, markup = get_admin_rate_view(new_r, store_name=store_name)
            await safe_edit_message(query, rate_text, reply_markup=markup)
            return

        # 2. Admin Stock Overview
        if data == "admin_stocks":
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            products = database.get_products_with_stock(store_id=admin_store_id)
            lines = [f"📦 *[{store_name} - ပစ္စည်းအလိုက် လက်ကျန် Stock စာရင်း]*\n"]
            for p in products:
                status_icon = "🟢" if p["stock_count"] > 0 else "🔴"
                clean_name = clean_single_icon_name(p["name"])
                lines.append(
                    f"{status_icon} *ID {p['id']} -* {clean_name}\n"
                    f"   💰 `{p['price']:,} MMK` • လက်ကျန် - `{p['stock_count']} ခု`\n"
                )
            lines.append("💡 _Stock အသေးစိတ် (Mail/Pass) များကို အောက်ပါခလုတ်ဖြင့် စစ်ဆေး/ပြင်ဆင်နိုင်ပါသည် -_")

            keyboard = [
                [InlineKeyboardButton("📥 Stock အသစ် ထည့်မည်", callback_data="admin_interactive_add_stock")],
                [InlineKeyboardButton("🔑 Stock အားလုံး ပြင်/ကြည့်မည် (Mail & Pass)", callback_data="admin_view_all_credentials")],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="admin_stocks"),
                    InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
                ],
            ]
            stocks_text = "\n".join(lines)
            await safe_edit_message(
                query,
                stocks_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        # 3. View & EDIT ALL Available Stock Credentials (Email, Passwords, Keys)
        if data == "admin_view_all_credentials":
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            store_name = database.get_store_name(store_id=admin_store_id)
            active_stocks = database.get_all_available_stocks(store_id=admin_store_id)
            if not active_stocks:
                empty_text = (
                    f"🔑 *[{store_name} - ရောင်းရန်ကျန်ရှိသော Stock (Mail/Password) မရှိသေးပါ]*\n\n"
                    "`/addstock <id> <credentials>` ဖြင့် Stock အသစ် ထည့်သွင်းနိုင်ပါသည်။"
                )
                await safe_edit_message(
                    query,
                    empty_text,
                    reply_markup=get_admin_back_markup(),
                )
                return

            lines = [f"🔑 *[{store_name} - ရောင်းရန်ကျန်ရှိသော Stock စာရင်း ({len(active_stocks)} ခု)]*\n"]
            buttons = []
            for s in active_stocks:
                clean_name = clean_single_icon_name(s["product_name"])
                lines.append(
                    f"📦 *{clean_name}* [Stock ID - `#{s['id']}`]\n"
                    f"```\n{s['item_data']}\n```\n"
                )
                buttons.append([
                    InlineKeyboardButton(f"✏️ Edit #{s['id']} ({clean_name[:16]}...)", callback_data=f"adm_edit_stock_{s['id']}")
                ])

            lines.append("💡 _ပြင်လိုသော Stock ကို အောက်ပါ Edit ခလုတ်များဖြင့် ပြင်နိုင်ပါသည် သို့မဟုတ် `/editstock <id> <data>` သုံးပါ_")
            creds_text = "\n".join(lines)

            buttons.append([InlineKeyboardButton("⚙️ Product အလိုက် စီမံရန်", callback_data="admin_manage_prods")])
            buttons.append([
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_view_all_credentials"),
                InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
            ])

            await safe_edit_message(
                query,
                creds_text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 3.1 Edit a Specific Stock Item (Mail & Password)
        if data.startswith("adm_edit_stock_"):
            stock_id = int(data.split("_")[3])
            stock_item = database.get_stock_by_id(stock_id)
            if not stock_item:
                await query.answer("❌ Stock ရှာမတွေ့ပါ။", show_alert=True)
                return

            edit_stock_text = (
                "✏️ *Stock Credentials (Mail/Pass) ပြင်ဆင်ရန်*\n\n"
                f"🔢 *Stock ID -* `#{stock_id}`\n"
                f"📦 *Product -* {stock_item['product_name']}\n\n"
                f"🔑 *လက်ရှိ အချက်အလက် (Current Mail/Pass) -*\n"
                f"```\n{stock_item['item_data']}\n```\n\n"
                f"💡 *ပြင်ဆင်နည်း -*\n"
                f"အောက်ပါ Command ပုံစံအတိုင်း ရိုက်ပို့ပါ -\n\n"
                f"`/editstock {stock_id} <အချက်အလက်သစ်>`\n\n"
                f"ဥပမာ -\n"
                f"`/editstock {stock_id} Email - new@mail.com | Pass - NewPass123`"
            )

            buttons = [
                [
                    InlineKeyboardButton(f"🗑️ ဤ Stock (#{stock_id}) ကို ဖျက်မည်", callback_data=f"adm_del_stock_{stock_id}"),
                ],
                [
                    InlineKeyboardButton("🔙 Stock စာရင်းသို့ ပြန်သွားမည်", callback_data="admin_view_all_credentials"),
                ],
            ]

            await safe_edit_message(
                query,
                edit_stock_text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 3.2 Delete a Specific Stock Item
        if data.startswith("adm_del_stock_"):
            stock_id = int(data.split("_")[3])
            stock_item = database.get_stock_by_id(stock_id)
            if not stock_item:
                await query.answer("❌ Stock ရှာမတွေ့ပါ။", show_alert=True)
                return

            database.delete_stock(stock_id)
            try:
                await query.answer(f"✅ Stock #{stock_id} ကို ဖျက်ပြီးပါပြီ!", show_alert=True)
            except Exception:
                pass

            await safe_edit_message(
                query,
                f"🗑️ Stock ID `#{stock_id}` ကို အောင်မြင်စွာ ဖျက်လိုက်ပါပြီ!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 Stock စာရင်းသို့ ပြန်သွားမည်", callback_data="admin_view_all_credentials")]
                ]),
            )
            return

        # 4. Product Management (Edit / Delete / View Stocks)
        if data == "admin_manage_prods":
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            s_name = database.get_store_name(store_id=admin_store_id)
            products = database.get_products_with_stock(store_id=admin_store_id)
            buttons = []
            for p in products:
                status_icon = "🟢" if p["stock_count"] > 0 else "🔴"
                clean_name = clean_single_icon_name(p["name"])
                btn_title = f"{status_icon} ID {p['id']} - {clean_name} ({p['stock_count']} ကျန်)"
                buttons.append([InlineKeyboardButton(btn_title, callback_data=f"adm_pview_{p['id']}")])

            buttons.append([InlineKeyboardButton("➕ Product အသစ် ထည့်မည်", callback_data="admin_interactive_add_product")])
            buttons.append([InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")])

            manage_text = (
                f"⚙️ *[{s_name} - Product စီမံခန့်ခွဲမှု Panel]*\n\n"
                "အောက်တွင် စီမံလိုသော Product ကို နှိပ်ပါ (Mail/Password များ ကြည့်ရှု/ပြင်ခြင်း၊ စျေးပြင်ခြင်း၊ ဖျက်ခြင်းများ ပြုလုပ်နိုင်ပါသည်) -"
            )
            await safe_edit_message(
                query,
                manage_text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 4.1 View Specific Product Details & Actions
        if data.startswith("adm_pview_") and not data.startswith("adm_pview_stocks_"):
            p_id = int(data.split("_")[2])
            product = database.get_product(p_id)
            if not product:
                await safe_edit_message(
                    query,
                    "❌ Product ရှာမတွေ့တော့ပါ။",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 စာရင်းသို့", callback_data="admin_manage_prods")]]),
                )
                return

            stock_badge = "🟢" if product["stock_count"] > 0 else "🔴"
            clean_name = clean_single_icon_name(product["name"])
            prod_info = (
                f"*{clean_name}* (ID - {product['id']})\n\n"
                f"• စျေးနှုန်း - `{product['price']:,} MMK`\n"
                f"• အုပ်စု - {product['category']}\n"
                f"• လက်ကျန် Stock - `{stock_badge} {product['stock_count']} ခု`\n"
                f"• အသေးစိတ် - {product['description']}\n\n"
                "👇 အောက်ပါခလုတ်များဖြင့် Mail/Password ကြည့်ရှုပြင်ဆင်ခြင်း၊ စျေးပြင်ခြင်း သို့မဟုတ် ဖျက်ခြင်း ပြုလုပ်ပါ -"
            )

            buttons = [
                [
                    InlineKeyboardButton("🔑 ဤပစ္စည်း၏ Stock အားလုံးကြည့်/ပြင်မည် (Mail/Pass)", callback_data=f"adm_pview_stocks_{product['id']}"),
                ],
                [
                    InlineKeyboardButton("💰 စျေးနှုန်း ပြင်မည်", callback_data=f"adm_pedit_price_{product['id']}"),
                    InlineKeyboardButton("📝 အမည် ပြင်မည်", callback_data=f"adm_pedit_name_{product['id']}"),
                ],
                [
                    InlineKeyboardButton("➕ Stock ထည့်မည်", callback_data=f"adm_padd_stock_{product['id']}"),
                    InlineKeyboardButton("🗑️ ပစ္စည်း ဖျက်မည်", callback_data=f"adm_pdel_confirm_{product['id']}"),
                ],
                [
                    InlineKeyboardButton("🔙 Product စာရင်းသို့", callback_data="admin_manage_prods"),
                ],
            ]

            await safe_edit_message(
                query,
                prod_info,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 4.2 View & Edit Stock Credentials for a Single Product
        if data.startswith("adm_pview_stocks_"):
            p_id = int(data.split("_")[3])
            product = database.get_product(p_id)
            if not product:
                return

            stocks = database.get_product_stock_items(p_id)
            if not stocks:
                no_stock_text = (
                    f"📦 *{product['name']}*\n\n"
                    "🔴 *လက်ရှိတွင် ဤ Product အတွက် မည်သည့် Stock မှ မရှိသေးပါ။*\n\n"
                    f"ထည့်သွင်းရန် - `/addstock {product['id']} <credentials>`"
                )
                buttons = [[InlineKeyboardButton("🔙 Product သို့", callback_data=f"adm_pview_{product['id']}")]]
                await safe_edit_message(query, no_stock_text, reply_markup=InlineKeyboardMarkup(buttons))
                return

            lines = [f"🔑 *{product['name']} - Stock စာရင်း ({len(stocks)} ခု) -*\n"]
            buttons = []
            for s in stocks:
                status_str = "🟢 [ရောင်းရန်ကျန် / Available]" if s["is_sold"] == 0 else f"🔴 [ရောင်းပြီး / Sold to ID - {s['sold_to_user_id']}]"
                lines.append(
                    f"{status_str} #Stock ID - `{s['id']}`\n"
                    f"```\n{s['item_data']}\n```\n"
                )
                if s["is_sold"] == 0:
                    buttons.append([
                        InlineKeyboardButton(f"✏️ Edit #{s['id']} (Mail/Pass)", callback_data=f"adm_edit_stock_{s['id']}")
                    ])

            buttons.append([InlineKeyboardButton("➕ Stock ထပ်ထည့်မည်", callback_data=f"adm_padd_stock_{product['id']}")])
            buttons.append([InlineKeyboardButton("🔙 Product သို့ ပြန်သွားမည်", callback_data=f"adm_pview_{product['id']}")])

            await safe_edit_message(
                query,
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 4.3 Delete Product - Confirmation Step
        if data.startswith("adm_pdel_confirm_"):
            p_id = int(data.split("_")[3])
            product = database.get_product(p_id)
            if not product:
                await query.answer("❌ ရှာမတွေ့ပါ။", show_alert=True)
                return

            confirm_text = (
                f"⚠️ *သတိပေးချက်!*\n\n"
                f"*{product['name']}* (ID - `{product['id']}`) နှင့် ၎င်း၏ မရောင်းရသေးသော Stock များကို "
                f"တကယ်ဖျက်ပစ်ရန် သေချာပါသလား?"
            )
            buttons = [
                [
                    InlineKeyboardButton("🗑️ ဟုတ်ကဲ့၊ အတည်ပြု ဖျက်မည်", callback_data=f"adm_pdel_do_{product['id']}"),
                ],
                [
                    InlineKeyboardButton("❌ မဖျက်တော့ပါ (Cancel)", callback_data=f"adm_pview_{product['id']}"),
                ],
            ]
            await safe_edit_message(
                query,
                confirm_text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 4.4 Delete Product - Execution
        if data.startswith("adm_pdel_do_"):
            p_id = int(data.split("_")[3])
            product = database.get_product(p_id)
            database.delete_product(p_id)
            try:
                await query.answer("✅ ပစ္စည်းကို အောင်မြင်စွာ ဖျက်လိုက်ပါပြီ!", show_alert=True)
            except Exception:
                pass

            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            products = database.get_products_with_stock(store_id=admin_store_id)
            buttons = []
            for p in products:
                status_icon = "🟢" if p["stock_count"] > 0 else "🔴"
                clean_name = clean_single_icon_name(p["name"])
                btn_title = f"{status_icon} ID {p['id']} - {clean_name} ({p['price']:,} K)"
                buttons.append([InlineKeyboardButton(btn_title, callback_data=f"adm_pview_{p['id']}")])
            buttons.append([InlineKeyboardButton("➕ Product အသစ် ထည့်မည်", callback_data="adm_padd_info")])
            buttons.append([InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")])

            del_result_text = (
                "🗑️ *Product ဖျက်ခြင်း ပြီးမြောက်ပါပြီ!*\n\n"
                "ကျန်ရှိသော Product များကို အောက်တွင် ဆက်လက် စီမံနိုင်ပါသည် -"
            )
            await safe_edit_message(
                query,
                del_result_text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 4.5 Edit Price Prompt & Presets
        if data.startswith("adm_pedit_price_"):
            p_id = int(data.split("_")[3])
            product = database.get_product(p_id)
            if not product:
                return

            curr_price = product["price"]
            price_text = (
                f"💰 *စျေးနှုန်း ပြင်ဆင်ရန် (ID - {product['id']})*\n\n"
                f"📦 *Product -* {product['name']}\n"
                f"💵 *လက်ရှိစျေးနှုန်း -* `{curr_price:,} MMK`\n\n"
                "အောက်ပါ Preset ခလုတ်များဖြင့် စျေးနှုန်း အတိုးအလျှော့ ပြုလုပ်နိုင်ပါသည် သို့မဟုတ် "
                f"Command ရိုက်ပို့ပါ -\n\n"
                f"`/editprice {product['id']} <စျေးနှုန်းသစ်>`"
            )

            buttons = [
                [
                    InlineKeyboardButton("➖ 1,000", callback_data=f"adm_pset_{product['id']}_{max(0, curr_price - 1000)}"),
                    InlineKeyboardButton("➕ 1,000", callback_data=f"adm_pset_{product['id']}_{curr_price + 1000}"),
                ],
                [
                    InlineKeyboardButton("➖ 5,000", callback_data=f"adm_pset_{product['id']}_{max(0, curr_price - 5000)}"),
                    InlineKeyboardButton("➕ 5,000", callback_data=f"adm_pset_{product['id']}_{curr_price + 5000}"),
                ],
                [
                    InlineKeyboardButton("🔙 နောက်သို့", callback_data=f"adm_pview_{product['id']}"),
                ],
            ]
            await safe_edit_message(
                query,
                price_text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        # 4.6 Quick Preset Price Set
        if data.startswith("adm_pset_"):
            parts = data.split("_")
            p_id = int(parts[2])
            new_p = int(parts[3])
            database.update_product_price(p_id, new_p)
            try:
                await query.answer(f"✅ စျေးနှုန်း {new_p:,} MMK သို့ ပြောင်းလဲပြီးပါပြီ!", show_alert=True)
            except Exception:
                pass

            product = database.get_product(p_id)
            if product:
                curr_price = product["price"]
                price_text = (
                    f"💰 *စျေးနှုန်း ပြင်ဆင်ရန် (ID - {product['id']})*\n\n"
                    f"📦 *Product -* {product['name']}\n"
                    f"💵 *လက်ရှိစျေးနှုန်း -* `{curr_price:,} MMK` (Updated!)\n\n"
                    f"Command ဖြင့် စိတ်ကြိုက်ပြင်ရန် -\n"
                    f"`/editprice {product['id']} <စျေးနှုန်းသစ်>`"
                )
                buttons = [
                    [
                        InlineKeyboardButton("➖ 1,000", callback_data=f"adm_pset_{product['id']}_{max(0, curr_price - 1000)}"),
                        InlineKeyboardButton("➕ 1,000", callback_data=f"adm_pset_{product['id']}_{curr_price + 1000}"),
                    ],
                    [
                        InlineKeyboardButton("➖ 5,000", callback_data=f"adm_pset_{product['id']}_{max(0, curr_price - 5000)}"),
                        InlineKeyboardButton("➕ 5,000", callback_data=f"adm_pset_{product['id']}_{curr_price + 5000}"),
                    ],
                    [
                        InlineKeyboardButton("🔙 နောက်သို့", callback_data=f"adm_pview_{product['id']}"),
                    ],
                ]
                await safe_edit_message(query, price_text, reply_markup=InlineKeyboardMarkup(buttons))
            return

        # 4.7 Edit Name Guide
        if data.startswith("adm_pedit_name_"):
            p_id = int(data.split("_")[3])
            product = database.get_product(p_id)
            if not product:
                return

            name_guide = (
                f"📝 *အမည် ပြင်ဆင်နည်း (ID - {product['id']})*\n\n"
                f"လက်ရှိအမည် - *{product['name']}*\n\n"
                "အမည်သစ် ပြောင်းလိုပါက အောက်ပါအတိုင်း Command ရိုက်ပို့ပါ -\n\n"
                f"`/editname {product['id']} <အမည်သစ်>`\n\n"
                f"ဥပမာ - `/editname {product['id']} {product['name']} (Promo)`"
            )
            buttons = [[InlineKeyboardButton("🔙 နောက်သို့", callback_data=f"adm_pview_{product['id']}")]]
            await safe_edit_message(query, name_guide, reply_markup=InlineKeyboardMarkup(buttons))
            return

        # 4.8 Interactive Add Stock for Product
        if data.startswith("adm_padd_stock_"):
            p_id = int(data.split("_")[3])
            await render_interactive_stock_input_prompt(query, user.id, p_id, context)
            return

        # 4.9 Interactive Add Product
        if data == "adm_padd_info":
            await start_interactive_add_product(query, user.id, context)
            return

        # 5. Admin Recent Orders
        if data == "admin_orders":
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            s_name = database.get_store_name(store_id=admin_store_id)
            orders = database.get_recent_orders(limit=8, store_id=admin_store_id)
            if not orders:
                orders_text = f"📜 *[{s_name} - မကြာမီက အော်ဒါများ]* မရှိသေးပါ"
            else:
                lines = [f"📜 *[{s_name} - လတ်တလော အရောင်းအော်ဒါများ (Recent Orders)]*\n"]
                for o in orders:
                    username_clean = str(o['username']).replace("_", "\\_") if o['username'] else "N/A"
                    first_name_clean = str(o['first_name']).replace("*", "").replace("_", "") if o['first_name'] else "User"
                    time_str = str(o["purchased_at"])[:16]
                    lines.append(
                        f"• *{o['product_name']}*\n"
                        f"  👤 {first_name_clean} (@{username_clean})\n"
                        f"  💰 `{o['price']:,} MMK` • 📅 {time_str}\n"
                    )
                orders_text = "\n".join(lines)

            await safe_edit_message(
                query,
                orders_text,
                reply_markup=get_admin_back_markup(),
            )
            return

        # 6. Admin Users List & Balance Management
        if data == "admin_users":
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            s_name = database.get_store_name(store_id=admin_store_id)
            users = database.get_all_users(store_id=admin_store_id)
            lines = [f"👥 *[{s_name} - အသုံးပြုသူ စာရင်း (စုစုပေါင်း {len(users)} ယောက်)]*\n"]
            for u in users[:10]:
                username_clean = str(u['username']).replace("_", "\\_") if u['username'] else "N/A"
                name_clean = str(u['first_name']).replace("*", "").replace("_", "") if u['first_name'] else "User"
                lines.append(
                    f"• *{name_clean}* (@{username_clean})\n"
                    f"  🆔 `{u['user_id']}` • 💰 `{u['balance']:,} MMK`\n"
                )
            if len(users) > 10:
                lines.append(f"...နှင့် အခြား {len(users) - 10} ဦး")
            lines.append("\n💡 _အောက်ပါခလုတ်များကို နှိပ်၍ အသုံးပြုသူတစ်ဦးချင်းစီ၏ Balance ကို ပြင်/ဖျက်နိုင်ပါသည် -_")
            users_text = "\n".join(lines)

            user_buttons = []
            for u in users[:6]:
                uname = f"@{u['username']}" if u.get("username") else f"User {u['user_id']}"
                user_buttons.append([
                    InlineKeyboardButton(f"💰 {uname} ({u['balance']:,} K)", callback_data=f"adm_bal_view_{u['user_id']}")
                ])

            user_buttons.append([
                InlineKeyboardButton("🔍 User ID ရှာဖွေ/ပြင်ဆင်မည်", callback_data="adm_bal_search_prompt")
            ])
            user_buttons.append([
                InlineKeyboardButton("🔄 Refresh", callback_data="admin_users"),
                InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home"),
            ])

            await safe_edit_message(
                query,
                users_text,
                reply_markup=InlineKeyboardMarkup(user_buttons),
            )
            return

        # 6.1. Balance Management Panel
        if data == "admin_bal_panel":
            await render_balance_manager_panel(query, user.id)
            return

        # 6.2. Balance Search Prompt
        if data == "adm_bal_search_prompt":
            context.user_data["admin_state"] = "waiting_bal_search_uid"
            prompt_msg = (
                "🔍 *[User ID ရှာဖွေ/ပြင်ဆင်ခြင်း]*\n\n"
                "Balance စစ်ဆေး/ပြင်ဆင်လိုသော **Telegram User ID** ကို စာရိုက်ပို့ပေးပါ -\n\n"
                "📌 _နမူနာ - `12345678`_\n\n"
                "_(မလုပ်ဆောင်တော့ပါက Cancel ကို နှိပ်ပါ)_"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ မလုပ်တော့ပါ (Cancel)", callback_data="admin_users")],
                [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
            ])
            await safe_edit_message(query, prompt_msg, reply_markup=markup)
            return

        # 6.3. User Balance Management View Card
        if data.startswith("adm_bal_view_"):
            target_id = int(data.replace("adm_bal_view_", ""))
            await render_user_balance_view(query, target_id, user.id, context)
            return

        # 6.4. User Balance Set (Direct Edit) Prompt
        if data.startswith("adm_bal_edit_"):
            target_id = int(data.replace("adm_bal_edit_", ""))
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, admin_store_id)
            if not can_manage:
                await query.answer(err_msg, show_alert=True)
                return

            context.user_data["admin_state"] = f"waiting_edit_bal_{target_id}"
            target_user = database.get_user(target_id)
            curr_b = target_user.get("balance", 0) if target_user else 0

            prompt_msg = (
                f"✏️ *[Balance အသစ် သတ်မှတ်ခြင်း]*\n\n"
                f"🆔 User ID - `{target_id}`\n"
                f"💵 လက်ရှိ Balance - `{curr_b:,} MMK`\n\n"
                f"👉 ဤ User အတွက် **သတ်မှတ်လိုသော Balance ပမာဏ (MMK)** ကို စာရိုက်ပို့ပေးပါ -\n\n"
                f"📌 _နမူနာ - `50000` (သို့မဟုတ် `0`)_\n\n"
                f"_(မပြင်ဆင်လိုပါက Cancel ကို နှိပ်ပါ)_"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ မပြင်တော့ပါ (Cancel)", callback_data=f"adm_bal_view_{target_id}")],
                [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
            ])
            await safe_edit_message(query, prompt_msg, reply_markup=markup)
            return

        # 6.5. User Balance Add (+) Prompt
        if data.startswith("adm_bal_add_"):
            target_id = int(data.replace("adm_bal_add_", ""))
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, admin_store_id)
            if not can_manage:
                await query.answer(err_msg, show_alert=True)
                return

            context.user_data["admin_state"] = f"waiting_add_bal_{target_id}"
            target_user = database.get_user(target_id)
            curr_b = target_user.get("balance", 0) if target_user else 0

            prompt_msg = (
                f"➕ *[ငွေဖြည့်သွင်းခြင်း (+)]*\n\n"
                f"🆔 User ID - `{target_id}`\n"
                f"💵 လက်ရှိ Balance - `{curr_b:,} MMK`\n\n"
                f"👉 ဤ User ထံ **ထည့်သွင်းလိုသော ပမာဏ (MMK)** ကို စာရိုက်ပို့ပေးပါ -\n\n"
                f"📌 _နမူနာ - `10000` သို့မဟုတ် `25000`_\n\n"
                f"_(မထည့်တော့ပါက Cancel ကို နှိပ်ပါ)_"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ မထည့်တော့ပါ (Cancel)", callback_data=f"adm_bal_view_{target_id}")],
                [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
            ])
            await safe_edit_message(query, prompt_msg, reply_markup=markup)
            return

        # 6.6. User Balance Deduct (-) Prompt
        if data.startswith("adm_bal_sub_"):
            target_id = int(data.replace("adm_bal_sub_", ""))
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, admin_store_id)
            if not can_manage:
                await query.answer(err_msg, show_alert=True)
                return

            context.user_data["admin_state"] = f"waiting_sub_bal_{target_id}"
            target_user = database.get_user(target_id)
            curr_b = target_user.get("balance", 0) if target_user else 0

            prompt_msg = (
                f"➖ *[ငွေနှုတ်ယူခြင်း (-)]*\n\n"
                f"🆔 User ID - `{target_id}`\n"
                f"💵 လက်ရှိ Balance - `{curr_b:,} MMK`\n\n"
                f"👉 ဤ User ထံမှ **နှုတ်ယူလိုသော ပမာဏ (MMK)** ကို စာရိုက်ပို့ပေးပါ -\n\n"
                f"📌 _နမူနာ - `5000` သို့မဟုတ် `10000`_\n\n"
                f"_(မနှုတ်တော့ပါက Cancel ကို နှိပ်ပါ)_"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ မနှုတ်တော့ပါ (Cancel)", callback_data=f"adm_bal_view_{target_id}")],
                [InlineKeyboardButton("🔙 Admin Panel သို့", callback_data="admin_home")],
            ])
            await safe_edit_message(query, prompt_msg, reply_markup=markup)
            return

        # 6.7. User Balance Reset / Remove (0 K) Action
        if data.startswith("adm_bal_zero_"):
            target_id = int(data.replace("adm_bal_zero_", ""))
            active_store = get_admin_active_store(user.id)
            admin_store_id = active_store["id"]
            can_manage, err_msg = can_admin_manage_user_balance(user.id, target_id, admin_store_id)
            if not can_manage:
                await query.answer(err_msg, show_alert=True)
                return

            old_user = database.get_user(target_id)
            old_bal = old_user.get("balance", 0) if old_user else 0
            database.set_user_balance(target_id, 0)
            s_name = database.get_store_name(store_id=admin_store_id)

            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🔔 *[{s_name} - Wallet Balance ရှင်းလင်းခြင်း]*\n\n"
                         f"Admin မှ သင့် Wallet Balance ကို `0 MMK` သို့ ဖျက်သိမ်း/ရှင်းလင်းလိုက်ပါသည်။\n"
                         f"💰 *လက်ရှိလက်ကျန်ငွေ -* `0 MMK`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_id} of zero balance - {e}")

            await query.answer("✅ Balance ကို 0 MMK သို့ ဖျက်သိမ်းလိုက်ပါပြီ!", show_alert=True)
            await render_user_balance_view(
                query,
                target_id,
                user.id,
                context,
                notice=f"🗑️ User ID `{target_id}` ၏ Balance (ယခင် `{old_bal:,} MMK`) ကို `0 MMK` သို့ အောင်မြင်စွာ ဖျက်သိမ်းလိုက်ပါပြီ!"
            )
            return

        # 7. Admin Close
        if data == "admin_close":
            await safe_edit_message(
                query,
                "✅ Admin Panel မှ ထွက်ခွာပြီးပါပြီ။ Menu ခလုတ်များဖြင့် ပုံမှန်အသုံးပြုနိုင်ပါသည်။",
                reply_markup=None,
            )
            return


class ClientBotManager:
    """
    Enterprise Dynamic Multi-Bot Manager:
    - Concurrently manages, starts, stops, and supervises all client store bots.
    - Handles automatic startup on server boot and instant start when admins link new tokens.
    - Continuous supervisor loop that recovers dead or unresponsive bots automatically.
    - Full store isolation using TypeHandler middleware and contextvars.
    """

    def __init__(self):
        self.running_apps: Dict[int, Application] = {}
        self.supervisor_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def build_client_app(self, store_id: int, bot_token: str) -> Application:
        """Constructs an Application configured for a specific tenant store with complete handler mapping."""
        request = HTTPXRequest(
            connect_timeout=45.0,
            read_timeout=45.0,
            write_timeout=45.0,
            pool_timeout=10.0,
            http_version="1.1",
        )
        app = ApplicationBuilder().token(bot_token).request(request).build()
        app.bot_data["store_id"] = store_id

        # TypeHandler middleware in group=-100 to guarantee store isolation on every update
        async def _scope_tenant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            current_store_id_ctx.set(store_id)
            if hasattr(context, "bot_data"):
                context.bot_data["store_id"] = store_id

        app.add_handler(TypeHandler(Update, _scope_tenant), group=-100)

        # User Navigation & Service Commands
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("menu", show_home))
        app.add_handler(CommandHandler("cancel", cancel_command))
        app.add_handler(CommandHandler("products", show_products))
        app.add_handler(CommandHandler("topup", show_topup))
        app.add_handler(CommandHandler(["thaiqr", "qr"], show_thai_qr))
        app.add_handler(CommandHandler("status", show_member_status))
        app.add_handler(CommandHandler("referral", show_referral))
        app.add_handler(CommandHandler("history", show_history))
        app.add_handler(CommandHandler("guide", show_guide))
        app.add_handler(CommandHandler("language", show_language_menu))
        app.add_handler(CommandHandler("help", show_help))

        # Admin Store Management Commands
        app.add_handler(CommandHandler("admin", admin_panel_command))
        app.add_handler(CommandHandler("setrate", set_rate_command))
        app.add_handler(CommandHandler("claimadmin", claim_admin_command))
        app.add_handler(CommandHandler("addadmin", add_admin_command))
        app.add_handler(CommandHandler("editbalance", edit_balance_command))
        app.add_handler(CommandHandler(["removebalance", "resetbalance"], remove_balance_command))
        app.add_handler(CommandHandler(["deductbalance", "subbalance"], deduct_balance_command))
        app.add_handler(CommandHandler("addbalance", add_balance_command))
        app.add_handler(CommandHandler("addstock", add_stock_command))
        app.add_handler(CommandHandler("editstock", edit_stock_command))
        app.add_handler(CommandHandler("deletestock", delete_stock_command))
        app.add_handler(CommandHandler("editprice", edit_price_command))
        app.add_handler(CommandHandler("editname", edit_name_command))
        app.add_handler(CommandHandler("deleteproduct", delete_product_command))
        app.add_handler(CommandHandler("addproduct", add_product_command))
        app.add_handler(CommandHandler("broadcast", broadcast_command))
        app.add_handler(CommandHandler("checkuser", check_user_command))
        app.add_handler(CommandHandler("setstorename", set_store_name_command))
        app.add_handler(CommandHandler("setcontact", set_contact_command))
        app.add_handler(CommandHandler("license", license_status_command))
        app.add_handler(CommandHandler(["renew", "extendlicense"], renew_license_command))
        app.add_handler(CommandHandler("bindbot", bind_bot_command))
        app.add_handler(CommandHandler("unlinkbot", unlink_bot_command))

        # Bot Token Linking Conversation Handler
        app.add_handler(create_bot_token_conv_handler())

        # Callback Queries & Interactive Menus
        app.add_handler(CallbackQueryHandler(callback_query_handler))

        # Payment Slip Upload Handler
        app.add_handler(MessageHandler(filters.PHOTO, slip_photo_handler))

        # Text Messages & Reply Keyboards Router
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

        # Error handling
        app.add_error_handler(error_handler)

        return app

    async def stop_instance(self, store_id: int) -> bool:
        """Stops polling and shuts down an individual tenant bot application."""
        async with self._lock:
            if store_id in self.running_apps:
                old_app = self.running_apps.pop(store_id)
                try:
                    if old_app.updater and old_app.updater.running:
                        await old_app.updater.stop()
                    if old_app.running:
                        await old_app.stop()
                    await old_app.shutdown()
                    logger.info(f"Stopped bot instance for store #{store_id}")
                    return True
                except Exception as e:
                    logger.warning(f"Error stopping bot instance for store #{store_id}: {e}")
        return False

    async def start_instance(self, store_id: int, bot_token: str, max_retries: int = 3) -> bool:
        """Starts polling for a tenant bot token with retry logic and command menu configuration."""
        bot_token = clean_bot_token(bot_token)
        if not bot_token:
            logger.warning(f"Empty bot token for store #{store_id}")
            return False

        # Fast pre-validation check against Telegram API to avoid hanging on invalid tokens
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe")
                if res.status_code != 200 or not res.json().get("ok"):
                    logger.warning(f"Token for store #{store_id} rejected by Telegram API ({res.status_code}). Skipping start.")
                    return False
        except Exception as e:
            logger.warning(f"Pre-check connection error for store #{store_id}: {e}. Proceeding with cautious startup...")

        # Stop existing instance if already registered
        if store_id in self.running_apps:
            await self.stop_instance(store_id)

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[Attempt {attempt}/{max_retries}] Starting tenant bot instance for store #{store_id}...")
                app = self.build_client_app(store_id, bot_token)
                await app.initialize()
                await app.start()
                await app.updater.start_polling(drop_pending_updates=True, bootstrap_retries=-1, poll_interval=1.0)

                # Configure Bot Command Menu for this tenant
                try:
                    commands = [
                        BotCommand("start", "Shop home"),
                        BotCommand("menu", "Main menu"),
                        BotCommand("language", "Change language"),
                        BotCommand("cancel", "Cancel current action"),
                    ]
                    await app.bot.set_my_commands(commands)
                except Exception as ce:
                    logger.warning(f"Could not configure command menu for store #{store_id}: {ce}")

                async with self._lock:
                    self.running_apps[store_id] = app

                logger.info(f"✅ Successfully launched and listening to client bot instance for store #{store_id}")
                return True
            except Exception as e:
                logger.error(f"[Attempt {attempt}/{max_retries}] Failed to start bot instance for store #{store_id} - {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)

        return False

    async def sync_all_active_bots(self) -> None:
        """
        Synchronizes all active stores in the database with running bot instances:
        - Starts bots for stores with valid tokens that aren't running yet.
        - Restarts bots whose tokens changed or whose updater stopped.
        - Stops bots for stores whose tokens were removed or unlinked.
        """
        try:
            stores = database.get_all_stores()
            active_store_ids = set()

            for s in stores:
                s_id = s["id"]
                token = clean_bot_token(s.get("bot_token"))
                if s_id == 1 or not token or token == TOKEN:
                    continue

                active_store_ids.add(s_id)

                # Check if already running
                if s_id in self.running_apps:
                    curr_app = self.running_apps[s_id]
                    # Check if token changed
                    if getattr(curr_app.bot, "token", "") != token:
                        logger.info(f"Token changed for store #{s_id}. Restarting bot instance...")
                        await self.stop_instance(s_id)
                        await self.start_instance(s_id, token)
                    elif not (curr_app.updater and curr_app.updater.running):
                        logger.warning(f"Updater not running for store #{s_id}. Restarting...")
                        await self.stop_instance(s_id)
                        await self.start_instance(s_id, token)
                else:
                    logger.info(f"Starting newly discovered tenant bot for store #{s_id} ({s.get('store_name')})...")
                    await self.start_instance(s_id, token)

            # Cleanup removed or unlinked instances
            running_ids = list(self.running_apps.keys())
            for rid in running_ids:
                if rid not in active_store_ids:
                    logger.info(f"Stopping unlinked bot instance for store #{rid}")
                    await self.stop_instance(rid)

        except Exception as e:
            logger.error(f"Error in sync_all_active_bots: {e}", exc_info=True)

    async def run_supervisor(self) -> None:
        """Periodic supervisor loop ensuring all tenant bots stay active, responsive, and recover from failures."""
        logger.info("Multi-bot supervisor background task started.")
        while True:
            try:
                await self.sync_all_active_bots()
            except Exception as e:
                logger.error(f"Supervisor error during bot synchronization: {e}")
            await asyncio.sleep(30)

    async def stop_all(self) -> None:
        """Shuts down all client bot instances cleanly."""
        running_ids = list(self.running_apps.keys())
        for rid in running_ids:
            await self.stop_instance(rid)


client_bot_manager = ClientBotManager()


# ==============================================================================
# Main Startup
# ==============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error("Exception while handling an update -", exc_info=context.error)


async def start_optional_healthcheck_server() -> Optional[asyncio.Server]:
    """
    Starts a lightweight HTTP health check server if PORT is defined (e.g., Render Web Service or Railway).
    Allows the bot to function seamlessly as both a Background Worker or a Web Service with zero crashes.
    """
    port_str = os.getenv("PORT")
    if not port_str or not port_str.strip().isdigit():
        return None
    port = int(port_str.strip())

    async def handle_http_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
            body = b"OK - Multi-tenant Telegram Bot is Healthy\n"
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Content-Length: " + str(len(body)).encode("utf-8") + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(handle_http_request, "0.0.0.0", port)
        logger.info(f"🌐 Cloud HTTP health check server listening on 0.0.0.0:{port}")
        return server
    except Exception as e:
        logger.warning(f"Could not start cloud health check server on port {port}: {e}")
        return None


async def post_shutdown(application: Application) -> None:
    """Gracefully shuts down all background tasks and tenant bot applications on container restart/exit."""
    logger.info("Application shutting down. Stopping all tenant client bot instances...")
    if client_bot_manager.supervisor_task:
        client_bot_manager.supervisor_task.cancel()
    await client_bot_manager.stop_all()
    logger.info("All tenant instances closed cleanly.")


async def post_init(application: Application) -> None:
    """Configures the Telegram Bot Command Menu and launches the dynamic multi-bot manager and healthcheck."""
    commands = [
        BotCommand("start", "Shop home"),
        BotCommand("menu", "Main menu"),
        BotCommand("language", "Change language"),
        BotCommand("cancel", "Cancel current action"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Telegram Bot Command Menu successfully configured via set_my_commands().")

    # Start optional cloud HTTP healthcheck server for Render/Railway Web Service deployments
    asyncio.create_task(start_optional_healthcheck_server())

    # Initial synchronization and launch of all active client store bots
    await client_bot_manager.sync_all_active_bots()

    # Start background supervisor for automatic self-healing and dynamic token listening
    client_bot_manager.supervisor_task = asyncio.create_task(client_bot_manager.run_supervisor())


def main() -> None:
    """Starts the Digital Store Bot with Manual Slip Approval & Admin Panel."""
    print("Initializing Database...")
    database.init_db()

    print("Configuring Telegram Bot Application...")
    request = HTTPXRequest(
        connect_timeout=45.0,
        read_timeout=45.0,
        write_timeout=45.0,
        pool_timeout=10.0,
        http_version="1.1",
    )
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # User Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", show_home))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("products", show_products))
    application.add_handler(CommandHandler("topup", show_topup))
    application.add_handler(CommandHandler(["thaiqr", "qr"], show_thai_qr))
    application.add_handler(CommandHandler("status", show_member_status))
    application.add_handler(CommandHandler("referral", show_referral))
    application.add_handler(CommandHandler("history", show_history))
    application.add_handler(CommandHandler("guide", show_guide))
    application.add_handler(CommandHandler("language", show_language_menu))
    application.add_handler(CommandHandler("help", show_help))

    # Admin Management Commands
    application.add_handler(CommandHandler("admin", admin_panel_command))
    application.add_handler(CommandHandler("setrate", set_rate_command))
    application.add_handler(CommandHandler("claimadmin", claim_admin_command))
    application.add_handler(CommandHandler("addadmin", add_admin_command))
    application.add_handler(CommandHandler("editbalance", edit_balance_command))
    application.add_handler(CommandHandler(["removebalance", "resetbalance"], remove_balance_command))
    application.add_handler(CommandHandler(["deductbalance", "subbalance"], deduct_balance_command))
    application.add_handler(CommandHandler("addbalance", add_balance_command))
    application.add_handler(CommandHandler("addstock", add_stock_command))
    application.add_handler(CommandHandler("editstock", edit_stock_command))
    application.add_handler(CommandHandler("deletestock", delete_stock_command))
    application.add_handler(CommandHandler("editprice", edit_price_command))
    application.add_handler(CommandHandler("editname", edit_name_command))
    application.add_handler(CommandHandler("deleteproduct", delete_product_command))
    application.add_handler(CommandHandler("addproduct", add_product_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("checkuser", check_user_command))

    # Super Admin Multi-tenant Store Management Commands
    application.add_handler(CommandHandler("stores", list_stores_command))
    application.add_handler(CommandHandler("linkbot", link_bot_command))
    application.add_handler(CommandHandler("extendstore", extend_store_command))
    application.add_handler(CommandHandler("suspendstore", suspend_store_command))
    application.add_handler(CommandHandler("activatestore", activate_store_command))
    application.add_handler(CommandHandler("storeinfo", store_info_command))

    # Store Admin Bot Binding Commands
    application.add_handler(CommandHandler("bindbot", bind_bot_command))
    application.add_handler(CommandHandler("unlinkbot", unlink_bot_command))

    # SaaS Store Customization & Licensing Commands
    application.add_handler(CommandHandler("setstorename", set_store_name_command))
    application.add_handler(CommandHandler("setcontact", set_contact_command))
    application.add_handler(CommandHandler("license", license_status_command))
    application.add_handler(CommandHandler(["renew", "extendlicense"], renew_license_command))
    application.add_handler(CommandHandler("setlicense", set_license_command))
    application.add_handler(CommandHandler(["killswitch", "suspendlicense"], kill_switch_command))
    application.add_handler(CommandHandler("activatelicense", activate_license_command))

    # Bot Token Linking Conversation Handler
    application.add_handler(create_bot_token_conv_handler())

    # Callback Queries
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    # Photo Handler (For user payment slip uploads & store requests)
    application.add_handler(MessageHandler(filters.PHOTO, slip_photo_handler))

    # Text Messages (Reply Keyboard Menu)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Error handler
    application.add_error_handler(error_handler)

    print("==========================================================")
    print(f" 🚀 {STORE_NAME} Bot is running!")
    print(" 📱 Telegram Bot: @pyae_test_bot")
    print(" 👑 Admin Command: /admin")
    print(" 🏢 SaaS Super Admin Commands: /stores, /linkbot, /storeinfo")
    print(" 🛑 Press Ctrl+C to stop.")
    print("==========================================================")

    application.run_polling(drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
