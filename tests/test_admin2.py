"""2nd Admin: gets new-user and payment alerts, can verify / fail a payment from Telegram, and can connect its own chat.
Run: python tests/test_admin2.py"""
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from core import db, utils
from services import notify, settings

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()
ADMIN, ADMIN2 = "999", "998"
ok(notify.admin_chats() == [ADMIN, ADMIN2], "both admin chats are configured")

CAT = j(c.post("/admin/api/categories", json={"name": "Tools"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001"}, headers=H))["id"]
PLAN = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 100, "validity_value": 7, "validity_unit": "days", "sort": 1}, headers=H))["id"]
db.set("/upis/u1", {"id": "u1", "upi_id": "shop@upi", "name": "Shop", "active": True, "created_at": 1})

# ---- a new user starts the bot: both admins hear about it
h.CALLS.clear(); handlers.process_update(msg(701, "/start", name="Newbie"))
ok(h.sent("sendMessage", "New user", ADMIN) or h.sent("sendPhoto", "New user", ADMIN), "Super Admin is told about the new user")
ok(h.sent("sendMessage", "New user", ADMIN2) or h.sent("sendPhoto", "New user", ADMIN2), "2nd Admin is told about the new user")

# ---- a payment: card created -> both admins get it; after the proof both get Verify / Fail buttons
import itertools
_n = itertools.count(1)


def pay(uid, name):
    n = next(_n); h.tgmod.Telegram.download = lambda self, fid, n=n: (png(color=(n * 7 % 255, n * 13 % 255, 90)), "png")
    handlers.process_update(cbq(uid, f"pc:{PLAN}", name)); handlers.process_update(cbq(uid, f"py:{PLAN}", name))
    t = sorted(db.query_equal("/txns", "status", "pending"), key=lambda t: -t["created_at"])[0]
    h.CALLS.clear()
    handlers.process_update(cbq(uid, f"paid:{t['id']}", name))
    handlers.process_update(msg(uid, photo=f"SHOT{utils.ts_id()[-10:]}", name=name))
    handlers.process_update(msg(uid, str(100000000000 + int(time.time() * 1000) % 899999999999), name=name))
    return t["id"]


h.CALLS.clear()
T1 = pay(702, "Payer")
btns = lambda chat: [b["callback_data"] for m_, p, *_ in h.CALLS if str(p.get("chat_id")) == chat for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row if b.get("callback_data")]
ok(f"av:{T1}" in btns(ADMIN), "Super Admin gets the Verify button")
ok(f"av:{T1}" in btns(ADMIN2) and f"af:{T1}" in btns(ADMIN2), "2nd Admin gets Verify / Fail buttons too")

# ---- the 2nd admin confirms from Telegram
h.CALLS.clear(); handlers.process_update(cbq(998, f"av:{T1}", "Second"))
ok(db.get(f"/txns/{T1}")["status"] == "success", "2nd Admin verified the payment from Telegram")
ok(db.get(f"/txns/{T1}").get("license_key"), "the buyer got their key")

# ---- ... or fails it with a reason
T2 = pay(703, "Payer2")
h.CALLS.clear(); handlers.process_update(cbq(998, f"af:{T2}", "Second"))
handlers.process_update(msg(998, "wrong amount", name="Second"))
ok(db.get(f"/txns/{T2}")["status"] == "failed", "2nd Admin failed a payment with a reason")
ok(h.sent("sendMessage", "", 703), "the buyer was told")

# ---- a stranger cannot moderate
T3 = pay(704, "Payer3")
h.CALLS.clear(); handlers.process_update(cbq(5555, f"av:{T3}", "Stranger"))
ok(db.get(f"/txns/{T3}")["status"] == "verification", "a stranger's tap does nothing")

# ---- Telegram link from the panel (the 2nd admin signs in and connects its own chat)
c2, H2, _ = login_admin(app, "boss2", "TestPassw0rd456")
r = c2.post("/admin/api/settings/admin-link", headers=H2)
ok(r.status_code == 200 and "?start=al_" in j(r)["url"], "the 2nd Admin gets a one-time Telegram link")
arg = j(r)["url"].split("?start=")[1]
h.CALLS.clear(); handlers.process_update(msg(4321, "/start " + arg, name="Second Admin"))
ok(settings.bot_settings()["admin2_chat_id"] == "4321" and settings.bot_settings()["admin_chat_id"] == "999", "only the 2nd Admin's chat changed")
ok(h.sent("sendMessage", "Telegram connected", 4321), "the chat is told it is connected")
h.CALLS.clear(); handlers.process_update(msg(4322, "/start " + arg, name="Intruder"))
ok(settings.bot_settings()["admin2_chat_id"] == "4321" and h.sent("sendMessage", "expired", 4322), "the link works only once")
ok(c.post("/admin/api/admins", json={"username": "viewer7", "password": "ViewerPass1234"}, headers=H).status_code in (200, 201), "view-only admin created")
vc, VH, _ = login_admin(app, "viewer7", "ViewerPass1234")
ok(vc.post("/admin/api/settings/admin-link", headers=VH).status_code == 403, "a view-only admin cannot connect Telegram")

# ---- test alerts + the reason shown when a chat cannot be reached
import bot.tg as tgmod
real_send = tgmod.Telegram.send_message


def flaky(self, chat_id, *a, **k):
    if str(chat_id) == "4321":
        raise tgmod.TelegramError("Forbidden: bot can't initiate conversation with a user", 403)
    return real_send(self, chat_id, *a, **k)


tgmod.Telegram.send_message = flaky
r = c.post("/admin/api/settings/bot/test-admins", headers=H)
items = {x["role"]: x for x in j(r)["items"]}
ok(items["admin"]["ok"] and not items["admin2"]["ok"] and "initiate" in items["admin2"]["error"], "Test alerts: 2nd Admin unreachable, with Telegram's reason")
st = j(c.get("/admin/api/settings"))["admin_status"]
ok(st["admin2"]["ok"] is False and "initiate" in st["admin2"]["error"] and st["admin"]["ok"], "Settings shows who could not be reached")
tgmod.Telegram.send_message = real_send
j(c.post("/admin/api/settings/bot/test-admins", headers=H))
ok(j(c.get("/admin/api/settings"))["admin_status"]["admin2"]["ok"], "the warning clears once an alert gets through")
print("ALL ADMIN2 TESTS OK")
