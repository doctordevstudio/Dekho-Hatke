"""Admin Login gate, linked Telegram account (fake Telethon backend), storage channel, channel-copy delivery, real file names.
Run: python tests/test_tg.py"""
import io
import json
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from bot.context import get_ctx
from bot.tg import TelegramError
from config import Config
from core import admin_gate, db, utils
from services import delivery, tgchannel, userbot

app = create_app(background=False)
j = lambda r: r.get_json()
P0 = handlers.process_update


def P(u, ctx=None):
    spam._s.clear(); handlers.reset_gates()
    return P0(u, ctx) if ctx else P0(u)


# =============================================================== 1. Admin Login gate
anon = app.test_client()
for path in ("/", "/login"):
    r = anon.get(path)
    ok(r.status_code == 401 and "Unauthorised" in r.get_data(as_text=True) and "<form" not in r.get_data(as_text=True), f"direct visit to {path} -> Unauthorised, no form")
ok(anon.post("/login", data={"username": "boss", "password": "TestPassw0rd123"}).status_code == 401, "POST /login without the link -> Unauthorised")
ok(anon.get("/go/not-a-token").status_code == 401 and anon.get("/go/1.2.3.4").status_code == 401, "garbage links -> Unauthorised")
tok = admin_gate.make_token(999)
ok(anon.get(f"/go/{tok[:-2]}xx").status_code == 401, "tampered signature -> Unauthorised")
ok(anon.get(f"/go/{admin_gate.make_token(31337)}").status_code == 401, "a link for a chat that is not an admin -> Unauthorised")
ok(anon.get(f"/go/{admin_gate.make_token(999, now=time.time() - 1801)}").status_code == 401, "a link older than 30 minutes -> Unauthorised")
chat, exp = admin_gate.check_token(admin_gate.make_token(999, now=1_000_000), now=1_000_000)
ok(exp == 1_000_000 + 1800, "the link lasts exactly 30 minutes")
ok(admin_gate.check_token(admin_gate.make_token(999, now=1_000_000), now=1_000_000 + 1799) and not admin_gate.check_token(admin_gate.make_token(999, now=1_000_000), now=1_000_000 + 1801), "valid at 29:59, dead at 30:01")
r = anon.get(f"/go/{tok}")
ok(r.status_code == 302 and r.headers["Location"].endswith("/login"), "a good link unlocks the sign-in page")
page = anon.get("/login")
ok(page.status_code == 200 and "Sign in" in page.get_data(as_text=True), "…and the sign-in form now shows")
csrf = page.get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
ok(anon.post("/login", data={"username": "boss", "password": "wrong-pass-123", "csrf_token": csrf}).status_code == 401, "wrong password is still refused")
ok(anon.post("/login", data={"username": "boss", "password": "TestPassw0rd123", "csrf_token": csrf}).status_code == 302, "right password signs in")
ok(anon.get("/").status_code == 200, "signed-in admin sees the panel")
anon.post("/admin/api/logout", headers={"X-CSRF-Token": j(anon.get("/admin/api/me"))["csrf"]})
ok(anon.get("/").status_code == 401 and anon.get("/login").status_code == 401, "after sign-out the entrance is closed again")
late = app.test_client()
late.get(f"/go/{tok}")
with late.session_transaction() as s:
    s["gate"] = int(time.time()) - 5
ok(late.get("/login").status_code == 401, "an unlocked browser is locked again once the link's 30 minutes are over")
ok(app.test_client().get("/seller").status_code == 200 and app.test_client().get("/reseller").status_code == 200, "seller / reseller sign-in pages stay public")
Config.ADMIN_GATE = False
ok(app.test_client().get("/login").status_code == 200, "ADMIN_GATE=0 (local dev) opens the sign-in page")
Config.ADMIN_GATE = True

