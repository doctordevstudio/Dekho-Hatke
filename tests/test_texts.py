"""Editable bot messages, category pictures, app preview video + provider line, plan button wording, delivery order.
Run: python tests/test_texts.py"""
from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from bot.context import MAIN
from core import db, utils
from services import delivery, texts

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()

# ------------------------------------------------------------------ catalog: category with a picture, app with preview video + provider
IMG = "https://example.com/games.png"
CAT = j(c.post("/admin/api/categories", json={"name": "Games", "icon": "🎮", "image": IMG}, headers=H))["id"]
cat_row = [k for k in j(c.get("/admin/api/categories"))["items"] if k["id"] == CAT][0]
ok(cat_row["image"] == IMG, "category stores its picture")
CAT2 = j(c.post("/admin/api/categories", json={"name": "Plain", "icon": "📦"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001", "video_id": "PREVIEWVID00000000001",
                                        "setup_video_id": "SETUPVID000000000001", "provider_name": "Acme Studio",
                                        "image_url": "https://example.com/logo.png"}, headers=H))["id"]
P1 = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 100, "validity_value": 7, "validity_unit": "days", "sort": 1}, headers=H))["id"]
P2 = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 300, "validity_value": 30, "validity_unit": "days", "sort": 2,
                                        "button_text": "{label} for only {price} 🔥"}, headers=H))["id"]
db.set("/upis/u1", {"id": "u1", "upi_id": "shop@upi", "name": "Shop", "active": True, "created_at": 1, "telegram_chat_id": "999"})

UID = 801
handlers.process_update(msg(UID, "/start", name="Asha"))
h.CALLS.clear(); handlers.process_update(cbq(UID, f"cat:{CAT}", "Asha"))
ph = [p for m_, p, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == str(UID)]
ok(ph and ph[0].get("photo") == IMG, "opening a category shows the category's own picture")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"ap:{APP}", "Asha"))
vid = [p for m_, p, *_ in h.CALLS if m_ == "sendVideo" and str(p.get("chat_id")) == str(UID)]
ok(vid and vid[0].get("video") == "PREVIEWVID00000000001", "opening an app shows its PREVIEW VIDEO (not the category picture)")
cap = str(vid[0].get("caption", "")) if vid else ""
ok("provided by" in cap and "Acme Studio" in cap, "the app screen shows 'This service is provided by <name>'")
ok("for only" in str(vid[0].get("reply_markup")) and "🔥" in str(vid[0].get("reply_markup")), "a plan's own button wording is used")

# ------------------------------------------------------------------ editable messages
d = j(c.get("/admin/api/settings/texts"))
keys = {i["key"] for i in d["items"]}
ok(len(keys) >= 60 and {"shop_title", "license_done", "btn_plan_limit", "how_upi", "card_upi", "crypto_detected"} <= keys, "the registry lists every customer-facing message")
ok(c.put("/admin/api/settings/texts/nope", json={"text": "x"}, headers=H).status_code == 404, "unknown message key refused")
ok(c.put("/admin/api/settings/texts/shop_title", json={"text": "<b>Open</b"}, headers=H).status_code == 400, "broken HTML is refused (Telegram could not show it)")
ok(c.put("/admin/api/settings/texts/shop_title", json={"text": "<script>x</script>"}, headers=H).status_code == 400, "tags Telegram does not support are refused")
ok(c.put("/admin/api/settings/texts/shop_title", json={"text": ""}, headers=H).status_code == 400, "an empty message is refused (use Reset)")
r = c.put("/admin/api/settings/texts/shop_title", json={"text": "🛍 <b>Our store</b>\n{line}\nChoose a section 👇"}, headers=H)
ok(r.status_code == 200 and [i for i in j(r)["items"] if i["key"] == "shop_title"][0]["custom"], "message saved")
h.CALLS.clear(); handlers.process_update(cbq(UID, "buy", "Asha"))
ok("Our store" in str(h.CALLS) and "Choose a section" in str(h.CALLS) and "Pick a category" not in str(h.CALLS), "the bot uses the edited Shop message")
ok(c.delete("/admin/api/settings/texts/shop_title", headers=H).status_code == 200, "reset works")
h.CALLS.clear(); handlers.process_update(cbq(UID, "buy", "Asha"))
ok("Pick a category" in str(h.CALLS), "after reset the built-in wording is back")
# a placeholder typo can never crash the bot
c.put("/admin/api/settings/texts/trial_used", json={"text": "Hello {nobody} — {line}"}, headers=H)
ok("{nobody}" in texts.t("trial_used"), "an unknown {placeholder} is left as it is")
c.delete("/admin/api/settings/texts/trial_used", headers=H)
# view-only admins cannot edit
ok(c.post("/admin/api/admins", json={"username": "viewer9", "password": "ViewerPass1234"}, headers=H).status_code in (200, 201), "view-only admin created")
vc, VH, _ = login_admin(app, "viewer9", "ViewerPass1234")
ok(vc.put("/admin/api/settings/texts/shop_title", json={"text": "x"}, headers=VH).status_code == 403, "view-only admin cannot edit bot messages")

