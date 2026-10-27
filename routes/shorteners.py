"""Admin: Shortener (step-gate) -- create, history, tracking."""
from flask import g, jsonify, request

from config import Config
from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import shortener
from . import body, bp

FIELDS = ("id", "name", "kind", "pages", "top_min", "top_max", "bottom_min", "bottom_max", "ttl_min", "pool", "note", "active",
          "redirect_url", "notify_pages", "max_active", "cooldown", "daily_limit", "created_at", "created_by")


def _public(sh):
    out = {k: sh.get(k, "" if k in ("note", "redirect_url", "created_by") else 0) for k in FIELDS}
    out["pool"] = list(sh.get("pool") or [])
    out["active"] = bool(sh.get("active", True))
    out["notify_pages"] = bool(sh.get("notify_pages", sh.get("kind") == "license"))
    out["link"] = shortener.public_link(sh)
    return out


def _config():
    cfg = shortener.get_config()
    return {"site_url": cfg["site_url"], "pages": cfg["pages"], "default_pages": cfg.get("default_pages", False), "proxy_ready": bool(Config.PROXY_SECRET),
            "turnstile": bool(Config.TURNSTILE_SITEKEY and Config.TURNSTILE_SECRET)}


def _get(sid):
    sh = shortener.shortener(sid)
    if not sh:
        raise ApiError("Shortener not found.", 404)
    return sh


@bp.get("/shorteners")
def list_shorteners():
    rows = shortener.all_shorteners()
    counts = db.pmap(shortener.quick_counts, rows)
    plans = [p for p in db.get_list("/plans") if not p.get("deleted") and p.get("shorturl_id")]
    items = []
    for sh, c in zip(rows, counts):
        items.append({**_public(sh), "counts": c, "plans": sum(1 for p in plans if p["shorturl_id"] == sh["id"])})
    return jsonify(items=items, config=_config())


@bp.get("/shorteners/options")
def shortener_options():
    """For the plan form: only shorteners that generate license keys and are on."""
    return jsonify(items=[{"id": r["id"], "name": r["name"], "pages": r.get("pages", 0)} for r in shortener.usable_for_plans()])


@bp.get("/shorteners/config")
def get_shortener_config():
    return jsonify(_config())


@bp.put("/shorteners/config")
def put_shortener_config():
    d = body()
    cfg = shortener.save_config(d.get("site_url") or Config.SITE_URL, d.get("pages"))
    log_action("shortener.config", f"{cfg['site_url']} · {len(cfg['pages'])} pages")
    return jsonify(ok=True, config=_config())


@bp.post("/shorteners")
def create_shortener():
    sh = shortener.create(body(), g.admin["username"])
    log_action("shortener.created", f"{sh['name']} · {sh['kind']} · {sh['pages']} pages", target=sh["id"])
    return jsonify(ok=True, item=_public(sh)), 201


@bp.patch("/shorteners/<sid>")
def edit_shortener(sid):
    sh = shortener.update(sid, body())
    log_action("shortener.edited", sh["name"], target=sid)
    return jsonify(ok=True, item=_public(sh))


@bp.post("/shorteners/<sid>/toggle")
def toggle_shortener(sid):
    sh = _get(sid)
    on = bool(body().get("active", True))
    db.update(f"/shorteners/{sid}", {"active": on})
    shortener.forget(sid)
    log_action("shortener.on" if on else "shortener.off", sh["name"], target=sid)
    return jsonify(ok=True, active=on)


@bp.delete("/shorteners/<sid>")
def delete_shortener(sid):
    sh = shortener.remove(sid, g.admin["username"])
    log_action("shortener.deleted", sh["name"], target=sid)
    return jsonify(ok=True)


@bp.get("/shorteners/<sid>/stats")
def shortener_stats(sid):
    sh = _get(sid)
    lo, hi = utils.resolve_range(request.args.get("range", "today"), request.args.get("start"), request.args.get("end"))
    return jsonify(item=_public(sh), stats=shortener.stats_for(sh, lo, hi), range={"start": lo, "end": hi})


@bp.get("/shorteners/<sid>/sessions")
def shortener_sessions(sid):
    _get(sid)
    items, meta = shortener.sessions(sid, request.args.get("status", ""), request.args.get("page"), request.args.get("q", ""))
    return jsonify(items=items, **meta)