# ---- the bot hands out the link
c, H, me = login_admin(app)
h.CALLS.clear(); P(msg(999, "/adminlogin", name="Boss"))
kb = [p for m_, p, *_ in h.CALLS if m_ == "sendMessage" and str(p.get("chat_id")) == "999"][-1]
url = kb["reply_markup"]["inline_keyboard"][0][0]["url"]
ok("/go/" in url and url.startswith("https://shop.example.com/go/") and "30 minutes" in kb["text"], "super admin: /adminlogin -> a signed link valid 30 minutes")
ok(app.test_client().get("/go/" + url.split("/go/")[1]).status_code == 302, "…and that exact link works")
h.CALLS.clear(); P(msg(998, "/adminlogin", name="Boss2"))
ok(any("/go/" in str(p) for p in sent("sendMessage", "", 998)), "2nd admin gets the link too")
h.CALLS.clear(); P(msg(555, "/adminlogin", name="Stranger"))
ok(not any("/go/" in str(p) for p in sent("sendMessage", "")), "a normal user gets no link (and no hint)")
h.CALLS.clear(); P(msg(999, "/start", name="Boss"))
sc = [p for m_, p, *_ in h.CALLS if m_ == "setMyCommands" and p.get("scope")]
ok(sc and [x["command"] for x in sc[0]["commands"]] == ["start", "adminlogin"] and "Admin Login" in sc[0]["commands"][1]["description"], "/start of an admin puts Admin Login in the ≡ menu")
h.CALLS.clear(); P(msg(555, "/start", name="Stranger"))
ok(not [1 for m_, p, *_ in h.CALLS if m_ == "setMyCommands" and p.get("scope")], "normal users never get it")
h.CALLS.clear()
ok(c.put("/admin/api/settings/bot", json={"welcome_message": "Hi {first_name}", "welcome_format": "html", "admin_chat_id": "999", "admin2_chat_id": "997"}, headers=H).status_code == 200, "admin chat id change saved")
ok([p["scope"]["chat_id"] for m_, p, *_ in h.CALLS if m_ == "deleteMyCommands"] == [998] and 997 in [p["scope"]["chat_id"] for m_, p, *_ in h.CALLS if m_ == "setMyCommands" and p.get("scope")],
   "menu follows the admin chat ids: removed one loses Admin Login, new one gains it")
c.put("/admin/api/settings/bot", json={"welcome_message": "Hi {first_name}", "welcome_format": "html", "admin_chat_id": "999", "admin2_chat_id": "998"}, headers=H)

# =============================================================== 2. linked account (fake Telethon)
SESSION = "1BVtsOKwBu-FAKE-SESSION-STRING-0000000000000000"


class FakeBackend:
    def __init__(self):
        self.step, self.session, self.is_in = "", None, False
        self.granted, self.logged_out, self.expired = [], False, False
        self.chat = {"chat_id": "-1009876", "title": "Store Vault", "username": "storevault", "broadcast": True, "admin": True, "can_add_admins": True}

    def pending_step(self): return self.step
    def start_login(self, phone):
        if phone != "+919876543210": raise userbot.UserbotError("That phone number is not valid.")
        self.step = "code"
    def submit_code(self, code):
        if code != "12345": raise userbot.UserbotError("That code is wrong.")
        self.step = "password"; return "password", None
    def submit_password(self, pw):
        if pw != "hunter2": raise userbot.UserbotError("Wrong 2-step verification password.")
        self.step, self.is_in = "", True; return SESSION
    def cancel_login(self): self.step = ""
    def connected(self): return self.is_in
    def connect(self, session):
        if self.expired or session != SESSION: return False
        self.is_in = True; return True
    def resolve(self, ref):
        if "nope" in ref: raise userbot.UserbotError("Could not find that chat.")
        return dict(self.chat)
    def grant_bot(self, chat_ref, bot):
        self.granted.append((chat_ref, bot)); return True, "added as admin"
    def logout(self): self.logged_out, self.is_in = True, False


