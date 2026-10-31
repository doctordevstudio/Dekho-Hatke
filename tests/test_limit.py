"""Pay-as-you-go (limit) apps: plans with a request count, the consume / usage API, delivery.
Run: python tests/test_limit.py"""
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from bot.context import MAIN
from core import db, utils
from services import catalog, payments

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()
api = app.test_client()

CAT = j(c.post("/admin/api/categories", json={"name": "APIs"}, headers=H))["id"]
ok(c.post("/admin/api/apps", json={"name": "X", "plan_type": "weekly"}, headers=H).status_code == 400, "unknown plan type refused")
LAPP = j(c.post("/admin/api/apps", json={"name": "Photo API", "category_id": CAT, "plan_type": "limit", "apk_id": "APKFILEID0000000009",
                                         "delivery_mode": "message", "delivery_message": "Docs: <b>example.com/docs</b>"}, headers=H))["id"]
TAPP = j(c.post("/admin/api/apps", json={"name": "Time App", "category_id": CAT}, headers=H))["id"]
lapp = db.get(f"/apps/{LAPP}")
pub = j(c.get(f"/admin/api/apps/{LAPP}"))
ok(pub["plan_type"] == "limit" and pub["consume_endpoint"].endswith(f"/api/v1/{LAPP}/consume") and pub["usage_endpoint"].endswith("/usage"), "app shows its plan type + the consume / usage endpoints")

ok(c.post("/admin/api/plans", json={"app_id": LAPP, "amount": 100, "sort": 1}, headers=H).status_code == 400, "a limit plan needs a request count")
ok(c.post("/admin/api/plans", json={"app_id": LAPP, "amount": 100, "requests": 0, "sort": 1}, headers=H).status_code == 400, "0 requests refused")
P_EXP = j(c.post("/admin/api/plans", json={"app_id": LAPP, "amount": 100, "requests": 1000, "expiry_value": 30, "expiry_unit": "days", "sort": 1}, headers=H))["id"]
P_FOREVER = j(c.post("/admin/api/plans", json={"app_id": LAPP, "amount": 400, "requests": 5000, "sort": 2}, headers=H))["id"]
P_SMALL = j(c.post("/admin/api/plans", json={"app_id": LAPP, "amount": 50, "requests": 5, "sort": 3}, headers=H))["id"]
ok(c.post("/admin/api/plans", json={"app_id": LAPP, "amount": 5, "requests": 10, "sort": 1, "shorturl_id": "x"}, headers=H).status_code in (400, 404), "limit plans cannot be short-link plans")
plans = {p["id"]: p for p in j(c.get("/admin/api/plans"))["items"]}
ok(plans[P_EXP]["requests"] == 1000 and plans[P_EXP]["label"] == "1,000 requests · valid 30 Days" and plans[P_FOREVER]["label"] == "5,000 requests" and plans[P_FOREVER]["no_expiry"], "plan labels")
ok(c.patch(f"/admin/api/plans/{P_EXP}", json={"amount": 120, "requests": 1200}, headers=H).status_code == 200
   and db.get(f"/plans/{P_EXP}")["requests"] == 1200 and db.get(f"/plans/{P_EXP}")["validity_value"] == 30, "editing a plan keeps its expiry unless changed")
c.patch(f"/admin/api/plans/{P_EXP}", json={"amount": 100, "requests": 1000}, headers=H)
ok(c.patch(f"/admin/api/apps/{LAPP}", json={"plan_type": "time"}, headers=H).status_code == 400, "plan type is locked once plans exist")

# ---- bot: plan buttons + purchase + delivery
UID = 701
handlers.process_update(msg(UID, "/start", name="Lena"))
h.CALLS.clear(); handlers.process_update(cbq(UID, f"ap:{LAPP}", "Lena"))
shown = str(h.CALLS)
ok("1,000 requests · valid 30 Days" in shown and "5,000 requests" in shown, "the bot lists plans as request packs")
ok("Best value" not in shown, "no per-day 'best value' for request packs")

