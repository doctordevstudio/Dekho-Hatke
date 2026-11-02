"""Bot screens: admin edits the main bot, a reseller edits their own bot (message + picture of every button's screen).
Run: python tests/test_screens.py"""
import io

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from bot.context import get_ctx
from config import Config
from core import db

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()
PNG = png()

CAT = j(c.post("/admin/api/categories", json={"name": "Tools"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001"}, headers=H))["id"]
c.post("/admin/api/plans", json={"app_id": APP, "amount": 200, "validity_value": 30, "validity_unit": "days", "sort": 2}, headers=H)

r = c.post("/admin/api/resellers", data={"name": "Rita", "username": "rita", "chat_id": "4242", "bot_token": "654321:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "commission_pct": "10",
           "due_limit": "400", "login_username": "rita", "password": "ResellerPass123"}, headers=H, content_type="multipart/form-data")
RID = j(r)["id"]
rc = app.test_client(); page = rc.get("/reseller").get_data(as_text=True)
rcsrf = page.split('name="csrf_token" value="')[1].split('"')[0]
rc.post("/reseller", data={"username": "rita", "password": "ResellerPass123", "csrf_token": rcsrf})
RH = {"X-CSRF-Token": j(rc.get("/reseller/api/me"))["csrf"]}
rctx = get_ctx(RID)

# ---------------------------------------------------------------- listing
a_items = j(c.get("/admin/api/settings/screens"))["items"]
r_items = j(rc.get("/reseller/api/screens"))["items"]
ok(len(a_items) == 21 and {"home", "buy", "wallet", "keys", "refer", "reseller", "seller", "support"} <= {x["key"] for x in a_items}, "admin sees every screen of the main bot")
ok(len(r_items) == 16 and not {"reseller", "seller", "crypto_card", "faq", "how_crypto"} & {x["key"] for x in r_items}, "a reseller sees the screens of a shop (no Reseller / Seller)")
ok(next(x for x in a_items if x["key"] == "refer")["text"].startswith("🎁"), "refer & reseller show their current text in the editor")

# ---------------------------------------------------------------- admin: wallet screen gets a picture + message
up = c.post("/admin/api/upload/image", data={"file": (io.BytesIO(png(400)), "w.png")}, headers=H, content_type="multipart/form-data")
ok(up.status_code == 200 and j(up)["url"].startswith("/media/img/"), "admin uploads a picture")
IMG = j(up)["url"]
ok(c.put("/admin/api/settings/screens/wallet", json={"text": "💎 <b>VIP wallet</b>", "format": "html", "image": "javascript:alert(1)"}, headers=H).status_code == 400, "a bad picture link is refused")
ok(c.put("/admin/api/settings/screens/nope", json={"text": "x"}, headers=H).status_code == 404, "unknown screen is refused")
ok(c.put("/admin/api/settings/screens/wallet", json={"text": "💎 <b>VIP wallet</b>", "format": "html", "image": IMG}, headers=H).status_code == 200, "admin saves wallet message + picture")
handlers.process_update(msg(501, "/start", name="Asha"))
h.CALLS.clear(); handlers.process_update(cbq(501, "wallet", "Asha"))
ph = [(p, t) for m_, p, t, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == "501"]
ok(ph and "VIP wallet" in ph[0][0]["caption"] and "My Wallet" in ph[0][0]["caption"] and ph[0][1] == Config.BOT_TOKEN, "main bot: wallet shows the admin's picture, message on top, live balance below")
ok(len(ph[0][0]["caption"]) <= 1024, "caption within Telegram's limit")
# the same screen again (message already has that picture): edited in place, no new photo
h.CALLS.clear()
upd = cbq(501, "wallet", "Asha"); upd["callback_query"]["message"]["photo"] = [{"file_id": "x"}]
db.set("/bot_scr/501", {"mid": 7, "img": IMG})
handlers.process_update(upd)
ok(any(m_ == "editMessageCaption" for m_, *_ in h.CALLS) and not [1 for m_, *_ in h.CALLS if m_ == "sendPhoto"], "same picture again -> caption edited in place (no flicker)")
# deposit has no picture: plain text edit/new
h.CALLS.clear(); handlers.process_update(cbq(501, "dep", "Asha"))
ok(not [1 for m_, *_ in h.CALLS if m_ == "sendPhoto"], "other screens are unchanged")

# the admin's settings do NOT leak into the reseller's bot
handlers.process_update(msg(601, "/start", name="Rob"), rctx)
h.CALLS.clear(); handlers.process_update(cbq(601, "wallet", "Rob"), rctx)
ok("VIP wallet" not in str(h.CALLS) and not [1 for m_, *_ in h.CALLS if m_ == "sendPhoto"], "a reseller's bot never shows the admin's wallet picture / message")

# ---------------------------------------------------------------- reseller: purchase (buy) picture + message
up = rc.post("/reseller/api/upload/image", data={"file": (io.BytesIO(png(420, "blue")), "b.png")}, headers=RH, content_type="multipart/form-data")
ok(up.status_code == 200, "reseller uploads a picture")
RIMG = j(up)["url"]
ok(rc.put("/reseller/api/screens/reseller", json={"text": "x"}, headers=RH).status_code == 404, "reseller cannot edit main-bot-only screens")
ok(rc.put("/reseller/api/screens/buy", json={"text": "🔥 Best prices in town", "format": "html", "image": RIMG}, headers=RH).status_code == 200, "reseller saves Purchase picture + message")
h.CALLS.clear(); handlers.process_update(cbq(601, "buy", "Rob"), rctx)
ph = [(p, t, f) for m_, p, t, f in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == "601"]
ok(ph and ph[0][1] == rctx.tg.token and "Best prices in town" in ph[0][0]["caption"] and "Pick a category" in ph[0][0]["caption"], "reseller bot: Purchase shows the reseller's picture and message, sent by the reseller's own bot")
ok(ph[0][2] is not None, "the picture is uploaded to Telegram once for that bot")
# admin's own purchase screen still the default logo
h.CALLS.clear(); handlers.process_update(cbq(501, "buy", "Asha"))
ok([p for m_, p, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == "501"], "main bot Purchase keeps the default logo")
# reset
ok(rc.delete("/reseller/api/screens/buy", headers=RH).status_code == 200, "reseller resets")
h.CALLS.clear(); handlers.process_update(cbq(601, "buy", "Rob"), rctx)
ok("Best prices" not in str(h.CALLS), "after reset the reseller's text is gone")

# ---------------------------------------------------------------- long text -> picture dropped, text kept
ok(rc.put("/reseller/api/screens/buy", json={"text": "L" * 1200, "format": "text", "image": RIMG}, headers=RH).status_code == 200, "long intro saved")
h.CALLS.clear(); handlers.process_update(cbq(601, "buy", "Rob"), rctx)
ok(not [1 for m_, *_ in h.CALLS if m_ == "sendPhoto"] and "LLLL" in str(h.CALLS), "caption over 1024 characters: shown as text without the picture (never fails)")
rc.delete("/reseller/api/screens/buy", headers=RH)

# ---------------------------------------------------------------- reseller welcome picture + refer override
ok(rc.put("/reseller/api/screens/home", json={"text": "Welcome to Rita's shop, {first_name}!", "format": "html", "image": RIMG}, headers=RH).status_code == 200, "reseller saves welcome message + picture")
ok(db.get(f"/resellers/{RID}")["welcome_image"] == RIMG, "stored with the reseller (same place the old welcome message lived)")
rctx = get_ctx(RID)  # the webhook builds a fresh context for every update
h.CALLS.clear(); handlers.process_update(cbq(601, "home", "Rob"), rctx)
ph = [p for m_, p, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == "601"]
ok(ph and "shop, Rob" in ph[0]["caption"], "reseller welcome: own picture + text")
ok(rc.put("/reseller/api/screens/refer", json={"text": "Invite friends: earn {percent}% !", "format": "html", "image": RIMG}, headers=RH).status_code == 200, "reseller saves Refer & Earn")
h.CALLS.clear(); handlers.process_update(cbq(601, "refer", "Rob"), rctx)
ok(any("Invite friends" in str(p.get("caption", "")) for m_, p, *_ in h.CALLS if m_ == "sendPhoto"), "reseller Refer & Earn: own message + picture")
h.CALLS.clear(); handlers.process_update(cbq(501, "refer", "Asha"))
ok("Invite friends" not in str(h.CALLS), "…and the main bot's Refer & Earn is untouched")

# ---------------------------------------------------------------- admin: refer / reseller / welcome keep their old storage
ok(c.put("/admin/api/settings/screens/reseller", json={"text": "Be our reseller", "format": "html", "image": IMG}, headers=H).status_code == 200, "admin edits the Reseller button screen")
ok(db.get("/settings/messages/reseller")["text"] == "Be our reseller", "…stored where the Messages tab reads it")
ok(c.put("/admin/api/settings/screens/home", json={"text": "Hi {first_name}", "format": "html", "image": ""}, headers=H).status_code == 200 and db.get("/settings/bot")["welcome_message"] == "Hi {first_name}", "admin welcome shares storage with Settings → Bot")
ok(c.delete("/admin/api/settings/screens/reseller", headers=H).status_code == 200 and db.get("/settings/messages/reseller") is None, "reset removes the override")
ok(c.put("/admin/api/settings/screens/support", json={"text": "", "format": "html", "image": IMG}, headers=H).status_code == 200, "a picture alone (no text) is fine for an intro screen")
h.CALLS.clear(); handlers.process_update(cbq(501, "support", "Asha"))
ok([1 for m_, p, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == "501"] or "not configured" in str(h.CALLS), "support screen picture (or the 'not configured' text)")
print("ALL SCREEN TESTS OK")
