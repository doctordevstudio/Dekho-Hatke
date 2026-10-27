"""Purchase section: categories (Apps, Games, ...) and the app list grouped by them."""
from flask import g, jsonify

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog
from . import body, bp
from .apps import public_app


def _public(c, counts):
    return {"id": c["id"], "name": c["name"], "icon": c.get("icon", ""), "order": c.get("order", 0),
            "apps": counts.get(c["id"], 0), "disabled": bool(c.get("disabled")), "created_at": c.get("created_at", 0)}


def _read(d):
    name = utils.clean_text(d.get("name"), "Category name", 30)
    icon = utils.clean_text(d.get("icon"), "Icon", 8, False)
    try:
        order = int(d.get("order") or 0)
    except (TypeError, ValueError):
        raise ApiError("Order must be a number.")
    return {"name": name, "icon": icon, "order": order}


def _get(cid):
    c = db.get(f"/categories/{cid}")
    if not c or c.get("deleted"):
        raise ApiError("Category not found.", 404)
    return c


@bp.get("/categories")
def list_categories():
    counts = {}
    for a in catalog.live("/apps"):
        counts[a.get("category_id", "")] = counts.get(a.get("category_id", ""), 0) + 1
    cats = catalog.categories()
    known = {c["id"] for c in cats}
    uncategorised = sum(n for k, n in counts.items() if k not in known)
    return jsonify(items=[_public(c, counts) for c in cats], uncategorised=uncategorised)


@bp.post("/categories")
def create_category():
    f = _read(body())
    if any(c["name"].lower() == f["name"].lower() for c in catalog.categories()):
        raise ApiError("A category with that name already exists.")
    cid = utils.new_id("cat")
    db.set(f"/categories/{cid}", {"id": cid, **f, "created_at": utils.now(), "created_by": g.admin["username"]})
    log_action("category.created", f["name"], target=cid)
    return jsonify(ok=True, id=cid), 201


@bp.patch("/categories/<cid>")
def edit_category(cid):
    _get(cid)
    f = _read(body())
    db.update(f"/categories/{cid}", f)
    log_action("category.edited", f["name"], target=cid)
    return jsonify(ok=True)


@bp.post("/categories/<cid>/disable")
def disable_category(cid):
    """Hide (or show again) a whole category in the bots' Purchase flow. Existing customers are not affected."""
    from core.cache import cache
    c = _get(cid)
    dis = bool(body().get("disabled", True))
    db.update(f"/categories/{cid}", {"disabled": dis})
    cache.clear()
    log_action("category.disabled" if dis else "category.enabled", c["name"], target=cid)
    return jsonify(ok=True, disabled=dis)


@bp.delete("/categories/<cid>")
def delete_category(cid):
    c = _get(cid)
    db.update(f"/categories/{cid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": g.admin["username"]})
    log_action("category.deleted", c["name"], target=cid)
    return jsonify(ok=True)


@bp.post("/categories/reorder")
def reorder():
    """Category ranking: the shop lists categories in this order (first = top). Body: {"ids": [cid, ...]}"""
    ids = body().get("ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError("Send the category ids in the order you want.")
    known = {c["id"] for c in catalog.categories()}
    seen = []
    for cid in ids:
        if cid in known and cid not in seen:
            seen.append(cid)
    for i, cid in enumerate(seen, start=1):
        db.update(f"/categories/{cid}", {"order": i})
    log_action("category.reordered", ", ".join(seen)[:200])
    return jsonify(ok=True)