# ------------------------------------------------------------------ plan button wording
c.put("/admin/api/settings/texts/btn_plan_time", json={"text": "{label} → {price}"}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(UID, f"ap:{APP}", "Asha"))
mk = str([p.get("reply_markup") for m_, p, *_ in h.CALLS if m_ in ("sendVideo", "sendPhoto", "editMessageCaption", "editMessageText")][-1])
ok("7 days → ₹100" in mk or "7 Days → ₹100" in mk, "global plan button wording is editable")
c.delete("/admin/api/settings/texts/btn_plan_time", headers=H)

# ------------------------------------------------------------------ key message wording
c.put("/admin/api/settings/texts/license_done", json={"text": "{head}\nKEY for {app}: <code>{key}</code>\n{validity_line}"}, headers=H)
from bot import messages as m_
txt = m_.license_done_text({"app_name": "Alpha", "plan_label": "7 Days", "via": ""}, {"key": "ABCD", "expires_at": utils.now() + 86400}, 0)
ok("KEY for Alpha: <code>ABCD</code>" in txt and "Valid until" in txt, "the message sent with the license key is editable")
c.delete("/admin/api/settings/texts/license_done", headers=H)

# ------------------------------------------------------------------ delivery order
ok(c.patch(f"/admin/api/apps/{APP}", json={"delivery_order": "key,key,video"}, headers=H).status_code == 400, "a bad delivery order is refused")
ok(delivery.delivery_order({}) == ("key", "video", "content"), "default order: key, video, APK/message")
lic = {"key": "ABCDEFGHJKLMNPQRS2", "expires_at": utils.now() + 86400 * 7, "validity_label": "7 Days"}
app_rec = db.get(f"/apps/{APP}")


def order_of():
    seq = []
    for m_name, p, *_ in h.CALLS:
        if str(p.get("chat_id")) != "810":
            continue
        if m_name == "sendVideo":
            seq.append("video")
        elif m_name == "sendDocument":
            seq.append("content")
        elif m_name == "sendMessage" and "KEYMSG" in str(p.get("text")):
            seq.append("key")
    return seq