ctx = MAIN
user = {"chat_id": UID, "full_name": "Lena", "username": "u701"}
db.set("/upis/u1", {"id": "u1", "upi_id": "shop@upi", "name": "Shop", "active": True, "created_at": 1, "telegram_chat_id": "999"})


def buy(plan):
    st, t = payments.create_license_txn(ctx, user, plan)
    ok(st == "ok", f"order created for {plan}")
    db.update(f"/txns/{t['id']}", {"status": "verification"})
    h.CALLS.clear()
    lic = payments.verify_txn(t["id"], "boss")
    return lic

lic = buy(P_EXP)
KEY = lic["key"]
ok(lic["limit_total"] == 1000 and lic["limit_used"] == 0 and 29 * 86400 < lic["expires_at"] - utils.now() <= 30 * 86400 + 5, "key has 1,000 requests and a 30-day expiry")
ok(h.sent("sendMessage", "1,000", chat=UID) and h.sent("sendMessage", KEY, chat=UID), "the buyer is shown the license key and the request count")
ok(h.sent("sendMessage", "example.com/docs", chat=UID) and not h.sent("sendDocument", chat=UID), "limit plans use the app's APK / message delivery (message chosen: no APK)")
lic2 = buy(P_FOREVER)
ok(lic2["expires_at"] > utils.now() + 50 * 365 * 86400, "no expiry -> the key does not expire")

# ---- the debit API
AKEY = db.get(f"/apps/{LAPP}")["api_key"]
HD = {"X-API-Key": AKEY}
url = lambda p: f"/api/v1/{LAPP}/{p}"
ok(api.post(url("consume"), json={"license_key": KEY}).status_code == 401, "consume needs the app's API key")
ok(api.post(url("consume"), json={"license_key": "ABC"}, headers=HD).status_code == 400, "bad key format")
ok(api.post(url("consume"), json={"license_key": "A" * 18}, headers=HD).status_code == 404, "unknown key")
r = api.post(url("consume"), json={"license_key": KEY}, headers=HD)
d = j(r)
ok(r.status_code == 200 and d["success"] and d["usage"] == {"charged": 1, "total": 1000, "used": 1, "remaining": 999}, "1 request debited, remaining shown")
ok(r.headers["X-Limit-Remaining"] == "999" and r.headers["X-Limit-Total"] == "1000", "remaining limit also in response headers")
ok(d["license"]["status"] == "active" and d["license"]["no_expiry"] is False, "response carries the license state")
d = j(api.post(url("consume"), json={"license_key": KEY, "units": 9, "request_id": "job-1"}, headers=HD))
ok(d["usage"]["used"] == 10 and d["usage"]["remaining"] == 990, "units debit several at once")
r = api.post(url("consume"), json={"license_key": KEY, "units": 9, "request_id": "job-1"}, headers=HD)
ok(j(r)["duplicate"] is True and j(r)["usage"]["used"] == 10 and j(r)["usage"]["charged"] == 0, "a retry with the same request_id is not charged twice")
d = j(api.get(url("usage") + f"?license_key={KEY}", headers=HD))
ok(d["usage"]["remaining"] == 990 and d["usage"]["charged"] == 0, "usage shows the balance without charging")
ok(j(api.post(url("usage"), json={"license_key": KEY}, headers=HD))["usage"]["used"] == 10, "usage also works with POST")
r = api.post(url("consume"), json={"license_key": KEY, "units": 0}, headers=HD)
ok(r.status_code == 400 and j(r)["code"] == "INVALID_UNITS", "units must be >= 1")
v = j(api.post(url("verify"), json={"license_key": KEY, "device_id": "device-0001-abc"}, headers=HD))
ok(v["success"] and v["license"]["limit"]["remaining"] == 990, "verify also reports the remaining limit")

# exhaust with the 5-request pack
lic3 = buy(P_SMALL)
K3 = lic3["key"]
for i in range(5):
    r = api.post(url("consume"), json={"license_key": K3}, headers=HD)
