from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import licenses as svc
from services import stats
from .apps import get_app_or_404
from . import body, bp


def _names():
    return {a["id"]: a["name"] for a in db.get_list("/apps")}


@bp.get("/apps/<app_id>/licenses")
def app_licenses(app_id):
    get_app_or_404(app_id)
    q = (request.args.get("q") or "").strip().lower().lstrip("@")
    rows = [l for l in svc.all_licenses() if l["app_id"] == app_id]
    if q:
        rows = [l for l in rows if q in l["key"].lower() or q in str(l.get("chat_id", "")) or
                q in (l.get("username") or "").lower() or q in (l.get("full_name") or "").lower()]
    rows.sort(key=lambda l: l.get("created_at", 0), reverse=True)
    page, meta = utils.paginate(rows, request.args.get("page"))
    names = _names()
    return jsonify(items=[svc.public(l, names) for l in page], **meta)


@bp.post("/apps/<app_id>/licenses")
def create_manual(app_id):
    app = get_app_or_404(app_id)
    d = body()
    limit_app = (app.get("plan_type") or "time") == "limit"
    need = [("full_name", "Full name"), ("chat_id", "Telegram chat ID"), ("amount", "Amount paid (enter 0 if free)")]
    if limit_app:
        need.append(("requests", "Number of requests"))
    else:
        need += [("validity_value", "Validity"), ("validity_unit", "Validity unit")]
    for k, label in need:
        if str(d.get(k) if d.get(k) is not None else "").strip() == "":
            raise ApiError(f"{label} is required.")
    chat_id = utils.clean_chat_id(d.get("chat_id"))
    reqs, label = 0, None
    if limit_app:  # pay-as-you-go key: a number of requests, optional expiry
        try:
            reqs = int(d.get("requests"))
        except (TypeError, ValueError):
            raise ApiError("Requests must be a whole number.")
        if not 1 <= reqs <= 1_000_000_000:
            raise ApiError("Requests must be between 1 and 1,000,000,000.")
        if str(d.get("validity_value") or "").strip() in ("", "0"):
            val, unit, label = 100, "years", f"{reqs:,} requests"
        else:
            val, unit = utils.parse_validity(d.get("validity_value"), d.get("validity_unit") or "days")
            label = f"{reqs:,} requests · valid {utils.validity_label(val, unit)}"
    else:
        val, unit = utils.parse_validity(d.get("validity_value"), d.get("validity_unit"))
    lic = svc.create_license(
        app=app, chat_id=chat_id, full_name=utils.clean_text(d.get("full_name"), "Full name", 80),
        username=utils.clean_username(d.get("username")),
        profile_image_url=utils.clean_url(d.get("profile_image_url"), "Profile image URL"),
        amount=utils.parse_amount(d.get("amount", 0), allow_zero=True), validity_value=val, validity_unit=unit,
        source="manual", limit_requests=reqs, label=label)
    log_action("license.created", f"{app['name']} for {lic['full_name']} ({lic['validity_label']})", target=lic["key"])
    return jsonify(svc.public(lic, {app_id: app["name"]})), 201


@bp.post("/licenses/<key>/topup")
def topup_limit(key):
    """Add requests to a pay-as-you-go key (and re-open it when it was finished)."""
    lic = svc.get_license(key)
    if not svc.limit_of(lic):
        raise ApiError("This is not a pay-as-you-go key.")
    try:
        n = int(body().get("requests"))
    except (TypeError, ValueError):
        raise ApiError("Requests must be a whole number.")
    if not 1 <= n <= 1_000_000_000:
        raise ApiError("Requests must be between 1 and 1,000,000,000.")
    db.transaction(f"/licenses/{lic['key']}/limit_total", lambda c: int(c or 0) + n)
    db.update(f"/licenses/{lic['key']}", {"limit_warned": "", f"history/{utils.ts_id()}": {
        "ts": utils.now(), "action": "topup", "by": g.admin["username"], "detail": f"+{n:,} requests"}})
    log_action("license.topup", f"+{n:,} requests", target=lic["key"])
    return jsonify(svc.public(svc.get_license(key), {}))


@bp.post("/licenses/<key>/renew")
def renew(key):
    d = body()
    exp, label = svc.renew(key, d.get("validity_value"), d.get("validity_unit"), g.admin["username"])
    log_action("license.renewed", f"+{label}", target=key.upper())
    return jsonify(expires_at=exp)


@bp.post("/licenses/<key>/revoke")
def revoke(key):
    reason = utils.clean_text(body().get("reason"), "Reason", 200, required=False)
    svc.revoke(key, reason, g.admin["username"])
    log_action("license.revoked", reason, target=key.upper())
    return jsonify(ok=True)


@bp.post("/licenses/<key>/expire")
def expire(key):
    svc.expire_now(key, g.admin["username"])
    log_action("license.expired_now", "", target=key.upper())
    return jsonify(ok=True)


@bp.post("/licenses/<key>/reset-device")
def reset_device(key):
    svc.reset_device(key, g.admin["username"])
    log_action("license.device_reset", "", target=key.upper())
    return jsonify(ok=True)


@bp.delete("/licenses/<key>")
def delete_license(key):
    svc.soft_delete(key, g.admin["username"])
    log_action("license.deleted", "", target=key.upper())
    return jsonify(ok=True)
