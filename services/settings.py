from config import Config
from core import db, utils
from core.cache import cache
from core.errors import ApiError

DEFAULT_WELCOME = (
    "👋 <b>Welcome, {first_name}!</b>\n\n"
    "Get your license key in a few taps — pick an app, choose a plan and pay with UPI.\n\n"
    "Tap <b>Shop</b> below to begin 👇"
)


RICH_DEFAULTS = {
    "referral": ("🎁 <b>Refer &amp; Earn</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                 "Invite your friends with your personal link and earn <b>{percent}%</b> commission on <b>every purchase</b> "
                 "they make — forever.\n\n🔗 Your link:\n<code>{link}</code>\n\n"
                 "👥 Friends joined: <b>{count}</b>\n💰 You earned: <b>{earned}</b>"),
    "reseller": ("🏪 <b>Become a Reseller</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                 "Run your own store with <b>your own bot, your own UPI and your own prices</b>.\n\n"
                 "📩 Contact the admin from the button below — your reseller panel username &amp; password will be "
                 "given to you manually."),
    "reseller_panel_support": ("🛟 <b>Need help with your panel?</b>\n\nContact the admin and we will help you "
                               "as soon as possible."),
    "reseller_due": ("🛠 <b>Store under maintenance</b>\n\nThe store owner needs to clear the previous dues to renew "
                     "this store. Please try again later."),
}


def clean_rich(d, label="Message", required=True):
    """Validate a {text, format, image} message edited in the panel."""
    d = d or {}
    fmt = d.get("format", "html")
    if fmt not in ("html", "text"):
        raise ApiError("Message format must be html or text.")
    text = utils.clean_text(d.get("text"), label, 3500, required)
    image = utils.clean_text(d.get("image"), "Image", 400, False)
    return {"text": text, "format": fmt, "image": image}


def get_rich(key):
    s = db.get(f"/settings/messages/{key}") or {}
    return {"text": s.get("text") or RICH_DEFAULTS.get(key, ""), "format": s.get("format") or "html",
            "image": s.get("image") or ""}


def save_rich(key, d):
    db.set(f"/settings/messages/{key}", clean_rich(d))
    cache.clear()


def bot_settings():
    hit = cache.get("bot_settings")
    if hit is not None:
        return hit
    s = db.get("/settings/bot") or {}
    out = {
        "welcome_message": s.get("welcome_message") or DEFAULT_WELCOME,
        "welcome_format": s.get("welcome_format") or "html",
        "welcome_image": s.get("welcome_image") or "",
        "support_username": s.get("support_username") or "",
        "admin_chat_id": str(s.get("admin_chat_id") or Config.ADMIN_CHAT_ID or ""),
        "admin2_chat_id": str(s.get("admin2_chat_id") or Config.ADMIN2_CHAT_ID or ""),
    }
    cache.set("bot_settings", out, 5)
    return out


def save_bot_settings(data):
    fmt = data.get("welcome_format", "html")
    if fmt not in ("html", "text"):
        raise ApiError("Welcome format must be html or text.")
    admin_chat = str(data.get("admin_chat_id") or "").strip()
    if admin_chat:
        admin_chat = utils.clean_chat_id(admin_chat, "Admin chat ID")
    support = utils.clean_text(data.get("support_username"), "Support username", 64, False).lstrip("@")
    if support and not support.replace("_", "").isalnum():
        raise ApiError("Support username is invalid.")
    admin2 = str(data.get("admin2_chat_id") or "").strip()
    if admin2:
        admin2 = utils.clean_chat_id(admin2, "2nd admin chat ID")
    msg = utils.clean_text(data.get("welcome_message"), "Welcome message", 3500)
    image = utils.clean_text(data.get("welcome_image"), "Welcome image", 400, False)
    db.update("/settings/bot", {"welcome_message": msg, "welcome_format": fmt, "welcome_image": image,
                                "support_username": support, "admin_chat_id": admin_chat, "admin2_chat_id": admin2})
    cache.clear()


# ---------------------------------------------------------------- channels
def channels(include_inactive=False):
    rows = [c for c in db.get_list("/channels") if not c.get("deleted")]
    rows.sort(key=lambda c: c.get("created_at", 0))
    return rows


def channel_join_counts(channel_id, start, end):
    joins = db.get(f"/channel_joins/{channel_id}", {}) or {}
    return sum(1 for ts in joins.values() if isinstance(ts, (int, float)) and start <= ts <= end)


# ---------------------------------------------------------------- maintenance
def maintenance_rules():
    return [r for r in db.get_list("/maintenance") if not r.get("deleted")]


def active_maintenance(app_id):
    """Return the enabled maintenance rule that applies to the app (app-specific wins over 'all')."""
    hit = cache.get(("maint", app_id))
    if hit is not None:
        return hit[0]
    rules = [r for r in maintenance_rules() if r.get("enabled")]
    chosen = next((r for r in rules if r.get("app_id") == app_id), None) or \
        next((r for r in rules if r.get("app_id") == "all"), None)
    cache.set(("maint", app_id), (chosen,), 5)
    return chosen


# ---------------------------------------------------------------- feature switches (admin: Settings -> Features)
def features():
    """{'referral', 'reseller_signup', 'seller_signup'} -> bool. Everything is ON until the admin switches it off."""
    hit = cache.get("features")
    if hit is not None:
        return hit
    from services import sellers
    s = db.get("/settings/features") or {}
    out = {"referral": bool(s.get("referral", True)), "reseller_signup": bool(s.get("reseller_signup", True)),
           "seller_signup": bool(sellers.settings()["signup_open"])}
    cache.set("features", out, 5)
    return out


def feature(name):
    return bool(features().get(name, True))


def save_features(d):
    from services import sellers
    db.update("/settings/features", {"referral": bool(d.get("referral")), "reseller_signup": bool(d.get("reseller_signup"))})
    db.update("/settings/seller", {"signup_open": bool(d.get("seller_signup"))})
    cache.clear()
