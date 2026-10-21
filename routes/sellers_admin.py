"""Seller management in the admin panel: totals, sellers, submitted apps (review with live checks), withdrawals."""
import re

from flask import Response, g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import appwrite, catalog, licenses, media, reviewchecks, sellers
from . import body, bp


def _form():
    d = {k: v for k, v in request.form.items()}
    if "active" in d:
        d["active"] = d["active"] in ("1", "true", "on")
    return d


def _avatar():
    f = request.files.get("avatar")
    if f and f.filename:
        raw = f.read(2 * 1024 * 1024 + 1)
        ok, _ = media.verify_real_image(raw, 2 * 1024 * 1024, 16)
        if not ok:
            raise ApiError("The profile image must be a real JPG / PNG image (max 2 MB).")
        return raw
    return None


def _earn_item(r):
    return {"id": r["id"], "ts": r["ts"], "app_name": r.get("app_name", ""), "plan_label": r.get("plan_label", ""),
            "gross": r.get("gross", 0), "amount": r["amount"], "license_key": r.get("license_key", ""),
            "user": {"full_name": r["user"].get("full_name", ""), "chat_id": r["user"].get("chat_id", "")}}


def _sub_row(sub):
    app = catalog.get_app(sub["app_id"]) or {}
    st = reviewchecks.state(sub["id"]) if sub["status"] == "pending" else None
    return {"id": sub["id"], "app_id": sub["app_id"], "app_name": sub["app_name"], "seller_id": sub["seller_id"],
            "seller_name": sub["seller_name"], "kind": sub["kind"], "status": sub["status"],
            "created_at": sub["created_at"], "decided_at": sub.get("decided_at", 0), "reason": sub.get("reason", ""),
            "image_url": app.get("image_url", ""),
            "checks_passed": sum(1 for c in st["checks"] if c["passed"] and c["required"]) if st else 0,
            "checks_required": sum(1 for c in reviewchecks.CHECKS if c[2])}


# ------------------------------------------------------------------ overview / sellers
@bp.get("/sellers/overview")
def sellers_overview():
    out = {n: sellers.overview(*utils.resolve_range(n)) for n in ("today", "yesterday", "all")}
    if request.args.get("start") and request.args.get("end"):
        out["custom"] = sellers.overview(*utils.resolve_range("custom", request.args["start"], request.args["end"]))
    pending = sellers.submissions("pending")
    wd = sellers.withdrawals(None, "pending")
    return jsonify(totals=out, total_sellers=len(sellers.all_sellers()), pending_submissions=len(pending),
                   pending_withdrawals=len(wd), pending_withdraw_amount=sum(w["amount"] for w in wd),
                   recent=[_sub_row(s) for s in pending[:8]], storage_configured=appwrite.configured(),
                   settings=sellers.settings())


@bp.get("/sellers/earnings")
def all_earnings():
    """Every seller earning of a period (Today / Yesterday / custom ...) paginated."""
    lo, hi = utils.resolve_range(request.args.get("range", "today"), request.args.get("start"), request.args.get("end"))
    rows = sellers.earning_rows(None, lo, hi)
    page, meta = utils.paginate(rows, request.args.get("page"))
    names = {s["id"]: s["name"] for s in sellers.all_sellers()}
    return jsonify(amount_total=round(sum(r["amount"] for r in rows), 2), count=len(rows),
                   items=[{**_earn_item(r), "seller_name": names.get(r["seller_id"], "")} for r in page], **meta)


@bp.get("/sellers/signups")
def signups():
    lo, hi = utils.resolve_range(request.args.get("range", "today"), request.args.get("start"), request.args.get("end"))
    rows = [s for s in sellers.all_sellers() if lo <= s.get("created_at", 0) <= hi]
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(count=len(rows), items=[sellers.seller_row(s) for s in page], **meta)


@bp.get("/sellers/settings")
def seller_get_settings():
    return jsonify(sellers.settings())


@bp.put("/sellers/settings")
def seller_put_settings():
    sellers.save_settings(body())
    log_action("seller.settings_saved", str(sellers.settings()))
    return jsonify(ok=True)


@bp.get("/sellers")
def list_sellers():
    q = (request.args.get("q") or "").strip().lower().lstrip("@")
    rows = sellers.all_sellers()
    if q:
        rows = [s for s in rows if q in s.get("name", "").lower() or q in s.get("tg_username", "").lower()
                or q in str(s.get("chat_id", "")) or q in s.get("login_username", "").lower()]
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[sellers.seller_row(s) for s in page], **meta)


