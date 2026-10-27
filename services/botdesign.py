"""Bot screens: the message and image of every screen behind the bot's buttons.

* The ADMIN edits the main bot (Settings -> Bot screens). Each RESELLER edits their own bot (Bot screens in their panel).
* Every screen can have a picture and a message. Three kinds of screens:
    "welcome"  the /start message (placeholders {first_name} ...). Stored where it always was.
    "replace"  the whole text is yours (Refer & Earn, Reseller). Stored where it always was (main bot) / in /designs.
    "intro"    your text is shown ABOVE the live content of the screen (prices, balance, keys...). Stored in /designs.
* A reseller's bot never inherits the admin's pictures: no override -> the built-in look.
* Telegram limits a caption to 1024 characters: when picture + text would be longer, the screen is sent without the picture.
"""
import re

from core import db, utils
from core.cache import cache
from core.errors import ApiError

MAX_TEXT = 1500
CAPTION_LIMIT = 1024
BRAND = "/static/img/logo-welcome.jpg"

# key, label, kind, hint, placeholders, main only, default image
SCREENS = [
    ("home", "🏠 Welcome / Home", "welcome", "Shown on /start and when the user taps Home.", "{first_name} {full_name} {username} {chat_id}", False, ""),
    ("buy", "🛒 Purchase License Key", "intro", "Top of the category list.", "", False, BRAND),
    ("category", "📂 Category (apps list)", "intro", "Shown when a user opens a category.", "", False, ""),
    ("confirm", "✅ Plan confirmation", "intro", "Shown after the user taps a plan, before paying.", "", False, ""),
    ("pay_options", "💳 Payment options", "intro", "Wallet / UPI choice.", "", False, ""),
    ("keys", "🔑 My License Keys", "intro", "Top of the user's key list. Long lists are sent without the picture (Telegram's 1024-character limit).", "", False, ""),
    ("wallet", "👛 Wallet", "intro", "Top of the wallet screen.", "", False, ""),
    ("deposit", "➕ Deposit money", "intro", "Shown when the user taps Deposit.", "", False, ""),
    ("refer", "🎁 Refer & Earn", "replace", "The intro message before the user registers. The link appears right after they confirm.", "{percent} {link} {count} {earned}", False, ""),
    ("checkin", "📅 Daily check-in", "intro", "Top of the check-in screen.", "", False, ""),
    ("spin", "🎡 Spin the wheel", "intro", "Top of the spin screen.", "", False, ""),
    ("support", "🛟 Customer support", "intro", "Top of the support screen.", "", False, ""),
    ("join", "📢 Join channels (force join)", "intro", "Shown when the user must join your channels first.", "", False, ""),
    ("reseller", "🏪 Reseller button", "replace", "Shown when a user taps Reseller.", "", True, ""),
    ("seller", "💼 Become Seller", "intro", "Top of the Become Seller screen.", "", True, ""),
]
BY_KEY = {s[0]: s for s in SCREENS}
RICH_KEY = {"refer": "referral", "reseller": "reseller"}  # main bot: these two live in /settings/messages


def _tag_len(text):
    return len(re.sub(r"<[^>]+>", "", text or ""))


def keys_for(tenant):
    return [s[0] for s in SCREENS if not (s[5] and tenant)]


def _path(tenant, key):
    return f"/designs/{tenant or 'main'}/{key}"


def raw(tenant, key):
    """The stored override {text, format, image} for the intro / tenant screens (cached)."""
    ck = ("design", tenant or "main", key)
    hit = cache.get(ck)
    if hit is not None:
        return hit[0]
    d = db.get(_path(tenant, key)) or {}
    out = {"text": d.get("text") or "", "format": d.get("format") or "html", "image": d.get("image") or ""}
    cache.set(ck, (out,), 8)
    return out


