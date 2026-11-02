"""Wallet: two money buckets (deposit, refer) + expiring bonus rewards (see rewards.py).

All balance changes are atomic (database transactions) and leave a ledger row in /wallet_tx.
Spending order for purchases: bonus (expires soonest) -> deposit -> refer (refer money stays withdrawable longest).
"""
from config import Config
from core import db, utils
from core.errors import ApiError
from services import rewards

BUCKETS = ("deposit", "refer")
KIND_LABEL = {"deposit": "Deposit verified", "refer_commission": "Referral commission", "withdraw": "Withdrawal",
              "withdraw_refund": "Withdrawal refunded", "purchase": "License purchase", "refund": "Purchase refunded",
              "admin_adjust": "Admin adjustment"}


def _n(x):
    return round(float(x or 0), 2)


def settings():
    s = db.get("/settings/wallet") or {}
    # a deposit is a UPI payment request, so it is never below the minimum UPI amount (₹10)
    return {"min_deposit": max(_n(s.get("min_deposit", 10)), Config.MIN_UPI_PAYMENT), "min_withdraw": _n(s.get("min_withdraw", 50)) or 50}


def save_settings(d):
    md = utils.parse_amount(d.get("min_deposit"))
    if md < Config.MIN_UPI_PAYMENT:
        raise ApiError(f"The minimum deposit cannot be below {utils.inr(Config.MIN_UPI_PAYMENT)} (UPI requests start there).")
    mw = utils.parse_amount(d.get("min_withdraw"))
    db.update("/settings/wallet", {"min_deposit": md, "min_withdraw": mw})


def balance(uid):
    w = db.get(f"/wallets/{uid}") or {}
    bonus = rewards.active_bonus(uid)
    out = {"deposit": _n(w.get("deposit")), "refer": _n(w.get("refer")), "bonus": bonus}
    out["total"] = _n(out["deposit"] + out["refer"] + out["bonus"])
    return out


def _ledger(uid, bucket, amount, kind, ref, note, after):
    lid = utils.ts_id()
    db.set(f"/wallet_tx/{lid}", {"id": lid, "user_id": str(uid), "ts": utils.now(), "bucket": bucket,
                                 "amount": _n(amount), "kind": kind, "ref": ref, "note": note[:200],
                                 "balance_after": _n(after)})


def credit(uid, bucket, amount, kind, ref="", note=""):
    amount = _n(amount)
    if bucket not in BUCKETS or amount <= 0:
        raise ApiError("Invalid credit.")
    new = db.transaction(f"/wallets/{uid}/{bucket}", lambda cur: round(float(cur or 0) + amount, 2))
    _ledger(uid, bucket, amount, kind, ref, note, new)
    return _n(new)


def debit(uid, bucket, amount, kind, ref="", note=""):
    """All-or-nothing debit. Returns True when the money was taken."""
    amount = _n(amount)
    state = {"ok": False}

    def fn(cur):
        cur = float(cur or 0)
        state["ok"] = cur + 1e-9 >= amount
        return round(cur - amount, 2) if state["ok"] else round(cur, 2)

    new = db.transaction(f"/wallets/{uid}/{bucket}", fn)
    if state["ok"]:
        _ledger(uid, bucket, -amount, kind, ref, note, new)
    return state["ok"]


def take_up_to(uid, bucket, amount, kind, ref="", note=""):
    taken = {"v": 0.0}

    def fn(cur):
        cur = float(cur or 0)
        taken["v"] = _n(min(cur, amount))
        return round(cur - taken["v"], 2)

    new = db.transaction(f"/wallets/{uid}/{bucket}", fn)
    if taken["v"] > 0:
        _ledger(uid, bucket, -taken["v"], kind, ref, note, new)
    return taken["v"]


def reserve(uid, need, ref):
    """Take up to `need` rupees: bonus -> deposit -> refer. Returns a breakdown (kept on the txn so a failed
    or expired payment can give everything back)."""
    need = _n(need)
    bd = {"bonus": 0.0, "deposit": 0.0, "refer": 0.0, "rewards": [], "total": 0.0}
    if need <= 0:
        return bd
    spent = rewards.spend(uid, need, ref)
    bd["rewards"] = spent
    bd["bonus"] = _n(sum(x["amount"] for x in spent))
    left = _n(need - bd["bonus"])
    for bucket in ("deposit", "refer"):
        if left <= 0:
            break
        got = take_up_to(uid, bucket, left, "purchase", ref, f"Used for {ref}")
        bd[bucket] = got
        left = _n(left - got)
    bd["total"] = _n(bd["bonus"] + bd["deposit"] + bd["refer"])
    return bd


def release(uid, bd, ref):
    """Give a reservation back (payment failed / cancelled / expired)."""
    if not bd:
        return
    for bucket in ("deposit", "refer"):
        if _n(bd.get(bucket)) > 0:
            credit(uid, bucket, bd[bucket], "refund", ref, f"Refund for {ref}")
    for r in bd.get("rewards") or []:
        rewards.restore(r["id"], r["amount"], r.get("use", ""))


def confirm(bd):
    """The purchase went through: mark the reward uses as final."""
    for r in (bd or {}).get("rewards") or []:
        rewards.confirm_use(r["id"], r.get("use", ""))


def history(uid, limit=10):
    rows = db.query_equal("/wallet_tx", "user_id", str(uid))
    rows.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
    return rows[:limit]


def adjust(uid, bucket, amount, reason, actor):
    """Admin credit (+) / debit (-) on a user's wallet."""
    if bucket not in BUCKETS:
        raise ApiError("Choose deposit or refer.")
    try:
        amt = _n(amount)
    except (TypeError, ValueError):
        raise ApiError("Enter a valid amount.")
    if amt == 0 or abs(amt) > 1_000_000:
        raise ApiError("Enter a valid amount.")
    if amt > 0:
        return credit(uid, bucket, amt, "admin_adjust", actor, reason)
    if not debit(uid, bucket, -amt, "admin_adjust", actor, reason):
        raise ApiError("The user does not have that much in this wallet.")
    return _n((db.get(f"/wallets/{uid}") or {}).get(bucket))
