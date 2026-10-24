"""Sub-domain mode + Cloudflare origin lock + logo + C++ docs.   Run: python tests/test_hosts.py
Runs in its own process because the domain settings are read from the environment at import time."""
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


ADMIN, SELLER, RESELLER, API = "admin.dekhohatke.com", "seller.dekhohatke.com", "reseller.dekhohatke.com", "api.dekhohatke.com"

# ---------------------------------------------------------------- config helpers
ok(Config.HOST_MODE and Config.HOSTS == {ADMIN: "admin", SELLER: "seller", RESELLER: "reseller", API: "api"}, "four hosts derived from ROOT_DOMAIN")
ok(Config.api_base("http://x") == "http://api.dekhohatke.com", "API base is the api. sub-domain")
ok(Config.panel_url("seller") == "http://seller.dekhohatke.com", "seller panel URL is its own sub-domain")

# ---------------------------------------------------------------- origin lock
ok(req(ADMIN, "/login", secret=None).status_code == 404, "no secret header -> 404 (Render address is not usable directly)")
ok(req(ADMIN, "/login", secret="wrong").status_code == 404, "wrong secret -> 404")
ok(req(ADMIN, "/login").status_code == 200, "correct secret -> login page")
ok(req("shop-xyz.onrender.com", "/healthz", secret=None).get_data(as_text=True) == "ok", "/healthz works without secret (keep-alive / Render health check)")
ok(req("shop-xyz.onrender.com", "/login").status_code == 404, "unknown host (raw onrender.com) -> 404 even with the secret")
ok(req("evil.com", "/login").status_code == 404, "foreign Host header -> 404")
ok(req(ADMIN, "/login", headers={"X-Forwarded-Host": SELLER}).get_data(as_text=True).count("Sign in") >= 1
   and "seller" not in req(ADMIN, "/login", headers={"X-Forwarded-Host": SELLER}).get_data(as_text=True).lower().split("<title>")[1].split("</title>")[0],
   "X-Forwarded-Host is ignored")

# ---------------------------------------------------------------- every host serves only its own panel
page = req(ADMIN, "/login").get_data(as_text=True)
ok("/static/img/logo-login.jpg" in page and 'rel="manifest"' in page and "/favicon.ico" in page, "admin login page carries the Dekho Hatke logo, favicon and manifest")
ok("Dekho Hatke" in page, "branding name on the login page")
for host, path in ((ADMIN, "/seller"), (ADMIN, "/seller/api/me"), (ADMIN, "/reseller"), (ADMIN, "/reseller/api/me"), (ADMIN, "/api/v1/x/verify"),
                   (ADMIN, "/telegram/abc"),
                   (SELLER, "/admin/api/me"), (SELLER, "/reseller/api/me"), (SELLER, "/login"), (SELLER, "/reseller"),
                   (RESELLER, "/admin/api/me"), (RESELLER, "/seller/api/me"), (RESELLER, "/login"), (RESELLER, "/seller"),
                   (API, "/"), (API, "/login"), (API, "/admin/api/me"), (API, "/seller"), (API, "/reseller"), (API, "/static/css/app.css")):
    r = req(host, path)
    ok(r.status_code == 404 and r.get_data(as_text=True) == "Not found", f"{host}{path} -> plain 404")

# seller panel lives at "/" on seller.*
r = req(SELLER, "/")
ok(r.status_code == 200 and "Seller panel" in r.get_data(as_text=True) and 'data-home="/"' in r.get_data(as_text=True), "seller.* serves the seller sign-in at '/'")
r = req(SELLER, "/seller")
ok(r.status_code == 302 and r.headers["Location"] == "/", "old /seller path bounces to '/' on the seller sub-domain")
ok(req(SELLER, "/seller/api/me").status_code == 401, "seller API reachable on seller.* (needs login)")
ok(req(SELLER, "/static/css/app.css").status_code == 200, "static files served on panel hosts")
ok(req(SELLER, "/static/css/app.css").headers.get("Cache-Control", "").startswith("public"), "static files are edge-cacheable")

# reseller panel lives at "/" on reseller.*
r = req(RESELLER, "/")
t = r.get_data(as_text=True)
ok(r.status_code == 200 and 'action="/"' in t and "Reseller panel" in t, "reseller.* serves the reseller sign-in at '/', form posts to '/'")
r = req(RESELLER, "/", method="post", data={"username": "nobody", "password": "wrong-password-1", "csrf_token": t.split('name="csrf_token" value="')[1].split('"')[0]})
ok(r.status_code == 401 and "Invalid username or password" in r.get_data(as_text=True), "reseller login POST to '/' is handled")
ok(req(RESELLER, "/reseller").status_code == 302, "old /reseller path bounces to '/'")

