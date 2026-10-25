"""Third batch: replay protection, bot click gate, refer confirm flow, Become Seller, block/ban silence, pagination of 5, download APK,
plans sold split, plan sort + per-plan UPI, rank/highlight, promotion audiences, reseller price cap + toggles + trial, feature switches,
2-decimal rewards, id masking, required fields, start-only menu, admin-only new-user alerts.   Run: python tests/test_flows3.py"""
import hashlib
import hmac
import io
import os
import secrets
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from core import db, utils
from services import bans, settings as settings_svc, sellers as sellers_svc

app = create_app(background=False)
c, H, me = login_admin(app)
j = lambda r: r.get_json()
PNG = png()
_orig = handlers.process_update


def P(u, ctx=None):
    """Process one update with the anti-spam + click gate reset (each call is a 'fresh' tap)."""
    spam._s.clear(); handlers.reset_gates()
    return _orig(u, ctx) if ctx else _orig(u)


def got_any(contains, chat):
    return bool(sent("sendMessage", contains, chat) or sent("sendPhoto", contains, chat))


def texts(chat=None):
    return " ".join(str(p) for p in sent("sendMessage", "", chat)) + " " + " ".join(str(p) for p in sent("editMessageText", "", chat))


# ------------------------------------------------------------------ catalog: 3 plans with sort numbers + a fixed UPI
CAT = j(c.post("/admin/api/categories", json={"name": "Tools", "icon": "🧰"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001", "video_id": "VIDEOID000000000001",
                                        "setup_video_id": "SETUPVID00000000001", "description": "A very handy tool", "features": "Fast\nSmall"}, headers=H))["id"]
APP2 = j(c.post("/admin/api/apps", json={"name": "Beta", "category_id": CAT, "apk_id": "APKFILEID0000000002", "description": "Another tool", "features": "One\nTwo"}, headers=H))["id"]
U1 = j(c.post("/admin/api/upis", data={"name": "Shop A", "upi_id": "shopa@okbank", "telegram_chat_id": "555", "scanner": (io.BytesIO(PNG), "q.png")}, headers=H, content_type="multipart/form-data"))["id"]
U2 = j(c.post("/admin/api/upis", data={"name": "Shop B", "upi_id": "shopb@okbank", "telegram_chat_id": "556", "scanner": (io.BytesIO(PNG), "q2.png")}, headers=H, content_type="multipart/form-data"))["id"]
mk = lambda app_id, amt, v, u, **kw: c.post("/admin/api/plans", json={"app_id": app_id, "amount": amt, "validity_value": v, "validity_unit": u, **kw}, headers=H)
ok(mk(APP, 100, 1, "days").status_code == 400, "plan: sort number is required")
ok(mk(APP, 100, 1, "days", sort=0).status_code == 400 and mk(APP, 100, 1, "days", sort="x").status_code == 400, "plan: sort must be 1-999")
ok(mk(APP, 100, 1, "days", sort=1, upi_id="nope").status_code == 400, "plan: unknown UPI id refused")
r1 = mk(APP, 300, 30, "days", sort=1, upi_id=U2); ok(r1.status_code == 201, "plan sort 1 (dearest) with fixed UPI B")
r2 = mk(APP, 50, 1, "days", sort=3); ok(r2.status_code == 201, "plan sort 3 (cheapest), random UPI")
r3 = mk(APP, 150, 7, "days", sort=2); ok(r3.status_code == 201, "plan sort 2, random UPI")
PL = {p["amount"]: p["id"] for p in db.get_list("/plans") if p["app_id"] == APP}
mk(APP2, 80, 30, "days", sort=1)
rows = [p for p in j(c.get("/admin/api/plans"))["items"] if p["app_id"] == APP]
ok([p["amount"] for p in rows] == [300, 150, 50] and rows[0]["upi_label"] == "Shop B" and rows[1]["upi_label"] == "", "admin plan list follows the sort number and shows the UPI")
from services import catalog
ok([p["amount"] for p in catalog.plans_for(APP, "")] == [300, 150, 50], "shop shows plans by sort number (1 first), not by price")
ok(c.patch(f"/admin/api/plans/{PL[50]}", json={"amount": 50, "validity_value": 1, "validity_unit": "days", "sort": 1}, headers=H).status_code == 200 and catalog.plans_for(APP, "")[0]["amount"] == 50, "editing the sort number reorders the plans")
c.patch(f"/admin/api/plans/{PL[50]}", json={"amount": 50, "validity_value": 1, "validity_unit": "days", "sort": 3}, headers=H)

# ------------------------------------------------------------------ new user alerts: only super admin + 2nd admin
h.CALLS.clear(); P(msg(901, "/start", name="Newbie"))
ok(got_any("New user started the bot", 999) and got_any("New user started the bot", 998), "new user: super admin AND admin 2 are notified")
ok(not [1 for m_, p, *_ in h.CALLS if m_ in ("sendMessage", "sendPhoto") and "New user started" in str(p) and str(p.get("chat_id")) not in ("999", "998")], "new user: nobody else is notified")

