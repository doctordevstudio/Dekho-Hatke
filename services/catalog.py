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


def purchasable(app):
    """Admin-made apps have no status (= live). Seller apps are sold only once approved and not disabled."""
    return bool(app) and not app.get("deleted") and app.get("status", "live") == "live" and not app.get("disabled")


def get_plan(plan_id):
    p = db.get(f"/plans/{plan_id}")
    return p if p and not p.get("deleted") else None


def plan_days(p):
    return max(int(p["validity_value"]) * _UNIT_DAYS.get(p["validity_unit"], 1), 1 / 24)


def price_for(plan, tenant=""):
    """Effective price + enabled flag for a tenant ('' = main shop)."""
    if not tenant or plan.get("kind") == "trial":
        return plan.get("amount", 0), True
    ov = db.get(f"/reseller_prices/{tenant}/{plan['id']}") or {}
    return ov.get("price", plan["amount"]), ov.get("enabled", True)


def plans_for(app_id, tenant=""):
    """Plans a shopper of this tenant can see for an app, each with `price`, `per_day` and `save_pct`.
    Reseller shops never show free trials. Order: trial first, then cheapest first."""
    out = []
    for p in live("/plans"):
        if p["app_id"] != app_id:
            continue
        if p.get("kind") == "trial" and tenant:
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
    out.sort(key=lambda p: (p.get("kind") != "trial", p["price"], plan_days(p)))
    return out


@cached(5)
def tree(tenant=""):
    """[{id, name, icon, apps: [app, ...]}] -- only categories/apps that have something to buy."""
    plans_by_app = {}
    for p in live("/plans"):
        plans_by_app.setdefault(p["app_id"], []).append(p)
    apps = []
    for a in live("/apps"):
        if purchasable(a) and any(_visible(p, tenant) for p in plans_by_app.get(a["id"], [])):
            apps.append(a)
    apps.sort(key=lambda a: (-int(a.get("sales_count", 0)), a.get("created_at", 0)))  # best sellers first
    cats = categories()
    known = {c["id"] for c in cats}
    out = []
    for c in cats:
        mine = [a for a in apps if a.get("category_id") == c["id"]]
        if mine:
            out.append({"id": c["id"], "name": c["name"], "icon": c.get("icon") or "📁", "apps": mine})
    other = [a for a in apps if a.get("category_id") not in known]
    if other:
        out.append({"id": OTHER_ID, "name": "Other", "icon": "📦", "apps": other})
    return out


def _visible(plan, tenant):
    if plan.get("kind") == "trial":
        return not tenant
    return price_for(plan, tenant)[1]


def active_users(app_id):
    """Real social-proof number: licenses currently valid for the app."""
    from services import licenses
    return sum(1 for l in licenses.all_licenses() if l["app_id"] == app_id and licenses.status_of(l) == "active")
