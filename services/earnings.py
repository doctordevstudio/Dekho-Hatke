"""Earnings ledger (sales) and UPI ledger (cash received per UPI).

* /earnings   -> one row per sale (UPI cash + deposit-wallet part). Reseller sales carry reseller_id and the
                 admin's commission at the time of sale.
* /upi_ledger -> one row per verified UPI payment (license purchases AND wallet deposits).
"""
from core import db, utils
from core.cache import cache


def _user(u):
    return {"chat_id": str(u.get("chat_id", "")), "full_name": u.get("full_name", ""),
            "username": u.get("username", ""), "profile_image_url": u.get("profile_image_url", "")}


def record(*, app, amount, source, user, license_key, txn_id="", upi_id="", plan_id="", label="",
           reseller_id="", commission_pct=0):
    eid = utils.ts_id()
    amount = round(float(amount), 2)
    row = {
        "id": eid, "ts": utils.now(), "app_id": app["id"], "app_name": app.get("name", ""),
        "amount": amount, "source": source, "license_key": license_key, "txn_id": txn_id,
        "upi_id": upi_id, "plan_id": plan_id, "label": label, "reseller_id": reseller_id or "",
        "commission_pct": commission_pct or 0,
        "commission": round(amount * float(commission_pct or 0) / 100, 2) if reseller_id else 0,
        "user": _user(user),
    }
    db.set(f"/earnings/{eid}", row)
    if reseller_id:
        cache.clear()  # dues / forced-maintenance state must react immediately to a new sale
        try:
            from services import resellers
            resellers.after_sale(reseller_id)
        except Exception:  # never let a notification break a sale
            import logging
            logging.getLogger("earnings").exception("due notification failed")
    return row


def record_upi(*, upi_id, amount, kind, user, txn_id, app_id="", app_name="", reseller_id=""):
    eid = utils.ts_id()
    row = {"id": eid, "ts": utils.now(), "upi_id": upi_id, "amount": round(float(amount), 2), "kind": kind,
           "txn_id": txn_id, "app_id": app_id, "app_name": app_name or ("Wallet deposit" if kind == "deposit" else ""),
           "reseller_id": reseller_id or "", "user": _user(user)}
    db.set(f"/upi_ledger/{eid}", row)
    return row
