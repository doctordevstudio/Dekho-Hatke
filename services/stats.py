"""Active users + earnings, for today / yesterday / all time / custom range.

Active user = a distinct license (account) that successfully called the app API in the period.
Earnings come from the earnings ledger (UPI payments + manual licenses with an amount).
"""
from core import db, utils
from core.cache import cached
from services import licenses as lic_svc


def live_apps():
    return [a for a in db.get_list("/apps") if not a.get("deleted")]


def _active_sets(app_ids, days):
    jobs = [(a, d) for a in app_ids for d in days]
    res = db.pmap(lambda j: (j[0], db.shallow(f"/activity/{j[0]}/{j[1]}")), jobs)
    out = {a: set() for a in app_ids}
    for a, keys in res:
        out[a].update(keys)
    return out


@cached(10)
def ledger(tenant=""):
    """Sales rows for one shop: '' = main shop (excludes reseller sales), otherwise that reseller."""
    if tenant:
        return db.query_equal("/earnings", "reseller_id", tenant)
    return [e for e in db.get_list("/earnings") if not e.get("reseller_id")]


@cached(10)
def upi_ledger(tenant=""):
    if tenant:
        return db.query_equal("/upi_ledger", "reseller_id", tenant)
    return [e for e in db.get_list("/upi_ledger") if not e.get("reseller_id")]


def _sum(entries, lo, hi):
    amt, n = 0, 0
    for e in entries:
        if lo <= e.get("ts", 0) <= hi:
            amt += e.get("amount", 0)
            n += 1
    return round(amt, 2), n


def summary(app_id=None, start=None, end=None):
    """Return {'active': {...}, 'earning': {...}} for the 4 ranges (custom only when start+end given)."""
    bounds = {n: utils.resolve_range(n) for n in ("today", "yesterday", "all")}
    if start and end:
        bounds["custom"] = utils.resolve_range("custom", start, end)
    app_ids = [app_id] if app_id else [a["id"] for a in live_apps()]

    active = {}
    for name, (lo, hi) in bounds.items():
        if name == "all":
            active[name] = sum(1 for l in lic_svc.all_licenses()
                               if l.get("first_seen") and l["app_id"] in app_ids)
        else:
            sets = _active_sets(app_ids, utils.days_in_range(lo, hi))
            active[name] = len(set().union(*sets.values())) if sets else 0

    entries = [e for e in ledger() if not app_id or e.get("app_id") == app_id]
    earning = {n: _sum(entries, lo, hi)[0] for n, (lo, hi) in bounds.items()}
    sales = {n: _sum(entries, lo, hi)[1] for n, (lo, hi) in bounds.items()}
    return {"active": active, "earning": earning, "sales": sales}


def today_per_app():
    """{app_id: {'active': n, 'earning': amt}} for the dashboard cards."""
    ids = [a["id"] for a in live_apps()]
    lo, hi = utils.resolve_range("today")
    sets = _active_sets(ids, utils.days_in_range(lo, hi))
    entries = ledger()
    out = {}
    for a in ids:
        out[a] = {"active": len(sets.get(a, ())),
                  "earning": _sum([e for e in entries if e.get("app_id") == a], lo, hi)[0]}
    return out


def entries_between(lo, hi, app_id=None, upi_id=None, tenant="", upi=False):
    """Ledger rows in a time range. upi=True reads the UPI cash ledger instead of sales."""
    rows = [e for e in (upi_ledger(tenant) if upi else ledger(tenant)) if lo <= e.get("ts", 0) <= hi]
    if app_id:
        rows = [e for e in rows if e.get("app_id") == app_id]
    if upi_id:
        rows = [e for e in rows if e.get("upi_id") == upi_id]
    return rows
