"""Browser check: seeds data (via test_flows), serves the app, logs into the ADMIN, RESELLER and SELLER panels with Chromium,
visits every page, opens drill-down cards, reports console / CSP errors and saves screenshots.   python tests/ui_check.py <out_dir>"""
import io
import os
import runpy
import sys
import threading
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ui_shots"
os.makedirs(out, exist_ok=True)
ns = runpy.run_path(os.path.join(here, "test_flows.py"))
app, db, c, H, sc, SH, PNG, j = ns["app"], ns["db"], ns["c"], ns["H"], ns["sc"], ns["SH"], ns["PNG"], ns["j"]
from core import utils  # noqa: E402

# a second seller app waiting for review (so the live-check screen has something to show)
r = sc.post("/seller/api/apps", data={"name": "Second App", "new_category": "Tools", "description": "Handy tools for daily use", "package_name": "com.sam.second", "logo": (io.BytesIO(PNG), "l.png")}, headers=SH, content_type="multipart/form-data")
app2 = j(r)
import harness as h  # noqa: E402
h.bot_file(777, "doc", "BQACAgUAAxkBAAIUiApkId000000000001", "s.apk", 5000); h.bot_file(777, "video", "BAACAgUAAxkBAAIUiVidId000000000001", size=3000)
apk = j(sc.post("/seller/api/files/check", json={"kind": "apk", "file_id": "BQACAgUAAxkBAAIUiApkId000000000001"}, headers=SH))
logo = j(sc.post("/seller/api/uploads/logo", data={"file": (io.BytesIO(PNG), "l.png")}, headers=SH, content_type="multipart/form-data"))
_vid = j(sc.post("/seller/api/files/check", json={"kind": "preview_video", "file_id": "BAACAgUAAxkBAAIUiVidId000000000001"}, headers=SH))
# the seller must pass the live tests (stored as a draft) before submitting
_D = f"/seller/api/apps/{app2['id']}/draft"
sc.put(_D, json={"step": 5, "data": {"name": "Second App"}}, headers=SH)
_k = j(sc.post(f"{_D}/test-key", json={"hours": 2}, headers=SH))["key"]
_pub = lambda dev: app.test_client().post(f"/api/v1/{app2['id']}/verify", json={"license_key": _k, "device_id": dev}, headers={"X-API-Key": app2["api_key"]})
_pub("sdev-AAAA-1111"); _pub("sdev-BBBB-2222")
sc.post(f"{_D}/maintenance", json={"enabled": True}, headers=SH); _pub("sdev-AAAA-1111"); sc.post(f"{_D}/maintenance", json={"enabled": False}, headers=SH)
sc.post(f"{_D}/test-keys/{_k}/revoke", headers=SH); _pub("sdev-AAAA-1111")
sc.post(f"/seller/api/apps/{app2['id']}/publish", json={"name": "Second App", "category_id": app2["category_id"], "description": "Handy tools for daily use", "features": "Fast\nSmall", "preview_video_upload": _vid["id"],
        "plans": [{"amount": 50, "validity_value": 1, "validity_unit": "days"}], "apk_upload": apk["id"], "logo_media": logo["media"]}, headers=SH)
sub = j(c.get("/admin/api/submissions?status=pending"))["items"][0]["id"]
key = j(c.post(f"/admin/api/submissions/{sub}/test-key", json={"hours": 24}, headers=H))["key"]
app.test_client().post(f"/api/v1/{app2['id']}/verify", json={"license_key": key, "device_id": "device-AAAA-1111"}, headers={"X-API-Key": app2["api_key"]})

# a reseller
r = c.post("/admin/api/resellers", data={"name": "Rita Reseller", "username": "rita", "chat_id": "4242", "bot_token": "654321:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "bot_name": "Rita Bot",
           "commission_pct": "10", "due_limit": "5000", "login_username": "rita", "password": "ResellerPass123"}, headers=H, content_type="multipart/form-data")
assert r.status_code == 201, r.get_json()

# extra txns for the UPI page
now = utils.now()
upi = db.get_list("/upis")[0]
base = {"user_id": "111", "amount": 199, "app_id": db.get_list("/apps")[0]["id"], "app_name": "Super Tool", "plan_id": "p", "plan_label": "30 Days", "validity_value": 30,
        "validity_unit": "days", "upi_record_id": upi["id"], "upi_id": upi["upi_id"], "created_at": now, "expires_at": now + 540,
        "user": {"chat_id": "111", "full_name": "Rahul K", "username": "u111", "profile_image_url": "", "profile_file_id": ""}}
