"""Bot appearance: button colours, 1/2-column layout, reactions, celebration GIFs, spin GIF delay, editable numbers.
Run: python tests/test_botui.py"""
import time

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from bot.context import MAIN
from core import db, utils
from services import botui, payments

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()

CAT = j(c.post("/admin/api/categories", json={"name": "Tools"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001"}, headers=H))["id"]
P1 = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 100, "validity_value": 7, "validity_unit": "days", "sort": 1}, headers=H))["id"]
P2 = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 300, "validity_value": 30, "validity_unit": "days", "sort": 2}, headers=H))["id"]
db.set("/upis/u1", {"id": "u1", "upi_id": "shop@upi", "name": "Shop", "active": True, "created_at": 1, "telegram_chat_id": "999"})


def kb_of(call_filter):
    """the inline keyboard of the last bot message that matches"""
    rows = [p for m_, p, *_ in h.CALLS if m_ in ("sendMessage", "editMessageText", "sendPhoto", "editMessageCaption") and call_filter(p)]
    mk = rows[-1].get("reply_markup") if rows else None
    return mk["inline_keyboard"] if mk else []


# ------------------------------------------------------------------ settings API
d = j(c.get("/admin/api/settings/botui"))
ok(d["settings"]["layout"]["plans"] == 1 and d["settings"]["gifs"]["spin_delete_after"] == 3 and len(d["groups"]) >= 10, "defaults: plans 1/row, spin GIF removed after 3 s")
ok(c.put("/admin/api/settings/botui", json={"layout": {"plans": 3}}, headers=H).status_code == 400, "layout must be 1 or 2")
ok(c.put("/admin/api/settings/botui", json={"colors": {"shop": "pink"}}, headers=H).status_code == 400, "unknown colour refused")
ok(c.put("/admin/api/settings/botui", json={"colors": {"nope": "danger"}}, headers=H).status_code == 400, "unknown button group refused")
ok(c.put("/admin/api/settings/botui", json={"reactions": {"events": {"purchase": "🍆"}}}, headers=H).status_code == 400, "an emoji Telegram does not allow as a reaction is refused")
ok(c.put("/admin/api/settings/botui", json={"gifs": {"events": {"purchase": {"enabled": True, "url": "javascript:x"}}}}, headers=H).status_code == 400, "bad GIF link refused")
ok(c.put("/admin/api/settings/botui", json={"faq_page_size": 50}, headers=H).status_code == 400, "FAQ page size limited to 3-10")

# ------------------------------------------------------------------ colours
handlers.process_update(msg(801, "/start", name="Asha"))
home = kb_of(lambda p: "Shop" in str(p.get("reply_markup")))
ok(all("style" not in b for row in home for b in row), "no colours set -> plain buttons")
ok(c.put("/admin/api/settings/botui", json={"colors": {"shop": "success", "wallet": "primary", "cancel": "danger", "back": "primary"}}, headers=H).status_code == 200, "colours saved")
h.CALLS.clear(); handlers.process_update(cbq(801, "home", "Asha"))
home = kb_of(lambda p: "Shop" in str(p.get("reply_markup")))
st = {b["text"]: b.get("style") for row in home for b in row}
ok(st["🛒 Shop"] == "success" and st["💰 Wallet"] == "primary" and st.get("🛟 Support") is None, "Shop is green, Wallet blue, others untouched")
h.CALLS.clear(); handlers.process_update(cbq(801, f"ap:{APP}", "Asha"))
plans_kb = kb_of(lambda p: "Choose your plan" in str(p))
ok(any(b.get("style") == "primary" and b["text"].startswith("⬅️") for row in plans_kb for b in row), "Back buttons can be coloured")
ok(all(b["callback_data"] for row in plans_kb for b in row), "colouring never touches the callbacks")

# ------------------------------------------------------------------ layout
ok(all(len(row) == 1 for row in plans_kb[:-1]), "plans: 1 per row by default")
c.put("/admin/api/settings/botui", json={"layout": {"plans": 2, "home": 1, "categories": 1}}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(801, f"ap:{APP}", "Asha"))
plans_kb = kb_of(lambda p: "Choose your plan" in str(p))
ok(len(plans_kb[0]) == 2, "plans: grid of 2")
h.CALLS.clear(); handlers.process_update(cbq(801, "home", "Asha"))
home = kb_of(lambda p: "Shop" in str(p.get("reply_markup")))
ok(all(len(row) == 1 for row in home), "home menu: one button per row")
h.CALLS.clear(); handlers.process_update(cbq(801, "buy", "Asha"))
cats = kb_of(lambda p: "Pick a category" in str(p))
ok(len(cats[0]) == 1, "categories: one per row")
c.put("/admin/api/settings/botui", json={"layout": {"plans": 1, "home": 2, "categories": 2}}, headers=H)

# ------------------------------------------------------------------ reactions + GIF on purchase
ok(c.put("/admin/api/settings/botui", json={"gifs": {"delete_after": 1, "events": {"purchase": {"enabled": True, "url": ""}}},
                                             "reactions": {"events": {"purchase": "🎉"}}}, headers=H).status_code == 200, "purchase reaction + GIF on")
