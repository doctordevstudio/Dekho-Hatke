"""Balance page: wallet settings, every user's wallet, manual credit / debit, wallet ledger."""
from flask import jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import referral, rewards, users, wallet
from . import body, both, bp, tenant


def _scope(sid):
    t = tenant()
    return sid.startswith(f"{t}~") if t else "~" not in sid


@bp.get("/wallet/settings")
def get_wallet_settings():
    return jsonify(wallet.settings())


@bp.put("/wallet/settings")
def put_wallet_settings():
    wallet.save_settings(body())
    log_action("settings.wallet_saved", str(wallet.settings()))
    return jsonify(ok=True)


@both("/balances", methods=["GET"])
def balances():
    q = (request.args.get("q") or "").strip().lower().lstrip("@")
    t = tenant()
    pool = {u["chat_id"]: u for u in (db.get_list(f"/r_users/{t}") if t else db.get_list("/users")) if u.get("chat_id")}
    rows, tot = [], {"deposit": 0.0, "refer": 0.0}
    for sid, w in (db.get("/wallets") or {}).items():
        if not _scope(sid):
            continue
        chat = users.chat_of(sid)
        u = pool.get(chat, {"chat_id": chat, "full_name": chat})
        dep, ref = round(float(w.get("deposit", 0)), 2), round(float(w.get("refer", 0)), 2)
        tot["deposit"] += dep
        tot["refer"] += ref
        if q and q not in chat and q not in (u.get("full_name") or "").lower() and q not in (u.get("username") or "").lower():
            continue
        rows.append({"chat_id": chat, "full_name": u.get("full_name", ""), "profile_image_url": u.get("profile_image_url", ""),
                     "deposit": dep, "refer": ref, "bonus": rewards.active_bonus(sid) if not t else 0,
                     "total": round(dep + ref, 2)})
    rows.sort(key=lambda r: -r["total"])
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=page, totals={k: round(v, 2) for k, v in tot.items()},
                   settings=wallet.settings(), **meta)


@both("/users/<chat_id>/wallet", methods=["POST"])
def adjust(chat_id):
    d = body()
    if not users.get_user(chat_id, tenant()):
        raise ApiError("User not found.", 404)
    reason = utils.clean_text(d.get("reason"), "Reason", 120)
    sid = users.scoped(chat_id, tenant())
    new = wallet.adjust(sid, str(d.get("bucket")), d.get("amount"), reason, "panel")
    log_action("wallet.adjusted", f"{d.get('bucket')} {d.get('amount')} ({reason})", target=str(chat_id))
    return jsonify(ok=True, balance=wallet.balance(sid), new=new)


@both("/wallet/ledger", methods=["GET"])
def ledger():
    chat = (request.args.get("chat_id") or "").strip()
    if chat:
        rows = db.query_equal("/wallet_tx", "user_id", users.scoped(chat, tenant()))
    else:
        rows = [r for r in db.query_last("/wallet_tx", "ts", 2000) if _scope(str(r.get("user_id", "")))]
    rows.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[{**r, "kind_label": wallet.KIND_LABEL.get(r["kind"], r["kind"]),
                           "chat_id": users.chat_of(r["user_id"])} for r in page], **meta)
