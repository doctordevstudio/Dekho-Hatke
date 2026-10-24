"""End-to-end flows of the new platform. Run: python tests/test_flows.py"""
from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from core import db

app = create_app(background=False)
c, H, me = login_admin(app)
ok(me["role"] == "admin", "super admin login")
c2, H2, me2 = login_admin(app, "boss2", "TestPassw0rd456")
ok(me2["role"] == "admin2", "2nd admin (from env) can log in")
ok(c2.delete("/admin/api/admins/x", headers=H2).status_code == 403, "2nd admin cannot delete")

_orig = handlers.process_update
def proc(u, ctx=None):
    spam._s.clear()
    handlers.reset_gates()
    return _orig(u, ctx) if ctx else _orig(u)
handlers.process_update = proc

j = lambda r: r.get_json()
PNG = png()

# ---- catalog: category, app, plans, upi
r = c.post("/admin/api/categories", json={"name": "Tools", "icon": "🧰"}, headers=H); ok(r.status_code == 201, "category created")
CAT = j(r)["id"]
r = c.post("/admin/api/apps", json={"name": "Super Tool", "image_url": "https://example.com/i.png", "category_id": CAT,
                                     "apk_id": "APKFILEID0000000001", "video_id": "VIDEOID000000000001",
                                     "setup_video_id": "SETUPVID00000000001"}, headers=H)
ok(r.status_code == 201, "app created"); APP = j(r)["id"]
ok(c.post("/admin/api/plans", json={"app_id": APP, "amount": 1, "validity_value": 1, "validity_unit": "days"}, headers=H).status_code == 400, "plan without a sort number is refused")
for srt, (amt, v, u) in enumerate(((199, 30, "days"), (49, 12, "hours"), (999, 1, "years")), start=2):
    r = c.post("/admin/api/plans", json={"app_id": APP, "amount": amt, "validity_value": v, "validity_unit": u, "sort": srt}, headers=H)
    ok(r.status_code in (200, 201), f"plan {v} {u} created")
r = c.post("/admin/api/plans", json={"app_id": APP, "kind": "trial", "validity_value": 2, "validity_unit": "hours", "sort": 1}, headers=H)
ok(r.status_code in (200, 201), "free trial plan (hours)")
r = c.post("/admin/api/upis", data={"name": "Shop A", "upi_id": "shop.a@okbank", "telegram_chat_id": "555",
                                    "scanner": (io.BytesIO(PNG), "qr.png")}, headers=H, content_type="multipart/form-data")
ok(r.status_code == 201, "UPI added"); UPI1 = j(r)["id"]
ok(j(c.get("/admin/api/plans"))["items"][0]["label"] != "", "plans listed")

# ---- user buys: generated QR, not uploaded
U = 111
h.CALLS.clear()
handlers.process_update(msg(U, "/start"))
logo = [x for x in h.CALLS if x[0] == "sendPhoto" and str(x[1].get("chat_id")) == str(U)]
ok(len(logo) == 1 and logo[0][3] is not None, "welcome shows the Dekho Hatke logo (uploaded once)")
h.CALLS.clear()
handlers.process_update(msg(U, "/start"))
again = [x for x in h.CALLS if x[0] == "sendPhoto" and str(x[1].get("chat_id")) == str(U)]
ok(len(again) == 1 and again[0][3] is None and again[0][1].get("photo"), "next /start re-uses the cached Telegram file id")
h.CALLS.clear()
plan = [p for p in db.get_list("/plans") if p["amount"] == 199][0]
handlers.process_update(cbq(U, f"pc:{plan['id']}"))
handlers.process_update(cbq(U, f"pw:{plan['id']}:0"))
handlers.process_update(cbq(U, f"by:{plan['id']}:0"))
pend = db.query_equal("/txns", "status", "pending")
ok(len(pend) == 1, "txn created")
photos = [x for x in h.CALLS if x[0] == "sendPhoto" and str(x[1].get("chat_id")) == str(U)]
ok(len(photos) == 1 and photos[0][3] is not None, "user got a GENERATED QR (uploaded bytes, not the stored scanner id)")
import qrcode
data = getattr(qrcode.QRCode, "last_data", None)
if data:
    ok(data.startswith("upi://pay?pa=shop.a@okbank&pn=Shop%20A&am=199.00&cu=INR"), "QR carries upi id, name and amount")

