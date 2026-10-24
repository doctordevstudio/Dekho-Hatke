from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog
from .apps import get_app_or_404
from . import body, bp


def _public(p, app_names, sold, upis=None):
    u = (upis or {}).get(p.get("upi_id") or "")
    return {"disabled": bool(p.get("disabled")), "sort": int(p.get("sort") or 0), "upi_id": p.get("upi_id") or "",
            "upi_label": (u.get("name") or u.get("upi_id")) if u else ("" if not p.get("upi_id") else "(removed)"),
            "id": p["id"], "app_id": p["app_id"], "app_name": app_names.get(p["app_id"], ""),
            "kind": p.get("kind", "paid"), "is_best": bool(p.get("is_best")),
            "amount": p["amount"], "validity_value": p["validity_value"], "validity_unit": p["validity_unit"],
            "label": utils.validity_label(p["validity_value"], p["validity_unit"]),
            "sold": sold.get(p["id"], 0), "created_at": p.get("created_at", 0)}


def _sold_rows():
    """Verified license purchases: the admin bot and the reseller bots are counted separately."""
    return [t for t in db.query_equal("/txns", "status", "success") if t.get("kind", "license") == "license" and t.get("plan_id")]


def _bucket(d, key):
    return d.setdefault(key, {"main": 0, "reseller": 0, "amount_main": 0.0, "amount_reseller": 0.0})


def _add(b, t):
    side = "reseller" if t.get("reseller_id") else "main"
    amount = float(t.get("plan_price", t.get("amount", 0)) or 0)
    b[side] += 1
    b["amount_" + side] = round(b["amount_" + side] + amount, 2)


@bp.get("/plans")
def list_plans():
    apps = {a["id"]: a for a in db.get_list("/apps") if not a.get("deleted") and a.get("status") != "draft"}
    names = {k: v["name"] for k, v in apps.items()}
    by_plan, by_app = {}, {}
    for t in _sold_rows():
        _add(_bucket(by_plan, t["plan_id"]), t)
        _add(_bucket(by_app, t.get("app_id", "")), t)
    plans = [p for p in db.get_list("/plans") if not p.get("deleted") and p["app_id"] in apps]
    plans.sort(key=lambda p: (names.get(p["app_id"], ""), 0 if int(p.get("sort") or 0) > 0 else 1, int(p.get("sort") or 0),
                              p.get("kind") != "trial", p["amount"]))
    upis = {u["id"]: u for u in db.get_list("/upis") if not u.get("deleted") and not u.get("reseller_id")}
    empty = {"main": 0, "reseller": 0, "amount_main": 0, "amount_reseller": 0}
    app_rows = []
    for aid, a in apps.items():
        s = by_app.get(aid, empty)
        app_rows.append({"app_id": aid, "app_name": a["name"], "image_url": a.get("image_url", ""),
                         "plans": sum(1 for p in plans if p["app_id"] == aid), "seller_name": a.get("seller_id") and "seller app" or "",
                         "sold_main": s["main"], "sold_reseller": s["reseller"], "sold": s["main"] + s["reseller"],
                         "amount_main": s["amount_main"], "amount_reseller": s["amount_reseller"]})
    app_rows.sort(key=lambda r: (-r["sold"], r["app_name"].lower()))
    return jsonify(items=[_public(p, names, {k: v["main"] + v["reseller"] for k, v in by_plan.items()}, upis) | {
        "sold_main": (by_plan.get(p["id"]) or empty)["main"], "sold_reseller": (by_plan.get(p["id"]) or empty)["reseller"]}
        for p in plans], apps=app_rows,
        totals={"main": sum(v["main"] for v in by_app.values()), "reseller": sum(v["reseller"] for v in by_app.values()),
                "amount_main": round(sum(v["amount_main"] for v in by_app.values()), 2),
                "amount_reseller": round(sum(v["amount_reseller"] for v in by_app.values()), 2)})


@bp.get("/plans/sales")
def plan_sales():
    """Drill-down: who bought which plan. scope = all | main (admin bot) | reseller (all reseller bots) | <reseller id>."""
    app_id, plan_id = request.args.get("app_id", ""), request.args.get("plan_id", "")
    scope = request.args.get("scope", "all")
    rnames = {r["id"]: r.get("bot_name") or r.get("name", "") for r in db.get_list("/resellers")}
    rows = []
    for t in _sold_rows():
        if (app_id and t.get("app_id") != app_id) or (plan_id and t.get("plan_id") != plan_id):
            continue
        rid = t.get("reseller_id") or ""
        if scope == "main" and rid or scope == "reseller" and not rid or scope not in ("all", "main", "reseller") and rid != scope:
            continue
        rows.append(t)
    rows.sort(key=lambda t: t.get("verified_at") or t.get("created_at", 0), reverse=True)
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(count=len(rows), amount_total=round(sum(float(t.get("plan_price", t.get("amount", 0)) or 0) for t in rows), 2),
                   items=[{"id": t["id"], "ts": t.get("verified_at") or t.get("created_at", 0), "app_name": t.get("app_name", ""),
                           "plan_label": t.get("plan_label", ""), "amount": float(t.get("plan_price", t.get("amount", 0)) or 0),
                           "bot": rnames.get(t.get("reseller_id"), "Reseller bot") if t.get("reseller_id") else "Admin bot",
                           "reseller": bool(t.get("reseller_id")),
                           "user": {k: (t.get("user") or {}).get(k, "") for k in ("chat_id", "full_name", "username", "profile_image_url")}}
                          for t in page], **meta)


