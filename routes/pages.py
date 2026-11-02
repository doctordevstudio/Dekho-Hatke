import time

from flask import Blueprint, current_app, g, make_response, redirect, render_template, request, send_from_directory, session

from config import Config
from core import admin_gate as gate, db, security, utils
from core.audit import log_action

pages = Blueprint("pages", __name__)


PANELS = {
    "admin": {"title": "Admin", "login": "/login", "home": "/", "headline": "Welcome back", "sub": "Sign in to your control room.", "icon": "⚡"},
    "reseller": {"title": "Reseller", "login": "/reseller", "home": "/reseller", "headline": "Reseller panel", "sub": "Run your own store.", "icon": "🏪"},
    "seller": {"title": "Seller", "login": "/seller", "home": "/seller", "headline": "Seller panel", "sub": "Publish apps, earn on every sale.", "icon": "🚀"},
}


def _nocache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _login_page(error=None, status=200, panel="admin"):
    return _nocache(make_response(render_template("login.html", csrf=security.csrf_token(), error=error, panel=PANELS[panel],
                                                  panel_key=panel, action=Config.panel_login(panel)), status))


def _unauthorised():
    """What anybody sees at the admin address without a valid entrance link: nothing but a 401."""
    html = ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=robots content='noindex,nofollow'><title>Unauthorised</title>"
            "<body style='margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#8a8f98;"
            "font:600 18px system-ui,sans-serif;letter-spacing:.02em'>Unauthorised</body></html>")
    return _nocache(make_response(html, 401))


def _shell(panel):
    return _nocache(make_response(render_template("app.html", panel=PANELS[panel], panel_key=panel,
                                                  home=Config.panel_home(panel), login=Config.panel_login(panel))))


@pages.get("/")
def index():
    if not security.current_admin():
        return _unauthorised()  # opening the panel address directly never reveals a sign-in form
    return _shell("admin")


@pages.get("/go/<token>")
def admin_entrance(token):
    """The signed link from the bot's "Admin Login" menu item: unlocks the sign-in page for this browser."""
    ok, _ = security.limiter.hit(f"gate:{security.client_ip()}", 20, 60)
    found = gate.check_token(token) if ok else None
    if not found:
        return _unauthorised()
    gate.unlock(found[1])
    log_action("auth.entrance_link_used", "", source="panel", actor=f"admin-chat:{found[0]}")
    return redirect("/login")


# ------------------------------------------------------------------ reseller panel
@pages.route("/reseller", methods=["GET", "POST"])
def reseller_page():
    if request.method == "GET":
        return _shell("reseller") if security.current_reseller() else _login_page(panel="reseller")
    ip = security.client_ip()
    if not security.csrf_valid(request.form.get("csrf_token")):
        return _login_page("Session expired. Please try again.", 400, "reseller")
    ok, _ = security.limiter.hit(f"rlogin:{ip}", *Config.LOGIN_LIMIT)
    if not ok:
        return _login_page("Too many attempts. Wait a minute.", 429, "reseller")
    username = (request.form.get("username") or "").strip()[:64]
    password = (request.form.get("password") or "")[:256]
    key = "r:" + username
    wait = security.login_locked_for(key, ip)
    if wait:
        return _login_page(f"Too many failed attempts. Try again in {wait // 60 + 1} min.", 429, "reseller")
    rid = db.get(f"/reseller_usernames/{username.lower()}") if username else None
    r = db.get(f"/resellers/{rid}") if rid else None
    usable = bool(r) and not r.get("deleted") and r.get("active", True)
    if usable:
        good = security.verify_password(r["password_hash"], password)
    else:
        security.burn_password_check(password)
        good = False
    if not good:
        security.login_failed(key, ip)
        log_action("reseller.login_failed", f"username tried: {username[:32]}", source="reseller", actor="(anonymous)")
        time.sleep(0.4)
        return _login_page("Invalid username or password.", 401, "reseller")
    session.clear()
    session.permanent = True
    session["rid"], session["rsv"] = r["id"], r.get("session_version", 1)
    security.csrf_token()
    security.login_succeeded(key, ip)
    db.update(f"/resellers/{r['id']}", {"last_login": utils.now()})
    g.reseller = r
    log_action("reseller.login", "", source="reseller", actor=f"reseller:{r['name']}")
    return redirect(Config.panel_home("reseller"))


@pages.get("/reseller/logout")
def reseller_logout():
    session.clear()
    return redirect(Config.panel_home("reseller"))


# ------------------------------------------------------------------ seller panel (login / sign-up are JSON calls)
@pages.get("/seller")
def seller_page():
    if security.current_seller():
        return _shell("seller")
    return _nocache(make_response(render_template("seller_auth.html", csrf=security.csrf_token(),
                                                  home=Config.panel_home("seller"))))


@pages.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if security.current_admin():
            return redirect("/")
        return _login_page() if gate.unlocked() else _unauthorised()

    if not gate.unlocked():
        return _unauthorised()
    ip = security.client_ip()
    if not security.csrf_valid(request.form.get("csrf_token")):
        return _login_page("Session expired. Please try again.", 400)
    ok, _ = security.limiter.hit(f"login:{ip}", *Config.LOGIN_LIMIT)
    if not ok:
        return _login_page("Too many attempts. Wait a minute.", 429)

    username = (request.form.get("username") or "").strip()[:64]
    password = (request.form.get("password") or "")[:256]
    wait = security.login_locked_for(username, ip)
    if wait:
        return _login_page(f"Too many failed attempts. Try again in {wait // 60 + 1} min.", 429)

    aid = db.get(f"/admin_usernames/{username.lower()}") if username else None
    adm = db.get(f"/admins/{aid}") if aid else None
    usable = bool(adm) and not adm.get("deleted") and adm.get("active", True)
    if usable:
        good = security.verify_password(adm["password_hash"], password)
    else:
        security.burn_password_check(password)
        good = False
    if not good:
        security.login_failed(username, ip)
        log_action("auth.login_failed", f"username tried: {username[:32]}", source="panel", actor="(anonymous)")
        time.sleep(0.4)
        return _login_page("Invalid username or password.", 401)

    session.clear()  # prevent session fixation
    session.permanent = True
    session["aid"] = adm["id"]
    session["sv"] = adm.get("session_version", 1)
    security.csrf_token()
    security.login_succeeded(username, ip)
    db.update(f"/admins/{adm['id']}", {"last_login": utils.now()})
    g.admin = adm
    log_action("auth.login", "")
    return redirect("/")


@pages.get("/healthz")
def healthz():
    return "ok", 200, {"Cache-Control": "no-store"}


@pages.get("/favicon.ico")
def favicon():
    resp = send_from_directory(current_app.static_folder + "/img", "favicon.ico", mimetype="image/x-icon")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@pages.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}
