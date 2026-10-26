"""Shortener (step-gate): admin API, plans, public gate API, timing rules, bot flow, notifications, license delivery, tracking,
drop-offs, redirect kind, restart persistence, limits, speed under concurrent visitors.   Run: python tests/test_shortener.py"""
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from bot.context import get_ctx
from config import Config
from core import db, utils
from services import catalog, shortener, wallet

app = create_app(background=False)
c, H, me = login_admin(app)
j = lambda r: r.get_json()
_orig = handlers.process_update
Config.PROXY_SECRET = "proxy-secret-123"
Config.GATE_MIN_SCROLL_GAP = 1.2
SITE = "https://dekhohatke.com"


def P(u, ctx=None):
    spam._s.clear(); handlers.reset_gates()
    return _orig(u, ctx) if ctx else _orig(u)


# ---- a controllable clock for the engine (the waits are real seconds on the server)
class Clock:
    def __init__(self): self.t = time.time()
    def time(self): return self.t
    def sleep(self, s): pass
    def adv(self, s): self.t += s


CK = Clock()
shortener.time = CK


def wait_for(cond, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# =============================================================== catalog + admin API
CAT = j(c.post("/admin/api/categories", json={"name": "Tools", "icon": "🧰"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001",
                                        "setup_video_id": "SETUPVID00000000001", "description": "A handy tool", "features": "Fast"}, headers=H))["id"]
U1 = j(c.post("/admin/api/upis", data={"name": "Shop A", "upi_id": "shopa@okbank", "telegram_chat_id": "555", "scanner": (io.BytesIO(png()), "q.png")},
              headers=H, content_type="multipart/form-data"))["id"]
c.post("/admin/api/plans", json={"app_id": APP, "amount": 199, "validity_value": 30, "validity_unit": "days", "sort": 2}, headers=H)
PAID = [p for p in db.get_list("/plans") if p["amount"] == 199][0]["id"]

base = {"name": "Free key · 3 pages", "kind": "license", "pages": 3, "top_min": 1, "top_max": 1, "bottom_min": 1, "bottom_max": 1, "ttl_min": 45}
mk = lambda **kw: c.post("/admin/api/shorteners", json={**base, **kw}, headers=H)
ok(mk().status_code == 400, "no blog pages anywhere -> create is refused with a clear message")
r = c.put("/admin/api/shorteners/config", json={"site_url": SITE, "pages": "/blog/a/\n/blog/b/\nhttps://dekhohatke.com/blog/c/?x=1\n/blog/a/"}, headers=H)
ok(r.status_code == 200 and j(r)["config"]["pages"] == ["/blog/a/", "/blog/b/", "/blog/c/"], "pages saved (full URLs reduced to paths, duplicates dropped)")
ok(c.put("/admin/api/shorteners/config", json={"site_url": "https://x.com/blog", "pages": "/a/"}, headers=H).status_code == 400, "website address must be only a domain")
ok(c.put("/admin/api/shorteners/config", json={"site_url": SITE, "pages": "/bad path/"}, headers=H).status_code == 400, "invalid page path refused")
ok(mk(name="").status_code == 400, "name is required")
ok(mk(pages=0).status_code == 400 and mk(pages=99).status_code == 400, "pages must be 1-15")
ok(mk(top_min=20, top_max=5).status_code == 400, "max wait cannot be below min wait")
ok(mk(kind="redirect").status_code == 400, "redirect type needs a redirect URL")
ok(mk(kind="redirect", redirect_url="javascript:alert(1)").status_code == 400, "only http(s) redirect URLs")
ok(mk(kind="weird").status_code == 400, "unknown type refused")
r = mk(); ok(r.status_code == 201, "license shortener created"); SH = j(r)["item"]
ok(SH["link"] == "" and SH["kind"] == "license" and SH["notify_pages"] is True, "license shortener has no shared link and notifies per page")
r = mk(name="Promo redirect", kind="redirect", redirect_url="https://example.com/offer", pages=2); ok(r.status_code == 201, "redirect shortener created")
RD = j(r)["item"]
ok(RD["link"].startswith(SITE + "/blog/") and "?t=" + RD["id"] in RD["link"] and RD["notify_pages"] is False, "redirect shortener gets a root-domain link")
ok(c.patch(f"/admin/api/shorteners/{SH['id']}", json={**base, "kind": "redirect", "redirect_url": "https://x.com"}, headers=H).status_code == 400, "type cannot change after creation")
r = c.patch(f"/admin/api/shorteners/{SH['id']}", json={**base, "kind": "license", "note": "main", "max_active": 2, "daily_limit": 0}, headers=H)
ok(r.status_code == 200 and j(r)["item"]["max_active"] == 2, "edit works")
opts = j(c.get("/admin/api/shorteners/options"))["items"]
ok([o["id"] for o in opts] == [SH["id"]], "plan dropdown lists only license-key shorteners")
lst = j(c.get("/admin/api/shorteners"))
ok(len(lst["items"]) == 2 and lst["config"]["proxy_ready"] is True and all("counts" in x for x in lst["items"]), "history list carries counts + config state")
ok(c.get("/admin/api/shorteners/nope/stats").status_code == 404, "unknown shortener -> 404")

# ---- plans
mkp = lambda **kw: c.post("/admin/api/plans", json={"app_id": APP, "validity_value": 1, "validity_unit": "days", "sort": 1, **kw}, headers=H)
ok(mkp(shorturl_id=RD["id"]).status_code == 400, "a redirect shortener cannot be used for a plan")
ok(mkp(shorturl_id="s00000000").status_code == 400, "unknown shortener refused")
r = mkp(shorturl_id=SH["id"], amount="", validity_value=7, sort=3); ok(r.status_code == 201, "Free plan with a shorturl created (no price needed)")
FREE = j(r)["id"]
pl = [p for p in j(c.get("/admin/api/plans"))["items"] if p["id"] == FREE][0]
ok(pl["kind"] == "short" and pl["amount"] == 0 and pl["shorturl_name"] == SH["name"] and pl["shorturl_ok"], "plan list shows kind 'short' and the shortener name")
ok(c.delete(f"/admin/api/shorteners/{SH['id']}", headers=H).status_code == 400, "shortener used by a plan cannot be deleted")

# =============================================================== bot: new plan flow
U = 5001
h.CALLS.clear(); P(msg(U, "/start", name="Sita"))
h.CALLS.clear(); P(cbq(U, "buy", "Sita"))
ok(any(m_ == "sendPhoto" and str(p.get("chat_id")) == str(U) for m_, p, *_ in h.CALLS), "categories screen shows the Dekho Hatke logo")
h.CALLS.clear(); P(cbq(U, f"ap:{APP}", "Sita"))
kb = [b for m_, p, *_ in h.CALLS for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
labels = {b["callback_data"]: b["text"] for b in kb if b.get("callback_data")}
ok("FREE" in labels.get(f"pl:{FREE}", "") and "₹" not in labels.get(f"pl:{FREE}", ""), f"bot shows Free for the shorturl plan: {labels.get('pl:' + FREE)}")
ok("₹199" in labels.get(f"pl:{PAID}", "") and not any(k.startswith("pc:") for k in labels), "paid plan shows its price; no separate Continue step")
h.CALLS.clear(); P(cbq(U, f"pl:{PAID}", "Sita"))
t = h.sent("editMessageText", "Confirm your order", U) + h.sent("sendMessage", "Confirm your order", U) + h.sent("editMessageCaption", "Confirm your order", U)
yes = [b["callback_data"] for p in t for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
ok(t and f"py:{PAID}" in yes and not db.get_list("/txns"), "tapping a plan goes straight to the confirmation (Yes / No), nothing created yet")
h.CALLS.clear(); P(cbq(U, f"py:{PAID}", "Sita"))
ok(len(db.query_equal("/txns", "status", "pending")) == 1 and h.sent("sendPhoto", "Payment Request", U), "Yes -> payment card (QR) right away when there is no wallet")
tx = db.query_equal("/txns", "status", "pending")[0]
c.post(f"/admin/api/txns/{tx['id']}/reject", json={"reason": "test"}, headers=H)
wallet.credit("5002", "deposit", 500, "deposit", "t0", "test")
h.CALLS.clear(); P(cbq(5002, f"pl:{PAID}", "Wal")); P(cbq(5002, f"py:{PAID}", "Wal"))
opt = h.sent("editMessageText", "Choose how to pay", 5002) + h.sent("sendMessage", "Choose how to pay", 5002)
bt = [b["callback_data"] for p in opt for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
ok(opt and f"by:{PAID}:1" in bt and f"by:{PAID}:0" in bt, "with a wallet balance: Yes -> payment options (wallet / UPI)")

# =============================================================== gate API guards
anon = app.test_client()
HD = {"X-DH-Proxy": Config.PROXY_SECRET, "X-DH-IP": "9.9.9.9"}
post = lambda path, body, ip="9.9.9.9", hd=None: anon.post(path, json=body, headers={**(hd or HD), "X-DH-IP": ip})
ok(anon.post("/api/init", json={"t": "x"}).status_code == 404, "gate API without the proxy secret -> 404")
ok(anon.post("/api/init", json={"t": "x"}, headers={"X-DH-Proxy": "wrong"}).status_code == 404, "wrong proxy secret -> 404")
ok(anon.get("/api/health").status_code == 200 and anon.head("/api/health").status_code == 200, "/api/health is open (UptimeRobot)")
r = anon.get("/api/ping", headers=HD); ok(r.status_code == 200 and j(r)["proxy"] == "ok" and r.headers["Cache-Control"] == "no-store", "/api/ping proves the proxy secret")
ok(j(post("/api/init", {"t": "garbage", "p": "/"})) == {"ok": False}, "unknown link -> same empty answer")
ok(j(post("/api/init", {"t": SH["id"] + "A" * 20, "p": "/"})) == {"ok": False}, "right prefix, wrong code -> same empty answer")
Config.PROXY_SECRET, keep = "", Config.PROXY_SECRET
ok(anon.post("/api/init", json={"t": "x"}, headers=HD).status_code == 404, "no PROXY_SECRET configured -> gate stays closed")
Config.PROXY_SECRET = keep


# =============================================================== a full run through the bot
def link_of(chat):
    for m_, p, *_ in reversed(h.CALLS):
        if m_ in ("sendMessage", "editMessageText") and str(p.get("chat_id")) == str(chat):
            for row in (p.get("reply_markup") or {}).get("inline_keyboard", []):
                for b in row:
                    if b.get("url", "").startswith(SITE):
                        return b["url"]
    return ""


def code_of(url):
    return url.split("t=")[1]


def path_of(url):
    return url.split("?")[0][len(SITE):]


def drive(code, until_page=None, ip="1.1.1.1", turnstile=None):
    """Behave like gate.js on every page: init, wait, top click, scroll, bottom click, wait, verify. Returns the last answer."""
    out = None
    s = shortener._S[code] if code in shortener._S else shortener.load(code)
    for i, st in enumerate(s["steps"]):
        if i < s["cur"]:
            continue          # continue from the page the link is on
        if until_page is not None and i >= until_page:
            break
        a = j(post("/api/init", {"t": code, "p": st["path"]}, ip)); assert a["ok"] and a["step"] == i + 1, a
        CK.adv(st["top"] + 0.5)
        a = j(post("/api/event", {"t": code, "ev": "top_click", "n": a["n"], "sig": {"trusted": True}}, ip)); assert a["ok"], a
        CK.adv(2)
        a = j(post("/api/event", {"t": code, "ev": "bottom_click", "n": a["n"], "sig": {"trusted": True, "bottom": True}}, ip)); assert a["ok"], a
        CK.adv(st["bottom"] + 0.5)
        out = j(post("/api/verify", {"t": code, "n": a["n"], "sig": {"trusted": True}, "cf": ""}, ip)); assert out["ok"], out
    return out


h.CALLS.clear(); P(cbq(U, f"pl:{FREE}", "Sita"))
txt = " ".join(str(p) for p in h.sent("editMessageText", "Confirm", U) + h.sent("sendMessage", "Confirm", U) + h.sent("editMessageCaption", "Confirm", U))
ok("FREE" in txt and "3 pages" in txt and "₹" not in txt, "Free plan confirmation explains the pages, shows no price")
h.CALLS.clear(); P(cbq(U, f"py:{FREE}", "Sita"))
url = link_of(U); CODE = code_of(url)
ok(url.startswith(SITE + "/blog/") and CODE.startswith(SH["id"]), f"user got a root-domain link: {url[:60]}…")
ok(h.sent("sendMessage", "Complete all <b>3 page", U), "bot tells how many pages to complete")
S = shortener._S[CODE]
ok(len(S["steps"]) == 3 and all(S["steps"][i]["path"] != S["steps"][i + 1]["path"] for i in range(2)), "3 steps, never the same page twice in a row")
ok(db.get(f"/sl_links/{SH['id']}/{CODE}")["code"] == CODE, "the link is saved in Firebase at once (survives a redeploy)")

# ---- the timing rules are enforced by the server
st0 = S["steps"][0]
a = j(post("/api/init", {"t": CODE, "p": "/blog/other/"}))
ok(a["ok"] and a["redirect"] == f"{st0['path']}?t={CODE}", "wrong page -> redirected to the current step page")
a = j(post("/api/init", {"t": CODE, "p": st0["path"]}))
ok(a["ok"] and a["step"] == 1 and a["total"] == 3 and a["phase"] == "top" and a["remaining"] > 0, "init: step 1 of 3, waiting at the top")
r = j(post("/api/event", {"t": CODE, "ev": "top_click", "n": a["n"], "sig": {"trusted": True}}))
ok(r == {"ok": False, "err": "resync"}, "top click before the top wait is over -> refused")
CK.adv(st0["top"] + 0.5)
ok(j(post("/api/event", {"t": CODE, "ev": "top_click", "n": "stale", "sig": {"trusted": True}}))["err"] == "resync", "wrong / stale nonce -> refused")
a2 = j(post("/api/init", {"t": CODE, "p": st0["path"]}))  # a reload rotates the nonce
ok(j(post("/api/event", {"t": CODE, "ev": "top_click", "n": a["n"], "sig": {"trusted": True}}))["err"] == "resync", "the nonce of the previous load no longer works")
ok(j(post("/api/event", {"t": CODE, "ev": "top_click", "n": a2["n"], "sig": {"wd": True}}))["err"] == "resync" and shortener._S[CODE]["flags"] == 1, "webdriver flag -> refused and counted")
b = j(post("/api/event", {"t": CODE, "ev": "top_click", "n": a2["n"], "sig": {"trusted": True}}))
ok(b["ok"] and b["phase"] == "scroll", "top click after the wait -> scroll phase")
ok(j(post("/api/event", {"t": CODE, "ev": "bottom_click", "n": b["n"], "sig": {"trusted": True, "bottom": True}}))["err"] == "resync", "bottom click within 1.2 s of the top click -> refused")
CK.adv(1.5)
ok(j(post("/api/event", {"t": CODE, "ev": "bottom_click", "n": b["n"], "sig": {"trusted": True}}))["err"] == "resync", "bottom click without having reached the bottom -> refused")
c2 = j(post("/api/event", {"t": CODE, "ev": "bottom_click", "n": b["n"], "sig": {"trusted": True, "bottom": True}}))
ok(c2["ok"] and c2["phase"] == "bottom", "bottom click accepted -> bottom wait")
ok(j(post("/api/verify", {"t": CODE, "n": c2["n"], "sig": {"trusted": True}}))["err"] == "resync", "verify before the bottom wait is over -> refused")
CK.adv(st0["bottom"] + 0.5)
h.CALLS.clear()
v = j(post("/api/verify", {"t": CODE, "n": c2["n"], "sig": {"trusted": True}}))
ok(v["ok"] and v["done"] is False and v["next"].endswith(f"?t={CODE}") and v["next"].startswith(S["steps"][1]["path"]), "verify page 1 -> next page url")
ok(wait_for(lambda: h.sent("sendMessage", "Page 1 completed", U)), "bot: “Page 1 completed” arrives")
msg1 = h.sent("sendMessage", "Page 1 completed", U)[0]
ok("2</b> pages remaining" in msg1["text"] or "<b>2</b> pages" in msg1["text"], "…and says 2 pages remaining")
ok(any(b.get("url", "").startswith(SITE + S["steps"][1]["path"]) for row in msg1["reply_markup"]["inline_keyboard"] for b in row), "…with a button to page 2")
ok(not h.sent("sendMessage", "Your free key", U) and not [l for l in db.get_list("/licenses") if l.get("chat_id") == str(U)], "no key before the last step")
ok(j(post("/api/verify", {"t": CODE, "n": c2["n"], "sig": {"trusted": True}}))["ok"] is False, "re-sending the same verify does nothing")

# ---- pages 2 and 3, then the key
h.CALLS.clear()
step2 = drive(CODE, until_page=2)
ok(wait_for(lambda: h.sent("sendMessage", "Page 2 completed", U)), "bot: “Page 2 completed — 1 page remaining”")
ok("1</b> page remaining" in h.sent("sendMessage", "Page 2 completed", U)[0]["text"], "…singular wording for the last page")
h.CALLS.clear()
fin = drive(CODE)
ok(fin["done"] is True and "bot" in fin, "last Generate -> done (answer returns immediately)")
ok(wait_for(lambda: [l for l in db.get_list("/licenses") if l.get("chat_id") == str(U)]), "license key created after the last step")
lic = [l for l in db.get_list("/licenses") if l.get("chat_id") == str(U)][0]
ok(lic["source"] == "shorturl" and lic["validity_unit"] == "days" and lic["validity_value"] == 7 and lic["app_id"] == APP, "7-day key for the right app, source=shorturl")
ok(wait_for(lambda: h.sent("sendMessage", lic["key"], U)), "the key is sent in the bot")
ok(h.sent("sendMessage", "All steps verified", U), "…with the 'all steps verified' message")
ok(wait_for(lambda: h.sent("sendDocument", lic["key"], U) or h.sent("sendDocument", "", U)), "APK delivered too")
ok(shortener._S[CODE]["delivered"] and shortener._S[CODE]["lic_key"] == lic["key"], "session records delivery")
tx = db.get(f"/txns/{shortener._S[CODE]['txn_id']}")
ok(tx["status"] == "success" and tx["via"] == "shorturl" and tx["amount"] == 0 and "short link" in tx["plan_label"], "a free order is recorded (shows in user purchase history)")
ok(j(post("/api/verify", {"t": CODE, "n": "x", "sig": {}}))["ok"] is False and j(post("/api/init", {"t": CODE, "p": "/"}))["ok"] is False, "a finished link cannot be reused")
n_lic = len(db.get_list("/licenses"))
shortener._finish(CODE)
ok(len(db.get_list("/licenses")) == n_lic, "delivery is idempotent: a retry never makes a second key")

# =============================================================== tracking numbers
shortener.flush()
stt = j(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=today"))["stats"]
ok(stt["started"] == 1 and stt["done"] == 1 and stt["opened"] == 1, f"stats: started 1, opened 1, done 1 ({stt['started']}/{stt['opened']}/{stt['done']})")
ok([f["completed"] for f in stt["funnel"]] == [1, 1, 1], "funnel: 1 user completed each of the 3 pages")
ok(j(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=yesterday"))["stats"]["done"] == 0, "yesterday: 0")
ok(j(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=all"))["stats"]["done"] == 1, "all time: 1")
today = utils.day_key()
ok(j(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=custom&start={today}&end={today}"))["stats"]["done"] == 1, "custom range: 1")
ok(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=custom&start={today}").status_code == 400, "custom range needs both dates")
sess = j(c.get(f"/admin/api/shorteners/{SH['id']}/sessions"))
ok(sess["total"] == 1 and sess["items"][0]["status"] == "done" and sess["items"][0]["license_key"] == lic["key"] and sess["items"][0]["user"]["chat_id"] == str(U),
   "tracking list: user, status, license key")
ok(len(sess["items"][0]["pages"]) == 3 and all(p["done_at"] for p in sess["items"][0]["pages"]), "…with every page and its completion time")
ok("ip" not in sess["items"][0] and "code" not in sess["items"][0], "IP address and the secret code are never returned")

# =============================================================== people who leave
U2_ = 5003
h.CALLS.clear(); P(cbq(U2_, f"pl:{FREE}", "Leaver")); P(cbq(U2_, f"py:{FREE}", "Leaver"))
L = code_of(link_of(U2_))
drive(L, until_page=2)
U3_ = 5004
P(cbq(U3_, f"pl:{FREE}", "Never")); P(cbq(U3_, f"py:{FREE}", "Never"))
N_ = code_of(link_of(U3_))
live = j(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=today"))["stats"]
ok(live["active_now"] == 2 and live["active_by_page"] == {"0": 1, "2": 1}, f"in progress now: 1 on page 1, 1 on page 3 ({live['active_by_page']})")
CK.adv(46 * 60)
shortener._sweep(); shortener.flush()
stt = j(c.get(f"/admin/api/shorteners/{SH['id']}/stats?range=today"))["stats"]
ok(stt["started"] == 3 and stt["done"] == 1 and stt["left_total"] == 2, "two links expired unfinished")
ok(stt["funnel"][1]["left_after"] == 1 and stt["left_before_page1"] == 1, "1 user left after completing page 2, 1 never completed page 1")
ex = j(c.get(f"/admin/api/shorteners/{SH['id']}/sessions?status=expired"))
ok(ex["total"] == 2 and {x["completed_pages"] for x in ex["items"]} == {0, 2}, "‘left’ filter lists them with how far they got")
ok(j(c.get(f"/admin/api/shorteners/{SH['id']}/sessions?q=Leaver"))["total"] == 1 and j(c.get(f"/admin/api/shorteners/{SH['id']}/sessions?q=5003"))["total"] == 1, "search by name and chat id works")
ok(j(post("/api/init", {"t": L, "p": "/"}))["ok"] is False, "an expired link is dead")
lst = j(c.get("/admin/api/shorteners"))["items"]
cnt = [x for x in lst if x["id"] == SH["id"]][0]["counts"]
ok(cnt["today"] == 1 and cnt["yesterday"] == 0 and cnt["all"] == 1 and cnt["active_now"] == 0, "history card: today / yesterday / all time successes")

# =============================================================== limits + restart
U4 = 5005
P(cbq(U4, f"pl:{FREE}", "Lim")); P(cbq(U4, f"py:{FREE}", "Lim")); first = code_of(link_of(U4))
CK.adv(10)
h.CALLS.clear(); P(cbq(U4, f"pl:{FREE}", "Lim")); P(cbq(U4, f"py:{FREE}", "Lim"))
ok(code_of(link_of(U4)) != first, "second tap creates another link (limit is 2 open links)")
CK.adv(10); P(cbq(U4, f"pl:{FREE}", "Lim")); h.CALLS.clear(); P(cbq(U4, f"py:{FREE}", "Lim"))
ok(h.sent("sendMessage", "already have an open link", U4) and len([x for x in shortener._S.values() if x.get("chat_id") == str(U4) and shortener._alive(x)]) == 2, "over the open-link limit -> the existing link is shown, no new one")
CK.adv(1)
# restart: memory is empty, Firebase has everything
drive(first, until_page=1); shortener.flush()
shortener._S.clear(); shortener._BY_USER.clear()
a = j(post("/api/init", {"t": first, "p": shortener.load(first)["steps"][1]["path"]}))
ok(a["ok"] and a["step"] == 2, "after a restart the link continues exactly where it was (page 2)")
shortener._S.clear(); shortener._recover()
ok(first in shortener._S, "recovery re-loads running links on start")
c.patch(f"/admin/api/shorteners/{SH['id']}", json={**base, "kind": "license", "daily_limit": 1, "max_active": 5, "cooldown": 0}, headers=H); shortener.forget()
U5 = 5006
P(cbq(U5, f"pl:{FREE}", "Day")); P(cbq(U5, f"py:{FREE}", "Day")); dcode = code_of(link_of(U5)); drive(dcode)
wait_for(lambda: shortener._S[dcode].get("delivered"))
h.CALLS.clear(); P(cbq(U5, f"pl:{FREE}", "Day")); P(cbq(U5, f"py:{FREE}", "Day"))
ok(h.sent("sendMessage", "today's limit", U5) or h.sent("editMessageText", "today's limit", U5) or h.sent("editMessageCaption", "today's limit", U5), "keys-per-day limit enforced")
c.patch(f"/admin/api/shorteners/{SH['id']}", json={**base, "kind": "license", "daily_limit": 0, "max_active": 3, "cooldown": 5, "notify_pages": False}, headers=H); shortener.forget()
U6 = 5007
P(cbq(U6, f"pl:{FREE}", "Quiet")); P(cbq(U6, f"py:{FREE}", "Quiet")); qcode = code_of(link_of(U6))
h.CALLS.clear(); drive(qcode, until_page=1); time.sleep(0.4)
ok(not h.sent("sendMessage", "Page 1 completed", U6), "per-page notices can be switched off")
CK.adv(1)
h.CALLS.clear(); P(cbq(U6, f"pl:{FREE}", "Quiet")); P(cbq(U6, f"py:{FREE}", "Quiet"))
ok(h.sent("sendMessage", "Please wait", U6) or h.sent("editMessageText", "Please wait", U6) or h.sent("sendMessage", "link", U6), "cooldown between links is enforced")

# ---- a switched-off shortener hides the plan and refuses new links
c.post(f"/admin/api/shorteners/{SH['id']}/toggle", json={"active": False}, headers=H); shortener.forget()
ok(FREE not in [p["id"] for p in catalog.plans_for(APP, "")], "shortener off -> the Free plan disappears from the bot")
ok(not db.get_list("/shorteners") or j(c.get("/admin/api/shorteners/options"))["items"] == [], "…and from the plan dropdown")
h.CALLS.clear(); P(cbq(5010, f"py:{FREE}", "Late"))
ok(not [x for x in shortener._S.values() if x.get("chat_id") == "5010"], "a stale button cannot start a link while it is off")
c.post(f"/admin/api/shorteners/{SH['id']}/toggle", json={"active": True}, headers=H); shortener.forget()

# ---- reseller bots never sell Free (short link) plans
from services import resellers
rs = resellers.create({"name": "Rob", "login_username": "rob", "password": "RobPass12345", "bot_token": "777:resellerbottokenresellerbottokenxx"}, admin=True) if hasattr(resellers, "create") else None
if rs:
    rctx = get_ctx(rs["id"])
    ok(rctx is not None and all(p["id"] != FREE for p in catalog.plans_for(APP, rs["id"])), "reseller shops do not list Free (short link) plans")
    from services import payments
    ok(payments.quote(rctx, {"chat_id": 1}, FREE) is None, "…and cannot start one")

# =============================================================== redirect kind
base_calls = len(h.CALLS)
pub = RD["link"].split("t=")[1]
a = j(post("/api/init", {"t": pub, "p": RD["link"].split(SITE)[1].split("?")[0]}, ip="7.7.7.7"))
ok(a["ok"] and "redirect" in a and a["redirect"].split("?t=")[1].startswith(RD["id"]) and a["redirect"].split("?t=")[1] != pub, "public link gives every visitor an own run")
rc = a["redirect"].split("?t=")[1]
fin = drive(rc, ip="7.7.7.7")
ok(fin["ok"] and fin["final"] is True and fin["next"] == "https://example.com/offer" == fin["redirect"], "last Generate -> redirect link")
ok(len(h.CALLS) == base_calls, "redirect kind sends NOTHING to Telegram")
shortener.flush()
rst = j(c.get(f"/admin/api/shorteners/{RD['id']}/stats?range=today"))["stats"]
ok(rst["started"] == 1 and rst["done"] == 1 and [f["completed"] for f in rst["funnel"]] == [1, 1], "redirect stats counted")
rs_ = j(c.get(f"/admin/api/shorteners/{RD['id']}/sessions"))["items"][0]
ok(rs_["status"] == "done" and rs_["redirect"] == "https://example.com/offer" and rs_["user"]["chat_id"] == "", "tracking row for an anonymous visitor")
ok(j(post("/api/init", {"t": pub + "x", "p": "/"}))["ok"] is False, "a wrong public code gets nothing")
flood = [j(post("/api/init", {"t": pub, "p": "/x"}, ip="8.8.8.8")) for _ in range(45)]
ok(any(x.get("err") == "slow_down" for x in flood), "one visitor cannot create unlimited runs")

# =============================================================== speed / concurrency
FAST = j(mk(name="Fast", kind="redirect", redirect_url="https://example.com/x", pages=3, top_min=0, top_max=0, bottom_min=0, bottom_max=0))["item"]
Config.GATE_MIN_SCROLL_GAP = 0
shortener._hits.clear()
sh_fast = shortener.shortener(FAST["id"])
codes = [shortener._new_session(sh_fast, ip=f"10.0.{i // 250}.{i % 250}")["code"] for i in range(300)]


def walk(code):
    """One visitor going through all 3 pages (init, both clicks, verify) -- like 300 browsers at the same moment."""
    try:
        cl = app.test_client(); s = shortener._S[code]; ip = f"10.2.{hash(code) % 200}.{hash(code) % 250}"
        hd = {**HD, "X-DH-IP": ip}
        post_ = lambda p, b: cl.post(p, json=b, headers=hd).get_json()
        for st in s["steps"]:
            a = post_("/api/init", {"t": code, "p": st["path"]})
            a = post_("/api/event", {"t": code, "ev": "top_click", "n": a["n"], "sig": {"trusted": True}})
            a = post_("/api/event", {"t": code, "ev": "bottom_click", "n": a["n"], "sig": {"trusted": True, "bottom": True}})
            v = post_("/api/verify", {"t": code, "n": a["n"], "sig": {"trusted": True}})
            if not v.get("ok"):
                return False
        return v.get("final") is True
    except Exception:
        return False


t0 = time.time()
with ThreadPoolExecutor(16) as ex:
    done = list(ex.map(walk, codes))
dt = time.time() - t0
ok(sum(done) == 300, f"300 visitors finished all 3 pages at the same time ({sum(done)}/300, {dt:.2f}s = {1200 / max(dt, 1e-6):.0f} gate calls/s)")
shortener.flush()
fs = j(c.get(f"/admin/api/shorteners/{FAST['id']}/stats?range=today"))["stats"]
ok(fs["started"] == 300 and fs["done"] == 300 and [f["completed"] for f in fs["funnel"]] == [300, 300, 300], "all 300 completions counted (no lost updates under concurrency)")
Config.GATE_MIN_SCROLL_GAP = 1.2

# a burst from one address is rate limited, other visitors are untouched
shortener._hits.clear()
burst = [post("/api/init", {"t": "x", "p": "/"}, ip="6.6.6.6").status_code for _ in range(100)]
ok(429 in burst and post("/api/init", {"t": "x", "p": "/"}, ip="6.6.6.7").status_code == 200, "per-visitor rate limit (429) without hurting others")

# ---- deleting a shortener that no plan uses
c.delete(f"/admin/api/plans/{FREE}", headers=H)
ok(c.delete(f"/admin/api/shorteners/{SH['id']}", headers=H).status_code == 200 and SH["id"] not in [x["id"] for x in j(c.get("/admin/api/shorteners"))["items"]], "shortener deleted once no plan uses it (soft delete)")
ok(db.get(f"/shorteners/{SH['id']}")["deleted"] is True and db.get(f"/sl_links/{SH['id']}/{CODE}") is not None, "history is kept in the database")
ok(j(post("/api/init", {"t": CODE, "p": "/"}))["ok"] is False, "a deleted shortener's links stop working")
print("ALL SHORTENER TESTS OK")