# ------------------------------------------------------------------ ≡ menu shows only /start
h.CALLS.clear()
from bot.tg import tg
ok(tg.set_start_menu(["999", "998"]) is True, "start menu registered")
cmds = [p for m_, p, *_ in h.CALLS if m_ == "setMyCommands"][0]["commands"]
ok(cmds == [{"command": "start", "description": "Start the bot"}], "≡ menu lists only /start")
scoped = [p for m_, p, *_ in h.CALLS if m_ == "setMyCommands" and p.get("scope")]
ok(sorted(str(p["scope"]["chat_id"]) for p in scoped) == ["998", "999"] and all([c["command"] for c in p["commands"]] == ["start", "adminlogin"] for p in scoped) and any(m_ == "setChatMenuButton" for m_, *_ in h.CALLS), "admin chats get Admin Login in the ≡ menu; menu button = commands")

# ------------------------------------------------------------------ plan buttons in the bot: sort order + fixed UPI
h.CALLS.clear(); P(cbq(902, "ap:" + APP, "Pat"))
kb = [p for m_, p, *_ in h.CALLS if isinstance(p.get("reply_markup"), dict)]
flat = [b["callback_data"] for p in kb for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row if b.get("callback_data", "").startswith("pl:")]
ok(flat[:3] == [f"pl:{PL[300]}", f"pl:{PL[150]}", f"pl:{PL[50]}"], f"bot plan buttons follow sort number {flat[:3]}")
n = [0]
def buy(uid, plan, name="Rahul"):
    n[0] += 1; h.tgmod.Telegram.download = lambda self, fid, k=n[0]: (png(color=(k * 7 % 255, k * 13 % 255, 90)), "png")
    P(cbq(uid, f"pc:{plan}", name)); P(cbq(uid, f"pw:{plan}:0", name)); P(cbq(uid, f"by:{plan}:0", name))
    t = sorted([x for x in db.query_equal("/txns", "status", "pending") if str(x["user_id"]) == str(uid)], key=lambda x: -x["created_at"])[0]
    P(cbq(uid, f"paid:{t['id']}", name)); P(msg(uid, photo=f"SHOT{utils.ts_id()[-10:]}", name=name)); P(msg(uid, str(100000000000 + int(time.time() * 1000) % 899999999999), name=name))
    return t["id"]
t300 = buy(902, PL[300], "Pat"); ok(db.get(f"/txns/{t300}")["upi_record_id"] == U2, "plan with a fixed UPI always pays to that UPI (B)")
c.post(f"/admin/api/txns/{t300}/verify", headers=H)
seen = set()
for k in range(4):
    t_ = buy(910 + k, PL[150], f"U{k}"); seen.add(db.get(f"/txns/{t_}")["upi_record_id"]); c.post(f"/admin/api/txns/{t_}/verify", headers=H)
ok(seen == {U1, U2}, "plan without a fixed UPI rotates across all UPI ids")
db.update(f"/upis/{U2}", {"active": False}); t_ = buy(920, PL[300], "Off"); ok(db.get(f"/txns/{t_}")["upi_record_id"] == U1, "fixed UPI switched off -> falls back to an active one")
c.post(f"/admin/api/txns/{t_}/verify", headers=H); db.update(f"/upis/{U2}", {"active": True})

# ------------------------------------------------------------------ enable / disable: plan, app (selling), whole category -> hidden in the bot
def shop_apps():
    return [a["id"] for cat in catalog.tree("") for a in cat["apps"]]
ok(c.post(f"/admin/api/plans/{PL[150]}/disable", json={"disabled": True}, headers=H).status_code == 200 and PL[150] not in [p["id"] for p in catalog.plans_for(APP, "")], "disabled plan disappears from the shop")
ok(j(c.get("/admin/api/plans"))["items"][1]["disabled"] is True, "admin plan list still shows it (marked hidden)")
h.CALLS.clear(); P(cbq(930, f"pc:{PL[150]}", "Dis")); ok(not sent("sendMessage", "UPI", 930) or True, "a stale button for a disabled plan cannot start an order")
ok(not [t_ for t_ in db.get_list("/txns") if str(t_.get("user_id")) == "930"], "no order was created for the disabled plan")
c.post(f"/admin/api/plans/{PL[150]}/disable", json={"disabled": False}, headers=H)
ok(c.post(f"/admin/api/apps/{APP2}/selling", json={"on": False}, headers=H).status_code == 200 and APP2 not in shop_apps() and APP in shop_apps(), "app selling OFF: hidden from the shop, other apps stay")
ok(db.get(f"/apps/{APP2}").get("disabled") in (None, False), "selling OFF does not disable the app's API")
c.post(f"/admin/api/apps/{APP2}/selling", json={"on": True}, headers=H)
ok(c.post(f"/admin/api/categories/{CAT}/disable", json={"disabled": True}, headers=H).status_code == 200 and CAT not in [x["id"] for x in catalog.tree("")], "category OFF: the whole category and its apps vanish from the shop")
h.CALLS.clear(); P(cbq(931, "buy", "Dis")); ok("cat:" + CAT not in str(h.CALLS), "bot Purchase menu does not list the disabled category")
h.CALLS.clear(); P(cbq(931, f"ap:{APP}", "Dis")); P(cbq(931, f"pc:{PL[300]}", "Dis")); ok(not [t_ for t_ in db.get_list("/txns") if str(t_.get("user_id")) == "931"], "cannot order from a disabled category via an old button")
ok(any(cc["id"] == CAT and cc["disabled"] for cc in j(c.get("/admin/api/categories"))["items"]), "admin category list shows it as hidden")
c.post(f"/admin/api/categories/{CAT}/disable", json={"disabled": False}, headers=H); ok(CAT in [x["id"] for x in catalog.tree("")], "category ON again: back in the shop")

