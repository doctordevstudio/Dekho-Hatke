"""Application factory."""
import hashlib
import gzip
import logging
import secrets
import threading
import time
from datetime import timedelta

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from core import db, security, utils
from core.errors import ApiError
from core.hosts import HostRouter

log = logging.getLogger("app")

MB = 1024 * 1024
SIZE_CAPS = {"default": 3 * MB, "broadcast_upload": 55 * MB, "max": 56 * MB}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "style-src-attr 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' https: data:; media-src 'self' https:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def seed_admin():
    if any(not a.get("deleted") for a in db.get_list("/admins")):
        return
    pw, generated = Config.ADMIN_PASSWORD, False
    if not pw:
        pw, generated = secrets.token_urlsafe(12) + "a1", True
    problem = security.password_problem(pw)
    aid, username = utils.new_id("adm"), Config.ADMIN_USERNAME
    db.set(f"/admin_usernames/{username.lower()}", aid)
    db.set(f"/admins/{aid}", {"id": aid, "username": username, "username_lower": username.lower(),
                              "password_hash": security.hash_password(pw), "role": "admin", "active": True,
                              "session_version": 1, "created_at": utils.now(), "created_by": "system"})
    log.warning("Created first admin '%s'.", username)
    if generated:
        log.warning("GENERATED ADMIN PASSWORD (shown once, change it in Settings): %s", pw)
    if problem:
        log.warning("The ADMIN_PASSWORD you set is weak (%s). Change it after logging in.", problem)


def seed_admin2():
    """The 2nd admin lives in environment variables (ADMIN2_USERNAME / ADMIN2_PASSWORD): changing them rotates the
    account. They can do everything except delete."""
    user, pw = Config.ADMIN2_USERNAME.strip(), Config.ADMIN2_PASSWORD
    if not user or not pw:
        return
    problem = security.password_problem(pw)
    if problem:
        log.error("ADMIN2_PASSWORD rejected: %s", problem)
        return
    fp = hashlib.sha256(f"{user}\n{pw}".encode()).hexdigest()
    cur = next((a for a in db.get_list("/admins") if a.get("role") == "admin2" and not a.get("deleted")), None)
    if cur and cur.get("env_fp") == fp:
        return
    taken = db.get(f"/admin_usernames/{user.lower()}")
    if taken and (not cur or taken != cur["id"]):
        log.error("ADMIN2_USERNAME '%s' is already used by another account; 2nd admin not created.", user)
        return
    if cur:
        if cur.get("username_lower") and cur["username_lower"] != user.lower():
            db.set(f"/admin_usernames/{cur['username_lower']}", None)
        db.update(f"/admins/{cur['id']}", {"username": user, "username_lower": user.lower(), "env_fp": fp, "active": True,
                                           "password_hash": security.hash_password(pw),
                                           "session_version": int(cur.get("session_version", 1)) + 1})
        aid = cur["id"]
    else:
        aid = utils.new_id("adm")
        db.set(f"/admins/{aid}", {"id": aid, "username": user, "username_lower": user.lower(), "role": "admin2",
                                  "password_hash": security.hash_password(pw), "active": True, "session_version": 1,
                                  "created_at": utils.now(), "created_by": "env", "env_fp": fp})
    db.set(f"/admin_usernames/{user.lower()}", aid)
    log.info("2nd admin '%s' is ready.", user)


def _background(app):
    from bot.handlers import sweep_rewards
    from bot.tg import TelegramError, tg
    from services import broadcast, notify, payments, resellers, shortener

    def housekeeping():
        time.sleep(10)
        while True:
            for name, fn in (("payments", payments.expire_due), ("rewards", sweep_rewards),
                             ("broadcast", broadcast.resume_pending)):
                try:
                    fn()
                except Exception:
                    log.exception("housekeeping '%s' failed", name)
            time.sleep(30)

    def register_webhooks():
        time.sleep(3)
        if not (Config.BOT_TOKEN and Config.BASE_URL):
            log.warning("BOT_TOKEN / BASE_URL missing: webhook not registered automatically.")
            return
        if Config.webhook_problem():
            log.error("Webhooks NOT registered: %s", Config.webhook_problem())
            return
        try:
            tg.set_webhook(f"{Config.BASE_URL}/telegram/{Config.path_secret('')}", Config.header_secret(""))
            log.info("Main bot webhook registered at %s", Config.BASE_URL)
            tg.set_start_menu(notify.admin_chats())  # the ≡ menu shows only /start
        except TelegramError as e:
            log.error("Webhook registration failed: %s", e)
        for r in resellers.all_resellers():
            try:
                resellers.register_webhook(r)
            except Exception as e:
                log.warning("reseller %s webhook failed: %s", r.get("name"), e)

    shortener.start()  # step-gate engine: flusher + sweeper threads
    threading.Thread(target=housekeeping, daemon=True, name="housekeeping").start()
    threading.Thread(target=register_webhooks, daemon=True, name="webhook-setup").start()