h.CALLS.clear()
delivery.deliver_assets(MAIN, 810, app_rec, lic, "7 Days", send_key=lambda: MAIN.tg.send_message(810, "KEYMSG"))
ok(order_of() == ["key", "video", "content"], "default delivery: key, setup video, APK")
ok(c.patch(f"/admin/api/apps/{APP}", json={"delivery_order": "video,content,key"}, headers=H).status_code == 200, "admin saves a delivery order")
ok(j(c.get(f"/admin/api/apps/{APP}"))["delivery_order"] == "video,content,key", "app shows its delivery order")
h.CALLS.clear()
delivery.deliver_assets(MAIN, 810, db.get(f"/apps/{APP}"), lic, "7 Days", send_key=lambda: MAIN.tg.send_message(810, "KEYMSG"))
ok(order_of() == ["video", "content", "key"], "delivery follows the admin's order: video, APK, then the key")
# the key is never lost, even when a step fails
h.CALLS.clear()
broken = {**db.get(f"/apps/{APP}"), "setup_video_id": "", "apk_id": ""}
delivery.deliver_assets(MAIN, 810, broken, lic, "7 Days", send_key=lambda: MAIN.tg.send_message(810, "KEYMSG"))
ok(order_of() == ["key"], "an app with no files still delivers the key")

# ------------------------------------------------------------------ every other screen is editable too (wallet, rewards, referral, keys, admin chat)
import re as _re
for k, (grp, lbl, ph, dflt) in texts._R.items():
    used = set(_re.findall(r"\{(\w+)\}", dflt))
    declared = set(_re.findall(r"\{(\w+)\}", ph))
    if not used <= declared:
        ok(False, f"message {k}: the default uses {used - declared} but the panel does not list it")
ok(len(texts.KEYS) >= 180, "the registry now covers wallet, withdraw, rewards, referral, keys, support, seller and admin commands")
ok(all(check is None for check in (texts.check_html(v[3]) for v in texts._R.values())), "every built-in message is valid Telegram HTML")
adm = {i["key"] for i in j(c.get("/admin/api/settings/texts"))["items"]}
ok({"wallet_screen", "wd_done", "checkin_anim", "spin_done", "refer_dashboard", "keys_screen", "admin_help", "admin_block_ask"} <= adm, "the panel lists the new messages")
ok(any(g.startswith("🛠") for g in j(c.get("/admin/api/settings/texts"))["groups"]), "admin-chat commands have their own group")
ok(not any(k.startswith("admin_") for k in {i["key"] for i in texts.listing("R1")["items"]}), "a reseller does not see admin-chat commands")
try:
    texts.save("R1", "admin_help", "x")
    ok(False, "a reseller cannot edit admin-chat commands")
except Exception as e:  # noqa: BLE001
    ok(getattr(e, "status", getattr(e, "code", 404)) == 404, "a reseller cannot edit admin-chat commands")

U2 = 820
handlers.process_update(msg(U2, "/start", name="Ravi"))
h.CALLS.clear(); handlers.process_update(cbq(U2, "wallet", "Ravi"))
ok("My Wallet" in str(h.CALLS) and "Usable on purchases" in str(h.CALLS) and "Minimum withdrawal" in str(h.CALLS), "wallet screen: built-in wording unchanged")
c.put("/admin/api/settings/texts/wallet_screen", json={"text": "👛 Balance: {total}\nDeposit {deposit} / Refer {refer}{bonus_line}{crypto_line}"}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(U2, "wallet", "Ravi"))
ok("👛 Balance:" in str(h.CALLS) and "My Wallet" not in str(h.CALLS), "wallet screen follows the edited message")
c.delete("/admin/api/settings/texts/wallet_screen", headers=H)

h.CALLS.clear(); handlers.process_update(cbq(U2, "wdr", "Ravi"))
ok("Withdraw" in str(h.CALLS) and "Refer Money" in str(h.CALLS), "withdraw screen: built-in wording")
c.put("/admin/api/settings/texts/wd_low", json={"text": "🚧 Withdrawals start at {min}. You have {refer}."}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(U2, "wdr", "Ravi"))
ok("🚧 Withdrawals start at" in str(h.CALLS), "withdraw screen follows the edited message")
c.delete("/admin/api/settings/texts/wd_low", headers=H)

h.CALLS.clear(); handlers.process_update(cbq(U2, "keys", "Ravi"))
ok("don't have a license yet" in str(h.CALLS), "My Keys: built-in wording")
c.put("/admin/api/settings/texts/keys_empty", json={"text": "🗝 Nothing here, {line}"}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(U2, "keys", "Ravi"))
ok("🗝 Nothing here" in str(h.CALLS), "My Keys follows the edited message")
c.delete("/admin/api/settings/texts/keys_empty", headers=H)