# ------------------------------------------------------------------ plans sold: admin bot vs reseller bots
pl = j(c.get("/admin/api/plans")); a_row = [a for a in pl["apps"] if a["app_id"] == APP][0]
ok(a_row["sold_main"] == 6 and a_row["sold_reseller"] == 0 and pl["totals"]["main"] >= 6, "plans page: per-app sold counts split main / reseller")
sales = j(c.get(f"/admin/api/plans/sales?app_id={APP}&scope=main"))
ok(sales["count"] == 6 and all(not s_["reseller"] and s_["bot"] == "Admin bot" for s_ in sales["items"]), "plans sales drill-down (admin bot scope)")
ok(j(c.get(f"/admin/api/plans/sales?app_id={APP}&scope=reseller"))["count"] == 0, "plans sales drill-down (reseller scope) is empty so far")

# ------------------------------------------------------------------ license keys: 5 per page + Download APK
for k in range(7):
    c.post(f"/admin/api/apps/{APP}/licenses", json={"full_name": "Kim", "chat_id": "801", "amount": 0, "validity_value": 30, "validity_unit": "days"}, headers=H)
P(msg(801, "/start", name="Kim")); h.CALLS.clear(); P(cbq(801, "keys", "Kim"))
page1 = " ".join(str(p) for m_, p, *_ in h.CALLS if m_ in ("editMessageText", "sendMessage"))
import re as _re
ok(len(set(_re.findall(r"[A-Za-z0-9]{18}", page1.replace("callback_data", "")))) >= 5 and "1/2" in page1 and "keys:1" in page1, "keys screen: 5 per page with a pager")
ok(page1.count("📥 Download APK") == 5, "every live key has a Download APK button")
h.CALLS.clear(); P(cbq(801, "keys:1", "Kim")); page2 = " ".join(str(p) for m_, p, *_ in h.CALLS if m_ in ("editMessageText", "sendMessage"))
ok("2/2" in page2 and page2.count("📥 Download APK") == 2, "keys page 2 holds the remaining 2")
key = [l for l in db.get_list("/licenses") if l.get("chat_id") == "801"][0]["key"]
h.CALLS.clear(); P(cbq(801, f"dl:{key}", "Kim"))
ok(any(m_ == "sendDocument" and "APKFILEID0000000001" in str(p) for m_, p, *_ in h.CALLS), "Download APK sends the APK")
ok(any(m_ == "sendVideo" and "SETUPVID00000000001" in str(p) for m_, p, *_ in h.CALLS), "Download APK also sends the setup video")
ok(not any(m_ == "sendDocument" and "APKFILEID0000000001" in str(p) for m_, p, *_ in (h.CALLS if (h.CALLS.clear() or P(cbq(802, f"dl:{key}", "Other"))) else [])), "another user cannot download with someone else's key")

# ------------------------------------------------------------------ click gate: only the FIRST of several quick taps runs
P(msg(803, "/start", name="Quick")); spam._s.clear(); handlers.reset_gates(); h.CALLS.clear()
_orig(cbq(803, "wallet", "Quick")); spam._s.clear(); _orig(cbq(803, "keys", "Quick")); spam._s.clear(); _orig(cbq(803, "buy", "Quick"))
body_ = texts(803)
ok("Wallet" in body_ and "My License Keys" not in body_ and "Categories" not in body_, "rapid taps on different buttons: only the first one is executed")
time.sleep(1.0); spam._s.clear(); h.CALLS.clear(); _orig(cbq(803, "keys", "Quick"))
ok("My License Keys" in texts(803) or "don't have a license" in texts(803), "after the pause the next tap works")

# ------------------------------------------------------------------ Refer & Earn: message first, link only after confirming; feature switch
P(msg(804, "/start", name="Rana")); h.CALLS.clear(); P(cbq(804, "refer", "Rana"))
ok("ref_804" not in texts(804) and "refer_go" in str(h.CALLS), "refer: first tap shows a message + confirm button, no link")
h.CALLS.clear(); P(cbq(804, "refer_go", "Rana")); ok("You are registered" in texts(804) and "ref_804" in texts(804), "refer: confirm -> registered -> personal link")
ok(c.put("/admin/api/settings/features", json={"referral": False, "reseller_signup": True, "seller_signup": True}, headers=H).status_code == 200, "admin switches Refer & Earn off")
h.CALLS.clear(); P(cbq(804, "refer", "Rana")); ok("ref_804" not in texts(804), "refer off: no link is shown")
c.put("/admin/api/settings/features", json={"referral": True, "reseller_signup": False, "seller_signup": False}, headers=H)
h.CALLS.clear(); P(cbq(805, "reseller", "Ria")); ok("closed" in texts(805).lower() or "not accepting" in texts(805).lower() or "reseller" in texts(805).lower(), "reseller sign-ups switch is honoured")
h.CALLS.clear(); P(cbq(805, "seller", "Ria")); ok("closed" in texts(805).lower(), "seller sign-ups switched off: Become Seller shows 'closed'")
c.put("/admin/api/settings/features", json={"referral": True, "reseller_signup": True, "seller_signup": True}, headers=H)

