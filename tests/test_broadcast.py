"""Broadcast: one copy per person (double tap, two server copies, resume, admin preview).  Run: python tests/test_broadcast.py"""
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from core import db, utils
from services import broadcast

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()
broadcast.RATE_SLEEP = 0
for uid in (901, 902, 903):
    handlers.process_update(msg(uid, "/start", name=f"U{uid}"))


def wait(bid):
    for _ in range(100):
        if db.get(f"/broadcasts/{bid}")["status"] in ("done", "failed"):
            return
        time.sleep(0.05)


count = lambda txt, chat: len(h.sent("sendMessage", txt, chat))

# ---- a double tap on Send in the panel makes ONE broadcast
h.CALLS.clear()
r1 = c.post("/admin/api/broadcasts", json={"message": {"text": "Sale today ZZ1", "format": "html"}}, headers=H)
r2 = c.post("/admin/api/broadcasts", json={"message": {"text": "Sale today ZZ1", "format": "html"}}, headers=H)
ok(r1.status_code == 201 and r2.status_code == 200 and j(r2).get("duplicate") and j(r1)["id"] == j(r2)["id"], "a repeated request returns the same broadcast")
wait(j(r1)["id"])
ok(all(count("ZZ1", u) == 1 for u in (901, 902, 903)), "every user got exactly one copy")

# ---- two workers on the same run (deploy overlap / resume) never double-send
h.CALLS.clear()
b = broadcast.create("admin", "Admin", {"text": "Overlap ZZ2", "format": "html", "media": {}}, {"type": "all"}, actor="t")
db.update(f"/broadcasts/{b['id']}", {"status": "sending", "run": 1})
import threading
ts = [threading.Thread(target=broadcast._worker, args=(b["id"], 1)) for _ in range(3)]
[t.start() for t in ts]; [t.join() for t in ts]
ok(all(count("ZZ2", u) == 1 for u in (901, 902, 903)), "three workers on the same run still send one copy each")

# ---- a re-broadcast (new run) does send again
db.update(f"/broadcasts/{b['id']}", {"status": "sending", "run": 2})
h.CALLS.clear(); broadcast._worker(b["id"], 2)
ok(all(count("ZZ2", u) == 1 for u in (901, 902, 903)), "a new run sends again")

# ---- Telegram: the admin sees the preview, not a second copy
db.set("/users/999", {"chat_id": "999", "full_name": "Boss", "created_at": 1})
h.CALLS.clear()
handlers.process_update(msg(999, "/broadcast", name="Boss")); handlers.process_update(msg(999, "News ZZ3", name="Boss"))
handlers.process_update(cbq(999, "bcgo", "Boss"))
time.sleep(0.6)
ok(count("ZZ3", 999) == 1, "the admin gets the preview only (no second copy)")
ok(all(count("ZZ3", u) == 1 for u in (901, 902, 903)), "users each got one copy")
print("ALL BROADCAST TESTS OK")
