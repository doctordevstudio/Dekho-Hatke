"""Earnings (sales). Admin panel = the main shop; reseller panel = that reseller's shop (with the admin commission)."""
from flask import jsonify, request

from core import utils
from services import stats
from . import both, tenant


@both("/earnings", methods=["GET"])
def earnings():
    rng = request.args.get("range", "today")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    app_filter = request.args.get("app_id") or ""
    rows = stats.entries_between(lo, hi, tenant=tenant())

    by_app = {}
    for e in rows:
        a = by_app.setdefault(e["app_id"], {"app_id": e["app_id"], "app_name": e.get("app_name", ""),
                                            "amount": 0, "count": 0})
        a["amount"] = round(a["amount"] + e["amount"], 2)
        a["count"] += 1
    shown = [e for e in rows if not app_filter or e["app_id"] == app_filter]
    shown.sort(key=lambda e: e["ts"], reverse=True)
    page, meta = utils.paginate(shown, request.args.get("page"))  # all sales of the period, paginated
    items = [{"id": e["id"], "ts": e["ts"], "app_id": e["app_id"], "app_name": e.get("app_name", ""),
              "amount": e["amount"], "source": e.get("source", ""), "label": e.get("label", ""),
              "license_key": e.get("license_key", ""), "txn_id": e.get("txn_id", ""),
              "commission": e.get("commission", 0), "user": e["user"]} for e in page]
    return jsonify(amount_total=round(sum(e["amount"] for e in rows), 2), sale_count=len(rows),
                   commission_total=round(sum(e.get("commission", 0) for e in rows), 2),
                   by_app=sorted(by_app.values(), key=lambda x: -x["amount"]), items=items, **meta)
