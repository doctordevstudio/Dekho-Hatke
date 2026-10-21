from flask import g, jsonify

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog
from .apps import get_app_or_404
from . import body, bp


def _public(p, app_names, sold):
    return {"id": p["id"], "app_id": p["app_id"], "app_name": app_names.get(p["app_id"], ""),
            "kind": p.get("kind", "paid"), "is_best": bool(p.get("is_best")),
            "amount": p["amount"], "validity_value": p["validity_value"], "validity_unit": p["validity_unit"],
            "label": utils.validity_label(p["validity_value"], p["validity_unit"]),
            "sold": sold.get(p["id"], 0), "created_at": p.get("created_at", 0)}


@bp.get("/plans")
def list_plans():
    apps = {a["id"]: a for a in db.get_list("/apps") if not a.get("deleted")}
    names = {k: v["name"] for k, v in apps.items()}
    sold_by_plan, sold_by_app = {}, {}
    for t in db.query_equal("/txns", "status", "success"):  # a "sale" = a verified license purchase
        if t.get("kind", "license") != "license" or not t.get("plan_id"):
            continue
        sold_by_plan[t["plan_id"]] = sold_by_plan.get(t["plan_id"], 0) + 1
        sold_by_app[t["app_id"]] = sold_by_app.get(t["app_id"], 0) + 1
    plans = [p for p in db.get_list("/plans") if not p.get("deleted") and p["app_id"] in apps]
    plans.sort(key=lambda p: (names.get(p["app_id"], ""), p.get("kind") != "trial", p["amount"]))
    return jsonify(
        items=[_public(p, names, sold_by_plan) for p in plans],
        sold_by_app=[{"app_id": k, "app_name": v, "sold": sold_by_app.get(k, 0)} for k, v in names.items()])


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
    _one_per_app(app["id"], kind, best)
    pid = utils.new_id("plan")
    db.set(f"/plans/{pid}", {"id": pid, "app_id": app["id"], "kind": kind, "is_best": best, "amount": amount,
                             "validity_value": val, "validity_unit": unit, "created_at": utils.now(),
                             "created_by": g.admin["username"]})
    what = "FREE TRIAL" if kind == "trial" else utils.inr(amount)
    log_action("plan.created", f"{app['name']} {what} / {utils.validity_label(val, unit)}", target=pid)
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
    _one_per_app(p["app_id"], kind, best, keep_id=pid)
    db.update(f"/plans/{pid}", {"kind": kind, "is_best": best, "amount": amount, "validity_value": val,
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
