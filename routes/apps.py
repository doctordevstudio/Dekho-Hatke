from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog, sellers
from services import licenses as lic_svc
from services import stats
from . import body, bp


def public_app(a, with_key=False):
    base = request.url_root.rstrip("/")
    out = {"id": a["id"], "name": a["name"], "image_url": a.get("image_url", ""),
           "category_id": a.get("category_id", ""), "video_id": a.get("video_id", ""),
           "setup_video_id": a.get("setup_video_id", ""), "apk_id": a.get("apk_id", ""),
           "description": a.get("description", ""), "features": a.get("features", ""),
           "endpoint": f"{base}/api/v1/{a['id']}/verify", "heartbeat_endpoint": f"{base}/api/v1/{a['id']}/heartbeat",
           "created_at": a.get("created_at", 0), "status": sellers.review_state(a),
           "disabled": bool(a.get("disabled")), "disabled_by": a.get("disabled_by", ""),
           "sales_count": int(a.get("sales_count", 0)), "seller_id": a.get("seller_id", ""),
           "pending_submission": a.get("pending_submission", ""),
           "maintenance": bool((a.get("maintenance") or {}).get("enabled"))}
    if a.get("seller_id"):
        s = sellers.get(a["seller_id"])
        out["seller_name"] = s["name"] if s else "(deleted seller)"
    if with_key:
        out["api_key"] = a["api_key"]
    return out


def app_fields(d, partial=False):
    """Validate the editable app fields. With partial=True only the keys present are returned."""
    out = {}

    def want(k):
        return k in d or not partial

    if want("name"):
        out["name"] = utils.clean_text(d.get("name"), "App name", 60)
    if want("image_url"):
        out["image_url"] = utils.clean_url(d.get("image_url"), "Image URL")
    if want("category_id"):
        cid = str(d.get("category_id") or "")
        if cid:
            c = db.get(f"/categories/{cid}")
            if not c or c.get("deleted"):
                raise ApiError("Choose a valid category.")
        out["category_id"] = cid
    for key, label in (("video_id", "Video ID"), ("setup_video_id", "Setup video ID"), ("apk_id", "App (APK) ID")):
        if want(key):
            out[key] = utils.clean_text(d.get(key), label, 400, False)
    if want("description"):
        out["description"] = utils.clean_text(d.get("description"), "Description", 400, False)
    if want("features"):
        out["features"] = utils.clean_text(d.get("features"), "Features", 1500, False)
    return out


def get_app_or_404(app_id):
    a = db.get(f"/apps/{app_id}")
    if not a or a.get("deleted"):
        raise ApiError("App not found.", 404)
    return a


@bp.get("/dashboard")
def dashboard():
    start, end = request.args.get("start"), request.args.get("end")
    summ = stats.summary(None, start, end)
    apps = stats.live_apps()
    per = stats.today_per_app()
    counts = {}
    for l in lic_svc.all_licenses():
        counts[l["app_id"]] = counts.get(l["app_id"], 0) + 1
    shown = [a for a in apps if a.get("status") != "draft"]  # a seller's unpublished API app is not a shop item
    shown.sort(key=lambda x: (-int(x.get("sales_count", 0)), x.get("created_at", 0)))
    page, meta = utils.paginate(shown, request.args.get("page"), 12)
    cards = []
    for a in page:
        p = public_app(a)
        p.update(today_active=per.get(a["id"], {}).get("active", 0),
                 today_earning=per.get(a["id"], {}).get("earning", 0), licenses=counts.get(a["id"], 0))
        cards.append(p)
    users = db.shallow("/users")
    return jsonify(total_apps=len(shown), summary=summ, apps=cards, total_users=len(users),
                   pending_review=len(sellers.submissions("pending")), **meta)


@bp.get("/stats/active")
def active_list():
    """Drill-down for the 'Active users' cards: who used the app(s) in the period, paginated."""
    app_id = request.args.get("app_id") or None
    if app_id:
        get_app_or_404(app_id)
    lo, hi = utils.resolve_range(request.args.get("range", "today"), request.args.get("start"), request.args.get("end"))
    ids = [app_id] if app_id else [a["id"] for a in stats.live_apps()]
    keys = {}
    if request.args.get("range") == "all":
        for l in lic_svc.all_licenses():
            if l.get("first_seen") and l["app_id"] in ids:
                keys[l["key"]] = l
    else:
        for aid, ks in stats._active_sets(ids, utils.days_in_range(lo, hi)).items():
            for k in ks:
                keys[k] = None
    names = {a["id"]: a["name"] for a in stats.live_apps()}
    rows = []
    for k, l in keys.items():
        l = l or db.get(f"/licenses/{k}")
        if l and not l.get("deleted"):
            rows.append({"key": l["key"], "app_name": names.get(l["app_id"], ""), "full_name": l.get("full_name", ""),
                         "chat_id": l.get("chat_id", ""), "username": l.get("username", ""),
                         "last_seen": l.get("last_seen", 0), "status": lic_svc.status_of(l)})
    rows.sort(key=lambda r: -r["last_seen"])
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(count=len(rows), items=page, **meta)


@bp.get("/stats")
def app_stats():
    app_id = request.args.get("app_id")
    if app_id:
        get_app_or_404(app_id)
    return jsonify(stats.summary(app_id or None, request.args.get("start"), request.args.get("end")))


@bp.get("/apps")
def list_apps():
    rows = [a for a in stats.live_apps() if a.get("status") != "draft"]
    return jsonify(apps=[public_app(a) for a in rows])


@bp.post("/apps")
def create_app():
    f = app_fields(body())
    aid = utils.new_id("app")
    a = {"id": aid, **f, "api_key": utils.gen_api_key(), "created_at": utils.now(), "created_by": g.admin["username"],
         "status": "live", "sales_count": 0}
    db.set(f"/apps/{aid}", a)
    log_action("app.created", f["name"], target=aid)
    return jsonify(public_app(a, with_key=True)), 201


@bp.get("/apps/<app_id>")
def get_app(app_id):
    a = get_app_or_404(app_id)
    out = public_app(a, with_key=g.admin["role"] in ("admin", "admin2"))
    out["plans"] = [{"id": p["id"], "kind": p.get("kind", "paid"), "amount": p["amount"], "is_best": bool(p.get("is_best")),
                     "label": utils.validity_label(p["validity_value"], p["validity_unit"])}
                    for p in catalog.live("/plans") if p["app_id"] == app_id]
    out["licenses"] = sum(1 for l in lic_svc.all_licenses() if l["app_id"] == app_id)
    out["active_now"] = catalog.active_users(app_id)
    return jsonify(out)


@bp.patch("/apps/<app_id>")
def edit_app(app_id):
    a = get_app_or_404(app_id)
    upd = app_fields(body(), partial=True)
    if upd:
        db.update(f"/apps/{app_id}", upd)
        log_action("app.edited", ", ".join(upd), target=app_id)
    a.update(upd)
    return jsonify(public_app(a))


@bp.delete("/apps/<app_id>")
def delete_app(app_id):
    a = get_app_or_404(app_id)
    db.update(f"/apps/{app_id}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": g.admin["username"]})
    log_action("app.deleted", a["name"], target=app_id)
    return jsonify(ok=True)


@bp.post("/apps/<app_id>/regenerate-key")
def regen_key(app_id):
    get_app_or_404(app_id)
    key = utils.gen_api_key()
    db.update(f"/apps/{app_id}", {"api_key": key, "key_rotated_at": utils.now()})
    log_action("app.key_regenerated", "", target=app_id)
    return jsonify(api_key=key)
