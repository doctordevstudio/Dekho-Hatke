"""Reseller management (admin panel): totals, list, add / edit, dues & settlement, per-reseller earnings."""
from flask import g, jsonify, request

from bot.tg import TelegramError
from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import media, resellers, stats
from . import body, bp


def _form():
    d = {k: v for k, v in request.form.items()}
    for k in ("active",):
        if k in d:
            d[k] = d[k] in ("1", "true", "on")
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


@bp.get("/resellers/overview")
def resellers_overview():
    rng = request.args.get("range", "today")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    out = {n: resellers.totals(*utils.resolve_range(n)) for n in ("today", "yesterday", "all")}
    if request.args.get("start") and request.args.get("end"):
        out["custom"] = resellers.totals(lo, hi)
    return jsonify(totals=out, total_resellers=len(resellers.all_resellers()), counts=resellers.counts())


@bp.get("/resellers/earnings")
def reseller_earnings():
    """Every reseller sale of a period (drill-down of the overview tiles), paginated."""
    lo, hi = utils.resolve_range(request.args.get("range", "today"), request.args.get("start"), request.args.get("end"))
    rows = [e for e in db.get_list("/earnings") if e.get("reseller_id") and lo <= e.get("ts", 0) <= hi]
    rows.sort(key=lambda e: e["ts"], reverse=True)
    page, meta = utils.paginate(rows, request.args.get("page"))
    names = {r["id"]: r["name"] for r in resellers.all_resellers()}
    return jsonify(amount_total=round(sum(e["amount"] for e in rows), 2),
                   commission_total=round(sum(e.get("commission", 0) for e in rows), 2), count=len(rows),
                   items=[{"id": e["id"], "ts": e["ts"], "app_name": e.get("app_name", ""), "amount": e["amount"],
                           "commission": e.get("commission", 0), "reseller_name": names.get(e["reseller_id"], ""),
                           "reseller_id": e["reseller_id"], "label": e.get("label", ""),
                           "user": {k: e["user"].get(k, "") for k in ("chat_id", "full_name", "profile_image_url")}} for e in page], **meta)


@bp.get("/resellers")
def list_resellers():
    q = (request.args.get("q") or "").strip().lower().lstrip("@")
    rows = resellers.all_resellers()
    if q:
        rows = [r for r in rows if q in r.get("name", "").lower() or q in r.get("username", "").lower()
                or q in str(r.get("chat_id", "")) or q in r.get("login_username", "").lower()]
    page, meta = utils.paginate(rows, request.args.get("page"))
    items = []
    for r in page:
        due = resellers.due_info(r)
        total = round(sum(e["amount"] for e in resellers.entries(r["id"])), 2)
        items.append({**resellers.public(r), "due": due, "total_earned": total})
    return jsonify(items=items, **meta)


@bp.post("/resellers")
def create_reseller():
    f = _form()
    for k, label in (("name", "Name"), ("username", "Telegram @username"), ("chat_id", "Telegram chat ID"), ("bot_token", "Bot token"),
                     ("commission_pct", "Admin commission %"), ("login_username", "Panel login username"), ("password", "Panel password")):
        if not str(f.get(k) or "").strip():
            raise ApiError(f"{label} is required.")
    r = resellers.save(f, None, _avatar(), g.admin["username"])
    log_action("reseller.created", r["name"], target=r["id"])
    return jsonify(resellers.public(r, True)), 201


@bp.get("/resellers/<rid>")
def get_reseller(rid):
    r = resellers.get_or_404(rid)
    rng = request.args.get("range", "today")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    rows = stats.entries_between(lo, hi, tenant=rid)
    rows.sort(key=lambda e: e["ts"], reverse=True)
    page, meta = utils.paginate(rows, request.args.get("page"))
    ranges = {n: {"earned": round(sum(e["amount"] for e in stats.entries_between(*utils.resolve_range(n), tenant=rid)), 2),
                  "commission": round(sum(e.get("commission", 0) for e in stats.entries_between(*utils.resolve_range(n), tenant=rid)), 2)}
              for n in ("today", "yesterday", "all")}
    return jsonify(reseller=resellers.public(r, True), ranges=ranges, due_message=r.get("due_message", ""),
                   settlements=resellers.settlements(rid)[:20],
                   items=[{"id": e["id"], "ts": e["ts"], "app_name": e.get("app_name", ""), "amount": e["amount"],
                           "commission": e.get("commission", 0), "label": e.get("label", ""), "user": e["user"]} for e in page],
                   amount_total=round(sum(e["amount"] for e in rows), 2), **meta)


@bp.post("/resellers/<rid>")  # multipart (avatar) edit
def edit_reseller(rid):
    r = resellers.save(_form(), rid, _avatar(), g.admin["username"])
    log_action("reseller.edited", r["name"], target=rid)
    return jsonify(resellers.public(r, True))


@bp.delete("/resellers/<rid>")
def delete_reseller(rid):
    r = resellers.get_or_404(rid)
    resellers.soft_delete(rid, g.admin["username"])
    log_action("reseller.deleted", r["name"], target=rid)
    return jsonify(ok=True)


@bp.post("/resellers/<rid>/settle")
def settle(rid):
    note = utils.clean_text(body().get("note"), "Note", 200, False)
    info = resellers.settle(rid, g.admin["username"], note)
    log_action("reseller.settled", f"commission {utils.inr(info['commission'])} on {utils.inr(info['earned'])}", target=rid)
    return jsonify(ok=True, settled=info)


@bp.post("/resellers/<rid>/webhook")
def webhook(rid):
    r = resellers.get_or_404(rid)
    try:
        ok = resellers.register_webhook(r)
    except TelegramError as e:
        raise ApiError(f"Telegram error: {e.description}")
    if not ok:
        raise ApiError("BASE_URL is not set on the server.")
    return jsonify(ok=True)