TXN = pend[0]["id"]
handlers.process_update(cbq(U, f"paid:{TXN}"))
# a renamed text file posing as an image must be refused
fake = msg(U, photo="FAKEFILEID000000001")
h.tgmod.Telegram.download = lambda self, fid: (b"MZ\x90\x00" + b"A" * 3000, "jpg")
h.CALLS.clear()
handlers.process_update(fake)
ok(db.get(f"/txns/{TXN}")["status"] == "pending" and not db.get(f"/txns/{TXN}").get("screenshot_file_id"), "fake image (exe renamed) rejected")
ok(bool(sent("sendMessage", "not a valid screenshot", U)), "user told to send a real image")
h.tgmod.Telegram.download = lambda self, fid: (PNG, "png")
handlers.process_update(msg(U, photo="REALSHOT0000000001"))
handlers.process_update(msg(U, "123456789012"))
t = db.get(f"/txns/{TXN}")
ok(t["status"] == "verification", "txn moves to in verification after screenshot + UTR")
owner_msgs = sent("sendPhoto", "", 555) + sent("sendMessage", "", 555)
ok(owner_msgs, "UPI owner chat got the request")
ok(not any("@u111" in str(x) for x in owner_msgs), "UPI owner never sees @username")
ok(any("Rahul" in str(x) and "111" in str(x) for x in owner_msgs), "UPI owner sees name + chat id")
adm_msgs = sent("sendPhoto", "", 999) + sent("sendMessage", "", 999)
ok(any("@u111" in str(x) for x in adm_msgs), "super admin sees @username")
ok(any(str(x.get("chat_id")) == "998" for _, x, *_r in h.CALLS), "2nd admin notified")

# panel: pending lists, search by UTR, verify
r = c.get("/admin/api/txns?status=verification"); ok(j(r)["total"] == 1, "txn listed under In verification")
ok(j(c.get("/admin/api/txns?q=123456789012"))["total"] == 1, "search by UTR")
h.CALLS.clear()
r = c.post(f"/admin/api/txns/{TXN}/verify", headers=H); ok(r.status_code == 200, "admin verifies")
ok(db.get(f"/txns/{TXN}")["status"] == "success", "txn success")
ok(sent("sendDocument", "", U), "APK delivered to user")
ok(sent("sendVideo", "", U) or sent("sendVideo", "SETUPVID", U), "setup video delivered to user")
lic = [l for l in db.get_list("/licenses") if l.get("chat_id") == str(U)]
ok(len(lic) == 1, "license created for buyer")
e = j(c.get("/admin/api/earnings?range=today"))
ok(e["amount_total"] == 199 and e["sale_count"] == 1 and e["total"] == 1, "earning recorded + paginated")
ok(j(c.get("/admin/api/earnings?range=yesterday"))["amount_total"] == 0, "yesterday empty")
ok(j(c.get("/admin/api/stats/active?range=all")) ["count"] == 0, "active drill-down works")

# UPI owner collection screen (chat 555)
h.CALLS.clear()
handlers.process_update(msg(555, "/start"))
handlers.process_update(cbq(555, "collect"))
col = " ".join(str(x) for x in sent("sendMessage", "", 555) + sent("editMessageText", "", 555))
ok("199" in col and "All time" in col, "UPI owner 'My Payment Collection' shows totals")
handlers.process_update(cbq(555, "col:success:0"))
col = " ".join(str(x) for x in sent("editMessageText", "", 555) + sent("sendMessage", "", 555))
ok("Rahul" in col and "@u111" not in col, "collection list: name, amount, chat id, no @username")

# deposits / wallet screens render
for data in ("wallet", "hist:dep:0", "hist:wdr:0", "refer", "refer_go", "rl:0", "keys"):
    h.CALLS.clear(); handlers.process_update(cbq(U, data))
    ok(any(m in ("sendMessage", "editMessageText", "sendPhoto", "editMessageCaption") for m, *_ in h.CALLS), f"bot screen '{data}' renders")

