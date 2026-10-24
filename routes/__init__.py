"""Panel JSON APIs.

* /admin/api    -> admin panel (super admin, 2nd admin, view-only admins)
* /reseller/api -> reseller panel (each reseller only ever sees its own tenant)

Handlers that behave the same for both are registered on both blueprints with `@both(rule)`; they scope their
data with `g.tenant` ('' for the admin panel, the reseller id for the reseller panel).
"""
import importlib

from flask import Blueprint, g, jsonify, request

from config import Config
from core import security
from core.cache import cache
from core.errors import ApiError

bp = Blueprint("admin_api", __name__, url_prefix="/admin/api")
rbp = Blueprint("reseller_api", __name__, url_prefix="/reseller/api")
sbp = Blueprint("seller_api", __name__, url_prefix="/seller/api")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
VIEWER_OK = {"admin_api.logout", "admin_api.change_password"}  # view-only users may still do these

ROLE_LABEL = {"admin": "Super Admin", "admin2": "2nd Admin", "viewer": "View only"}


def both(rule, **options):
    """Register one handler on the admin AND reseller blueprints."""
    def deco(fn):
        for blueprint in (bp, rbp):
            blueprint.add_url_rule(rule, endpoint=fn.__name__, view_func=fn, **options)
        return fn
    return deco


@bp.before_request
def admin_guard():
    g.tenant, g.reseller = "", None
    g.admin = security.current_admin()
    if not g.admin:
        return jsonify(error="Unauthorized"), 401
    security.rate_limit_or_429(f"adm:{g.admin['id']}", Config.ADMIN_LIMIT)
    if request.method in UNSAFE:
        if not security.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify(error="Invalid CSRF token. Reload the page."), 403
        bad = security.verify_signed_request()
        if bad:
            return jsonify(error=bad, code="REPLAY"), 403
        role = g.admin["role"]
        if role == "viewer" and request.endpoint not in VIEWER_OK:
            return jsonify(error="View-only account: you cannot change anything."), 403
        if role == "admin2" and request.method == "DELETE":
            return jsonify(error="The 2nd admin cannot delete. Ask the Super Admin."), 403
    return None


@rbp.before_request
def reseller_guard():
    g.admin = None
    g.reseller = security.current_reseller()
    if not g.reseller:
        return jsonify(error="Unauthorized"), 401
    g.tenant = g.reseller["id"]
    security.rate_limit_or_429(f"rsl:{g.tenant}", Config.ADMIN_LIMIT)
    if request.method in UNSAFE:
        if not security.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify(error="Invalid CSRF token. Reload the page."), 403
        bad = security.verify_signed_request()
        if bad:
            return jsonify(error=bad, code="REPLAY"), 403
    return None


SELLER_OPEN = {"seller_api.login_api"}  # reachable without a session


@sbp.before_request
def seller_guard():
    g.admin, g.reseller, g.tenant = None, None, ""
    g.seller = security.current_seller()
    if request.endpoint in SELLER_OPEN:
        return None
    if not g.seller:
        return jsonify(error="Unauthorized"), 401
    security.rate_limit_or_429(f"slr:{g.seller['id']}", Config.ADMIN_LIMIT)
    if request.method in UNSAFE:
        if not security.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify(error="Invalid CSRF token. Reload the page."), 403
        bad = security.verify_signed_request()
        if bad:
            return jsonify(error=bad, code="REPLAY"), 403
    return None


def _after(resp):
    resp.headers["Cache-Control"] = "no-store"
    if request.method in UNSAFE and resp.status_code < 400 and request.endpoint != "seller_api.upload_chunk":
        cache.clear()
    return resp


def _api_error(e):
    resp = jsonify(error=e.message, code=e.code)
    resp.status_code = e.status
    if getattr(e, "retry_after", None):
        resp.headers["Retry-After"] = str(e.retry_after)
    return resp


for _b in (bp, rbp, sbp):
    _b.after_request(_after)
    _b.errorhandler(ApiError)(_api_error)


def require_admin_role():
    """Super admin only."""
    if not g.admin or g.admin["role"] != "admin":
        raise ApiError("Only the Super Admin can do this.", 403)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Invalid request body.")
    return data


def tenant():
    return getattr(g, "tenant", "") or ""


ADMIN_MODULES = ["admins", "apps", "auth_api", "earnings", "licenses", "logs", "plans", "settings", "upi", "users",
                 "categories", "balance", "rewards", "referral", "leaderboard", "broadcast", "resellers", "sellers_admin",
                 "uploads", "files"]
RESELLER_MODULES = ["reseller_panel"]
SELLER_MODULES = ["seller_panel"]
for _m in ADMIN_MODULES + RESELLER_MODULES + SELLER_MODULES:
    try:
        importlib.import_module(f"routes.{_m}")
    except ModuleNotFoundError as _e:  # module not written yet during development
        if _e.name != f"routes.{_m}":
            raise