fb = FakeBackend()
userbot._backend = fb
ok(userbot.status() == {"configured": False, "linked": False, "expired": False, "step": ""}, "no TG_API_ID / TG_API_HASH: not configured")
r = c.post("/admin/api/tg-account/start", json={"phone": "+919876543210"}, headers=H)
ok(r.status_code == 400 and "TG_API_ID" in j(r)["error"], "linking needs the API id / hash from the environment")
Config.TG_API_ID, Config.TG_API_HASH = 12345, "abcdef"
r = c.post("/admin/api/tg-account/start", json={"phone": "12"}, headers=H); ok(r.status_code == 400, "bad phone number refused")
r = c.post("/admin/api/tg-account/start", json={"phone": "+91 98765-43210"}, headers=H); ok(r.status_code == 200 and j(r)["step"] == "code", "login code requested")
ok(c.post("/admin/api/tg-account/code", json={"code": "00000"}, headers=H).status_code == 400, "wrong code refused")
r = c.post("/admin/api/tg-account/code", json={"code": "123 45"}, headers=H); ok(r.status_code == 200 and j(r)["result"] == "password" and j(r)["step"] == "password", "right code -> asks for the 2-step password")
ok(c.post("/admin/api/tg-account/password", json={"password": "nope"}, headers=H).status_code == 400, "wrong 2-step password refused")
r = c.post("/admin/api/tg-account/password", json={"password": "hunter2"}, headers=H)
ok(r.status_code == 200 and j(r)["linked"] is True, "2-step password accepted -> account linked")
rec = db.get(userbot.PATH)
ok(rec["session"] != SESSION and SESSION not in json.dumps(rec) and userbot._unseal(rec["session"]) == SESSION, "the session is stored ENCRYPTED in the database (and decrypts back)")
ok("phone" not in json.dumps(rec).lower() and "919876543210" not in json.dumps(rec), "no phone number in the database")
dump = json.dumps([j(c.get("/admin/api/settings")), j(c.get("/admin/api/tg-account"))])
ok("919876543210" not in dump and "FAKE-SESSION" not in dump and "hunter2" not in dump and '"linked": true' in dump, "the panel only learns 'linked' (no number, no 2-step password, no session)")
ok(set(j(c.get("/admin/api/tg-account"))) == {"configured", "linked", "expired", "step", "channel"}, "status fields are limited to yes / no")
logs = json.dumps(db.get_list("/logs"))
ok("919876543210" not in logs and "hunter2" not in logs and "tgaccount.linked" in logs, "audit log notes the link but never the number")
# roles
c2, H2, me2 = login_admin(app, "boss2", "TestPassw0rd456")
ok(me2["role"] == "admin2", "2nd admin signed in")
ok(c2.post("/admin/api/tg-account/unlink", headers=H2).status_code in (403,) and userbot.linked(), "2nd admin cannot unlink")
vc = app.test_client(); c.post("/admin/api/admins", json={"username": "viewer1", "password": "ViewerPass123"}, headers=H)
vc, VH, _ = login_admin(app, "viewer1", "ViewerPass123")
ok(vc.post("/admin/api/tg-account/start", json={"phone": "+919876543210"}, headers=VH).status_code == 403, "view-only admin cannot link")
# survives a redeploy: a brand-new backend object is connected from the stored session
fb2 = FakeBackend(); userbot._backend = fb2
ok(userbot.status()["linked"] and not fb2.connected(), "after a redeploy the account still shows linked")
info = userbot.resolve_chat("@storevault")
ok(fb2.connected() and info["title"] == "Store Vault", "…and reconnects itself from the saved session on first use")

# =============================================================== 3. storage channel
rr = c.post("/admin/api/resellers", data={"name": "Rita", "username": "rita", "chat_id": "4242", "bot_token": "654321:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "commission_pct": "10",
            "due_limit": "400", "login_username": "rita", "password": "ResellerPass123"}, headers=h.H if hasattr(h, "H") else H, content_type="multipart/form-data")
ok(rr.status_code == 201, "a reseller with its own bot exists"); RID = j(rr)["id"]
rctx = get_ctx(RID)
ok(c.put("/admin/api/tg-channel", json={"chat": "@nope"}, headers=H).status_code == 400, "unknown channel refused")
r = c.put("/admin/api/tg-channel", json={"chat": "@storevault"}, headers=H)
bots = j(r)["bots"]
ok(r.status_code == 200 and j(r)["channel"]["title"] == "Store Vault" and {b["bot"] for b in bots} >= {"main bot"} and len(bots) == 2 and all(b["ok"] for b in bots), "channel saved; main bot AND reseller bot are put into it by the linked account")
ok({u for _, u in fb2.granted} >= {"main_bot", "reseller_bot"}, "the account was asked to add @main_bot and the reseller's bot")
ok(c.post("/admin/api/tg-channel/sync", headers=H).status_code == 200, "re-check bot access works")