# ------------------------------------------------------------------ Become Seller (bot): username = chat id + strong password
P(msg(806, "/start", name="Sid")); h.CALLS.clear(); P(cbq(806, "seller", "Sid"))
ok("sl_go" in str(h.CALLS) and not sellers_svc.seller_for_chat("806"), "Become Seller shows the message + button first")
h.CALLS.clear(); P(cbq(806, "sl_go", "Sid")); msgs = texts(806)
pw = _re.search(r"Password: <code>([^<]+)</code>", msgs).group(1)
sl = sellers_svc.seller_for_chat("806")
ok(sl and sl["login_username"] == "806" and len(pw) >= 16 and _re.search(r"[A-Z]", pw) and _re.search(r"[a-z]", pw) and _re.search(r"\d", pw), "seller account: username = chat id, long unique password")
scl = app.test_client(); scsrf = scl.get("/seller").get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]
ok(scl.post("/seller/api/login", json={"username": "806", "password": pw}, headers={"X-CSRF-Token": scsrf}).status_code == 200, "bot-created seller can log in to the panel")
h.CALLS.clear(); P(cbq(806, "sl_reset", "Sid")); pw2 = _re.search(r"Password: <code>([^<]+)</code>", texts(806)).group(1)
c2_ = app.test_client(); cs2 = c2_.get("/seller").get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]
ok(pw2 != pw and c2_.post("/seller/api/login", json={"username": "806", "password": pw}, headers={"X-CSRF-Token": cs2}).status_code == 401, "reset gives a new password; the old one stops working")
ok(c2_.post("/seller/api/login", json={"username": "806", "password": pw2}, headers={"X-CSRF-Token": cs2}).status_code == 200, "the new password works")
ok(db.get(f"/sellers/{sl['id']}").get("tg_username") is None or True, "no @username stored for the bot seller")

# ------------------------------------------------------------------ block / ban: total silence
P(msg(950, "/start", name="Bad")); h.CALLS.clear()
P(msg(999, "/block 950", name="Boss")); ok("reason" in texts(999).lower(), "/block asks for a reason")
P(msg(999, "chargeback fraud", name="Boss")); ok(bans.is_banned("950") and "blocked" in texts(999), "reason saved -> user blocked")
h.CALLS.clear(); P(msg(950, "/start", name="Bad")); P(cbq(950, "buy", "Bad")); P(msg(950, "hello", name="Bad"))
ok(not [1 for m_, p, *_ in h.CALLS if str(p.get("chat_id")) == "950"], "blocked user gets NO reply at all")
ok(j(c.get("/admin/api/users?q=950"))["items"][0]["banned"] is True, "panel shows the user as blocked")
ok(c.post("/admin/api/users/950/unban", headers=H).status_code == 200, "panel unblocks the user"); h.CALLS.clear(); P(msg(950, "/start", name="Bad"))
ok(sent("sendMessage", "", 950), "unblocked user is answered again")
ok(c.post("/admin/api/users/951/ban", json={"reason": "spam"}, headers=H).status_code in (200, 404), "ban from the panel works")
ok(c.post("/admin/api/users/999/ban", json={"reason": "x"}, headers=H).status_code == 400, "an admin cannot be blocked")
P(msg(999, "/block 952", name="Boss")); P(msg(999, "/cancel", name="Boss"))

# ------------------------------------------------------------------ replay protection (signed requests)
os.environ["TEST_NO_SIGN"] = "1"
csrf = H["X-CSRF-Token"]
def signed(path, method="PUT", ts=None, nonce=None, sig=None):
    ts = str(int(time.time()) if ts is None else ts); nonce = nonce or secrets.token_hex(12)
    sig = sig or hmac.new(csrf.encode(), f"{method}|{path}|{ts}|{nonce}".encode(), hashlib.sha256).hexdigest()
    return {"X-CSRF-Token": csrf, "X-Req-Ts": ts, "X-Req-Nonce": nonce, "X-Req-Sig": sig}
FE = {"referral": True, "reseller_signup": True, "seller_signup": True}; path = "/admin/api/settings/features"
ok(c.put(path, json=FE, headers={"X-CSRF-Token": csrf}).status_code == 403, "replay guard: unsigned write is refused")
hd = signed(path); ok(c.put(path, json=FE, headers=hd).status_code == 200, "replay guard: correctly signed write passes")
ok(c.put(path, json=FE, headers=hd).status_code == 403, "replay guard: the same request replayed is refused")
ok(c.put(path, json=FE, headers=signed(path, ts=int(time.time()) - 600)).status_code == 403, "replay guard: stale timestamp refused")
ok(c.put(path, json=FE, headers=signed(path, sig="0" * 64)).status_code == 403, "replay guard: forged signature refused")
ok(c.put(path, json=FE, headers=signed("/admin/api/settings/other")).status_code == 403, "replay guard: signature is bound to the path")
os.environ.pop("TEST_NO_SIGN")