h.CALLS.clear(); handlers.process_update(cbq(U2, "checkin", "Ravi"))
ok("Daily Check-in" in str(h.CALLS) or "Check-in" in str(h.CALLS) or "not available" in str(h.CALLS), "check-in screen shows")
c.put("/admin/api/settings/texts/checkin_screen", json={"text": "📆 Come daily: {min} to {max}, valid {minutes} min"}, headers=H)
c.put("/admin/api/settings/texts/reward_off", json={"text": "💤 {label} is off"}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(U2, "checkin", "Ravi"))
ok("📆 Come daily" in str(h.CALLS) or "💤 Daily Check-in is off" in str(h.CALLS), "check-in screen follows the edited messages")
c.delete("/admin/api/settings/texts/checkin_screen", headers=H)
c.delete("/admin/api/settings/texts/reward_off", headers=H)

won = handlers._won_text("spin", {"amount": 12, "expires_at": utils.now() + 600, "expire_minutes": 10})
ok("You won" in won and "10 minutes" in won, "the 'You won' text keeps its wording")
c.put("/admin/api/settings/texts/reward_won", json={"text": "🏆 +{amount} for {minutes} min"}, headers=H)
ok(handlers._won_text("spin", {"amount": 12, "expires_at": utils.now() + 600, "expire_minutes": 10}).startswith("🏆 +"), "the 'You won' text can be reworded")
c.delete("/admin/api/settings/texts/reward_won", headers=H)

h.CALLS.clear(); handlers.process_update(cbq(U2, "refer", "Ravi"))
ok("Refer" in str(h.CALLS), "referral screen shows")

# admin chat commands (chat 999 is the admin)
h.CALLS.clear(); handlers.process_update(msg(999, "/blocked", name="Boss"))
ok("Nobody is blocked" in str(h.CALLS), "/blocked: built-in wording")
c.put("/admin/api/settings/texts/admin_blocked_none", json={"text": "🕊 The block list is empty"}, headers=H)
h.CALLS.clear(); handlers.process_update(msg(999, "/blocked", name="Boss"))
ok("🕊 The block list is empty" in str(h.CALLS), "/blocked follows the edited message")
c.put("/admin/api/settings/texts/admin_help", json={"text": "📖 My own help {line}"}, headers=H)
h.CALLS.clear(); handlers.process_update(msg(999, "/help", name="Boss"))
ok("📖 My own help" in str(h.CALLS), "/help can be reworded")
c.delete("/admin/api/settings/texts/admin_help", headers=H)
c.delete("/admin/api/settings/texts/admin_blocked_none", headers=H)
h.CALLS.clear(); handlers.process_update(msg(999, "/help", name="Boss"))
ok("/broadcast" in str(h.CALLS) and "File IDs" in str(h.CALLS), "after reset /help is the built-in text again")
h.CALLS.clear(); handlers.process_update(msg(999, "/block 5551234", name="Boss"))
ok("Block user" in str(h.CALLS) and "5551234" in str(h.CALLS), "/block asks for the reason (built-in wording)")
handlers.process_update(msg(999, "/cancel", name="Boss"))

# an edited message keeps its wording until it is reset
c.put("/admin/api/settings/texts/cancelled", json={"text": "🙅 Stopped."}, headers=H)
ok(texts.t("cancelled") == "🙅 Stopped." and [i for i in j(c.get("/admin/api/settings/texts"))["items"] if i["key"] == "cancelled"][0]["custom"], "an edited message stays edited")
c.delete("/admin/api/settings/texts/cancelled", headers=H)
ok(texts.t("cancelled").startswith("Cancelled."), "Reset brings the built-in wording back")

print("ALL TEXT TESTS OK")
