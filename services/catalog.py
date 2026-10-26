"""Catalog: categories -> apps -> plans, with per-reseller price overrides.

Everything the bot shows in the Purchase flow comes from here, so main bot and reseller bots stay consistent.
"""
from core import db
from core.cache import cached

OTHER_ID = "other"
_UNIT_DAYS = {"days": 1.0, "hours": 1 / 24, "years": 365.0}


def live(path):
    return [x for x in db.get_list(path) if not x.get("deleted")]


def categories():
    rows = live("/categories")
    rows.sort(key=lambda c: (int(c.get("order", 0)), c.get("created_at", 0)))
    return rows


def get_app(app_id):
    a = db.get(f"/apps/{app_id}")
    return a if a and not a.get("deleted") else None


def category_off(cid):
    """True when the admin switched the whole category off in the shop (its apps stay usable for existing customers)."""
    c = db.get(f"/categories/{cid}") if cid else None
    return bool(c and c.get("disabled"))


def purchasable(app):
    """Admin-made apps have no status (= live). Seller apps are sold only once approved and not disabled.
    The admin can also stop SELLING an app (`sale_off`) or a whole category without touching existing customers' keys."""
    return (bool(app) and not app.get("deleted") and app.get("status", "live") == "live" and not app.get("disabled")
            and not app.get("sale_off") and not category_off(app.get("category_id")))


def get_plan(plan_id):
    p = db.get(f"/plans/{plan_id}")
    return p if p and not p.get("deleted") else None


def plan_days(p):
    return max(int(p["validity_value"]) * _UNIT_DAYS.get(p["validity_unit"], 1), 1 / 24)


def price_for(plan, tenant=""):
    """Effective price + enabled flag for a tenant ('' = main shop)."""
    if plan.get("kind") == "short":  # Free (short link) plans exist in the main shop only
        return 0, not tenant
    if not tenant or plan.get("kind") == "trial":
        return plan.get("amount", 0), True
    ov = db.get(f"/reseller_prices/{tenant}/{plan['id']}") or {}
    return ov.get("price", plan["amount"]), ov.get("enabled", True)


def plans_for(app_id, tenant=""):
    """Plans a shopper of this tenant can see for an app, each with `price`, `per_day` and `save_pct`.
    Order: the admin's sort number (1 first); plans without one come after: trial first, then cheapest."""
    out = []
    trials = (db.get(f"/reseller_trials/{tenant}") or {}) if tenant else {}
    for p in live("/plans"):
        if p["app_id"] != app_id or p.get("disabled"):
            continue
        if p.get("kind") == "trial" and tenant and app_id not in trials:  # reseller shops show trials only when enabled
            continue
        if p.get("kind") == "short" and not _short_ready(p):
            continue
        price, enabled = price_for(p, tenant)
        if not enabled:
            continue
        out.append({**p, "price": price, "per_day": round(price / plan_days(p), 2) if price else 0})
    paid = [p for p in out if p.get("kind") != "trial" and p["per_day"]]
    if len(paid) > 1:
        top = max(p["per_day"] for p in paid)
        for p in paid:
            pct = round((1 - p["per_day"] / top) * 100)
            p["save_pct"] = pct if pct >= 10 else 0
    # the admin's sort number first (1 = shown first); plans without one follow: free trial, then cheapest
    out.sort(key=lambda p: (0 if int(p.get("sort") or 0) > 0 else 1, int(p.get("sort") or 0),
                            p.get("kind") != "trial", p["price"], plan_days(p)))
    return out


def app_rank_key(a):
    """Highlighted apps first, then the admin's manual ranking, then best sellers."""
    rank = int(a.get("rank", 0) or 0)
    return (0 if a.get("highlighted") else 1, rank if rank > 0 else 10 ** 6, -int(a.get("sales_count", 0)),
            a.get("created_at", 0))


@cached(5)
def tree(tenant=""):
    """[{id, name, icon, apps: [app, ...]}] -- only categories/apps that have something to buy."""
    plans_by_app = {}
    for p in live("/plans"):
        plans_by_app.setdefault(p["app_id"], []).append(p)
    hid = (db.get(f"/reseller_hidden/{tenant}") or {}) if tenant else {}
    hid_apps, hid_cats = hid.get("apps") or {}, hid.get("categories") or {}
    trials = (db.get(f"/reseller_trials/{tenant}") or {}) if tenant else {}
    apps = []
    for a in live("/apps"):
        if a["id"] in hid_apps or (a.get("category_id") or "other") in hid_cats:
            continue
        if purchasable(a) and any(_visible(p, tenant, trials) for p in plans_by_app.get(a["id"], [])):
            apps.append(a)
    apps.sort(key=app_rank_key)  # highlighted, manual rank, then most purchased
    cats = [c for c in categories() if c["id"] not in hid_cats and not c.get("disabled")]
    known = {c["id"] for c in cats}
    out = []
    for c in cats:
        mine = [a for a in apps if a.get("category_id") == c["id"]]
        if mine:
            out.append({"id": c["id"], "name": c["name"], "icon": c.get("icon") or "📁", "apps": mine})
    other = [a for a in apps if a.get("category_id") not in known]
    if other and OTHER_ID not in hid_cats:
        out.append({"id": OTHER_ID, "name": "Other", "icon": "📦", "apps": other})
    return out


def _short_ready(plan):
    from services import shortener
    return shortener.plan_ready(plan)


def _visible(plan, tenant, trials=None):
    if plan.get("disabled"):
        return False
    if plan.get("kind") == "short" and (tenant or not _short_ready(plan)):
        return False
    if plan.get("kind") == "trial":
        return (not tenant) or plan["app_id"] in (trials or {})
    return price_for(plan, tenant)[1]


def active_users(app_id):
    """Real social-proof number: licenses currently valid for the app."""
    from services import licenses
    return sum(1 for l in licenses.all_licenses() if l["app_id"] == app_id and licenses.status_of(l) == "active")