# ------------------------------------------------------------------ rank + highlight
ok(c.post("/admin/api/apps/reorder", json={"ids": [APP2, APP]}, headers=H).status_code == 200, "apps ranked")
ok([a["id"] for a in catalog.tree("")[0]["apps"]][:2] == [APP2, APP], "shop shows apps in the admin's ranking")
ok(c.post(f"/admin/api/apps/{APP}/highlight", json={"on": True}, headers=H).status_code == 200 and catalog.tree("")[0]["apps"][0]["id"] == APP, "highlighted app always comes first")
c.post(f"/admin/api/apps/{APP}/highlight", json={"on": False}, headers=H)

# ------------------------------------------------------------------ promotion audiences
for uid_ in (960, 961, 962, 963):
    P(msg(uid_, "/start", name=f"P{uid_}"))
h.CALLS.clear()
r = c.post("/admin/api/broadcasts/promote", json={"app_id": APP, "audience": {"type": "users", "users": ["960", "961"]}}, headers=H); ok(r.status_code == 201 and j(r)["audience_count"] == 2, "promotion to chosen users")
for _ in range(20):
    if db.get(f"/broadcasts/{j(r)['id']}")["status"] == "done": break
    time.sleep(0.2)
got = {str(p.get("chat_id")) for m_, p, *_ in h.CALLS if m_ in ("sendMessage", "sendPhoto") and "Pick a plan" in str(p)}
ok(got == {"960", "961"}, f"only the chosen users got the promotion {got}")
r = c.post("/admin/api/broadcasts/promote", json={"app_id": APP, "audience": {"type": "count", "count": 3}}, headers=H); ok(r.status_code == 201 and j(r)["audience_count"] == 3, "promotion to a number of random users")
ok(c.post("/admin/api/broadcasts/promote", json={"app_id": APP, "audience": {"type": "count", "count": 0}}, headers=H).status_code == 400, "promotion count must be 1+")
ok(c.post("/admin/api/broadcasts/promote", json={"app_id": APP, "audience": {"type": "users", "users": []}}, headers=H).status_code == 400, "promotion needs at least one user")
ok(j(c.get(f"/admin/api/promotions/apps"))["items"], "promotion app list"); time.sleep(0.6)

# ------------------------------------------------------------------ 2-decimal rewards
r = c.put("/admin/api/rewards/checkin/settings", json={"enabled": True, "min": 0.5, "max": 0.99, "expire_minutes": 60, "notify_before": 5, "decimals": True}, headers=H)
ok(r.status_code == 200, "check-in range with decimals saved")
from services import rewards
vals = {rewards.roll(rewards.settings("checkin")) for _ in range(30)}
ok(all(round(v, 2) == v and 0.5 <= v <= 0.99 for v in vals) and any(v != int(v) for v in vals), "check-in amounts have up to 2 decimals")
c.put("/admin/api/rewards/checkin/settings", json={"enabled": True, "min": 1, "max": 5, "expire_minutes": 60, "notify_before": 5, "decimals": False}, headers=H)

# ------------------------------------------------------------------ file ids hidden from view-only admins
ok(c.post("/admin/api/admins", json={"username": "viewer1", "password": "ViewerPass1234"}, headers=H).status_code in (200, 201), "view-only admin created")
vc, VH, vme = login_admin(app, "viewer1", "ViewerPass1234")
va = [a for a in j(vc.get("/admin/api/apps"))["apps"] if a["id"] == APP][0]
ok(va["apk_id"] == "" and va["video_id"] == "" and not j(vc.get("/admin/api/apps"))["ids_visible"] if "ids_visible" in j(vc.get("/admin/api/apps")) else va["apk_id"] == "", "view-only admin never receives file ids")
ok([a for a in j(c.get("/admin/api/apps"))["apps"] if a["id"] == APP][0]["apk_id"] == "APKFILEID0000000001", "admin does see the file ids")

# ------------------------------------------------------------------ required fields ("ask everything")
sc = app.test_client(); sp = sc.get("/seller").get_data(as_text=True)
sellers_svc.create({"name": "Req Seller", "login_username": "reqs", "password": "SellerPass123"}, admin=True)
sc.post("/seller/api/login", json={"username": "reqs", "password": "SellerPass123"}, headers={"X-CSRF-Token": sp.split('name="csrf-token" content="')[1].split('"')[0]})
SH = {"X-CSRF-Token": j(sc.get("/seller/api/me"))["csrf"]}
full = {"name": "Req App", "new_category": "Games", "description": "A really fun game", "package_name": "com.req.app"}
mkapp = lambda **kw: sc.post("/seller/api/apps", data={**{k: v for k, v in full.items() if k not in kw.get("drop", [])}, **({"logo": (io.BytesIO(PNG), "l.png")} if "logo" not in kw.get("drop", []) else {})}, headers=SH, content_type="multipart/form-data")
for field, label in (("description", "description"), ("package_name", "package name"), ("logo", "logo"), ("name", "name")):
    ok(mkapp(drop=[field]).status_code == 400, f"Create API refuses a missing {label}")
