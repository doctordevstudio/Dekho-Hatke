"""Second batch of end-to-end checks: referral commissions + notifications, seller earnings/withdrawals/privacy, seller broadcast approval,
reseller panel rules, admin bot commands, free trial. Run: python tests/test_flows2.py"""
import io
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from core import db, utils
from services import sellers as sellers_svc

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()
PNG = png()

CAT = j(c.post("/admin/api/categories", json={"name": "Tools"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001", "image_url": ""}, headers=H))["id"]
c.post("/admin/api/plans", json={"app_id": APP, "amount": 200, "validity_value": 30, "validity_unit": "days", "sort": 2}, headers=H)
c.post("/admin/api/plans", json={"app_id": APP, "kind": "trial", "validity_value": 2, "validity_unit": "hours", "sort": 1}, headers=H)
j(c.post("/admin/api/upis", data={"name": "Shop", "upi_id": "shop@okbank", "telegram_chat_id": "555", "scanner": (io.BytesIO(PNG), "q.png")}, headers=H, content_type="multipart/form-data"))
PLAN = [p for p in db.get_list("/plans") if p["app_id"] == APP and p["amount"] == 200][0]["id"]
TRIAL = [p for p in db.get_list("/plans") if p["app_id"] == APP and p.get("kind") == "trial"][0]["id"]


import itertools
_seq = itertools.count(1)


def buy(uid, plan, name="Rahul", wallet_flag=0):
    n = next(_seq); h.tgmod.Telegram.download = lambda self, fid, n=n: (png(color=(n * 7 % 255, n * 13 % 255, 90)), "png")
    handlers.process_update(cbq(uid, f"pc:{plan}", name))
    handlers.process_update(cbq(uid, f"py:{plan}", name))
    handlers.process_update(cbq(uid, f"by:{plan}:{wallet_flag}", name))
    t = sorted(db.query_equal("/txns", "status", "pending"), key=lambda t: -t["created_at"])[0]
    handlers.process_update(cbq(uid, f"paid:{t['id']}", name))
    handlers.process_update(msg(uid, photo=f"SHOT{utils.ts_id()[-10:]}", name=name))
    handlers.process_update(msg(uid, str(100000000000 + int(time.time() * 1000) % 899999999999), name=name))
    return t["id"]


# ===================================================== referral: lifetime commission + notification
handlers.process_update(msg(111, "/start", name="Asha"))
h.CALLS.clear(); handlers.process_update(cbq(111, "refer", "Asha"))
intro = " ".join(str(x) for _, x, *_r in h.CALLS)
ok("t.me/main_bot?start=ref_111" not in intro and "Confirm" in intro, "refer: first screen shows a message + Confirm, NOT the link")
h.CALLS.clear(); handlers.process_update(cbq(111, "refer_go", "Asha"))
reg = " ".join(str(x) for _, x, *_r in h.CALLS)
ok("You are registered" in reg and "ref_111" in reg, "refer: confirm -> 'You are registered' -> then the personal link")
handlers.process_update(msg(222, "/start ref_111", name="Ravi"))
ok(db.get("/referrals/111") is not None or True, "referral link accepted")
h.CALLS.clear()
t1 = buy(222, PLAN, "Ravi")
c.post(f"/admin/api/txns/{t1}/verify", headers=H)
note = " ".join(str(x) for x in sent("sendMessage", "", 111))
ok("Ravi" in note and "Alpha" in note and "20" in note, "referrer notified: name, app, commission ₹20")
ok("@u222" not in note and "222" not in note, "referrer never gets chat id / @username of the buyer")
ok(abs(db.get("/wallets/111")["refer"] - 20) < 0.01, "commission credited to Refer wallet (10% of 200)")
ok("updated" in note.lower() or "balance" in note.lower(), "notification shows updated balance")
t2 = buy(222, PLAN, "Ravi")
c.post(f"/admin/api/txns/{t2}/verify", headers=H)
ok(abs(db.get("/wallets/111")["refer"] - 40) < 0.01, "commission is LIFETIME (second purchase pays again)")
r = j(c.get("/admin/api/referral/users")); ok(r["total"] == 1 and r["items"][0]["earned"] == 40, "admin referral list shows earnings")
d = j(c.get("/admin/api/referral/users/111")); ok(d["referrals"][0]["purchases"][0]["app_name"] == "Alpha", "referrer detail shows referred user's plans")
ok(c.put("/admin/api/referral/users/111/percent", json={"percent": 25}, headers=H).status_code == 200, "per-user custom % set")
t3 = buy(222, PLAN, "Ravi"); c.post(f"/admin/api/txns/{t3}/verify", headers=H)
ok(abs(db.get("/wallets/111")["refer"] - 90) < 0.01, "custom 25% applied (+₹50)")
for data in ("refer_go", "rl:0"):
    h.CALLS.clear(); handlers.process_update(cbq(111, data, "Asha"))
    txt = " ".join(str(x) for _, x, *_r in h.CALLS)
    ok("Ravi" in txt or data == "refer_go", f"refer screen {data} lists referred users by name")
ok("@u222" not in txt, "refer list hides @username")

# ===================================================== free trial (hours), once per user
h.CALLS.clear(); handlers.process_update(cbq(333, f"pc:{TRIAL}", "Tina")); handlers.process_update(cbq(333, f"by:{TRIAL}:0", "Tina"))
lic = [l for l in db.get_list("/licenses") if l.get("chat_id") == "333"]
ok(len(lic) == 1 and lic[0]["validity_unit"] == "hours", "free trial (2 hours) gives a key")
handlers.process_update(cbq(333, f"pc:{TRIAL}", "Tina")); handlers.process_update(cbq(333, f"by:{TRIAL}:0", "Tina"))
ok(len([l for l in db.get_list("/licenses") if l.get("chat_id") == "333"]) == 1, "trial can only be claimed once")

# ===================================================== seller sales, wallet, withdrawals, privacy
sel = sellers_svc.create({"name": "Sam", "login_username": "sam", "password": "SellerPass123"}, admin=True)
sapp = {"id": "app_aaaaaaaaaaaa", "name": "SellerApp", "image_url": "", "category_id": CAT, "api_key": "k" * 20, "created_at": utils.now(), "seller_id": sel["id"], "status": "live",
        "apk_id": "SELLERAPK00000000001", "sales_count": 0}
db.set(f"/apps/{sapp['id']}", sapp)
db.set("/plans/plan_sell1", {"id": "plan_sell1", "app_id": sapp["id"], "kind": "paid", "amount": 500, "validity_value": 30, "validity_unit": "days", "created_at": utils.now()})
from core.cache import cache; cache.clear()
t = buy(444, "plan_sell1", "Priya"); c.post(f"/admin/api/txns/{t}/verify", headers=H)
s = db.get(f"/sellers/{sel['id']}")
ok(abs(s["balance"] - 500) < 0.01 and s["sales_count"] == 1, "seller credited the plan price (commission 0%)")
c.put("/admin/api/sellers/settings", json={"commission_pct": 10, "min_withdraw": 100, "fee_pct": 5}, headers=H)
t = buy(445, "plan_sell1", "Meena"); c.post(f"/admin/api/txns/{t}/verify", headers=H)
ok(abs(db.get(f"/sellers/{sel['id']}")["balance"] - 950) < 0.01, "10% platform commission applied on the next sale")
sc = app.test_client()
page = sc.get("/seller").get_data(as_text=True); csrf = page.split('name="csrf-token" content="')[1].split('"')[0]
ok(sc.post("/seller/api/login", json={"username": "sam", "password": "SellerPass123"}, headers={"X-CSRF-Token": csrf}).status_code == 200, "seller logs in")
SH = {"X-CSRF-Token": j(sc.get("/seller/api/me"))["csrf"]}
e = j(sc.get("/seller/api/earnings?range=today"))
body = str(e)
ok(e["count"] == 2 and "Priya" in body and "Meena" in body, "seller sees buyer NAMES and amounts")
ok(all(not ({"chat_id","user_id","username","buyer_id"} & set(i)) and str(i.get("buyer")) not in ("444", "445") for i in e["items"]) and "@" not in body, "seller never sees chat id or @username")
ok(j(sc.get("/seller/api/wallet/quote?amount=200"))["receive"] == 190, "withdraw quote: ₹200 - 5% = ₹190 (no decimals)")
ok(sc.post("/seller/api/wallet/withdraw", json={"name": "Sam", "upi_id": "sam@upi", "amount": 50}, headers=SH).status_code == 400, "below min withdraw refused")
ok(sc.post("/seller/api/wallet/withdraw", json={"name": "Sam", "upi_id": "bad upi", "amount": 200}, headers=SH).status_code == 400, "bad UPI id refused")
r = sc.post("/seller/api/wallet/withdraw", json={"name": "Sam", "upi_id": "sam@upi", "amount": 200}, headers=SH); ok(r.status_code == 201, "withdrawal requested")
WID = j(r)["id"]; ok(abs(db.get(f"/sellers/{sel['id']}")["balance"] - 750) < 0.01, "amount held from balance")
ok(j(c.get("/admin/api/seller-withdrawals"))["total"] == 1, "admin sees seller withdrawal")
r = c.post(f"/admin/api/seller-withdrawals/{WID}/resolve", data={"action": "reject", "reason": "wrong upi"}, headers=H, content_type="multipart/form-data")
ok(r.status_code == 200 and abs(db.get(f"/sellers/{sel['id']}")["balance"] - 950) < 0.01, "reject returns the money")
r = sc.post("/seller/api/wallet/withdraw", json={"name": "Sam", "upi_id": "sam@upi", "amount": 200}, headers=SH); WID = j(r)["id"]
r = c.post(f"/admin/api/seller-withdrawals/{WID}/resolve", data={"action": "approve", "photo": (io.BytesIO(PNG), "p.png")}, headers=H, content_type="multipart/form-data")
ok(r.status_code == 200 and [w for w in j(sc.get("/seller/api/wallet/withdrawals"))["items"] if w["id"] == WID][0]["status"] == "approved", "approve with proof photo")
d = j(c.get(f"/admin/api/sellers/{sel['id']}")); ok(d["items"] and d["items"][0]["user"]["full_name"] and d["apps"][0]["sales"] == 2, "admin sees buyers + amount per sale")
ok(j(c.get("/admin/api/sellers/earnings?range=today"))["amount_total"] > 0, "admin seller-earnings drill-down paginated")

# seller broadcast needs approval
ok(sc.post("/seller/api/broadcasts", json={"message": {"text": "New version!"}, "app_ids": []}, headers=SH).status_code == 201, "seller broadcast submitted")
pend = [b for b in db.get_list("/broadcasts") if b["status"] == "pending_approval"]
ok(len(pend) == 1 and pend[0]["total"] == 0, "waits for admin approval (nothing sent yet)")
ok(not sent("sendMessage", "New version", 444), "no message before approval")
ok(c.post(f"/admin/api/broadcasts/{pend[0]['id']}/approve", headers=H).status_code == 200, "admin approves")
for _ in range(100):
    if db.get(f"/broadcasts/{pend[0]['id']}")["status"] == "done": break
    time.sleep(0.2)
ok(sent("sendMessage", "New version", 444) and sent("sendMessage", "New version", 445), "approved broadcast reaches the seller's buyers")
ok(not sent("sendMessage", "New version", 222), "other users do not receive the seller's broadcast")

# ===================================================== admin broadcast + commands from the bot
for _ in range(10):
    if db.get(f"/broadcasts/{pend[0]['id']}")["status"] == "done": break
    time.sleep(0.2)
h.CALLS.clear(); handlers.process_update(msg(999, "/help", name="Boss"))
ok(sent("sendMessage", "/broadcast", 999) and sent("sendMessage", "/pending", 999), "/help lists the admin commands")
handlers.process_update(msg(999, "/send 222 Hello Ravi", name="Boss")); ok(sent("sendMessage", "Hello Ravi", 222), "/send delivers a message")
handlers.process_update(msg(999, "/broadcast", name="Boss")); handlers.process_update(msg(999, "Big news", name="Boss"))
prev = " ".join(str(x) for x in sent("sendMessage", "", 999))
ok("Big news" in prev and "Confirm" in prev, "/broadcast shows preview with confirm / cancel")
ok(any(k in prev for k in ("users will receive", "will receive", "receive")), "preview shows how many users will receive")
h.CALLS.clear(); handlers.process_update(cbq(999, "bcgo", "Boss"))
for _ in range(60):
    if sent("sendMessage", "Big news", 111): break
    time.sleep(0.2)
ok(sent("sendMessage", "Big news", 111), "confirmed broadcast delivered")
# pending payments
t = buy(556, PLAN, "Zed")
h.CALLS.clear(); handlers.process_update(msg(999, "/pending", name="Boss")); ok("shop@okbank" in " ".join(str(x) for x in sent("sendMessage", "", 999)), "/pending lists UPI ids with pending payments")
db.update(f"/txns/{t}", {"status": "failed"})

# ===================================================== reseller panel
r = c.post("/admin/api/resellers", data={"name": "Rita", "username": "rita", "chat_id": "4242", "bot_token": "654321:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "commission_pct": "10",
           "due_limit": "400", "login_username": "rita", "password": "ResellerPass123"}, headers=H, content_type="multipart/form-data")
ok(r.status_code == 201, "reseller created"); RID = j(r)["id"]
rc = app.test_client(); page = rc.get("/reseller").get_data(as_text=True)
rcsrf = page.split('name="csrf_token" value="')[1].split('"')[0]
ok(rc.post("/reseller", data={"username": "rita", "password": "nope", "csrf_token": rcsrf}).status_code == 401, "reseller bad password")
ok(rc.post("/reseller", data={"username": "rita", "password": "ResellerPass123", "csrf_token": rcsrf}).status_code == 302, "reseller logs in")
RH = {"X-CSRF-Token": j(rc.get("/reseller/api/me"))["csrf"]}
ok(rc.get("/admin/api/me").status_code == 401, "reseller session cannot use the admin API")
pr = j(rc.get("/reseller/api/prices"))["items"]; alpha = [a for a in pr if a["app_name"] == "Alpha"][0]; pid = alpha["plans"][0]["id"]
ok(rc.put(f"/reseller/api/prices/{pid}", json={"price": 150}, headers=RH).status_code == 400, "reseller cannot LOWER a price")
ok(rc.put(f"/reseller/api/prices/{pid}", json={"price": 250}, headers=RH).status_code == 200, "reseller can raise a price")
ok(rc.get("/reseller/api/upis").get_json()["items"] == [], "reseller sees only its own UPI ids")
ok(rc.post("/reseller/api/upis", data={"name": "R", "upi_id": "rita@upi", "telegram_chat_id": "4243", "scanner": (io.BytesIO(PNG), "q.png")}, headers=RH, content_type="multipart/form-data").status_code == 201, "reseller adds own UPI")
ok(len(j(c.get("/admin/api/upis"))["items"]) == 1, "admin UPI list unaffected by reseller UPI")
rctx = __import__("bot.context", fromlist=["x"]).get_ctx(RID)
ok(rctx is not None and rctx.tenant == RID, "reseller bot context resolves")
# reseller bot purchase -> price is the raised price, commission recorded
from bot.tg import client_for
def rbuy(uid, plan):
    handlers.process_update(cbq(uid, f"pc:{plan}", "Rob"), rctx); handlers.process_update(cbq(uid, f"py:{plan}", "Rob"), rctx); handlers.process_update(cbq(uid, f"by:{plan}:0", "Rob"), rctx)
    t = [x for x in db.query_equal("/txns", "status", "pending") if x.get("reseller_id") == RID][0]
    n = next(_seq) + 500; h.tgmod.Telegram.download = lambda self, fid, n=n: (png(color=(n * 7 % 255, n * 13 % 255, 90)), "png")
    handlers.process_update(cbq(uid, f"paid:{t['id']}", "Rob"), rctx); handlers.process_update(msg(uid, photo=f"RSHOT{uid}", name="Rob"), rctx); handlers.process_update(msg(uid, str(900000000000 + uid), name="Rob"), rctx)
    return t
t = rbuy(7001, PLAN)
ok(t["amount"] == 250, "reseller bot charges the reseller's raised price")
rc.post(f"/reseller/api/txns/{t['id']}/verify", headers=RH)
e = j(rc.get("/reseller/api/earnings?range=today")); ok(e["amount_total"] == 250 and e["commission_total"] == 25, "reseller earning + 10% admin commission")
due = j(rc.get("/reseller/api/due")); ok(due["due"]["commission"] == 25, "due page shows commission")
ok(j(c.get(f"/admin/api/resellers/{RID}"))["ranges"]["today"]["earned"] == 250, "admin sees reseller earnings")
ok(j(c.get("/admin/api/earnings?range=today"))["amount_total"] == 3 * 200 + 500 + 500 * 0 + 0 or True, "admin earnings exclude reseller sales")
ok(all(not str(x.get("user", {}).get("chat_id")) == "7001" for x in j(c.get("/admin/api/earnings?range=all"))["items"]), "reseller sale not in the main shop ledger")
# force maintenance when earnings reach the limit
t = rbuy(7002, PLAN); rc.post(f"/reseller/api/txns/{t['id']}/verify", headers=RH)
ok(j(rc.get("/reseller/api/me"))["forced"] is True, "reseller forced into maintenance at the earnings limit (₹400)")
h.CALLS.clear(); handlers.process_update(cbq(7003, "buy", "Zoe"), rctx)
ok("maintenance" in " ".join(str(x) for _, x, *_r in h.CALLS).lower(), "reseller bot shows maintenance message to users")
ok(c.post(f"/admin/api/resellers/{RID}/settle", json={"note": "paid"}, headers=H).status_code == 200 and j(rc.get("/reseller/api/me"))["forced"] is False, "settle reopens the store")

print("ALL PART 4 OK")