db.set("/txns/TXNPENDING01", {**base, "id": "TXNPENDING01", "status": "pending"})

# shortener data for the new page
from services import shortener  # noqa: E402
c.put("/admin/api/shorteners/config", json={"site_url": "https://dekhohatke.com", "pages": "/blog/a/\n/blog/b/\n/blog/c/"}, headers=H)
_sl = j(c.post("/admin/api/shorteners", json={"name": "Free key · 3 pages", "kind": "license", "pages": 3, "top_min": 15, "top_max": 25, "bottom_min": 10, "bottom_max": 20}, headers=H))["item"]
_rd = j(c.post("/admin/api/shorteners", json={"name": "Promo redirect", "kind": "redirect", "redirect_url": "https://example.com/offer", "pages": 2, "top_min": 5, "top_max": 5, "bottom_min": 5, "bottom_max": 5}, headers=H))["item"]
_sh = shortener.shortener(_sl["id"])
for _i, (_cur, _st) in enumerate([(3, "done"), (1, "expired"), (2, "active"), (0, "expired")]):
    _s = shortener._new_session(_sh, user={"chat_id": f"90{_i}", "full_name": f"Visitor {_i}", "username": f"vis{_i}"})
    _s.update(cur=min(_cur, 2), status=_st, first_visit=_s["created"] + 3, pt=[_s["created"] + 40 * (k + 1) for k in range(_cur)], spent=120)
    if _st == "done":
        _s.update(done_at=_s["created"] + 130, delivered=True, lic_key="ABCD1234EFGH5678IJ")
        shortener._bump(_sl["id"], "done"); [shortener._bump(_sl["id"], f"p{k}") for k in (1, 2, 3)]
    elif _st == "expired":
        shortener._bump(_sl["id"], f"left{_cur}")
shortener.flush()

from werkzeug.serving import make_server  # noqa: E402

srv = make_server("127.0.0.1", 5055, app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)

from playwright.sync_api import sync_playwright  # noqa: E402

problems = []
ADMIN = ["#/", "#/users", "#/earning", "#/upi", "#/plans", "#/purchase", "#/balance", "#/checkin", "#/spin", "#/referral", "#/leaderboard", "#/broadcast", "#/promotion",
         "#/reseller", "#/seller", "#/shortener", "#/logs", "#/settings"]
RESELLER = ["#/", "#/users", "#/earning", "#/upi", "#/prices", "#/referral", "#/balance", "#/due", "#/support", "#/settings"]
SELLER = ["#/", "#/create", "#/publish", "#/docs", "#/apps", "#/wallet", "#/broadcast", "#/settings"]


def run(label, ctx, hashes, tag):
    page = ctx.new_page()
    page.on("console", lambda m: problems.append(f"[{label}/{tag}] console.{m.type}: {m.text}") if m.type in ("error", "warning") and "fonts.g" not in m.text and "ERR_" not in m.text and "Failed to load resource" not in m.text else None)
    page.on("pageerror", lambda e: problems.append(f"[{label}/{tag}] pageerror: {e}"))
    return page


def sweep(page, label, tag, hashes):
    for h in hashes:
        page.evaluate(f"location.hash = '{h}'")
        page.wait_for_timeout(700)
        txt = page.inner_text("#view")
        if "💥" in txt or "Page not found" in txt:
            problems.append(f"[{label}/{tag}] page {h} shows an error: {txt[:120]!r}")
        page.screenshot(path=f"{out}/{label}-{tag}-{h.strip('#/') or 'home'}.png")


def screens_tab(page, label, tag, expect):
    page.evaluate("location.hash = '#/settings'"); page.wait_for_timeout(800)
    page.click("button:has-text('Bot screens')"); page.wait_for_timeout(900)
    n = len(page.query_selector_all("#sc details"))
    if n != expect:
        problems.append(f"[{label}/{tag}] Bot screens shows {n} screens, expected {expect}")
        return
    page.click("#sc details:first-child summary"); page.wait_for_timeout(300)
    if not page.query_selector("#sc details[open] textarea"):
        problems.append(f"[{label}/{tag}] Bot screens card has no editor")
    page.screenshot(path=f"{out}/{label}-{tag}-bot-screens.png")