# =============================================================== SELLER: signup, create API, publish, review checks
sc = app.test_client()
page = sc.get("/seller").get_data(as_text=True)
ok("csrf" in page.lower(), "seller login page served")
scsrf = page.split('name="csrf-token" content="')[1].split('"')[0] if 'name="csrf-token"' in page else None
ok(scsrf is not None, "seller page exposes csrf meta")
SH = {"X-CSRF-Token": scsrf}
r = sc.post("/seller/api/signup", json={"name": "Sam Seller", "username": "sam", "password": "SellerPass123"}, headers=SH)
ok(r.status_code in (404, 405), "web sign-up is gone (accounts come from the bot)")
from services import sellers as _sl
_sl.create({"name": "Sam Seller", "login_username": "sam", "password": "SellerPass123"}, admin=True)
ok(sc.post("/seller/api/login", json={"username": "sam", "password": "bad"}, headers=SH).status_code == 401, "seller bad password")
r = sc.post("/seller/api/login", json={"username": "sam", "password": "SellerPass123"}, headers=SH); ok(r.status_code == 200, "seller login")
me = j(sc.get("/seller/api/me")); SH = {"X-CSRF-Token": me["csrf"]}
ok("chat_id" not in me and "username" not in me, "seller me has no chat id/username")
r = sc.get("/seller/api/publish-check"); ok(j(r)["has_api"] is False, "publish without API app says create first")
r = sc.post("/seller/api/apps", data={"name": "Seller App", "new_category": "Games", "description": "A fun game for everyone", "package_name": "com.sam.sellerapp", "logo": (io.BytesIO(PNG), "logo.png")}, headers=SH, content_type="multipart/form-data")
ok(r.status_code == 201, "seller creates API app"); SAPP = j(r); ok(SAPP["endpoint"] and SAPP["api_key"], "endpoint + api key generated")
ok(SAPP["status"] == "draft", "new API app is a draft")
# connect telegram (as the bot would)
tok = j(sc.post("/seller/api/telegram/link", headers=SH))["url"].split("sl_")[1]
handlers.process_update(msg(777, f"/start sl_{tok}", name="Sam"))
ok(db.get([s for s in db.get_list("/sellers")][0]["id"] and f"/sellers/{[s for s in db.get_list('/sellers')][0]['id']}")["chat_id"] == "777", "telegram linked to seller")
# files go through the bot: the seller sends the APK / video, the bot replies with an id, the seller pastes it
APKID, VIDID = "BQACAgUAAxkBAAISellerApkId000000001", "BAACAgUAAxkBAAISellerVidId000000001"
ok(APKID not in h.bot_file(777, "doc", APKID, "notanapk.zip"), "bot refuses to hand out an id for a non-APK file from a seller")
reply = h.bot_file(777, "doc", APKID, "game.apk", 5000)
ok(APKID in reply and "Only you" in reply, "seller sends an .apk to the bot and gets its id back")
ok(VIDID in h.bot_file(777, "video", VIDID, size=3000), "seller sends a video to the bot and gets its id back")
chk = lambda kind, fid: sc.post("/seller/api/files/check", json={"kind": kind, "file_id": fid}, headers=SH)
ok(chk("apk", "BQACAgUAAxkBAAISomeoneElses000000001").status_code == 400, "an id the seller did not send is refused")
ok(chk("apk", VIDID).status_code == 400, "a video id cannot be used as the APK")
ok(chk("preview_video", APKID).status_code == 400, "an APK id cannot be used as the video")
apk = chk("apk", APKID); ok(apk.status_code == 200 and j(apk)["filename"] == "game.apk", "apk id accepted")
vid = chk("preview_video", VIDID); ok(vid.status_code == 200, "preview video id accepted")
mine = j(sc.get("/seller/api/files?kind=apk"))["items"]
ok([x["file_id"] for x in mine] == [APKID], "seller sees only their own APK ids")
STR = "BQACAgUAAxkBAAIStranger0000000000001"
ok(STR not in h.bot_file(555, "doc", STR, "x.apk", 10) and not db.query_equal("/tg_files", "owner", "555"), "an ordinary user's file is neither answered nor recorded")
logo = sc.post("/seller/api/uploads/logo", data={"file": (io.BytesIO(PNG), "l.png")}, headers=SH, content_type="multipart/form-data"); ok(logo.status_code == 200, "logo uploaded")
payload = {"name": "Seller App", "category_id": SAPP["category_id"], "description": "A fun game for everyone", "features": "Fast\nSmooth",
           "plans": [{"amount": 100, "validity_value": 7, "validity_unit": "days"}, {"amount": 900, "validity_value": 1, "validity_unit": "years", "is_best": True}],
           "apk_upload": j(apk)["id"], "preview_video_upload": j(vid)["id"], "logo_media": j(logo)["media"]}
