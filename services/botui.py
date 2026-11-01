"""Bot look & behaviour that the admin (main bot) or a reseller (own bot) can change without code:

* button colours  (Telegram button styles: blue / green / red) per group of buttons,
* layout          (one button per row, or a grid of two) per list,
* reactions       (the bot reacts to the user's message after a purchase and other good moments),
* celebration GIFs (sent after a purchase / check-in / deposit, removed after a few seconds),
* a few numbers   (FAQ page size, UPI payment window, payment requests per day, spin GIF removal delay).

Stored at /botui/<tenant or "main">; every missing value falls back to DEFAULTS.
"""
import copy
import io
import logging
import random
import threading
import time

from core import db, utils
from core.cache import cache
from core.errors import ApiError

log = logging.getLogger("botui")

# --------------------------------------------------------------------------------------------- catalogue
STYLES = {"": "Default", "primary": "Blue", "success": "Green", "danger": "Red"}

# colour groups: key -> (label, callback heads)
GROUPS = {
    "shop": ("🛒 Shop", ["buy"]),
    "categories": ("📂 Categories", ["cat"]),
    "apps": ("📱 Apps", ["ap"]),
    "plans": ("🏷 Plans", ["pl", "plan", "pc"]),
    "confirm": ("✅ Yes / Pay / Wallet", ["py", "pu", "pw", "by", "paid", "pay"]),
    "crypto": ("🪙 Crypto pay + Check payment", ["cr", "xchk"]),
    "cancel": ("❌ Cancel / No", ["cancel"]),
    "guide": ("📖 How to pay guides", ["how"]),
    "wallet": ("💰 Wallet + Deposit", ["wallet", "dep", "wdr", "hist"]),
    "refer": ("🎁 Refer & Earn", ["refer", "refer_go", "rl", "rd"]),
    "keys": ("🔑 My Keys + Download", ["keys", "dl"]),
    "rewards": ("📅 Check-in + 🎡 Spin", ["checkin", "cin_go", "spin", "spin_go"]),
    "faq": ("❓ FAQs", ["faq", "fq"]),
    "support": ("🛟 Support", ["support"]),
    "partner": ("🏪 Reseller / 💼 Seller", ["reseller", "seller", "sl_go", "sl_reset"]),
    "home": ("🏠 Home", ["home"]),
    "back": ("⬅️ Back buttons", []),
}
HEAD_GROUP = {h: g for g, (_l, heads) in GROUPS.items() for h in heads}

LAYOUTS = {"home": "🏠 Home menu", "categories": "📂 Categories", "apps": "📱 Apps in a category", "plans": "🏷 Plans of an app",
           "faq": "❓ FAQ questions"}

EVENTS = {  # key -> (label, default reaction, default GIF on?)
    "purchase": ("Purchase done (key sent)", "🎉", True),
    "deposit": ("Wallet deposit added", "💯", True),
    "checkin": ("Daily check-in", "🔥", True),
    "spin": ("Spin wheel result", "🏆", False),  # the spin has its own animation
    "proof": ("Payment proof received", "👌", False),
    "detected": ("Crypto payment detected", "👀", False),
}
# Telegram only allows these emoji as reactions
ALLOWED_REACTIONS = ["👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🎉", "🤩", "🙏", "👌", "🕊", "🤡", "😍", "🐳",
                     "💯", "🤣", "⚡", "🏆", "🍾", "💋", "😈", "😎", "🤝", "🫡", "🆒", "💘", "😘", "🦄", "👀", "🤗", "😇", "✍", "🎃", "👻"]