# api host: license API + webhook only
r = req(API, "/api/v1/app_000000000000/verify", method="post", json={})
ok(r.status_code == 404 and r.get_json() and r.get_json().get("code") == "APP_NOT_FOUND", "api.* reaches the license API (JSON answer)")
r = req(API, "/telegram/not-the-secret", method="post", json={})
ok(r.status_code == 404, "api.* reaches the webhook route (wrong secret -> 404 from the app)")
r = req(API, f"/telegram/{Config.path_secret('')}", method="post", json={})
ok(r.status_code == 403, "webhook with right path but no Telegram header -> 403 from the app (so it IS routed)")

# admin: login -> shell -> API, all on admin.*
page = req(ADMIN, "/login").get_data(as_text=True)
csrf = page.split('name="csrf_token" value="')[1].split('"')[0]
r = req(ADMIN, "/login", method="post", data={"username": "boss", "password": "TestPassw0rd123", "csrf_token": csrf})
ok(r.status_code == 302 and r.headers["Location"].endswith("/"), "admin login redirects to '/' on admin.*")
shell = req(ADMIN, "/").get_data(as_text=True)
ok('data-panel="admin"' in shell and 'data-login="/login"' in shell and "/static/img/mark.png" in shell, "admin shell with logo mark and login path")
me = req(ADMIN, "/admin/api/me").get_json()
ok(me and me.get("csrf"), "admin API works on admin.*")
ok(req(SELLER, "/seller/api/me").status_code == 401 and req(API, "/admin/api/me").status_code == 404, "admin session never opens another host")

# the endpoint shown to sellers points at the api sub-domain
H = {"X-CSRF-Token": me["csrf"]}
apps = req(ADMIN, "/admin/api/apps").get_json().get("apps", [])
ok(all(a["endpoint"].startswith("http://api.dekhohatke.com/api/v1/") for a in apps) or not apps, "app endpoints use the api. sub-domain")

# ---------------------------------------------------------------- real client IP comes from CF-Connecting-IP (only with the secret)
def anon_login(user, **kw):
    cl = app.test_client()  # fresh cookie jar -> its own CSRF token
    t = cl.get("/login", base_url=f"http://{ADMIN}", headers={"X-Origin-Auth": SECRET}).get_data(as_text=True)
    tok = t.split('name="csrf_token" value="')[1].split('"')[0]
    hd = {"X-Origin-Auth": SECRET, **kw.pop("headers", {})} if kw.pop("secret", True) else dict(kw.pop("headers", {}))
    return cl.post("/login", base_url=f"http://{ADMIN}", headers=hd, data={"username": user, "password": "wrong-password-1", "csrf_token": tok})


def last_fail_ip():
    rows = sorted([x for x in db.get_list("/logs") if x.get("action") == "auth.login_failed"], key=lambda x: x["id"])
    return rows[-1]["ip"] if rows else None


anon_login("ghost", headers={"CF-Connecting-IP": "203.0.113.7"})
ok(last_fail_ip() == "203.0.113.7", "client IP read from CF-Connecting-IP")
anon_login("ghost2", headers={"CF-Connecting-IP": "not-an-ip"})
ok(last_fail_ip() != "not-an-ip", "garbage CF-Connecting-IP is ignored")
anon_login("ghost3", headers={"CF-Connecting-IP": "2001:db8::1"})
ok(last_fail_ip() == "2001:db8::1", "IPv6 visitor addresses work too")
cl = app.test_client()
r = cl.post("/login", base_url=f"http://{ADMIN}", headers={"CF-Connecting-IP": "198.51.100.9"}, data={"username": "ghost4", "password": "x"})
ok(r.status_code == 404, "spoofing CF-Connecting-IP without the secret gets nowhere")

# ---------------------------------------------------------------- logo + manifest + docs
ok(req(ADMIN, "/favicon.ico").status_code == 200, "favicon served")
m = req(ADMIN, "/static/manifest.webmanifest")
ok(m.status_code == 200 and "Dekho Hatke" in m.get_data(as_text=True), "web manifest carries the brand")
for f in ("logo-login.jpg", "logo-welcome.jpg", "mark.png", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
    ok(req(ADMIN, f"/static/img/{f}").status_code == 200, f"logo asset {f}")
docs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static/js/pages/s_docs.js")).read()
ok("smali" not in docs.lower() and "cpp:" in docs and "#include <curl/curl.h>" in docs and "['cpp', 'C++']" in docs, "API docs: smali removed, C++ added")

print("ALL HOST TESTS OK")
