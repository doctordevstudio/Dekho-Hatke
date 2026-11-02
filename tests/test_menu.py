"""Bottom menu keyboard (hidden by default, show/hide), 'Download APK' only for APK apps, /start wording, Support icon.
Run: TIMEZONE=UTC python3 tests/test_menu.py"""
from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers, messages as m
from bot.antispam import spam
from core import db, utils
from services import settings as settings_svc, botui

app = create_app(background=False)
c, H, me = login_admin(app)
j = lambda r: r.get_json()
_orig = handlers.process_update


def P(u):
    spam._s.clear(); handlers.reset_gates()
    return _orig(u)


def markups(chat, method=None):
    return [p.get("reply_markup") for m_, p, *_ in h.CALLS if str(p.get("chat_id")) == str(chat) and (method is None or m_ == method)]


def has_reply_kb(chat):
    return any(isinstance(r, dict) and r.get("keyboard") for r in markups(chat))


def inline_texts(chat):
    out = []
    for r in markups(chat):
        for row in (r or {}).get("inline_keyboard", []):
            out += [b["text"] for b in row]
    return out


# ---------- /start: no bottom keyboard, inline Home menu has the toggle + the new Support icon
h.CALLS.clear(); P(msg(7001, "/start", name="Ann"))
ok(not has_reply_kb(7001), "default: the bottom keyboard is NOT shown")
bt = inline_texts(7001)
ok("⌨️ Show menu" in bt, "Home has a 'Show menu' button")
ok("🎧 Support" in bt and not any("🛟" in b for b in bt), "Support uses the new 🎧 icon")
ok(not any("🛟" in str(p) for m_, p, *_ in h.CALLS if str(p.get("chat_id")) == "7001"), "no 🛟 anywhere in what the user sees")

# ---------- show it
h.CALLS.clear(); P(cbq(7001, "menu_on", name="Ann"))
ok(has_reply_kb(7001), "tapping Show menu brings the keyboard up")
kb = [r for r in markups(7001) if isinstance(r, dict) and r.get("keyboard")][-1]["keyboard"]
flat = [b["text"] if isinstance(b, dict) else b for row in kb for b in row]
ok(m.M_HIDE in flat and any("Shop" in t for t in flat), "keyboard has Shop … and a Hide menu key")
ok(any("🎧" in t for t in flat) and not any("🛟" in t for t in flat), "keyboard uses the 🎧 Support icon")
ok(db.get("/bot_menu/7001") is True or any(v is True for v in (db.get("/bot_menu") or {}).values()), "choice is remembered")

# ---------- stays on after /start, then hide
h.CALLS.clear(); P(msg(7001, "/start", name="Ann"))
ok(has_reply_kb(7001), "once switched on it stays on across /start")
h.CALLS.clear(); P(msg(7001, m.M_HIDE, name="Ann"))
ok(any(isinstance(r, dict) and r.get("remove_keyboard") for r in markups(7001)), "Hide menu removes the keyboard")
h.CALLS.clear(); P(msg(7001, "/start", name="Ann"))
ok(not has_reply_kb(7001), "after hiding, /start does not bring it back")
h.CALLS.clear(); P(msg(7001, "/menu", name="Ann"))
ok(has_reply_kb(7001), "/menu shows it again")
h.CALLS.clear(); P(msg(7001, "/hide", name="Ann"))
ok(any(isinstance(r, dict) and r.get("remove_keyboard") for r in markups(7001)), "/hide hides it")

# ---------- welcome wording
ok("Purchase" not in settings_svc.bot_settings()["welcome_message"], "default welcome says Shop")
settings_svc.update({"welcome_message": "Tap <b>Purchase</b> below to begin 👇"}) if hasattr(settings_svc, "update") else None
ok("Purchase" not in settings_svc.modern_welcome("Click on Purchase button to start") and "Shop" in settings_svc.modern_welcome("Tap <b>Purchase</b> below"), "an old saved welcome is shown with 'Shop'")
ok("purchase history" in settings_svc.modern_welcome("your purchase history"), "ordinary uses of the word are untouched")

# ---------- My Keys: Download APK only when the app delivers an APK
CAT = j(c.post("/admin/api/categories", json={"name": "T", "icon": "🧰"}, headers=H))["id"]
A_APK = j(c.post("/admin/api/apps", json={"name": "WithApk", "category_id": CAT, "apk_id": "APKFILEID0000000001", "description": "d", "features": "a\nb"}, headers=H))["id"]
A_NO = j(c.post("/admin/api/apps", json={"name": "NoApk", "category_id": CAT, "description": "d", "features": "a\nb"}, headers=H))["id"]
A_MSG = j(c.post("/admin/api/apps", json={"name": "MsgApp", "category_id": CAT, "apk_id": "APKFILEID0000000003", "delivery_mode": "message",
                                           "delivery_message": "Use setup video", "description": "d", "features": "a\nb"}, headers=H))["id"]
now = utils.now()
for i, (aid, nm) in enumerate(((A_APK, "WithApk"), (A_NO, "NoApk"), (A_MSG, "MsgApp"))):
    k = f"KEY{nm}".ljust(18, "X")[:18]
    db.set(f"/licenses/{k}", {"key": k, "app_id": aid, "app_name": nm, "chat_id": "7002", "full_name": "Bob", "reseller_id": "", "created_at": now - i,
                              "expires_at": now + 86400 * 30, "validity_label": "30 days", "status": "active"})
h.CALLS.clear(); P(msg(7002, "/start", name="Bob")); h.CALLS.clear(); P(cbq(7002, "keys", name="Bob"))
bt = inline_texts(7002)
ok(any("WithApk" in b and "Download APK" in b for b in bt), "app with an APK: Download APK button shown")
ok(not any("NoApk" in b for b in bt), "app without an APK file: no Download APK button")
ok(not any("MsgApp" in b for b in bt), "message / video / key-only app: no Download APK button")
k_msg = f"KEYMsgApp".ljust(18, "X")[:18]
h.CALLS.clear(); P(cbq(7002, f"dl:{k_msg}", name="Bob"))
ok(not [1 for m_, *_ in h.CALLS if m_ == "sendDocument"], "tapping an old download button on a message app sends no APK")
print("ALL MENU TESTS OK")