def _sort(d, current=None):
    raw = d.get("sort")
    if raw in (None, ""):
        if current:
            return int(current)
        raise ApiError("Enter the sort number (1 = shown first).")
    try:
        n = int(str(raw).strip())
    except ValueError:
        raise ApiError("The sort number must be a whole number (1 = shown first).")
    if not 1 <= n <= 999:
        raise ApiError("The sort number must be between 1 and 999.")
    return n


def _upi(d, kind, current=""):
    """'' / 'random' = round-robin over all active UPI IDs; otherwise the id of one of the main shop's UPI records."""
    if kind == "trial":
        return ""
    if "upi_id" not in d:
        return current or ""
    v = str(d.get("upi_id") or "")
    if v in ("", "random"):
        return ""
    rec = db.get(f"/upis/{v}")
    if not rec or rec.get("deleted") or rec.get("reseller_id"):
        raise ApiError("Choose a UPI ID from the list, or Random.")
    return v


@bp.post("/plans/<pid>/disable")
def disable_plan(pid):
    """Hide (or show again) one plan in the bots. Keys already sold are not affected."""
    from core.cache import cache
    p = _get_plan(pid)
    dis = bool(body().get("disabled", True))
    db.update(f"/plans/{pid}", {"disabled": dis})
    cache.clear()
    log_action("plan.disabled" if dis else "plan.enabled", f"{utils.inr(p['amount'])} / {utils.validity_label(p['validity_value'], p['validity_unit'])}", target=pid)
    return jsonify(ok=True, disabled=dis)


def _read(d):
    kind = "trial" if str(d.get("kind")) == "trial" else "paid"
    val, unit = utils.parse_validity(d.get("validity_value"), d.get("validity_unit"))
    amount = 0 if kind == "trial" else utils.parse_amount(d.get("amount"))
    return kind, amount, val, unit, bool(d.get("is_best")) and kind == "paid"


def _one_per_app(app_id, kind, best, keep_id=None):
    """At most one free trial and one 'best' plan per app."""
    for p in catalog.live("/plans"):
        if p["app_id"] != app_id or p["id"] == keep_id:
            continue
        if kind == "trial" and p.get("kind") == "trial":
            raise ApiError("This app already has a free-trial plan. Edit or delete it first.")
        if best and p.get("is_best"):
            db.update(f"/plans/{p['id']}", {"is_best": False})


@bp.post("/plans")
def create_plan():
    d = body()
    app = get_app_or_404(str(d.get("app_id", "")))
    kind, amount, val, unit, best = _read(d)
    sort, upi = _sort(d), _upi(d, kind)
    _one_per_app(app["id"], kind, best)
    pid = utils.new_id("plan")
    db.set(f"/plans/{pid}", {"id": pid, "app_id": app["id"], "kind": kind, "is_best": best, "amount": amount, "sort": sort, "upi_id": upi,
                             "validity_value": val, "validity_unit": unit, "created_at": utils.now(),
                             "created_by": g.admin["username"]})
    what = "FREE TRIAL" if kind == "trial" else utils.inr(amount)
    log_action("plan.created", f"{app['name']} {what} / {utils.validity_label(val, unit)} · sort {sort} · {'UPI ' + upi if upi else 'random UPI'}", target=pid)
    return jsonify(ok=True, id=pid), 201


def _get_plan(pid):
    p = db.get(f"/plans/{pid}")
    if not p or p.get("deleted"):
        raise ApiError("Plan not found.", 404)
    return p


@bp.patch("/plans/<pid>")
def edit_plan(pid):
    p = _get_plan(pid)
    d = body()
    d.setdefault("kind", p.get("kind", "paid"))
    kind, amount, val, unit, best = _read(d)
    sort, upi = _sort(d, p.get("sort")), _upi(d, kind, p.get("upi_id"))
    _one_per_app(p["app_id"], kind, best, keep_id=pid)
    db.update(f"/plans/{pid}", {"kind": kind, "is_best": best, "sort": sort, "upi_id": upi, "amount": amount, "validity_value": val,
                                "validity_unit": unit, "updated_at": utils.now()})
    log_action("plan.edited", f"{utils.inr(p['amount'])} -> {utils.inr(amount)} / {utils.validity_label(val, unit)}"
               + (" (best)" if best else ""), target=pid)
    return jsonify(ok=True)


@bp.delete("/plans/<pid>")
def delete_plan(pid):
    _get_plan(pid)
    db.update(f"/plans/{pid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": g.admin["username"]})
    log_action("plan.deleted", "", target=pid)
    return jsonify(ok=True)