@bp.post("/sellers")
def create_seller():
    img = _avatar()
    s = sellers.create(_form(), g.admin["username"], admin=True)
    if img:
        s = sellers.update(s["id"], {}, img, g.admin["username"])
    log_action("seller.created", s["name"], target=s["id"])
    return jsonify(sellers.seller_row(s)), 201


@bp.get("/sellers/<sid>")
def seller_detail(sid):
    s = sellers.get_or_404(sid)
    rng = request.args.get("range", "today")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    rows = sellers.earning_rows(sid, lo, hi)
    page, meta = utils.paginate(rows, request.args.get("page"))
    apps = []
    for a in sellers.my_apps(sid):
        er = sellers.earning_rows(sid, 0, utils.FAR_FUTURE, a["id"])
        apps.append({"id": a["id"], "name": a["name"], "image_url": a.get("image_url", ""), "status": sellers.review_state(a),
                     "disabled": bool(a.get("disabled")), "sales": len(er), "earned": round(sum(r["amount"] for r in er), 2),
                     "category_id": a.get("category_id", "")})
    return jsonify(seller=sellers.seller_row(s), apps=apps, ranges=sellers.range_summary(sid, request.args.get("start"), request.args.get("end")),
                   period_total=round(sum(r["amount"] for r in rows), 2), period_count=len(rows),
                   items=[_earn_item(r) for r in page], withdrawals=sellers.withdrawals(sid)[:20], **meta)


@bp.post("/sellers/<sid>")
def edit_seller(sid):
    img = _avatar()
    s = sellers.update(sid, _form(), img, g.admin["username"])
    log_action("seller.edited", s["name"], target=sid)
    return jsonify(sellers.seller_row(s))


@bp.delete("/sellers/<sid>")
def delete_seller(sid):
    s = sellers.get_or_404(sid)
    sellers.soft_delete(sid, g.admin["username"])
    log_action("seller.deleted", s["name"], target=sid)
    return jsonify(ok=True)


@bp.post("/apps/<app_id>/disable")
def disable_app(app_id):
    a = catalog.get_app(app_id)
    if not a:
        raise ApiError("App not found.", 404)
    dis = bool(body().get("disabled", True))
    db.update(f"/apps/{app_id}", {"disabled": dis, "disabled_by": "admin" if dis else ""})
    from core.cache import cache
    cache.clear()
    log_action("app.disabled" if dis else "app.enabled", a["name"], target=app_id)
    return jsonify(ok=True)


# ------------------------------------------------------------------ submissions (review)
@bp.get("/submissions")
def list_submissions():
    status = request.args.get("status", "pending")
    rows = sellers.submissions(status if status in ("pending", "approved", "rejected") else None)
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[_sub_row(s) for s in page], **meta)


def _sub_detail(sub):
    files = {}
    for kind, f in (sub.get("files") or {}).items():
        if kind == "logo":
            files["logo"] = {"url": f}
        else:
            files[kind] = {"filename": f.get("filename", ""), "size": f.get("size", 0), "tg_file_id": f.get("tg_file_id", ""),
                           "in_storage": bool(f.get("appwrite_id")), "storage_configured": appwrite.configured()}
    app = catalog.get_app(sub["app_id"]) or {}
    base = {"id": sub["id"], "app_id": sub["app_id"], "seller_id": sub["seller_id"], "seller_name": sub["seller_name"],
            "kind": sub["kind"], "status": sub["status"], "created_at": sub["created_at"], "data": sub["data"],
            "files": files, "note": sub.get("note", ""), "reason": sub.get("reason", ""),
            "current": {"apk_id": app.get("apk_id", ""), "video_id": app.get("video_id", ""),
                        "setup_video_id": app.get("setup_video_id", ""), "name": app.get("name", ""),
                        "image_url": app.get("image_url", ""), "status": app.get("status", "")},
            "endpoint": f"{request.url_root.rstrip('/')}/api/v1/{app.get('id', '')}/verify",
            "api_key": app.get("api_key", ""), "app_disabled": bool(app.get("disabled")),
            "review_maintenance": bool((app.get("review_maintenance") or {}).get("enabled"))}
    if sub["status"] == "pending":
        base.update(reviewchecks.state(sub["id"]), test_keys=sellers.test_keys(sub["id"]))
    return base


@bp.get("/submissions/<sub_id>")
def submission(sub_id):
    return jsonify(_sub_detail(sellers.get_submission(sub_id)))


@bp.patch("/submissions/<sub_id>")
def save_review(sub_id):
    sellers.save_review(sub_id, body(), g.admin["username"])
    log_action("submission.edited", "", target=sub_id)
    return jsonify(_sub_detail(sellers.get_submission(sub_id)))