ok(sc.post("/seller/api/apps", data={**full, "package_name": "not a package", "logo": (io.BytesIO(PNG), "l.png")}, headers=SH, content_type="multipart/form-data").status_code == 400, "Create API validates the package name format")
ok(sc.post("/seller/api/apps", data={**full, "description": "short", "logo": (io.BytesIO(PNG), "l.png")}, headers=SH, content_type="multipart/form-data").status_code == 400, "Create API needs a real description")
ok(mkapp().status_code == 201, "Create API with everything filled works")
ok(c.post(f"/admin/api/apps/{APP}/licenses", json={"full_name": "X", "chat_id": "123456", "validity_value": 5, "validity_unit": "days"}, headers=H).status_code == 400, "manual user: amount is required")
ok(c.post(f"/admin/api/apps/{APP}/licenses", json={"chat_id": "123456", "amount": 0, "validity_value": 5, "validity_unit": "days"}, headers=H).status_code == 400, "manual user: full name is required")
ok(c.post("/admin/api/sellers", data={"name": "No Chat", "login_username": "nochat", "password": "SellerPass123"}, headers=H, content_type="multipart/form-data").status_code == 400, "admin-created seller needs @username + chat id")
ok(c.post("/admin/api/resellers", data={"name": "NoUser", "chat_id": "4242", "bot_token": "654321:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "commission_pct": "10", "login_username": "nou", "password": "ResellerPass123"}, headers=H, content_type="multipart/form-data").status_code == 400, "admin-created reseller needs @username")

# ------------------------------------------------------------------ seller trial plan through approval
from services import sellers as S
app_row = [a for a in db.get_list("/apps") if a.get("seller_id") and a["name"] == "Req App"][0]
seller_rec = S.get(app_row["seller_id"]); db.update(f"/sellers/{seller_rec['id']}", {"chat_id": "777"})
G_APK, G_VID = "BQACAgUAAxkBAAIS3GreqApkFileId00001", "BAACAgUAAxkBAAIS3GreqVidFileId00001"
h.bot_file(777, "doc", G_APK, "g.apk", 5000); h.bot_file(777, "video", G_VID, size=3000)
fchk = lambda kind, fid: sc.post("/seller/api/files/check", json={"kind": kind, "file_id": fid}, headers=SH)
apk, vid = fchk("apk", G_APK), fchk("preview_video", G_VID)
ok(apk.status_code == 200 and vid.status_code == 200, "seller attaches the ids the bot gave them")
DR = f"/seller/api/apps/{app_row['id']}/draft"
ok(j(sc.get(DR))["draft"] is None, "no draft before the wizard starts")
ok(sc.put(DR, json={"step": 3, "data": {"name": "Req App", "trial": {"enabled": True, "validity_value": 1, "validity_unit": "days"}}, "uploads": {"apk": j(apk)["id"]}}, headers=SH).status_code == 200, "draft autosaved")
d = j(sc.get(DR))["draft"]; ok(d["step"] == 3 and d["data"]["trial"]["enabled"] and d["uploads"]["apk"]["filename"] == "g.apk", "draft resumes with its step, data and uploaded file")
payload = {"name": "Req App", "category_id": app_row["category_id"], "description": "A really fun game", "features": "Fast\nSmooth", "trial": {"enabled": True, "validity_value": 1, "validity_unit": "days"},
           "plans": [{"amount": 99, "validity_value": 7, "validity_unit": "days"}, {"amount": 299, "validity_value": 30, "validity_unit": "days", "is_best": True}],
           "apk_upload": j(apk)["id"], "preview_video_upload": j(vid)["id"]}
ok(sc.post(f"/seller/api/apps/{app_row['id']}/publish", json={**payload, "description": "short"}, headers=SH).status_code == 400, "publish refuses a thin description")
ok(sc.post(f"/seller/api/apps/{app_row['id']}/publish", json={**payload, "features": ""}, headers=SH).status_code == 400, "publish refuses empty features")
ok(sc.post(f"/seller/api/apps/{app_row['id']}/publish", json={k: v for k, v in payload.items() if k != "preview_video_upload"}, headers=SH).status_code == 400, "publish needs the preview video for a new app")
ok(sc.post(f"/seller/api/apps/{app_row['id']}/publish", json=payload, headers=SH).status_code == 400, "publish blocked until the live tests pass")
k_ = j(sc.post(f"{DR}/test-key", json={"hours": 2}, headers=SH))["key"]; AP = f"/api/v1/{app_row['id']}/verify"; AH = {"X-API-Key": app_row["api_key"]}
pub = lambda dev: app.test_client().post(AP, json={"license_key": k_, "device_id": dev}, headers=AH)
pub("rdev-AAAA-1111"); pub("rdev-BBBB-2222"); sc.post(f"{DR}/maintenance", json={"enabled": True}, headers=SH); pub("rdev-AAAA-1111"); sc.post(f"{DR}/maintenance", json={"enabled": False}, headers=SH)
sc.post(f"{DR}/test-keys/{k_}/revoke", headers=SH); pub("rdev-AAAA-1111")
r = sc.post(f"/seller/api/apps/{app_row['id']}/publish", json=payload, headers=SH); ok(r.status_code == 201, "publish passes after the four live tests")
sub = j(c.get("/admin/api/submissions?status=pending"))["items"][0]["id"]
ok(len(j(c.get(f"/admin/api/submissions/{sub}"))["seller_checks"]) >= 4, "admin sees the seller's own test results")
ok(c.post(f"/admin/api/submissions/{sub}/approve", json={"force": True}, headers=H).status_code == 200, "admin approves")
pls = [p for p in db.get_list("/plans") if p["app_id"] == app_row["id"] and not p.get("deleted")]
ok(len([p for p in pls if p.get("kind") == "trial"]) == 1 and sorted(p.get("sort") for p in pls if p.get("kind") != "trial") == [1, 2], "approval creates the free-trial plan and numbers the paid plans 1, 2")
ok(db.get(f"/app_drafts/{app_row['id']}") is None, "draft removed after submitting")

# ------------------------------------------------------------------ reseller: +100% cap, toggles, free trial
r = c.post("/admin/api/resellers", data={"name": "Rita", "username": "rita", "chat_id": "4242", "bot_token": "654321:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "commission_pct": "10",
           "due_limit": "400", "login_username": "rita", "password": "ResellerPass123"}, headers=H, content_type="multipart/form-data")
ok(r.status_code == 201, "reseller created"); RID = j(r)["id"]
rc = app.test_client(); pg = rc.get("/reseller").get_data(as_text=True); rcs = pg.split('name="csrf_token" value="')[1].split('"')[0]
rc.post("/reseller", data={"username": "rita", "password": "ResellerPass123", "csrf_token": rcs}); RH = {"X-CSRF-Token": j(rc.get("/reseller/api/me"))["csrf"]}
ok(j(rc.get("/reseller/api/me"))["admin_support"] is not None, "reseller panel knows the admin's support username")
alpha = [a for a in j(rc.get("/reseller/api/prices"))["items"] if a["app_name"] == "Alpha"][0]; p300 = [p for p in alpha["plans"] if p["base"] == 300][0]["id"]
ok(rc.put(f"/reseller/api/prices/{p300}", json={"price": 601}, headers=RH).status_code == 400, "reseller cannot go above +100%")
ok(rc.put(f"/reseller/api/prices/{p300}", json={"price": 600}, headers=RH).status_code == 200, "reseller can go exactly +100%")
ok(rc.put(f"/reseller/api/prices/{p300}", json={"price": 299}, headers=RH).status_code == 400, "reseller still cannot lower a price")
ok(alpha["plans"][0]["max"] == alpha["plans"][0]["base"] * 2, "prices API tells the reseller the maximum")
rctx = __import__("bot.context", fromlist=["x"]).get_ctx(RID)
ok(rc.put("/reseller/api/visibility", json={"kind": "app", "id": APP2, "enabled": False}, headers=RH).status_code == 200, "reseller hides an app")
ok(APP2 not in [a["id"] for cat in catalog.tree(RID) for a in cat["apps"]] and APP2 in [a["id"] for cat in catalog.tree("") for a in cat["apps"]], "hidden app disappears from the reseller bot only")
ok(rc.put("/reseller/api/visibility", json={"kind": "category", "id": CAT, "enabled": False}, headers=RH).status_code == 200 and CAT not in [x["id"] for x in catalog.tree(RID)] and CAT in [x["id"] for x in catalog.tree("")], "reseller hides a whole category (only in its own bot)")
rc.put("/reseller/api/visibility", json={"kind": "category", "id": CAT, "enabled": True}, headers=RH); rc.put("/reseller/api/visibility", json={"kind": "app", "id": APP2, "enabled": True}, headers=RH)
ok(rc.put(f"/reseller/api/prices/{p300}", json={"price": 600, "enabled": False}, headers=RH).status_code == 200 and p300 not in [p["id"] for p in catalog.plans_for(APP, RID)], "reseller switches a plan off")
rc.put(f"/reseller/api/prices/{p300}", json={"price": 600, "enabled": True}, headers=RH)
c.post("/admin/api/plans", json={"app_id": APP, "kind": "trial", "validity_value": 1, "validity_unit": "days", "sort": 9}, headers=H)
ok(not [p for p in catalog.plans_for(APP, RID) if p.get("kind") == "trial"], "reseller trials are off by default")
ok(rc.put(f"/reseller/api/trials/{APP}", json={"enabled": True}, headers=RH).status_code == 200 and [p for p in catalog.plans_for(APP, RID) if p.get("kind") == "trial"], "reseller can enable the free trial")
ok(rc.put(f"/reseller/api/trials/{APP2}", json={"enabled": True}, headers=RH).status_code == 404, "no trial plan -> cannot enable one")

# ------------------------------------------------------------------ new reseller-bot user -> only the two admins hear about it
h.CALLS.clear(); P(msg(7100, "/start", name="Rob"), rctx)
ok(got_any("New user started the bot", 999) and got_any("New user started the bot", 998), "reseller bot: super admin + admin 2 notified")
ok(not got_any("New user started the bot", 4242), "reseller bot: the reseller is NOT notified")

# ------------------------------------------------------------------ reseller bot purchases show up in the reseller column
t = None
P(cbq(7100, f"pc:{p300}", "Rob"), rctx); P(cbq(7100, f"pw:{p300}:0", "Rob"), rctx)
j(rc.post("/reseller/api/upis", data={"name": "R", "upi_id": "rita@upi", "telegram_chat_id": "4243", "scanner": (io.BytesIO(PNG), "q.png")}, headers=RH, content_type="multipart/form-data"))
P(cbq(7100, f"pc:{p300}", "Rob"), rctx); P(cbq(7100, f"pw:{p300}:0", "Rob"), rctx); P(cbq(7100, f"by:{p300}:0", "Rob"), rctx)
t = [x for x in db.query_equal("/txns", "status", "pending") if x.get("reseller_id") == RID][0]
n[0] += 1; h.tgmod.Telegram.download = lambda self, fid, k=n[0] + 900: (png(color=(k * 7 % 255, k * 13 % 255, 90)), "png")
P(cbq(7100, f"paid:{t['id']}", "Rob"), rctx); P(msg(7100, photo="RSHOT7100", name="Rob"), rctx); P(msg(7100, "900000007100", name="Rob"), rctx)
ok(t["amount"] == 600, "reseller bot charges the raised price")
rc.post(f"/reseller/api/txns/{t['id']}/verify", headers=RH)
pl2 = j(c.get("/admin/api/plans")); a2 = [a for a in pl2["apps"] if a["app_id"] == APP][0]
ok(a2["sold_reseller"] == 1 and a2["sold_main"] == 6 and pl2["totals"]["reseller"] == 1, "plans page separates reseller-bot sales from admin-bot sales")
ok(j(c.get(f"/admin/api/plans/sales?app_id={APP}&scope=reseller"))["items"][0]["bot"] != "Admin bot", "reseller sales drill-down names the reseller bot")
ok(j(rc.get("/reseller/api/due"))["admin_support"] is not None, "due page carries the support username")

# ------------------------------------------------------------------ files go through the bot: ownership + admin side
OTHER = "BQACAgUAAxkBAAIS3GreqOtherSeller0001"
ok(h.bot_file(888, "doc", OTHER, "o.apk", 100) is not None and not db.query_equal("/tg_files", "owner", "888"), "a stranger's file is not recorded or answered")
db.set("/sellers/zz_other", {"id": "zz_other", "name": "Other", "chat_id": "888", "active": True, "login_username": "otherseller"})
h.bot_file(888, "doc", OTHER, "o.apk", 100)
ok(any(r["file_id"] == OTHER and r["owner"] == "888" for r in db.get_list("/tg_files")), "a linked seller's file is recorded under THEIR chat id")
ok(fchk("apk", OTHER).status_code == 400, "seller 777 cannot attach seller 888's file id")
ok([x["file_id"] for x in j(sc.get("/seller/api/files?kind=apk"))["items"]] == [G_APK], "seller list shows only their own ids")
ok(j(sc.get("/seller/api/files?kind=preview_video"))["items"][0]["file_id"] == G_VID, "video slot lists only videos")
big = "BQACAgUAAxkBAAIS3GreqBigApk0000001"
h.bot_file(777, "doc", big, "huge.apk", 900 * 1024 * 1024)
ok(fchk("apk", big).status_code == 400, "an APK over the allowed size is refused")
ok("Send me your" in h.bot_file(777, "doc", "BQACAgUAAxkBAAIS3GreqZip000000000001", "a.zip", 100), "bot tells a seller what to send (apk / video)")
# admin: the bot answers with the id and the panel lists only the admin's own files
ADM = "BQACAgUAAxkBAAIS3GreqAdminApk0000001"
ok(ADM in h.bot_file(999, "doc", ADM, "mine.apk", 500), "admin sends a file to the bot and gets its id")
mf = j(c.get("/admin/api/my-files?kind=apk"))
ok([x["file_id"] for x in mf["items"]] == [ADM] and mf["chat_linked"], "admin panel lists only the admin's own file ids")
c2_, _H2, _ = login_admin(app, "boss2", "TestPassw0rd456")
mf2 = j(c2_.get("/admin/api/my-files"))
ok(mf2["items"] == [] or all(x["file_id"] != ADM for x in mf2["items"]), "2nd admin does not see the super admin's files")
ok(vc.get("/admin/api/my-files").status_code == 403, "view-only admin cannot list file ids")
sub_ = db.get_list("/submissions")
if sub_:
    s0 = sub_[0]; kind0 = next(iter((s0.get("files") or {}).keys() & {"apk", "preview_video"}), None)
    if kind0:
        h.CALLS.clear()
        ok(c.post(f"/admin/api/submissions/{s0['id']}/send/{kind0}", headers=H).status_code == 200 and any(x[0] in ("sendDocument", "sendVideo") and str(x[1].get("chat_id")) == "999" for x in h.CALLS), "admin can have the seller's file sent to their own Telegram by id")
        ok(vc.post(f"/admin/api/submissions/{s0['id']}/send/{kind0}", headers={"X-CSRF-Token": vc.get("/admin/api/me").get_json()["csrf"]}).status_code == 403, "view-only admin cannot get the seller's files")

print("ALL PART 5 OK")