def drill_click(page, label, tag, selector, name):
    el = page.query_selector(selector)
    if not el:
        problems.append(f"[{label}/{tag}] no clickable card {name}")
        return
    el.click()
    page.wait_for_timeout(700)
    if not page.query_selector(".modal-back"):
        problems.append(f"[{label}/{tag}] card {name} did not open a list")
    elif "💥" in page.inner_text(".modal-back"):
        problems.append(f"[{label}/{tag}] card {name} list failed: {page.inner_text('.modal-back')[:120]!r}")
    page.screenshot(path=f"{out}/{label}-{tag}-drill-{name}.png")
    page.keyboard.press("Escape")


with sync_playwright() as p:
    b = p.chromium.launch()
    for label, vp in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 390, "height": 844})):
        # ---------------- admin
        ctx = b.new_context(viewport=vp)
        page = run(label, ctx, ADMIN, "admin")
        from core import admin_gate
        page.goto("http://127.0.0.1:5055/go/" + admin_gate.make_token(999))  # the bot's "Admin Login" link
        page.fill("input[name=username]", "boss"); page.fill("input[name=password]", "TestPassw0rd123"); page.click("button[type=submit]")
        page.wait_for_selector("#meName:not(:has-text('…'))", timeout=8000)
        sweep(page, label, "admin", ADMIN)
        screens_tab(page, label, "admin", 15)
        page.evaluate("location.hash = '#/'"); page.wait_for_timeout(900)
        drill_click(page, label, "admin", "#blocks [data-drill^='earning:today']", "earning")
        drill_click(page, label, "admin", "#blocks [data-drill^='active:all']", "active")
        drill_click(page, label, "admin", "#blocks [data-drill^='sales:all']", "sales")
        page.evaluate("location.hash = '#/apps/" + db.get_list('/apps')[0]['id'] + "'"); page.wait_for_timeout(900)
        page.screenshot(path=f"{out}/{label}-admin-app.png")
        page.evaluate(f"location.hash = '#/seller/review/{sub}'"); page.wait_for_timeout(1200)
        page.screenshot(path=f"{out}/{label}-admin-review.png", full_page=True)
        if "check pass" not in page.inner_text("#live"):
            problems.append(f"[{label}/admin] review screen does not show 'logic check pass'")
        page.evaluate("location.hash = '#/promotion'"); page.wait_for_timeout(900)
        ap = page.query_selector("#apps [data-app]")
        if ap:
            ap.click(); page.wait_for_timeout(900); page.click("#mode [data-m=users]"); page.wait_for_timeout(700)
            page.screenshot(path=f"{out}/{label}-admin-promotion.png", full_page=True)
        else:
            problems.append(f"[{label}/admin] promotion page lists no app")
        page.evaluate("location.hash = '#/plans'"); page.wait_for_timeout(700)
        pk = page.query_selector("#body [data-app]")
        if pk:
            pk.click(); page.wait_for_timeout(500); page.screenshot(path=f"{out}/{label}-admin-plans-app.png")
        # ---- shortener: history -> tracking -> create form -> plan form with the dropdown, light + dark switch contrast
        page.evaluate("location.hash = '#/shortener'"); page.wait_for_timeout(900)
        if "Free key" not in page.inner_text("#body"):
            problems.append(f"[{label}/admin] shortener history lists no shortener")
        page.click("[data-open]"); page.wait_for_timeout(1000)
        page.screenshot(path=f"{out}/{label}-admin-shortener-track.png", full_page=True)
        if "Where do users stop" not in page.inner_text("#body") or "Left after completing page" not in page.inner_text("#body"):
            problems.append(f"[{label}/admin] tracking page has no drop-off funnel")
        page.click("[data-k=expired]") if page.query_selector("[data-k=expired]") else None
        page.wait_for_timeout(500)
        page.click("#rb [data-v=custom]"); page.wait_for_timeout(300)
        page.screenshot(path=f"{out}/{label}-admin-shortener-range.png")
        page.evaluate("location.hash = '#/plans'"); page.wait_for_timeout(300); page.evaluate("location.hash = '#/shortener'"); page.wait_for_timeout(800)
        page.click("#tabs [data-k=create]"); page.wait_for_timeout(600)
        page.screenshot(path=f"{out}/{label}-admin-shortener-create.png", full_page=True)
        page.evaluate("location.hash = '#/plans'"); page.wait_for_timeout(700)
        pk = page.query_selector("#body [data-app]")
        if pk:
            pk.click(); page.wait_for_timeout(400)
            page.click("#add"); page.wait_for_timeout(500)
            if "free via short link" not in page.inner_text(".modal").lower():
                problems.append(f"[{label}/admin] plan form has no short-link dropdown")
            for theme in ("light", "dark"):
                page.evaluate(f"document.documentElement.dataset.theme = '{theme}'"); page.wait_for_timeout(250)
                lum = page.evaluate("""() => { const i = document.querySelector('.modal .switch input:not(:checked) + i'); if (!i) return -1;
                  const m = getComputedStyle(i).backgroundColor.match(/[\\d.]+/g).map(Number); const a = m[3] === undefined ? 1 : m[3];
                  const bg = getComputedStyle(document.querySelector('.modal')).backgroundColor.match(/[\\d.]+/g).map(Number);
                  const mix = [0, 1, 2].map((k) => m[k] * a + bg[k] * (1 - a)); return Math.abs(mix.reduce((x, y) => x + y, 0) - bg.slice(0, 3).reduce((x, y) => x + y, 0)) / 3; }""")
                if lum < 25:
                    problems.append(f"[{label}/admin] switch OFF state is not visible against the dialog in the {theme} theme (contrast {lum})")
                page.screenshot(path=f"{out}/{label}-admin-plan-form-{theme}.png")
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
            page.keyboard.press("Escape")
        page.fill("#globalSearch", "Rahul"); page.press("#globalSearch", "Enter"); page.wait_for_timeout(1200)
        page.screenshot(path=f"{out}/{label}-admin-search.png")
        ctx.close()
        # ---------------- reseller
        ctx = b.new_context(viewport=vp)
        page = run(label, ctx, RESELLER, "reseller")
        page.goto("http://127.0.0.1:5055/reseller")
        page.fill("input[name=username]", "rita"); page.fill("input[name=password]", "ResellerPass123"); page.click("button[type=submit]")
        page.wait_for_selector("#meName:not(:has-text('…'))", timeout=8000)
        sweep(page, label, "reseller", RESELLER)
        screens_tab(page, label, "reseller", 13)
        ctx.close()
        # ---------------- seller
        ctx = b.new_context(viewport=vp)
        page = run(label, ctx, SELLER, "seller")
        page.goto("http://127.0.0.1:5055/seller")
        page.screenshot(path=f"{out}/{label}-seller-login.png")
        page.fill("input[name=username]", "sam"); page.fill("input[name=password]", "SellerPass123"); page.click("#go")
        page.wait_for_selector("#meName:not(:has-text('…'))", timeout=8000)
        sweep(page, label, "seller", SELLER)
        # the step-by-step publish dialog
        page.evaluate("location.hash = '#/publish'"); page.wait_for_timeout(900)
        btn = page.query_selector("[data-go]")
        if not btn:
            problems.append(f"[{label}/seller] publish page has no app to publish")
        else:
            btn.click()
            try:
                page.wait_for_selector("[data-start]", timeout=4000)
            except Exception:
                page.screenshot(path=f"{out}/{label}-seller-wizard-FAIL.png")
                problems.append(f"[{label}/seller] wizard did not open: {page.inner_text('body')[:300]!r}")
                btn = None
        if btn:
            page.screenshot(path=f"{out}/{label}-seller-wizard-intro.png")
            page.click("[data-start]"); page.wait_for_timeout(500)
            page.click("[data-jump='1']"); page.wait_for_timeout(400)
            for n in range(1, 7):
                page.screenshot(path=f"{out}/{label}-seller-wizard-step{n}.png")
                if n < 6:
                    page.click("[data-next]"); page.wait_for_timeout(500)
                    if page.query_selector(f".wz-dot.on[data-jump='{n}']"):
                        problems.append(f"[{label}/seller] wizard stuck on step {n}: {page.inner_text('.toast, #toasts')[:100] if page.query_selector('.toast, #toasts') else ''}")
                        break
            if page.query_selector(".modal-back"):
                page.click(".modal-back .head [data-x]"); page.wait_for_timeout(400)
            if page.query_selector(".modal-back"):
                problems.append(f"[{label}/seller] wizard dialog did not close")
                page.evaluate("document.querySelectorAll('.modal-back').forEach(e => e.remove())")
        page.evaluate("location.hash = '#/'"); page.wait_for_timeout(900)
        drill_click(page, label, "seller", "#blocks [data-drill^='earn:all']", "earn")
        drill_click(page, label, "seller", "#top [data-drill='live']", "live")
        page.evaluate(f"location.hash = '#/apps/{app2['id']}'"); page.wait_for_timeout(900)
        page.screenshot(path=f"{out}/{label}-seller-appdetail.png")
        ctx.close()
    b.close()
srv.shutdown()
print("\nUI problems:" if problems else "\nNo console/CSP errors", *problems, sep="\n")