ok(j(r)["usage"]["remaining"] == 0, "5 of 5 used")
time.sleep(0.4)
ok(h.sent("sendMessage", "request limit is finished", chat=UID), "the buyer is told in Telegram when the limit is finished")
r = api.post(url("consume"), json={"license_key": K3}, headers=HD)
ok(r.status_code == 402 and j(r)["code"] == "LIMIT_EXHAUSTED" and j(r)["usage"]["remaining"] == 0 and r.headers["X-Limit-Remaining"] == "0", "when finished: 402 LIMIT_EXHAUSTED with the balance")
r = api.post(url("verify"), json={"license_key": K3, "device_id": "device-0002-abc"}, headers=HD)
ok(r.status_code == 402, "verify refuses a finished key")
lic4 = buy(P_SMALL)
K4 = lic4["key"]
r = api.post(url("consume"), json={"license_key": K4, "units": 6}, headers=HD)
ok(r.status_code == 402 and j(r)["usage"]["used"] == 0, "all-or-nothing: asking for more than is left charges nothing")

# expiry + revoke + wrong app type
db.update(f"/licenses/{KEY}", {"expires_at": utils.now() - 5})
r = api.post(url("consume"), json={"license_key": KEY}, headers=HD)
ok(r.status_code == 403 and j(r)["code"] == "LICENSE_EXPIRED", "an expired limit key cannot be used")
c.post(f"/admin/api/licenses/{lic2['key']}/revoke", json={"reason": "abuse"}, headers=H)
ok(api.post(url("consume"), json={"license_key": lic2["key"]}, headers=HD).status_code == 403, "a revoked key cannot be used")
TLIC = j(c.post(f"/admin/api/apps/{TAPP}/licenses", json={"chat_id": "555123", "full_name": "T", "amount": 0, "validity_value": 3, "validity_unit": "days"}, headers=H))
tk = TLIC["key"]
r = c.post(f"/admin/api/apps/{LAPP}/licenses", json={"chat_id": "555123", "full_name": "Manual", "amount": 0}, headers=H)
ok(r.status_code == 400, "a manual key for a limit app needs a request count")
MK = j(c.post(f"/admin/api/apps/{LAPP}/licenses", json={"chat_id": "555123", "full_name": "Manual", "amount": 0, "requests": 3}, headers=H))
ok(MK["limit"] == {"total": 3, "used": 0, "remaining": 3} and MK["validity_label"] == "3 requests", "admin can create a manual pay-as-you-go key")
for _ in range(3):
    api.post(url("consume"), json={"license_key": MK["key"]}, headers=HD)
ok(api.post(url("consume"), json={"license_key": MK["key"]}, headers=HD).status_code == 402, "manual key runs out")
r = c.post(f"/admin/api/licenses/{MK['key']}/topup", json={"requests": 10}, headers=H)
ok(r.status_code == 200 and j(r)["limit"]["remaining"] == 10, "admin tops up a finished key")
ok(api.post(url("consume"), json={"license_key": MK["key"]}, headers=HD).status_code == 200, "...and it works again")
TKEY = db.get(f"/apps/{TAPP}")["api_key"]
r = api.post(f"/api/v1/{TAPP}/consume", json={"license_key": tk}, headers={"X-API-Key": TKEY})
ok(r.status_code == 400 and j(r)["code"] == "NOT_A_LIMIT_LICENSE", "a time-based key cannot be debited")

# admin sees the remaining limit on the key
L = [x for x in j(c.get(f"/admin/api/apps/{LAPP}/licenses"))["items"] if x["key"] == K4][0]
ok(L["limit"] == {"total": 5, "used": 0, "remaining": 5}, "the admin's license list shows the limit balance")
h.CALLS.clear(); handlers.process_update(cbq(UID, "keys", "Lena"))
ok("Left:" in str(h.CALLS) and "Limit finished" in str(h.CALLS), "My Keys shows the remaining requests (and 'limit finished')")
print("ALL LIMIT TESTS OK")