# seller must pass the live APK tests (stored as a draft) before submitting
r = sc.post(f"/seller/api/apps/{SAPP['id']}/publish", json=payload, headers=SH)
ok(r.status_code == 400 and "live APK tests" in j(r)["error"], "submit blocked until live tests pass")
DR = f"/seller/api/apps/{SAPP['id']}/draft"
ok(sc.put(DR, json={"step": 2, "data": {"name": "Seller App"}}, headers=SH).status_code == 200, "draft saved")
skey_ = j(sc.post(f"{DR}/test-key", json={"hours": 2}, headers=SH))["key"]
SAPI = f"/api/v1/{SAPP['id']}/verify"; SAH = {"X-API-Key": SAPP["api_key"]}
spub = lambda dev: app.test_client().post(SAPI, json={"license_key": skey_, "device_id": dev}, headers=SAH)
sst = lambda: {x["key"]: x["passed"] for x in j(sc.get(f"{DR}/tests"))["checks"]}
ok(spub("sdev-AAAA-1111").status_code == 200 and sst()["login"], "seller test: login passes")
ok(spub("sdev-BBBB-2222").get_json()["code"] == "DEVICE_MISMATCH" and sst()["device"], "seller test: device bind passes")
ok(sc.post(f"{DR}/maintenance", json={"enabled": True}, headers=SH).status_code == 200, "seller test: maintenance on")
ok(spub("sdev-AAAA-1111").status_code != 200 and sst()["maintenance"], "seller test: maintenance passes")
sc.post(f"{DR}/maintenance", json={"enabled": False}, headers=SH)
ok(sc.post(f"{DR}/test-keys/{skey_}/revoke", headers=SH).status_code == 200, "seller test: key revoked")
ok(spub("sdev-AAAA-1111").get_json()["code"] == "LICENSE_REVOKED" and sst()["revoke"], "seller test: revoke passes")
r = sc.post(f"/seller/api/apps/{SAPP['id']}/publish", json=payload, headers=SH); ok(r.status_code == 201, "app submitted for review")
ok(j(sc.get("/seller/api/apps"))["items"][0]["status"] == "in_verification", "seller sees In verification")
ok(sc.post(f"/seller/api/apps/{SAPP['id']}/publish", json=payload, headers=SH).status_code == 400, "no second submission while under review")

# admin review
subs = j(c.get("/admin/api/submissions?status=pending"))["items"]; ok(len(subs) == 1, "submission visible to admin")
SUB = subs[0]["id"]
d = j(c.get(f"/admin/api/submissions/{SUB}"))
ok(d["ready"] is False and len(d["checks"]) == 5, "checks start unpassed")
ok(c.post(f"/admin/api/submissions/{SUB}/approve", json={}, headers=H).status_code == 400, "cannot approve before checks pass")
ok(c2.post(f"/admin/api/submissions/{SUB}/approve", json={"force": True}, headers=H2).status_code == 403, "2nd admin cannot force-approve")
ok(d["files"]["apk"]["tg_file_id"], "apk file id captured from Telegram")
key = j(c.post(f"/admin/api/submissions/{SUB}/test-key", json={"hours": 24}, headers=H))["key"]
API = f"/api/v1/{SAPP['id']}/verify"; AH = {"X-API-Key": SAPP["api_key"]}
pub = lambda dev, k=key: app.test_client().post(API, json={"license_key": k, "device_id": dev}, headers=AH)
ok(pub("device-AAAA-1111").status_code == 200, "test key logs in")
st = lambda: {x["key"]: x["passed"] for x in j(c.get(f"/admin/api/submissions/{SUB}"))["checks"]}
ok(st()["login"] and not st()["device"], "login check passes")
ok(pub("device-BBBB-2222").get_json()["code"] == "DEVICE_MISMATCH" and st()["device"], "device uniqueness check passes")
ok(c.post(f"/admin/api/submissions/{SUB}/maintenance", json={"enabled": True}, headers=H).status_code == 200, "review maintenance on")
r = pub("device-AAAA-1111"); ok(r.status_code != 200 and st()["maintenance"], "maintenance check passes")
c.post(f"/admin/api/submissions/{SUB}/maintenance", json={"enabled": False}, headers=H)
ok(c.post(f"/admin/api/submissions/{SUB}/test-keys/{key}/revoke", headers=H).status_code == 200, "test key revoked")
ok(pub("device-AAAA-1111").get_json()["code"] == "LICENSE_REVOKED" and st()["revoke"], "revoke check passes")
ok(j(c.get(f"/admin/api/submissions/{SUB}"))["ready"] is True, "ready once all 4 required checks passed")
ok(c.post(f"/admin/api/submissions/{SUB}/approve", json={}, headers=H).status_code == 200, "approve after live checks")
a = db.get(f"/apps/{SAPP['id']}"); ok(a["status"] == "live" and a["apk_id"], "app is live with apk id")
ok(len([p for p in db.get_list("/plans") if p["app_id"] == SAPP["id"]]) == 2, "plans synced")
ok(sent("sendMessage", "approved", 777), "seller notified on Telegram")
print("ALL PART 3 OK")
