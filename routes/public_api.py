"""App-facing license API:  POST /api/v1/<app_id>/verify  (and /heartbeat)

Your app sends the license key + a stable device id. See API_DOCS.md.
"""
import hmac
import re

from flask import Blueprint, jsonify, request

from config import Config
from core import db, utils
from core.security import client_ip, limiter
from services import licenses as lic_svc
from services import reviewchecks, settings

api_bp = Blueprint("public_api", __name__, url_prefix="/api/v1")
APP_ID_RE = re.compile(r"^app_[a-f0-9]{12}$")
KEY_RE = re.compile(r"^[A-Za-z0-9]{18}$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9_.:\-]{8,128}$")


def fail(status, code, message, **extra):
    resp = jsonify(success=False, code=code, message=message, server_time=utils.now(), **extra)
    resp.status_code = status
    return resp


def _limited(retry):
    r = fail(429, "RATE_LIMITED", "Too many requests. Try again later.", retry_after=retry)
    r.headers["Retry-After"] = str(retry)
    return r


@api_bp.after_request
def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _peek_key(data):
    """The license key of a request, if the body is valid JSON (used to recognise test keys)."""
    if isinstance(data, dict):
        k = str(data.get("license_key", "")).strip().upper()
        return k if KEY_RE.match(k) else ""
    return ""


def _review_ctx(app, key):
    """(license, context id) for a TEST key that is being used to test / review this app, else None.
    Context = the admin's review submission, or "draft:<app id>" while the seller tests before submitting."""
    if not key:
        return None
    lic = db.get(f"/licenses/{key}")
    if not (lic and lic.get("test") and lic.get("app_id") == app["id"]):
        return None
    ctx = lic.get("review_ctx") or app.get("pending_submission")
    if not ctx or (str(ctx).startswith("draft:") and not app.get("draft_test")):
        return None
    return lic, ctx


@api_bp.route("/<app_id>/verify", methods=["POST"], endpoint="verify")
@api_bp.route("/<app_id>/heartbeat", methods=["POST"], endpoint="heartbeat")
def verify(app_id):
    ip = client_ip()
    ok, retry = limiter.hit(f"api:ip:{ip}", *Config.API_IP_LIMIT)
    if not ok:
        return _limited(retry)

    app = db.get(f"/apps/{app_id}") if APP_ID_RE.match(app_id) else None
    if not app or app.get("deleted"):
        return fail(404, "APP_NOT_FOUND", "Unknown app.")
    if not hmac.compare_digest(str(request.headers.get("X-API-Key", "")), str(app.get("api_key", ""))):
        _count_bad(ip)
        return fail(401, "INVALID_API_KEY", "Missing or invalid X-API-Key header.")

    data = request.get_json(silent=True)
    key_hint = _peek_key(data)
    review = _review_ctx(app, key_hint)
    review_lic = review[0] if review else None
    dev = str((data or {}).get("device_id", "")) if isinstance(data, dict) else ""
    dev_name = str((data or {}).get("device_name", "")) if isinstance(data, dict) else ""
    ver = str((data or {}).get("app_version", "")) if isinstance(data, dict) else ""

    def seen(code):
        if review:
            reviewchecks.observe(review[1], code, dev, dev_name, ver)

    if app.get("disabled"):
        return fail(403, "APP_DISABLED", "This app is currently disabled.")

    # maintenance: global rule -> the app's own (seller) switch -> the admin's review-test switch
    rule = settings.active_maintenance(app_id)
    own = app.get("maintenance") or {}
    if not rule and own.get("enabled"):
        rule = own
    if not rule and review_lic and (app.get("review_maintenance") or {}).get("enabled"):  # only TEST keys see it
        rule = {"message": "Under maintenance (review test)", "status_code": 503}
    if rule:
        code = int(rule.get("status_code", 200))
        seen("MAINTENANCE")
        resp = jsonify(success=False, maintenance=True, code="MAINTENANCE", status_code=code,
                       message=rule.get("message", "Under maintenance"), server_time=utils.now())
        resp.status_code = code
        return resp

    if not isinstance(data, dict):
        return fail(400, "BAD_REQUEST", "Send a JSON body.")
    key = str(data.get("license_key", "")).strip().upper()
    device_id = str(data.get("device_id", "")).strip()
    if not KEY_RE.match(key):
        _count_bad(ip)
        return fail(400, "INVALID_LICENSE_FORMAT", "license_key must be 18 letters/numbers.")
    if not DEVICE_RE.match(device_id):
        return fail(400, "INVALID_DEVICE_ID", "device_id must be 8-128 chars: letters, numbers, . _ : -")

    ok, retry = limiter.hit(f"api:lic:{key}", *Config.API_KEY_LIMIT)
    if not ok:
        return _limited(retry)

    lic = db.get(f"/licenses/{key}")
    if not lic or lic.get("deleted") or lic.get("app_id") != app_id:
        _count_bad(ip)
        return fail(404, "LICENSE_NOT_FOUND", "License key not found for this app.")
    status = lic_svc.status_of(lic)
    if status == "revoked":
        seen("LICENSE_REVOKED")
        return fail(403, "LICENSE_REVOKED", "This license has been revoked.")
    if status == "expired":
        seen("LICENSE_EXPIRED")
        return fail(403, "LICENSE_EXPIRED", "This license has expired.", expired_at=lic["expires_at"])

    # one license = one device. First successful call binds the device atomically.
    bound = db.transaction(f"/licenses/{key}/device_id", lambda cur: cur or device_id)
    if bound != device_id:
        seen("DEVICE_MISMATCH")
        return fail(403, "DEVICE_MISMATCH", "This license is already linked to another device.")

    t = utils.now()
    last = int(lic.get("last_seen", 0))
    if not lic.get("first_seen") or t - last > 300 or utils.day_key(last) != utils.day_key(t):
        upd = {f"licenses/{key}/last_seen": t, f"activity/{app_id}/{utils.day_key(t)}/{key}": t}
        if not lic.get("first_seen"):
            upd[f"licenses/{key}/first_seen"] = t
        name = str(data.get("device_name", ""))[:60]
        if name:
            upd[f"licenses/{key}/device_name"] = name
        ver = str(data.get("app_version", ""))[:20]
        if ver:
            upd[f"licenses/{key}/app_version"] = ver
        db.update("/", upd)

    seen("OK")
    return jsonify(success=True, code="OK", message="License valid.", server_time=t, license={
        "key": key, "status": "active", "plan": lic.get("validity_label", ""), "expires_at": lic["expires_at"],
        "remaining_seconds": max(0, lic["expires_at"] - t),
        "user": ({"full_name": lic.get("full_name", "")} if app.get("seller_id") else
                 {"full_name": lic.get("full_name", ""), "username": lic.get("username", ""), "chat_id": lic.get("chat_id", "")})})


def _count_bad(ip):
    ok, _ = limiter.hit(f"api:bad:{ip}", *Config.API_BAD_LIMIT)
    if not ok:  # key guessing / scraping: shut this IP out for 5 minutes
        limiter.block(f"api:ip:{ip}", 300)