DEFAULTS = {
    "layout": {"home": 2, "categories": 2, "apps": 1, "plans": 1, "faq": 1},
    "colors": {g: "" for g in GROUPS},
    "reactions": {"enabled": True, "events": {k: v[1] for k, v in EVENTS.items()}},
    "gifs": {"enabled": True, "delete_after": 4, "spin_delete_after": 3,
             "events": {k: {"enabled": v[2], "url": ""} for k, v in EVENTS.items()}},
    "faq_page_size": 10,
    "upi_window_min": 10,
    "max_txn_per_day": 5,
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def get(tenant=""):
    ck = ("botui", tenant or "main")
    hit = cache.get(ck)
    if hit is not None:
        return hit[0]
    out = _merge(DEFAULTS, db.get(f"/botui/{tenant or 'main'}") or {})
    cache.set(ck, (out,), 8)
    return out


def _bool(v):
    return bool(v) and str(v).lower() not in ("0", "false", "no", "off")


def _int(v, label, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ApiError(f"{label} must be a whole number.")
    if not lo <= n <= hi:
        raise ApiError(f"{label} must be between {lo} and {hi}.")
    return n


def save(tenant, d):
    d = d or {}
    cur = get(tenant)
    new = copy.deepcopy(cur)
    for k, v in (d.get("layout") or {}).items():
        if k not in LAYOUTS:
            raise ApiError(f"Unknown list '{k}'.")
        new["layout"][k] = _int(v, f"Layout of {LAYOUTS[k]}", 1, 2)
    for k, v in (d.get("colors") or {}).items():
        if k not in GROUPS:
            raise ApiError(f"Unknown button group '{k}'.")
        if (v or "") not in STYLES:
            raise ApiError("Colour must be default, blue, green or red.")
        new["colors"][k] = v or ""
    r = d.get("reactions")
    if r:
        if "enabled" in r:
            new["reactions"]["enabled"] = _bool(r["enabled"])
        for k, v in (r.get("events") or {}).items():
            if k not in EVENTS:
                raise ApiError(f"Unknown event '{k}'.")
            if v and v not in ALLOWED_REACTIONS:
                raise ApiError(f"Telegram does not allow {v} as a reaction. Pick one from the list.")
            new["reactions"]["events"][k] = v or ""
    g = d.get("gifs")
    if g:
        if "enabled" in g:
            new["gifs"]["enabled"] = _bool(g["enabled"])
        for key, label in (("delete_after", "Remove GIF after"), ("spin_delete_after", "Remove spin GIF after")):
            if key in g:
                new["gifs"][key] = _int(g[key], label, 1, 60)
        for k, v in (g.get("events") or {}).items():
            if k not in EVENTS:
                raise ApiError(f"Unknown event '{k}'.")
            url = utils.clean_text((v or {}).get("url"), "GIF link", 300, False)
            if url and not (url.startswith("https://") or (len(url) > 20 and url.replace("_", "").replace("-", "").isalnum())):
                raise ApiError("GIF must be an https:// link or a Telegram file id.")
            new["gifs"]["events"][k] = {"enabled": _bool((v or {}).get("enabled")), "url": url}
    if "faq_page_size" in d:
        new["faq_page_size"] = _int(d["faq_page_size"], "FAQ questions per page", 3, 10)
    if "upi_window_min" in d:
        new["upi_window_min"] = _int(d["upi_window_min"], "UPI payment window (minutes)", 3, 120)
    if "max_txn_per_day" in d:
        new["max_txn_per_day"] = _int(d["max_txn_per_day"], "Payment requests per user per day", 1, 50)
    db.set(f"/botui/{tenant or 'main'}", new)
    cache.clear()
    return new


def reset(tenant):
    db.set(f"/botui/{tenant or 'main'}", None)
    cache.clear()


def listing(tenant=""):
    return {"settings": get(tenant), "defaults": DEFAULTS,
            "groups": [{"key": k, "label": v[0]} for k, v in GROUPS.items()],
            "layouts": [{"key": k, "label": v} for k, v in LAYOUTS.items()],
            "events": [{"key": k, "label": v[0]} for k, v in EVENTS.items()],
            "styles": [{"key": k, "label": v} for k, v in STYLES.items()], "reactions": ALLOWED_REACTIONS}


# --------------------------------------------------------------------------------------------- buttons
def style_markup(tenant, markup):
    """Add Telegram's button `style` (blue / green / red) from the colour settings. Never raises, never changes callbacks."""
    try:
        if not markup or "inline_keyboard" not in markup:
            return markup
        colors = get(tenant)["colors"]
        if not any(colors.values()):
            return markup
        rows = []
        for row in markup["inline_keyboard"]:
            out = []
            for b in row:
                group = None
                if b.get("callback_data"):
                    group = HEAD_GROUP.get(b["callback_data"].split(":", 1)[0])
                if str(b.get("text", "")).startswith("⬅️"):
                    group = "back"
                st = colors.get(group or "", "")
                out.append({**b, "style": st} if st and "style" not in b else b)
            rows.append(out)
        return {**markup, "inline_keyboard": rows}
    except Exception:  # noqa: BLE001
        log.exception("button styling failed")
        return markup


def cols(ctx, name):
    return int(get(ctx.tenant)["layout"].get(name, DEFAULTS["layout"].get(name, 1)))


def grid(buttons, n):
    """Flat list of (text, data) -> rows of n."""
    n = 2 if int(n) == 2 else 1
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


# --------------------------------------------------------------------------------------------- reactions
_last_msg = {}


def remember_message(ctx, uid, message_id):
    _last_msg[(ctx.key, str(uid))] = int(message_id)
    if len(_last_msg) > 20000:
        _last_msg.clear()


def react(ctx, uid, event, message_id=None):
    """React to the user's latest message (🎉 after a purchase ...). Never raises."""
    try:
        cfg = get(ctx.tenant)["reactions"]
        emoji = cfg["events"].get(event, "")
        mid = message_id or _last_msg.get((ctx.key, str(uid)))
        if not (cfg["enabled"] and emoji and mid):
            return False
        ctx.tg.set_reaction(uid, mid, emoji, big=event == "purchase")
        return True
    except Exception as e:  # noqa: BLE001  (old messages, blocked users, reactions off in the chat ...)
        log.info("reaction skipped: %s", e)
        return False


# --------------------------------------------------------------------------------------------- GIFs
_gif_cache = {}


def celebration_gif():
    """A small confetti animation (made once, then reused). Pillow only: no files to ship."""
    if "gif" in _gif_cache:
        return _gif_cache["gif"]
    from PIL import Image, ImageDraw
    size, frames = 300, []
    rnd = random.Random(7)
    pieces = [(rnd.randint(0, size), rnd.randint(-size, 0), rnd.randint(3, 8), rnd.choice(
        [(138, 92, 255), (255, 79, 163), (61, 224, 255), (200, 255, 61), (255, 200, 61), (61, 255, 162)]), rnd.randint(6, 16)) for _ in range(70)]
    for f in range(16):
        im = Image.new("RGB", (size, size), (14, 14, 30))
        d = ImageDraw.Draw(im)
        for x, y, w, col, speed in pieces:
            yy = (y + f * speed * 2 + size) % (size + 40) - 20
            d.rectangle((x, yy, x + w, yy + w * 2), fill=col)
        r = 52 + (6 if f % 4 < 2 else 0)
        d.ellipse((150 - r, 150 - r, 150 + r, 150 + r), fill=(61, 255, 162))
        d.line((125, 152, 145, 174, 180, 128), fill=(14, 14, 30), width=12, joint="curve")
        frames.append(im)
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=90, loop=0, optimize=True)
    _gif_cache["gif"] = buf.getvalue()
    return _gif_cache["gif"]


def later(ctx, chat_id, message_id, seconds):
    """Delete a message after `seconds` (a daemon timer: the worker thread is not held)."""
    t = threading.Timer(float(seconds), lambda: ctx.tg.delete_message(chat_id, message_id))
    t.daemon = True
    t.start()
    return t


def celebrate(ctx, uid, event, caption=None):
    """Send the GIF configured for this event, remove it after `delete_after` seconds. Never raises."""
    try:
        cfg = get(ctx.tenant)["gifs"]
        ev = cfg["events"].get(event) or {}
        if not (cfg["enabled"] and ev.get("enabled")):
            return None
        if ev.get("url"):
            from services import delivery
            msg = delivery.send_ref(ctx, uid, "animation", ev["url"], caption)
        else:
            msg = ctx.tg.send_media("animation", uid, celebration_gif(), caption, filename="celebrate.gif")
        later(ctx, uid, msg["message_id"], cfg["delete_after"])
        return msg
    except Exception as e:  # noqa: BLE001
        log.info("celebration gif skipped: %s", e)
        return None


# --------------------------------------------------------------------------------------------- numbers other modules read
def faq_page_size():
    return int(get("")["faq_page_size"])


def upi_ttl(tenant=""):
    return int(get(tenant)["upi_window_min"]) * 60


def max_txn_per_day(tenant=""):
    return int(get(tenant)["max_txn_per_day"])


# the Telegram client asks us to colour every inline keyboard it sends
from bot import tg as _tg  # noqa: E402

_tg.Telegram.styler = staticmethod(style_markup)
