from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog, shortener
from .apps import get_app_or_404
from . import body, bp


def _public(p, app_names, sold, upis=None, shorts=None):
    u = (upis or {}).get(p.get("upi_id") or "")
    sh = (shorts or {}).get(p.get("shorturl_id") or "")
    return {"shorturl_id": p.get("shorturl_id") or "", "shorturl_name": (sh or {}).get("name", "") if p.get("shorturl_id") else "",
            "shorturl_ok": bool(sh and not sh.get("deleted") and sh.get("active", True)),
            "disabled": bool(p.get("disabled")), "sort": int(p.get("sort") or 0), "upi_id": p.get("upi_id") or "",
            "upi_label": (u.get("name") or u.get("upi_id")) if u else ("" if not p.get("upi_id") else "(removed)"),
            "id": p["id"], "app_id": p["app_id"], "app_name": app_names.get(p["app_id"], ""),
            "kind": p.get("kind", "paid"), "is_best": bool(p.get("is_best")),
            "amount": p["amount"], "requests": int(p.get("requests") or 0), "no_expiry": bool(p.get("no_expiry")),
            "currency": catalog.currency_of(p), "pay_methods": catalog.pay_methods_of(p),
            "amount_inr": catalog.plan_inr(p), "validity_value": p["validity_value"], "validity_unit": p["validity_unit"],
            "label": catalog.plan_text(p),
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
    shorts = {r["id"]: r for r in shortener.all_shorteners(include_deleted=True)}
    empty = {"main": 0, "reseller": 0, "amount_main": 0, "amount_reseller": 0}
    app_rows = []
    for aid, a in apps.items():
        s = by_app.get(aid, empty)
        app_rows.append({"app_id": aid, "app_name": a["name"], "image_url": a.get("image_url", ""),
                         "plans": sum(1 for p in plans if p["app_id"] == aid), "seller_name": a.get("seller_id") and "seller app" or "",
                         "sold_main": s["main"], "sold_reseller": s["reseller"], "sold": s["main"] + s["reseller"],
                         "amount_main": s["amount_main"], "amount_reseller": s["amount_reseller"]})
    app_rows.sort(key=lambda r: (-r["sold"], r["app_name"].lower()))
    from services import xmr
    crypto_ready = xmr.configured()
    return jsonify(items=[_public(p, names, {k: v["main"] + v["reseller"] for k, v in by_plan.items()}, upis, shorts) | {
        "sold_main": (by_plan.get(p["id"]) or empty)["main"], "sold_reseller": (by_plan.get(p["id"]) or empty)["reseller"]}
        for p in plans], apps=app_rows, crypto_ready=crypto_ready,
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
    if kind in ("trial", "short"):
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


def _read(d, short=""):
    kind = "trial" if str(d.get("kind")) == "trial" else ("short" if short else "paid")
    val, unit = utils.parse_validity(d.get("validity_value"), d.get("validity_unit"))
    amount = 0 if kind in ("trial", "short") else utils.parse_amount(d.get("amount"))
    return kind, amount, val, unit, bool(d.get("is_best")) and kind == "paid"


def _money(d, cur=None):
    """(currency, pay_methods). A plan can be priced in ₹ or $ and sold by UPI, crypto or both."""
    cur = cur or {}
    currency = str(d.get("currency", catalog.currency_of(cur))).upper()
    if currency not in ("INR", "USD"):
        raise ApiError("Currency must be INR or USD.")
    pm = str(d.get("pay_methods", catalog.pay_methods_of(cur)))
    if pm not in ("upi", "crypto", "both"):
        raise ApiError("Payment methods must be UPI, Crypto or both.")
    if pm != "upi":
        from services import xmr
        if not xmr.configured() and pm == "crypto":
            raise ApiError("Create the crypto wallet first (Crypto page) before making a crypto-only plan.")
    return currency, pm


def _limit(d, app, cur=None):
    """Pay-as-you-go apps sell a number of requests (+ an optional expiry) instead of a validity period.
    Returns (requests, no_expiry) and fills validity_value / validity_unit in `d` so the rest of the code keeps working."""
    if (app.get("plan_type") or "time") != "limit":
        return 0, False
    cur = cur or {}
    try:
        n = int(d.get("requests", cur.get("requests") or 0))
    except (TypeError, ValueError):
        raise ApiError("Requests must be a whole number.")
    if not 1 <= n <= 1_000_000_000:
        raise ApiError("Enter how many requests this plan gives (1 to 1,000,000,000).")
    if "expiry_value" not in d and cur:  # editing without touching the expiry
        d["validity_value"], d["validity_unit"] = cur.get("validity_value"), cur.get("validity_unit")
        return n, bool(cur.get("no_expiry"))
    ev = d.get("expiry_value")
    if ev in (None, "", 0, "0"):
        d["validity_value"], d["validity_unit"] = 100, "years"
        return n, True
    d["validity_value"], d["validity_unit"] = ev, d.get("expiry_unit") or "days"
    return n, False


def _short(d, current=""):
    """'' = a normal plan; otherwise the id of a shortener that generates license keys (Free via short link)."""
    if "shorturl_id" not in d:
        return current or ""
    v = str(d.get("shorturl_id") or "")
    if not v or str(d.get("kind")) == "trial":
        return ""
    sh = shortener.shortener(v)
    if not sh or sh.get("kind") != "license" or not sh.get("active", True):
        raise ApiError("Choose a shortener that generates license keys and is switched on.")
    return v


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
    short = _short(d)
    requests_, no_exp = _limit(d, app)
    if requests_ and short:
        raise ApiError("Free short-link plans are for time-based apps only.")
    kind, amount, val, unit, best = _read(d, short)
    sort, upi = _sort(d), _upi(d, kind)
    currency, pm = _money(d)
    if app.get("seller_id"):  # crypto + dollar prices are for the admin's own apps only
        currency, pm = "INR", "upi"
    _one_per_app(app["id"], kind, best)
    pid = utils.new_id("plan")
    db.set(f"/plans/{pid}", {"id": pid, "app_id": app["id"], "kind": kind, "is_best": best, "amount": amount, "sort": sort, "upi_id": upi,
                             "currency": currency, "pay_methods": pm, "shorturl_id": short,
                             "requests": requests_, "no_expiry": no_exp,
                             "validity_value": val, "validity_unit": unit, "created_at": utils.now(),
                             "created_by": g.admin["username"]})
    what = "FREE TRIAL" if kind == "trial" else ("FREE via short link " + short if kind == "short" else (f"${amount:g}" if currency == "USD" else utils.inr(amount)))
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
    d.setdefault("kind", "trial" if p.get("kind") == "trial" else "paid")
    short = _short(d, p.get("shorturl_id"))
    requests_, no_exp = _limit(d, db.get(f"/apps/{p['app_id']}") or {}, p)
    kind, amount, val, unit, best = _read(d, short)
    sort, upi = _sort(d, p.get("sort")), _upi(d, kind, p.get("upi_id"))
    currency, pm = _money(d, p)
    if (db.get(f"/apps/{p['app_id']}") or {}).get("seller_id"):
        currency, pm = "INR", "upi"
    _one_per_app(p["app_id"], kind, best, keep_id=pid)
    db.update(f"/plans/{pid}", {"kind": kind, "is_best": best, "sort": sort, "upi_id": upi, "amount": amount, "currency": currency,
                                "pay_methods": pm, "shorturl_id": short, "requests": requests_, "no_expiry": no_exp, "validity_value": val,
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
