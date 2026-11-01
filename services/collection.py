""""My Payment Collection": what a UPI owner (the person whose Telegram chat is attached to a UPI id) sees in the
bot -- money received today / yesterday / all time and the pending / failed / success payments of THEIR UPI ids."""
from core import db, utils
from core.cache import cached
from services import payments, stats

BUCKETS = ("pending", "failed", "success")
LABEL = {"pending": "⏳ Pending", "failed": "❌ Failed", "success": "✅ Success"}


def owned_upi_ids(chat_id, tenant=""):
    return {u["id"] for u in db.get_list("/upis") if not u.get("deleted")
            and str(u.get("telegram_chat_id") or "") == str(chat_id) and (u.get("reseller_id") or "") == (tenant or "")}


def is_collector(chat_id, tenant=""):
    return bool(owned_upi_ids(chat_id, tenant))


def money(upi_ids, tenant=""):
    rows = [e for e in stats.upi_ledger(tenant) if e.get("upi_id") in upi_ids]
    out = {}
    for name in ("today", "yesterday", "all"):
        lo, hi = utils.resolve_range(name)
        sel = [e for e in rows if lo <= e.get("ts", 0) <= hi]
        out[name] = {"amount": round(sum(e["amount"] for e in sel), 2), "count": len(sel)}
    return out


@cached(5)
def _txns(tenant, upi_key):
    ids = set(upi_key)
    return [t for t in db.get_list("/txns") if t.get("upi_record_id") in ids and (t.get("reseller_id") or "") == tenant]


def bucket_of(t, now=None):
    now = now or utils.now()
    if t["status"] == "success":
        return "success"
    if t["status"] == "failed":
        return "failed"
    return "pending"  # waiting for the user to pay, or for the owner to verify


def rows(upi_ids, bucket, tenant=""):
    now = utils.now()
    out = [t for t in _txns(tenant, tuple(sorted(upi_ids))) if bucket_of(t, now) == bucket]
    out.sort(key=lambda t: t["created_at"], reverse=True)
    return out


def counts(upi_ids, tenant=""):
    now = utils.now()
    c = {b: 0 for b in BUCKETS}
    for t in _txns(tenant, tuple(sorted(upi_ids))):
        c[bucket_of(t, now)] += 1
    return c


def pending_by_upi():
    """Admin /pending: every open payment grouped by UPI id."""
    groups = {}
    upis = {u["id"]: u for u in db.get_list("/upis")}
    for status in ("pending", "verification"):
        for t in db.query_equal("/txns", "status", status):
            if not payments.uses_upi(t) or (status == "pending" and t.get("expires_at", 0) <= utils.now()):
                continue
            g = groups.setdefault(t["upi_record_id"], {"upi_id": t["upi_id"], "reseller_id": t.get("reseller_id", ""),
                                                        "awaiting_payment": 0, "awaiting_amount": 0,
                                                        "verification": 0, "verification_amount": 0})
            key = "verification" if status == "verification" else "awaiting"
            if key == "verification":
                g["verification"] += 1
                g["verification_amount"] = round(g["verification_amount"] + t["amount"], 2)
            else:
                g["awaiting_payment"] += 1
                g["awaiting_amount"] = round(g["awaiting_amount"] + t["amount"], 2)
    return sorted(groups.values(), key=lambda g: -(g["verification_amount"] + g["awaiting_amount"]))