# =============================================================== 4. files sent to the bot are copied to the channel
APK_A, APK_S, VID_A = "BQACAgUAAxkBAAIAdminApkId00000000001", "BQACAgUAAxkBAAISellerApkId00000000002", "BAACAgUAAxkBAAIAdminVidId00000000003"
reply = h.bot_file(999, "doc", APK_A, "Super Game v2.apk", 5000)
cp = [p for m_, p, *_ in h.CALLS if m_ == "copyMessage"]
ok(cp and cp[0]["chat_id"] == "-1009876" and str(cp[0]["from_chat_id"]) == "999" and "message_id" in cp[0], "an admin's APK is copied to the storage channel")
ok("Saved to the storage channel" in reply and APK_A in reply, "admin is told it is stored; the id is still shown")
ix = tgchannel.index(APK_A)
ok(ix["mid"] and ix["chat"] == "-1009876" and ix["name"] == "Super Game v2.apk", "channel message id + real file name remembered")
from services import sellers as sellers_svc
sel = sellers_svc.create({"name": "Sam", "login_username": "sam", "password": "SellerPass123"}, admin=True)
db.update(f"/sellers/{sel['id']}", {"chat_id": "777"})
reply = h.bot_file(777, "doc", APK_S, "seller.apk", 4000)
ok(any(m_ == "copyMessage" and str(p["from_chat_id"]) == "777" for m_, p, *_ in h.CALLS) and APK_S in reply and "storage channel" not in reply, "a seller's APK is copied too (the seller just gets the id)")
reply = h.bot_file(999, "video", VID_A, size=3000)
ok(tgchannel.index(VID_A).get("mid"), "videos are copied as well")
# copy fails -> seller warned, admins told
_orig_call = h.tgmod.Telegram.call
def failing(self, method, payload=None, files=None, timeout=20):
    if method == "copyMessage" and (payload or {}).get("chat_id") == "-1009876" and failing.on:
        raise TelegramError("Bad Request: chat not found", 400)
    return _orig_call(self, method, payload, files, timeout)
failing.on = True
h.tgmod.Telegram.call = failing
fb2.granted.clear()
APK_F = "BQACAgUAAxkBAAISellerApkFail0000000004"
reply = h.bot_file(777, "doc", APK_F, "fail.apk", 4000)
ok(fb2.granted and "admin has to check" in reply and any("could not be copied" in str(p) for p in sent("sendMessage", "", 999)), "channel refuses: the bot is re-added once, then seller is warned and the admin is notified")
ok(not tgchannel.index(APK_F).get("mid"), "no channel copy recorded for the failed file")
failing.on = False

# =============================================================== 5. delivery: reseller bots copy from the channel, real names, no re-upload
cat = db.get_list("/categories")
CAT = j(c.post("/admin/api/categories", json={"name": "Games"}, headers=H))["id"] if not cat else cat[0]["id"]
app_rec = {"id": "app_x", "name": "Super Game", "apk_id": APK_A, "setup_video_id": VID_A}
lic = {"key": "ABCDEFGHJKLMNPQRST", "validity_label": "30 days", "expires_at": utils.now() + 86400}
h.CALLS.clear()
sent_ids = delivery.deliver_assets(rctx, 31001, app_rec, lic, "30 days")
cps = [p for m_, p, *_ in h.CALLS if m_ == "copyMessage"]
ok(len(cps) == 2 and all(str(p["chat_id"]) == "31001" and p["from_chat_id"] == "-1009876" for p in cps), "reseller bot: video + APK are COPIED from the channel to the customer")
ok("ABCDEFGHJKLMNPQRST" in str([p for p in cps if p.get("caption")]) and all(t == rctx.tg.token for m_, p, t, *_ in h.CALLS if m_ == "copyMessage"), "…through the reseller's own bot, with the license key as caption")
ok(not [1 for m_, *_ in h.CALLS if m_ in ("sendDocument", "sendVideo", "getFile")], "no download, no upload, no re-send")
ok(len(sent_ids) == 2, "message ids are returned")
h.CALLS.clear()
delivery.deliver_assets(get_ctx(""), 31002, app_rec, lic, "30 days")
ok([m_ for m_, *_ in h.CALLS if m_ in ("sendDocument", "sendVideo")] == ["sendVideo", "sendDocument"] and not [1 for m_, *_ in h.CALLS if m_ == "copyMessage"], "main bot sends its own file ids directly")
# a bot that is not in the channel yet gets brought in, once
fb2.granted.clear(); state = {"n": 0}
def needs_access(self, method, payload=None, files=None, timeout=20):
    if method == "copyMessage" and (payload or {}).get("from_chat_id") == "-1009876" and not state["n"]:
        state["n"] = 1; raise TelegramError("Bad Request: message to copy not found", 400)
    return _orig_call(self, method, payload, files, timeout)
h.tgmod.Telegram.call = needs_access
h.CALLS.clear()
r = delivery.safe_send(rctx, 31003, "document", APK_A, "x")
ok(r and fb2.granted and sum(1 for m_, *_ in h.CALLS if m_ == "copyMessage") == 1, "a bot that cannot see the channel is added by the account and the copy is retried")
h.tgmod.Telegram.call = _orig_call
# old ids (no channel copy): fallback upload keeps a real .apk name -- never file.bin
OLD = "BQACAgUAAxkBAAIOldApkId000000000009"
def other_bots_refuse_ids(self, method, payload=None, files=None, timeout=20):  # real Telegram: a file id only works for its own bot
    if method == "sendDocument" and isinstance((payload or {}).get("document"), str) and self.token != Config.BOT_TOKEN:
        raise TelegramError("Bad Request: wrong file identifier/HTTP URL specified", 400)
    return _orig_call(self, method, payload, files, timeout)
