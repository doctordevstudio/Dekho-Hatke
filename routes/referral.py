"""Refer & Earn admin: overview, per-referrer details, custom commission %, withdrawals (approve / reject).
Shared with the reseller panel (each reseller has their own referral system)."""
from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import referral, resellers, settings, users, wallet
from . import body, both, bp, tenant


def _actor():
    return (g.admin or {}).get("username") or (g.reseller or {}).get("login_username") or "reseller"


def _with_image(w):
    return {**{k: w.get(k) for k in ("id", "amount", "upi_id", "status", "created_at", "resolved_at", "resolved_by", "reason")},
            "user": {k: (w.get("user") or {}).get(k, "") for k in ("chat_id", "full_name", "username", "profile_image_url")},
            "proof_url": f"/media/img/{w['proof_media_id']}" if w.get("proof_media_id") else ""}


@both("/referral", methods=["GET"])
def overview():
    out = {"overview": referral.overview(tenant()), "enabled": referral.enabled(tenant()),
           "global_enabled": settings.feature("referral")}
    if not tenant():
        out["message"] = settings.get_rich("referral")
    return jsonify(out)


@both("/referral/settings", methods=["PUT"])
def save_settings():
    d = body()
    t = tenant()
    if "percent" in d:
        referral.save_percent(d["percent"], t)
    if "enabled" in d:
        if t:
            db.update(f"/resellers/{t}", {"referral_enabled": bool(d["enabled"])})
        else:
            settings.save_features({**settings.features(), "referral": bool(d["enabled"])})
    if "min_withdraw" in d:
        mw = utils.parse_amount(d["min_withdraw"])
        if t:
            db.update(f"/resellers/{t}", {"min_withdraw": mw})
        else:
            cur = wallet.settings()
            wallet.save_settings({"min_deposit": cur["min_deposit"], "min_withdraw": mw})
    log_action("referral.settings_saved", f"{d.get('percent', '')}% / min {d.get('min_withdraw', '')}")
    return jsonify(ok=True, overview=referral.overview(t))


@bp.put("/referral/message")
def save_message():
    settings.save_rich("referral", body())
    log_action("referral.message_saved", "")
    return jsonify(ok=True)


@both("/referral/users", methods=["GET"])
def referrers():
    rows = referral.referrer_rows(tenant(), request.args.get("q"))
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=page, **meta)


@both("/referral/users/<chat_id>", methods=["GET"])
def referrer(chat_id):
    t = tenant()
    u = users.get_user(chat_id, t) or {"chat_id": chat_id}
    sid = users.scoped(chat_id, t)
    cp = referral.custom_percent(sid)
    return jsonify(user={k: u.get(k, "") for k in ("chat_id", "full_name", "username", "profile_image_url")},
                   stats=referral.stats_for(sid), custom_percent=cp, default_percent=referral.default_percent(t),
                   referrals=referral.referrer_detail(t, chat_id))


@both("/referral/users/<chat_id>/percent", methods=["PUT"])
def set_percent(chat_id):
    if not users.get_user(chat_id, tenant()):
        raise ApiError("User not found.", 404)
    v = body().get("percent")
    p = referral.set_custom_percent(users.scoped(chat_id, tenant()), v)
    log_action("referral.custom_percent", f"{p if p is not None else 'default'}%", target=str(chat_id))
    return jsonify(ok=True, custom_percent=p)


@both("/referral/withdrawals", methods=["GET"])
def withdrawals():
    status = request.args.get("status", "pending")
    rows = referral.list_withdrawals(tenant(), status if status in ("pending", "approved", "rejected") else "")
    page, meta = utils.paginate(rows, request.args.get("page"))
    ov = referral.overview(tenant())
    return jsonify(items=[_with_image(w) for w in page], pending=ov["pending_withdrawals"],
                   pending_amount=ov["pending_withdraw_amount"], **meta)


@both("/referral/withdrawals/<wid>/resolve", methods=["POST"])
def resolve(wid):
    f = request.form
    approve = f.get("action") == "approve"
    if f.get("action") not in ("approve", "reject"):
        raise ApiError("Choose approve or reject.")
    photo = request.files.get("photo")
    data = photo.read() if photo and photo.filename else None
    if data:
        from services import media
        ok, why = media.verify_real_image(data, 5 * 1024 * 1024, 50)
        if not ok:
            raise ApiError("The proof must be a real JPG / PNG image.")
    rec = referral.resolve_withdrawal(wid, approve, f.get("reason", ""), data, _actor(), "panel", tenant=tenant())
    return jsonify(_with_image(rec))
