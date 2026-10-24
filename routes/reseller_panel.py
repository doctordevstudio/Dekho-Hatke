"""Reseller panel specifics (/reseller/api): profile, home counts, price increases, dues, support, settings, password.
UPI, earnings, users, referral, wallet and force-join are the shared routes registered with @both."""
from flask import g, jsonify, request, session

from bot.tg import TelegramError
from core import db, security, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog, resellers, settings, stats
from . import body, rbp


def _log(action, detail="", target=""):
    log_action(f"reseller.{action}", detail, target=target)


@rbp.get("/me")
def me():
    r = g.reseller
    return jsonify(**resellers.public(r, True), csrf=security.csrf_token(), forced=resellers.is_forced(r["id"]),
                   admin_support=settings.bot_settings()["support_username"])


@rbp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@rbp.post("/password")
def password():
    d = body()
    r = g.reseller
    if not security.verify_password(r["password_hash"], str(d.get("current") or "")):
        raise ApiError("Current password is wrong.", 403)
    problem = security.password_problem(str(d.get("new") or ""))
    if problem:
        raise ApiError(problem)
    sv = int(r.get("session_version", 1)) + 1
    db.update(f"/resellers/{r['id']}", {"password_hash": security.hash_password(d["new"]), "session_version": sv})
    session["rsv"] = sv
    _log("password_changed")
    return jsonify(ok=True)


@rbp.get("/dashboard")
def dashboard():
    r = g.reseller
    rid = r["id"]
    ranges = {}
    names = ["today", "yesterday", "all"] + (["custom"] if request.args.get("start") and request.args.get("end") else [])
    for n in names:
        lo, hi = utils.resolve_range(n, request.args.get("start"), request.args.get("end"))
        rows = stats.entries_between(lo, hi, tenant=rid)
        ranges[n] = {"earned": round(sum(e["amount"] for e in rows), 2), "sales": len(rows),
                     "commission": round(sum(e.get("commission", 0) for e in rows), 2)}
    users = db.shallow(f"/r_users/{rid}") or []
    return jsonify(ranges=ranges, counts=resellers.counts(), users=len(users), due=resellers.due_info(r),
                   forced=resellers.is_forced(rid), admin_support=settings.bot_settings()["support_username"])


@rbp.get("/prices")
def prices():
    """Catalog with base price (set by the admin) and the reseller's own price. Prices can only go UP."""
    rid = g.reseller["id"]
    cats = {c["id"]: c["name"] for c in catalog.categories()}
    mine = resellers.prices(rid)
    hid, trials = resellers.hidden(rid), resellers.trials(rid)
    out = []
    for a in catalog.live("/apps"):
        if not catalog.purchasable(a):
            continue
        plans = []
        has_trial = any(p["app_id"] == a["id"] and p.get("kind") == "trial" for p in catalog.live("/plans"))
        for p in catalog.live("/plans"):
            if p["app_id"] != a["id"] or p.get("kind") == "trial":
                continue
            ov = mine.get(p["id"]) or {}
            plans.append({"id": p["id"], "label": utils.validity_label(p["validity_value"], p["validity_unit"]),
                          "base": p["amount"], "max": p["amount"] * 2, "price": ov.get("price", p["amount"]),
                          "enabled": ov.get("enabled", True), "is_best": bool(p.get("is_best"))})
        if plans:
            plans.sort(key=lambda x: x["base"])
            out.append({"app_id": a["id"], "app_name": a["name"], "image_url": a.get("image_url", ""),
                        "category": cats.get(a.get("category_id"), "Other"), "category_id": a.get("category_id") or "other",
                        "hidden": a["id"] in (hid.get("apps") or {}), "has_trial": has_trial,
                        "trial": a["id"] in trials, "plans": plans})
    out.sort(key=lambda x: (x["category"], x["app_name"]))
    page, meta = utils.paginate(out, request.args.get("page"), 10)
    categories = [{"id": c["id"], "name": c["name"], "icon": c.get("icon") or "📁", "hidden": c["id"] in (hid.get("categories") or {})}
                  for c in catalog.categories()]
    return jsonify(items=page, categories=categories, commission_pct=float(g.reseller.get("commission_pct", 0)), **meta)


