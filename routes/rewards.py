"""Daily check-in + spin wheel: settings and statistics (valid / expired / claimed / used for purchases)."""
from flask import jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import rewards
from . import body, bp


def _kind(kind):
    if kind not in rewards.KINDS:
        raise ApiError("Unknown reward type.", 404)
    return kind


@bp.get("/rewards/<kind>")
def reward_overview(kind):
    _kind(kind)
    rng = request.args.get("range", "today")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    s = rewards.stats(kind, lo, hi)
    now = utils.now()
    rows = sorted(s["rows"], key=lambda r: r["created_at"], reverse=True)
    q = (request.args.get("q") or "").strip().lower().lstrip("@")
    if q:
        rows = [r for r in rows if q in r["user_id"] or q in (r["user"].get("full_name") or "").lower()
                or q in (r["user"].get("username") or "").lower()]
    page, meta = utils.paginate(rows, request.args.get("page"))
    txn_cache = {}

    def txn(tid):
        if tid not in txn_cache:
            txn_cache[tid] = db.get(f"/txns/{tid}") or {}
        return txn_cache[tid]

    items = []
    for r in page:
        uses = []
        for u in (r.get("uses") or {}).values():
            if u.get("status") in ("held", "used"):
                t = txn(u.get("txn_id", ""))
                uses.append({"ts": u["ts"], "amount": u["amount"], "status": u["status"], "txn_id": u.get("txn_id", ""),
                             "app_name": t.get("app_name", ""), "plan_label": t.get("plan_label", "")})
        items.append({"id": r["id"], "ts": r["created_at"], "expires_at": r["expires_at"], "amount": r["amount"],
                      "remaining": r["remaining"], "used": round(float(r["amount"]) - float(r["remaining"]), 2),
                      "status": rewards.status_of(r, now), "user": r["user"], "uses": uses})
    return jsonify(settings=rewards.settings(kind), items=items,
                   stats={k: v for k, v in s.items() if k != "rows"}, **meta)


@bp.put("/rewards/<kind>/settings")
def reward_save(kind):
    _kind(kind)
    rewards.save_settings(kind, body())
    log_action(f"{kind}.settings_saved", str(rewards.settings(kind)))
    return jsonify(ok=True)