def create_app(background=True):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        SESSION_COOKIE_NAME="__Host-gzsid" if Config.COOKIE_SECURE else "gzsid",
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=Config.COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=Config.SESSION_HOURS),
        MAX_CONTENT_LENGTH=SIZE_CAPS["max"], JSON_SORT_KEYS=False,
    )
    # HostRouter runs inside ProxyFix: it enforces the sub-domain split + origin lock, then sets the real client IP
    # from CF-Connecting-IP (only when the origin secret matched). We never trust X-Forwarded-Host.
    app.wsgi_app = ProxyFix(HostRouter(app.wsgi_app), x_for=1, x_proto=1, x_host=0)
    if Config.HOST_MODE and not Config.ORIGIN_SECRET and not Config.IS_DEV:
        log.warning("ROOT_DOMAIN is set but ORIGIN_SECRET is not: the Render address is NOT locked to Cloudflare yet.")
    if not Config.SECRET_KEY_FROM_ENV:
        log.warning("SECRET_KEY not set: using a random key (admins are logged out on every restart).")

    db.init()
    seed_admin()
    seed_admin2()

    from routes import bp as admin_bp, rbp, sbp
    from routes.media_routes import media_bp
    from routes.gate import gate_bp
    from routes.pages import pages
    from routes.public_api import api_bp
    from routes.webhook import webhook_bp

    for b in (pages, admin_bp, rbp, sbp, api_bp, media_bp, webhook_bp, gate_bp):
        app.register_blueprint(b)

    @app.before_request
    def cap_request_size():
        """Small bodies everywhere; big ones only on the endpoints that really take files."""
        n = request.content_length
        if n is None or request.method not in ("POST", "PUT", "PATCH"):
            return None
        limit = SIZE_CAPS["default"]
        if request.path == "/admin/api/broadcast/upload":
            limit = SIZE_CAPS["broadcast_upload"]
        if n > limit:
            raise ApiError(f"File too large (max {limit // (1024 * 1024)} MB for this upload).", 413)
        return None

    @app.after_request
    def secure_headers(resp):
        h = resp.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.path.startswith("/static/") and resp.status_code == 200:
            # lets Cloudflare serve JS/CSS/images from its own edge (no trip to Render); 5 min keeps deploys fresh
            h["Cache-Control"] = "public, max-age=86400" if request.path.startswith("/static/img/") else "public, max-age=300"
        if Config.COOKIE_SECURE:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    _COMPRESSIBLE = ("text/", "application/json", "application/javascript", "text/javascript", "image/svg+xml")

    @app.after_request
    def compress(resp):
        """gzip text responses (JSON API, JS, CSS, HTML) -- 5-10x smaller, much faster on mobile networks."""
        try:
            if (resp.status_code != 200 or "Content-Encoding" in resp.headers or resp.is_streamed and not resp.direct_passthrough
                    or "gzip" not in request.headers.get("Accept-Encoding", "")
                    or not (resp.mimetype or "").startswith(_COMPRESSIBLE)):
                return resp
            resp.direct_passthrough = False
            data = resp.get_data()
            if len(data) < 800:
                return resp
            resp.set_data(gzip.compress(data, compresslevel=5, mtime=0))
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers.add("Vary", "Accept-Encoding")
            resp.headers["Content-Length"] = str(len(resp.get_data()))
        except Exception:  # never break a response because of compression
            log.exception("compress failed")
        return resp

    def wants_json():
        return request.path.startswith(("/api/", "/admin/api/", "/reseller/api/", "/seller/api/"))

    @app.errorhandler(ApiError)
    def on_api_error(e):
        resp = jsonify(success=False, error=e.message, message=e.message, code=e.code)
        resp.status_code = e.status
        if getattr(e, "retry_after", None):
            resp.headers["Retry-After"] = str(e.retry_after)
        return resp

    for code, msg in ((400, "Bad request"), (401, "Unauthorized"), (403, "Forbidden"), (404, "Not found"),
                      (405, "Method not allowed"), (413, "File too large")):
        def handler(e, code=code, msg=msg):
            if wants_json():
                return jsonify(success=False, error=msg, message=msg, code=str(code)), code
            return msg, code
        app.register_error_handler(code, handler)

    @app.errorhandler(Exception)
    def on_crash(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        log.exception("Unhandled error")
        if wants_json():
            return jsonify(success=False, error="Server error", message="Server error", code="SERVER_ERROR"), 500
        return "Server error", 500

    if background:
        _background(app)
    return app


if __name__ == "__main__":  # local dev:  APP_ENV=development USE_MEMORY_DB=1 python app.py
    create_app().run(host="127.0.0.1", port=5000, debug=False)