@rbp.put("/visibility")
def set_visibility():
    """Show / hide an app or a category in the reseller's own bot."""
    d = body()
    kind = {"app": "apps", "category": "categories"}.get(str(d.get("kind")))
    if not kind:
        raise ApiError("Choose app or category.")
    resellers.set_hidden(g.reseller["id"], kind, d.get("id"), not bool(d.get("enabled")))
    _log("visibility", f"{d.get('kind')} {d.get('id')} {'shown' if d.get('enabled') else 'hidden'}", str(d.get("id")))
    return jsonify(ok=True)


@rbp.put("/trials/<app_id>")
def set_trial(app_id):
    app = catalog.get_app(app_id)
    if not app or not any(p["app_id"] == app_id and p.get("kind") == "trial" for p in catalog.live("/plans")):
        raise ApiError("This app has no free-trial plan.", 404)
    resellers.set_trial(g.reseller["id"], app_id, bool(body().get("enabled")))
    _log("trial", f"{app['name']} {'on' if body().get('enabled') else 'off'}", app_id)
    return jsonify(ok=True)


@rbp.put("/prices/<plan_id>")
def set_price(plan_id):
    d = body()
    plan = catalog.get_plan(plan_id)
    if not plan or plan.get("kind") == "trial":
        raise ApiError("Plan not found.", 404)
    resellers.set_price(g.reseller["id"], plan, d.get("price"), d.get("enabled", True))
    _log("price_set", f"{plan_id} → {d.get('price')}", plan_id)
    return jsonify(ok=True)


@rbp.get("/due")
def due():
    r = g.reseller
    rows = stats.entries_between(int(r.get("period_start") or r.get("created_at", 0)), utils.FAR_FUTURE, tenant=r["id"])
    rows.sort(key=lambda e: e["ts"], reverse=True)
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(due=resellers.due_info(r), message=r.get("due_message", ""), settlements=resellers.settlements(r["id"])[:20],
                   admin_support=settings.bot_settings()["support_username"],
                   items=[{"id": e["id"], "ts": e["ts"], "app_name": e.get("app_name", ""), "amount": e["amount"],
                           "commission": e.get("commission", 0), "user": e["user"].get("full_name", "")} for e in page], **meta)


@rbp.get("/support")
def get_support():
    return jsonify(username=g.reseller.get("support_username", ""), text=g.reseller.get("support_text", ""),
                   html=g.reseller.get("support_html", ""))


@rbp.put("/support")
def put_support():
    d = body()
    upd = {"support_text": utils.clean_text(d.get("text"), "Support text", 1500, False),
           "support_username": utils.clean_username(d.get("username"))}
    db.update(f"/resellers/{g.reseller['id']}", upd)
    _log("support_saved")
    return jsonify(ok=True)


@rbp.get("/settings")
def get_settings():
    r = g.reseller
    return jsonify(bot_name=r.get("bot_name", ""), bot_username=r.get("bot_username", ""),
                   bot_token=resellers.mask_token(r.get("bot_token", "")), support_username=r.get("support_username", ""),
                   welcome_message=r.get("welcome_message", ""), login_username=r.get("login_username", ""),
                   commission_pct=float(r.get("commission_pct", 0)))


@rbp.put("/settings")
def put_settings():
    d = body()
    allowed = {k: d[k] for k in ("bot_token", "bot_name", "support_username", "welcome_message") if k in d}
    if "welcome_message" in allowed:
        allowed["welcome_message"] = utils.clean_text(allowed["welcome_message"], "Welcome message", 1500, False)
        db.update(f"/resellers/{g.reseller['id']}", {"welcome_message": allowed.pop("welcome_message")})
    try:
        r = resellers.save(allowed, g.reseller["id"], None, f"reseller:{g.reseller['name']}", admin=False)
    except TelegramError as e:
        raise ApiError(f"Telegram refused that bot token: {e.description}")
    _log("settings_saved", ", ".join(allowed))
    return jsonify(ok=True, bot_username=r.get("bot_username", ""))