def decorate(ctx, key, text):
    """(text, media) for an 'intro' screen: the admin's / reseller's message on top, and the picture if there is one."""
    spec = BY_KEY.get(key)
    if not spec:
        return text, None
    d = raw(ctx.tenant, key)
    if d["text"]:
        top = d["text"] if d["format"] != "text" else _esc(d["text"])
        text = top + "\n\n" + text
    image = d["image"] or spec[6]
    media = ("photo", image) if image else None
    if media and _tag_len(text) > CAPTION_LIMIT:
        media = None
    return text, media


def override(ctx, key):
    """(text, format, image) overrides for a 'replace' screen on a RESELLER bot; empty strings when none."""
    d = raw(ctx.tenant, key)
    return d["text"], d["format"], d["image"]


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ editor (admin panel = main bot, reseller panel = own bot)
def _default_text(key):
    from services import settings
    if key == "home":
        return settings.DEFAULT_WELCOME
    return settings.RICH_DEFAULTS.get(RICH_KEY.get(key, ""), "")


def _stored(tenant, key):
    """{text, format, image} as currently in force."""
    from services import settings
    if not tenant:
        if key == "home":
            s = settings.bot_settings()
            return {"text": s["welcome_message"], "format": s["welcome_format"], "image": s["welcome_image"]}
        if key in RICH_KEY:
            r = settings.get_rich(RICH_KEY[key])
            return {"text": r["text"], "format": r["format"], "image": r["image"]}
    else:
        if key == "home":
            r = db.get(f"/resellers/{tenant}") or {}
            return {"text": r.get("welcome_message") or settings.DEFAULT_WELCOME, "format": "html", "image": r.get("welcome_image") or ""}
        if key == "refer":
            d = raw(tenant, key)
            return {"text": d["text"] or settings.RICH_DEFAULTS["referral"], "format": d["format"], "image": d["image"]}
    return raw(tenant, key)


def listing(tenant=""):
    out = []
    for key in keys_for(tenant):
        _, label, kind, hint, ph, _main, dimg = BY_KEY[key]
        d = _stored(tenant, key)
        out.append({"key": key, "label": label, "kind": kind, "hint": hint, "placeholders": ph,
                    "text": d["text"], "format": d["format"], "image": d["image"], "default_image": dimg,
                    "default_text": _default_text(key)})
    return out


def save(tenant, key, data):
    from services import settings
    if key not in keys_for(tenant):
        raise ApiError("Unknown screen.", 404)
    kind = BY_KEY[key][2]
    data = data or {}
    fmt = data.get("format", "html")
    if fmt not in ("html", "text"):
        raise ApiError("Message format must be html or text.")
    text = utils.clean_text(data.get("text"), "Message", MAX_TEXT if kind == "intro" else 3500, kind != "intro")
    image = utils.clean_text(data.get("image"), "Image", 400, False)
    if image and not (image.startswith(("/media/img/", "https://", "http://"))):
        raise ApiError("Use the Upload button, or an image link that starts with https://")
    if not tenant and key == "home":
        db.update("/settings/bot", {"welcome_message": text, "welcome_format": fmt, "welcome_image": image})
    elif not tenant and key in RICH_KEY:
        settings.save_rich(RICH_KEY[key], {"text": text, "format": fmt, "image": image})
    elif tenant and key == "home":
        db.update(f"/resellers/{tenant}", {"welcome_message": text, "welcome_image": image})
    else:
        db.set(_path(tenant, key), {"text": text, "format": fmt, "image": image, "updated_at": utils.now()})
    cache.clear()


def reset(tenant, key):
    from services import settings
    if key not in keys_for(tenant):
        raise ApiError("Unknown screen.", 404)
    if not tenant and key == "home":
        db.update("/settings/bot", {"welcome_message": settings.DEFAULT_WELCOME, "welcome_format": "html", "welcome_image": ""})
    elif not tenant and key in RICH_KEY:
        db.set(f"/settings/messages/{RICH_KEY[key]}", None)
    elif tenant and key == "home":
        db.update(f"/resellers/{tenant}", {"welcome_message": "", "welcome_image": ""})
    else:
        db.set(_path(tenant, key), None)
    cache.clear()
