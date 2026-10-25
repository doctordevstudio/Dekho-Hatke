"""Two-domain mode (drdev. = all panels, api. = license API, Render address = Telegram only) + Cloudflare origin lock + logo + C++ docs.
Run: python tests/test_hosts.py     (own process: the domain settings are read from the environment at import time)"""
import os

SECRET = "origin-secret-for-tests-0123456789"
os.environ.update(ROOT_DOMAIN="dekhohatke.com", ORIGIN_SECRET=SECRET)

from harness import *  # noqa: F401,F403,E402
import harness as h  # noqa: E402
from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from core import db  # noqa: E402

app = create_app(background=False)
clients = {}


def req(host, path, method="get", secret=SECRET, headers=None, **kw):
    cl = clients.setdefault(host, app.test_client())
    hd = dict(headers or {})
    if secret:
        hd["X-Origin-Auth"] = secret
    return getattr(cl, method)(path, base_url=f"http://{host}", headers=hd, **kw)


PANEL, API, RENDER = "drdev.dekhohatke.com", "api.dekhohatke.com", "shop-xyz.onrender.com"
NF = lambda r: r.status_code == 404 and r.get_data(as_text=True) == "Not found"

# ---------------------------------------------------------------- config helpers
ok(Config.HOST_MODE and Config.HOSTS == {PANEL: "panel", API: "api"}, "two hosts derived from ROOT_DOMAIN")
ok(Config.api_base("http://x") == "http://api.dekhohatke.com", "API base is the api. sub-domain")
ok(Config.panel_url("seller") == "http://drdev.dekhohatke.com/seller" and Config.panel_url("admin") == "http://drdev.dekhohatke.com", "panel URLs live on drdev.")

# ---------------------------------------------------------------- origin lock on the two custom domains
ok(NF(req(PANEL, "/login", secret=None)), "panel host without the secret header -> 404")
ok(NF(req(PANEL, "/login", secret="wrong")), "panel host with a wrong secret -> 404")
from core import admin_gate  # noqa: E402
r = req(PANEL, "/")
ok(r.status_code == 401 and "Unauthorised" in r.get_data(as_text=True), "drdev. opened directly -> Unauthorised")
ok(req(PANEL, "/login").status_code == 401, "drdev./login without the bot's link -> Unauthorised")
ok(NF(req(PANEL, f"/go/{admin_gate.make_token(999)}", secret=None)), "the entrance link without the origin secret -> 404 (Cloudflare lock first)")
ok(NF(req(API, f"/go/{admin_gate.make_token(999)}")) and NF(req(RENDER, f"/go/{admin_gate.make_token(999)}", secret=None)), "the entrance link exists on the panel host only")
ok(req(PANEL, f"/go/{admin_gate.make_token(999)}").status_code == 302, "the bot's signed link unlocks the sign-in page")
ok(req(PANEL, "/login").status_code == 200, "panel host with the secret + link -> admin login page")
ok(NF(req(API, "/api/v1/app_000000000000/verify", method="post", secret=None, json={})), "api host without the secret -> 404")
ok(req(RENDER, "/healthz", secret=None).get_data(as_text=True) == "ok", "/healthz works on the Render address (health check / keep-alive)")
for path in ("/login", "/", "/seller", "/reseller", "/admin/api/me", "/api/v1/app_000000000000/verify", "/static/css/app.css"):
    ok(NF(req(RENDER, path, secret=None)) and NF(req(RENDER, path)), f"Render address {path} -> 404 (with or without the secret)")
ok(NF(req("evil.com", "/login")), "foreign Host header -> 404")
t = req(PANEL, "/login", headers={"X-Forwarded-Host": API}).get_data(as_text=True)
ok("Sign in" in t and "Welcome back" in t, "X-Forwarded-Host is ignored")

