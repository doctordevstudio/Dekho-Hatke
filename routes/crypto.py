"""Crypto (Monero) -- SUPER ADMIN ONLY. Every endpoint refuses the 2nd admin and view-only admins (even for reading),
so wallet data, balances and the seed can never reach them."""
from flask import g, jsonify, request

from core import db, security, utils
from core.audit import log_action
from core.errors import ApiError
from services import xmr
from . import body, bp, require_admin_role


@bp.before_request
def _crypto_gate():
    """Runs for the whole admin blueprint but only acts on /crypto/*. (Order: after admin_guard, which sets g.admin.)"""
    if request.path.startswith("/admin/api/crypto"):
        require_admin_role()
    return None


def _try(limit_key, limit=6, window=600):
    security.rate_limit_or_429(f"xmr:{limit_key}:{g.admin['id']}", (limit, window), "Too many attempts. Wait a few minutes.")


@bp.get("/crypto/status")
def crypto_status():
    return jsonify(xmr.status())


@bp.post("/crypto/wallet")
def crypto_create_wallet():
    _try("create", 5)
    d = body()
    w = xmr.create_wallet(g.admin["username"], d.get("password"))
    log_action("crypto.wallet_created", f"restore height {w['restore_height']}", target="xmr")
    return jsonify(ok=True, wallet=w, status=xmr.status()), 201


@bp.get("/crypto/balance")
def crypto_balance():
    return jsonify(xmr.balance())


@bp.get("/crypto/stats")
def crypto_stats():
    out = {k: xmr.received_stats(k) for k in ("today", "yesterday", "all")}
    if request.args.get("start") and request.args.get("end"):
        out["custom"] = xmr.received_stats("custom", request.args["start"], request.args["end"])
    return jsonify(out)


@bp.put("/crypto/settings")
def crypto_settings():
    d = body()
    saved = xmr.save_settings(d)
    log_action("crypto.settings", ", ".join(k for k in d if k != "password"), target="xmr")
    return jsonify(ok=True, settings=saved)


def _row(t):
    c = t.get("crypto") or {}
    return {"id": t["id"], "status": t["status"], "fail_type": t.get("fail_type", ""), "created_at": t["created_at"],
            "expires_at": t.get("expires_at", 0), "app_name": t.get("app_name", ""), "plan_label": t.get("plan_label", ""),
            "usd": c.get("usd"), "xmr": c.get("xmr"), "address": c.get("address", ""), "index": c.get("index"),
            "txids": c.get("txids", []), "received_xmr": xmr.pico_to_xmr(c.get("received_pico", 0)),
            "user": {"chat_id": t["user_id"], "full_name": (t.get("user") or {}).get("full_name", ""),
                     "username": (t.get("user") or {}).get("username", "")}}


@bp.get("/crypto/invoices")
def crypto_invoices():
    """All crypto orders (open, paid, expired, cancelled). ?status= filters."""
    want = request.args.get("status") or ""
    rows = [t for t in db.get_list("/txns") if t.get("via") == "crypto" and (not want or t["status"] == want)]
    rows.sort(key=lambda t: t["created_at"], reverse=True)
    page, meta = utils.paginate([_row(t) for t in rows], request.args.get("page"))
    return jsonify(items=page, **meta)


@bp.get("/crypto/received")
def crypto_received():
    """Money received (one row per paid order) for a date range."""
    lo, hi = utils.resolve_range(request.args.get("range") or "all", request.args.get("start"), request.args.get("end"))
    rows = [r for r in xmr.ledger() if lo <= r["ts"] <= hi]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    items = [{**r, "xmr": xmr.pico_to_xmr(r["pico"])} for r in rows]
    page, meta = utils.paginate(items, request.args.get("page"))
    return jsonify(items=page, **meta)


@bp.post("/crypto/seed")
def crypto_seed():
    """Show / download the 25-word seed. Needs the password again; 6 tries per 10 minutes."""
    _try("seed")
    out = xmr.reveal_seed(g.admin["username"], body().get("password"))
    log_action("crypto.seed_viewed", "seed phrase revealed", target="xmr")
    return jsonify(ok=True, **out)


@bp.post("/crypto/sweep")
def crypto_sweep():
    """Transfer the whole unlocked balance to the main account address."""
    _try("sweep", 5)
    d = body()
    r = xmr.sweep(g.admin["username"], d.get("password"), d.get("address"))
    log_action("crypto.sweep", f"{r['xmr']} XMR -> {r['to'][:12]}…", target="xmr")
    return jsonify(ok=True, **r)


@bp.get("/crypto/sweeps")
def crypto_sweeps():
    return jsonify(items=xmr.sweeps())