h.tgmod.Telegram.call = other_bots_refuse_ids
h.CALLS.clear()
delivery.deliver_assets(rctx, 31004, {"id": "a2", "name": "Old Game!", "apk_id": OLD}, lic, "30 days")
post = [(p, t) for m_, p, t, f in h.CALLS if m_ == "sendDocument" and p.get("chat_id") == "-1009876"]
ok(post and post[0][1] == Config.BOT_TOKEN and post[0][0]["document"] == OLD, "no channel copy yet (file from before the channel): the MAIN bot posts it into the channel by id")
ok(tgchannel.index(OLD).get("mid") and any(m_ == "copyMessage" and str(p["chat_id"]) == "31004" for m_, p, *_ in h.CALLS), "…and the reseller's bot copies it to the customer — no download, no 20 MB limit")
ok(not [1 for m_, p, t, f in h.CALLS if m_ == "sendDocument" and f], "nothing was uploaded")
# main bot cannot post to the channel either -> last resort: download + upload under the real name
OLD2 = "BQACAgUAAxkBAAIOldApkId000000000010"
def channel_closed(self, method, payload=None, files=None, timeout=20):
    if method == "sendDocument" and (payload or {}).get("chat_id") == "-1009876":
        raise TelegramError("Bad Request: not enough rights to send documents", 400)
    return other_bots_refuse_ids(self, method, payload, files, timeout)
h.tgmod.Telegram.call = channel_closed
fb2.fail_grant = True if hasattr(fb2, "fail_grant") else None
h.CALLS.clear()
delivery.deliver_assets(rctx, 31005, {"id": "a3", "name": "Old Game!", "apk_id": OLD2}, lic, "30 days")
h.tgmod.Telegram.call = _orig_call
up = [(f, p) for m_, p, t, f in h.CALLS if m_ == "sendDocument" and f]
ok(up and up[0][0]["document"][0] == "Old_Game.apk", "last-resort upload (file from before the channel) is named Old_Game.apk, not file.bin")
# a failure tells the admin WHY
h.CALLS.clear()
def everything_fails(self, method, payload=None, files=None, timeout=20):
    if (method in ("sendDocument", "sendVideo", "copyMessage") and self.token != Config.BOT_TOKEN) or method == "getFile":
        raise TelegramError("Bad Request: file is too big", 400)
    if method == "sendDocument" and (payload or {}).get("chat_id") == "-1009876":
        raise TelegramError("Bad Request: not enough rights to send documents", 400)
    return _orig_call(self, method, payload, files, timeout)
h.tgmod.Telegram.call = everything_fails
OLD3 = "BQACAgUAAxkBAAIOldApkId000000000011"
delivery.deliver_assets(rctx, 31006, {"id": "a4", "name": "Big", "apk_id": OLD3, "setup_video_id": OLD3}, lic, "30 days")
h.tgmod.Telegram.call = _orig_call
alerts = [str(p) for m_, p, *_ in h.CALLS if m_ == "sendMessage" and "Reason:" in str(p)]
ok(len(alerts) == 2 and any("APK delivery" in a for a in alerts) and any("Setup video" in a for a in alerts) and "too big" in alerts[0], "failed APK and video both alert the owner with the real reason")
db.set(f"/file_cache/{delivery._cache_key(rctx.key, OLD)}", None)
ok(delivery._cache_key("t", "x").startswith("v2/"), "ids cached as file.bin by older versions are never reused")
# unlinking
ok(c.post("/admin/api/tg-account/unlink", headers=H).status_code == 200 and not userbot.linked() and fb2.logged_out and db.get(userbot.PATH) is None, "Super Admin unlinks: session deleted and logged out")
ok(j(c.get("/admin/api/tg-account"))["linked"] is False, "panel shows not linked")
fb3 = FakeBackend(); fb3.expired = True; userbot._backend = fb3
userbot._store(SESSION, "x")
try:
    userbot.resolve_chat("@x"); ok(False, "expired session must raise")
except userbot.UserbotError:
    ok(userbot.status()["expired"] and not userbot.linked(), "a session revoked from the phone shows 'expired — link again'")
print("ALL TG TESTS OK")