# ---------------------------------------------------------------- Telegram goes straight to the Render address
r = req(RENDER, f"/telegram/{Config.path_secret('')}", method="post", secret=None, json={})
ok(r.status_code == 403, "Render address: webhook with the right secret URL but no Telegram header -> 403 (routed, not 404)")
r = req(RENDER, "/telegram/not-the-secret", method="post", secret=None, json={})
ok(r.status_code == 404, "Render address: wrong webhook URL -> 404")
ok(NF(req(PANEL, f"/telegram/{Config.path_secret('')}", method="post", json={})), "webhooks are NOT served on the panel host")
ok(NF(req(API, f"/telegram/{Config.path_secret('')}", method="post", json={})), "webhooks are NOT served on the api host")
upd = {"update_id": 424242, "message": {"message_id": 1, "from": {"id": 4711, "first_name": "Tele"}, "chat": {"id": 4711, "type": "private"}, "text": "/start"}}
h.CALLS.clear()
r = req(RENDER, f"/telegram/{Config.path_secret('')}", method="post", secret=None, json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": Config.header_secret("")})
ok(r.status_code == 200, "a real Telegram update (right URL + header, no Cloudflare secret) is accepted")
import time  # noqa: E402
for _ in range(50):
    if any(str(c[1].get("chat_id")) == "4711" for c in h.CALLS):
        break
    time.sleep(0.1)
ok(any(str(c[1].get("chat_id")) == "4711" for c in h.CALLS), "...and the bot answers it")

# ---------------------------------------------------------------- the panel host serves all three panels, nothing else
page = req(PANEL, "/login").get_data(as_text=True)
ok("/static/img/logo-login.jpg" in page and 'rel="manifest"' in page and "/favicon.ico" in page and "Dekho Hatke" in page, "login page carries the Dekho Hatke logo, favicon, manifest and name")
r = req(PANEL, "/seller")
ok(r.status_code == 200 and "Seller panel" in r.get_data(as_text=True) and 'data-home="/seller"' in r.get_data(as_text=True), "drdev./seller = seller sign-in")
t = req(PANEL, "/reseller").get_data(as_text=True)
ok('action="/reseller"' in t and "Reseller panel" in t, "drdev./reseller = reseller sign-in")
ok(req(PANEL, "/seller/api/me").status_code == 401 and req(PANEL, "/reseller/api/me").status_code == 401 and req(PANEL, "/admin/api/me").status_code == 401, "all three panel APIs live on drdev.")
ok(req(PANEL, "/static/css/app.css").status_code == 200 and req(PANEL, "/static/css/app.css").headers.get("Cache-Control", "").startswith("public"), "static files served and edge-cacheable")
for path in ("/api/v1/app_000000000000/verify", "/reseller/logout/x", "/.env", "/admin", "/go", "/go/"):
    ok(NF(req(PANEL, path)), f"drdev.{path} -> plain 404")
r = req(PANEL, "/reseller", method="post", data={"username": "nobody", "password": "wrong-password-1", "csrf_token": t.split('name="csrf_token" value="')[1].split('"')[0]})
ok(r.status_code == 401 and "Invalid username or password" in r.get_data(as_text=True), "reseller login POST is handled")

# ---------------------------------------------------------------- the api host: license verification only
r = req(API, "/api/v1/app_000000000000/verify", method="post", json={})
ok(r.status_code == 404 and r.get_json() and r.get_json().get("code") == "APP_NOT_FOUND", "api. reaches the license API (JSON answer)")
for path in ("/", "/login", "/seller", "/reseller", "/admin/api/me", "/seller/api/me", "/static/css/app.css", "/media/img/x"):
    ok(NF(req(API, path)), f"api.{path} -> plain 404")

# ---------------------------------------------------------------- admin session on the panel host
csrf = page.split('name="csrf_token" value="')[1].split('"')[0]
r = req(PANEL, "/login", method="post", data={"username": "boss", "password": "TestPassw0rd123", "csrf_token": csrf})
ok(r.status_code == 302 and r.headers["Location"].endswith("/"), "admin login redirects to '/'")
shell = req(PANEL, "/").get_data(as_text=True)
ok('data-panel="admin"' in shell and 'data-login="/login"' in shell and "/static/img/mark.png" in shell, "admin shell with logo mark")
me = req(PANEL, "/admin/api/me").get_json()
ok(me and me.get("csrf"), "admin API works on drdev.")
ok(req(PANEL, "/seller/api/me").status_code == 401 and req(PANEL, "/reseller/api/me").status_code == 401, "an admin session never opens the seller / reseller APIs")
ok(NF(req(API, "/admin/api/me")), "admin API is not reachable on the api host")
apps = req(PANEL, "/admin/api/apps").get_json().get("apps", [])
ok(all(a["endpoint"].startswith("http://api.dekhohatke.com/api/v1/") for a in apps) or not apps, "app endpoints use the api. sub-domain")


# ---------------------------------------------------------------- real client IP comes from CF-Connecting-IP (only with the secret)
def anon_login(user, **kw):
    cl = app.test_client()  # fresh cookie jar -> its own CSRF token
    cl.get(f"/go/{admin_gate.make_token(999)}", base_url=f"http://{PANEL}", headers={"X-Origin-Auth": SECRET})
    t = cl.get("/login", base_url=f"http://{PANEL}", headers={"X-Origin-Auth": SECRET}).get_data(as_text=True)
    tok = t.split('name="csrf_token" value="')[1].split('"')[0]
    hd = {"X-Origin-Auth": SECRET, **kw.pop("headers", {})}
    return cl.post("/login", base_url=f"http://{PANEL}", headers=hd, data={"username": user, "password": "wrong-password-1", "csrf_token": tok})


def last_fail_ip():
    rows = sorted([x for x in db.get_list("/logs") if x.get("action") == "auth.login_failed"], key=lambda x: x["id"])
    return rows[-1]["ip"] if rows else None


anon_login("ghost", headers={"CF-Connecting-IP": "203.0.113.7"})
ok(last_fail_ip() == "203.0.113.7", "client IP read from CF-Connecting-IP")
anon_login("ghost2", headers={"CF-Connecting-IP": "not-an-ip"})
ok(last_fail_ip() != "not-an-ip", "garbage CF-Connecting-IP is ignored")
anon_login("ghost3", headers={"CF-Connecting-IP": "2001:db8::1"})
ok(last_fail_ip() == "2001:db8::1", "IPv6 visitor addresses work too")
r = app.test_client().post("/login", base_url=f"http://{PANEL}", headers={"CF-Connecting-IP": "198.51.100.9"}, data={"username": "ghost4", "password": "x"})
ok(r.status_code == 404, "spoofing CF-Connecting-IP without the secret gets nowhere")

# ---------------------------------------------------------------- logo + manifest + docs
ok(req(PANEL, "/favicon.ico").status_code == 200, "favicon served")
m = req(PANEL, "/static/manifest.webmanifest")
ok(m.status_code == 200 and "Dekho Hatke" in m.get_data(as_text=True), "web manifest carries the brand")
for f in ("logo-login.jpg", "logo-welcome.jpg", "mark.png", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
    ok(req(PANEL, f"/static/img/{f}").status_code == 200, f"logo asset {f}")
docs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static/js/pages/s_docs.js")).read()
ok("smali" not in docs.lower() and "cpp:" in docs and "#include <curl/curl.h>" in docs and "['cpp', 'C++']" in docs, "API docs: smali removed, C++ added")

print("ALL HOST TESTS OK")