@bp.get("/submissions/<sub_id>/download/<kind>")
def download_submission_file(sub_id, kind):
    sub = sellers.get_submission(sub_id)
    f = (sub.get("files") or {}).get(kind)
    if kind == "logo" or not f or not f.get("appwrite_id"):
        raise ApiError("This file is not in storage. It was sent to the admin Telegram chat — download it there.", 404)
    r = appwrite.download_response(f["appwrite_id"])
    name = re.sub(r"[^A-Za-z0-9._\-]", "_", f.get("filename", "file"))
    resp = Response(r.iter_content(chunk_size=262144), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    if r.headers.get("Content-Length"):
        resp.headers["Content-Length"] = r.headers["Content-Length"]
    resp.call_on_close(r.close)
    log_action("submission.downloaded", f"{kind} {name}", target=sub_id)
    return resp


@bp.post("/submissions/<sub_id>/test-key")
def test_key(sub_id):
    lic = sellers.create_test_key(sub_id, body().get("hours", 24), g.admin["username"])
    log_action("submission.test_key", f"{lic['validity_label']}", target=sub_id)
    return jsonify(key=lic["key"], expires_at=lic["expires_at"]), 201


@bp.post("/submissions/<sub_id>/test-keys/<key>/<action>")
def test_key_action(sub_id, key, action):
    sellers.test_license_for(sub_id, key)
    who = g.admin["username"]
    if action == "revoke":
        licenses.revoke(key, "review test", who)
    elif action == "expire":
        licenses.expire_now(key, who)
    elif action == "reset-device":
        licenses.reset_device(key, who)
    elif action == "restore":  # undo revoke/expire so the tests can be repeated
        lic = licenses.get_license(key)
        db.update(f"/licenses/{key}", {"status": "active", "revoked_reason": "",
                                        "expires_at": max(int(lic["expires_at"]), utils.now()) + 24 * 3600})
    else:
        raise ApiError("Unknown action.", 404)
    log_action(f"submission.test_key_{action.replace('-', '_')}", key, target=sub_id)
    return jsonify(ok=True)


@bp.post("/submissions/<sub_id>/maintenance")
def review_maintenance(sub_id):
    sellers.set_review_maintenance(sub_id, bool(body().get("enabled")))
    return jsonify(ok=True)


@bp.post("/submissions/<sub_id>/checks/reset")
def reset_checks(sub_id):
    sellers.get_submission(sub_id)
    reviewchecks.reset(sub_id)
    return jsonify(ok=True)


@bp.post("/submissions/<sub_id>/approve")
def approve_submission(sub_id):
    d = body()
    force = bool(d.get("force"))
    if force and g.admin["role"] != "admin":
        raise ApiError("Only the Super Admin can approve without finishing the checks.", 403)
    sellers.approve(sub_id, g.admin["username"], force)
    log_action("submission.approved", "FORCED (checks skipped)" if force else "all live checks passed", target=sub_id)
    return jsonify(ok=True)


@bp.post("/submissions/<sub_id>/reject")
def reject_submission(sub_id):
    sellers.reject(sub_id, body().get("reason"), g.admin["username"])
    log_action("submission.rejected", body().get("reason", ""), target=sub_id)
    return jsonify(ok=True)


# ------------------------------------------------------------------ withdrawals
def _wd(w):
    return {**{k: w.get(k) for k in ("id", "seller_id", "seller_name", "name", "upi_id", "amount", "fee_pct", "fee",
                                     "receive", "status", "created_at", "resolved_at", "resolved_by", "reason")},
            "proof_url": f"/media/img/{w['proof_media_id']}" if w.get("proof_media_id") else ""}


@bp.get("/seller-withdrawals")
def seller_withdrawals():
    status = request.args.get("status", "pending")
    rows = sellers.withdrawals(None, status if status in ("pending", "approved", "rejected") else "")
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[_wd(w) for w in page], **meta)


@bp.post("/seller-withdrawals/<wid>/resolve")
def resolve_seller_withdrawal(wid):
    f = request.form
    if f.get("action") not in ("approve", "reject"):
        raise ApiError("Choose approve or reject.")
    photo = request.files.get("photo")
    data = photo.read() if photo and photo.filename else None
    if data:
        ok, _ = media.verify_real_image(data, 5 * 1024 * 1024, 50)
        if not ok:
            raise ApiError("The proof must be a real JPG / PNG image.")
    rec = sellers.resolve_withdraw(wid, f.get("action") == "approve", f.get("reason", ""), data, g.admin["username"])
    log_action("swithdraw." + rec["status"], f"{utils.inr(rec['amount'])} → {rec['upi_id']}", target=wid)
    return jsonify(_wd(rec))