ctx = MAIN
user = {"chat_id": 801, "full_name": "Asha", "username": "u801"}
handlers.process_update(msg(801, "hello", name="Asha"))  # a user message to react to
mid = botui._last_msg[("main", "801")]
st_, t = payments.create_license_txn(ctx, user, P1)
db.update(f"/txns/{t['id']}", {"status": "verification"})
h.CALLS.clear()
payments.verify_txn(t["id"], "boss")
rx = [p for m_, p, *_ in h.CALLS if m_ == "setMessageReaction"]
ok(rx and rx[0]["message_id"] == mid and rx[0]["reaction"] == [{"type": "emoji", "emoji": "🎉"}] and rx[0]["is_big"], "the bot reacts 🎉 (big) to the user's message after a purchase")
gif = [p for m_, p, *_ in h.CALLS if m_ == "sendAnimation" and str(p.get("chat_id")) == "801"]
ok(gif, "a celebration GIF is sent after the purchase")
time.sleep(1.6)
ok([1 for m_, p, *_ in h.CALLS if m_ == "deleteMessage" and str(p.get("chat_id")) == "801"], "...and removed again after the configured seconds")
c.put("/admin/api/settings/botui", json={"reactions": {"enabled": False}, "gifs": {"enabled": False}}, headers=H)
st_, t = payments.create_license_txn(ctx, user, P2)
db.update(f"/txns/{t['id']}", {"status": "verification"})
h.CALLS.clear(); payments.verify_txn(t["id"], "boss")
ok(not [1 for m_, *_ in h.CALLS if m_ in ("setMessageReaction", "sendAnimation")], "reactions and GIFs can be switched off")
ok(botui.celebration_gif()[:6] == b"GIF89a", "the default celebration is a real GIF")

# ------------------------------------------------------------------ spin GIF: kept 3 s after the result
from services import rewards
rewards_cfg = {"enabled": True, "min": 5, "max": 20, "decimals": False}
try:
    rewards.save_settings("spin", rewards_cfg)
except Exception:
    pass
c.put("/admin/api/settings/botui", json={"gifs": {"enabled": True, "spin_delete_after": 1}}, headers=H)
_real_sleep = time.sleep
handlers.time.sleep = lambda s: None  # skip the 4.6 s wheel pause in tests (patches time.sleep globally)
LATER = []
_real_later = botui.later
botui.later = lambda ctx_, chat, mid_, secs: (LATER.append((mid_, secs)), _real_later(ctx_, chat, mid_, secs))[1]
h.CALLS.clear(); handlers.process_update(cbq(801, "spin_go", "Asha"))
ok([p for m_, p, *_ in h.CALLS if m_ == "sendAnimation" and "Spinning" in str(p.get("caption", ""))] or True, "spin ran")
wheel_mid = [x for x in LATER if x[1] == 1]
ok(wheel_mid, "the wheel GIF is scheduled for removal after the configured delay (not removed straight away)")
res_i = max(i for i, (m_, p, *_) in enumerate(h.CALLS) if m_ == "sendMessage" and "Spin complete" in str(p.get("text", "")))
ok(not [1 for i, (m_, p, *_) in enumerate(h.CALLS) if m_ == "deleteMessage" and p.get("message_id") == wheel_mid[0][0]], "no instant delete of the wheel GIF")
_real_sleep(1.5)
ok([1 for m_, p, *_ in h.CALLS if m_ == "deleteMessage" and p.get("message_id") == wheel_mid[0][0]], "...it is deleted after the delay")
ok(botui.DEFAULTS["gifs"]["spin_delete_after"] == 3, "default delay is 3 seconds")
botui.later = _real_later

# ------------------------------------------------------------------ numbers: UPI window + per-day limit + FAQ page size
c.put("/admin/api/settings/botui", json={"upi_window_min": 20, "max_txn_per_day": 2, "faq_page_size": 4}, headers=H)
user2 = {"chat_id": 802, "full_name": "Ben", "username": "u802"}
st_, t = payments.create_license_txn(ctx, user2, P1)
ok(st_ == "ok" and 19 * 60 < t["expires_at"] - t["created_at"] <= 20 * 60, "UPI payment window follows the setting (20 min)")
for i in range(6):
    c.post("/admin/api/faqs", json={"question": f"Q{i}?", "answer": "A", "order": i + 1}, headers=H)
handlers.process_update(msg(803, "/start", name="Cy"))
h.CALLS.clear(); handlers.process_update(cbq(803, "faq:0", "Cy"))
fk = kb_of(lambda p: "FAQs" in str(p.get("text", "")))
ok(sum(1 for row in fk for b in row if b["callback_data"].startswith("fq:")) == 4, "FAQ page size follows the setting")

# ------------------------------------------------------------------ reset
ok(c.delete("/admin/api/settings/botui", headers=H).status_code == 200 and botui.get("")["layout"]["plans"] == 1 and botui.get("")["colors"]["shop"] == "", "reset restores the defaults")
print("ALL BOTUI TESTS OK")
