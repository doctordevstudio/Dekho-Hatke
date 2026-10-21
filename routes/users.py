"""Users: list, search, full details + purchase history, edit everything. Shared by admin and reseller panels."""
from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import licenses as svc
from services import payments, referral, users, wallet
from . import body, both, tenant


def _user_pool():
    t = tenant()
    path = f"/r_users/{t}" if t else "/users"
    return {u["chat_id"]: u for u in db.get_list(path) if not u.get("deleted") and u.get("chat_id")}


def _licenses_by_user():
    by_user = {}
    for l in svc.all_licenses():
        if (l.get("reseller_id") or "") == tenant() and l.get("chat_id") != "test":
            by_user.setdefault(str(l.get("chat_id")), []).append(l)
    return by_user


def _user_rows(q=""):
    q = (q or "").strip().lower().lstrip("@")
    names = {a["id"]: a["name"] for a in db.get_list("/apps")}
    by_user = _licenses_by_user()
    pool = _user_pool()
    for cid, ls in by_user.items():  # license holders that never opened the bot
        if cid not in pool:
            l = ls[0]
            pool[cid] = {"chat_id": cid, "full_name": l.get("full_name", ""), "username": l.get("username", ""),
                         "profile_image_url": l.get("profile_image_url", ""), "joined_at": l.get("created_at", 0),
                         "source": "manual"}
    rows = []
    for cid, u in pool.items():
        ls = sorted(by_user.get(cid, []), key=lambda x: x.get("created_at", 0), reverse=True)
        if q:
            hay = [str(cid), (u.get("full_name") or "").lower(), (u.get("username") or "").lower()]
            hay += [l["key"].lower() for l in ls]
            if not any(q in h for h in hay):
                continue
        rows.append({"chat_id": cid, "full_name": u.get("full_name", ""), "username": u.get("username", ""),
                     "profile_image_url": u.get("profile_image_url", ""), "joined_at": u.get("joined_at", 0),
                     "last_seen": u.get("last_seen", 0), "source": u.get("source", "bot"), "blocked": bool(u.get("blocked")),
                     "licenses": [svc.public(l, names) for l in ls]})
    rows.sort(key=lambda r: r["joined_at"], reverse=True)
    return rows


@both("/users", methods=["GET"])
def list_users():
    page, meta = utils.paginate(_user_rows(request.args.get("q")), request.args.get("page"))
    return jsonify(items=page, **meta)


@both("/search", methods=["GET"])
def global_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(items=[])
    return jsonify(items=_user_rows(q)[:20])


def _find(chat_id):
    chat_id = str(chat_id)
    u = users.get_user(chat_id, tenant())
    lics = [l for l in svc.all_licenses() if str(l.get("chat_id")) == chat_id and (l.get("reseller_id") or "") == tenant()]
    if not u:
        if not lics:
            raise ApiError("User not found.", 404)
        l = lics[0]
        u = {"chat_id": chat_id, "full_name": l.get("full_name", ""), "username": l.get("username", ""),
             "profile_image_url": l.get("profile_image_url", ""), "joined_at": l.get("created_at", 0), "source": "manual"}
    return u, lics


@both("/users/<chat_id>", methods=["GET"])
def user_detail(chat_id):
    u, lics = _find(chat_id)
    t = tenant()
    sid = users.scoped(chat_id, t)
    names = {a["id"]: a["name"] for a in db.get_list("/apps")}
    txns = [x for x in db.query_equal("/txns", "user_id", str(chat_id)) if (x.get("reseller_id") or "") == t]
    txns.sort(key=lambda x: x["created_at"], reverse=True)
    history = [{"id": x["id"], "kind": x.get("kind", "license"), "status": x["status"], "app_name": x.get("app_name", ""),
                "plan_label": x.get("plan_label", ""), "amount": x["amount"], "plan_price": x.get("plan_price", x["amount"]),
                "wallet_used": round(float((x.get("wallet") or {}).get("total", 0)), 2), "created_at": x["created_at"],
                "license_key": x.get("license_key", ""), "via": x.get("via", "upi"), "fail_reason": x.get("fail_reason", "")}
               for x in txns]
    ref_by = u.get("referred_by")
    ref_user = users.get_user(ref_by, t) if ref_by else None
    wal = wallet.balance(sid)
    cp = referral.custom_percent(sid)
    out = {"chat_id": str(chat_id), "full_name": u.get("full_name", ""), "username": u.get("username", ""),
           "profile_image_url": u.get("profile_image_url", ""), "joined_at": u.get("joined_at", 0),
           "last_seen": u.get("last_seen", 0), "source": u.get("source", "bot"), "blocked": bool(u.get("blocked")),
           "note": u.get("note", ""), "wallet": wal,
           "referred_by": {"chat_id": ref_by, "full_name": (ref_user or {}).get("full_name", "")} if ref_by else None,
           "referral": {**referral.stats_for(sid), "custom_percent": cp, "default_percent": referral.default_percent(t),
                        "percent": cp if cp is not None else referral.default_percent(t)},
           "licenses": [svc.public(l, names) for l in sorted(lics, key=lambda x: x.get("created_at", 0), reverse=True)],
           "purchases": history,
           "totals": {"spent": round(sum(h["amount"] + h["wallet_used"] for h in history
                                        if h["status"] == "success" and h["kind"] == "license"), 2),
                      "orders": sum(1 for h in history if h["status"] == "success" and h["kind"] == "license")}}
    return jsonify(out)


@both("/users/<chat_id>", methods=["PATCH"])
def edit_user(chat_id):
    u, _ = _find(chat_id)
    d = body()
    upd = {}
    if "full_name" in d:
        upd["full_name"] = utils.clean_text(d.get("full_name"), "Full name", 80)
    if "username" in d:
        upd["username"] = utils.clean_username(d.get("username"))
    if "profile_image_url" in d:
        upd["profile_image_url"] = utils.clean_url(d.get("profile_image_url"), "Profile image URL")
    if "note" in d:
        upd["note"] = utils.clean_text(d.get("note"), "Note", 400, False)
    if "blocked" in d:
        upd["blocked"] = bool(d.get("blocked"))
    path = users.user_path(chat_id, tenant())
    if not db.get(path):  # manual-license holder that never opened the bot: create the record
        db.set(path, {"chat_id": str(chat_id), "id": str(chat_id), "joined_at": u.get("joined_at", utils.now()),
                      "last_seen": 0, "source": "manual", "full_name": u.get("full_name", ""),
                      "username": u.get("username", ""), "profile_image_url": u.get("profile_image_url", "")})
    if upd:
        db.update(path, upd)
    if "referral_percent" in d:
        referral.set_custom_percent(users.scoped(chat_id, tenant()), d.get("referral_percent"))
        upd["referral_percent"] = d.get("referral_percent") or "default"
    if "full_name" in upd or "username" in upd or "profile_image_url" in upd:  # keep the denormalised copies in sync
        for l in svc.all_licenses():
            if str(l.get("chat_id")) == str(chat_id) and (l.get("reseller_id") or "") == tenant():
                db.update(f"/licenses/{l['key']}", {k: upd[k] for k in ("full_name", "username", "profile_image_url") if k in upd})
    if not upd:
        raise ApiError("Nothing to change.")
    log_action("user.edited", ", ".join(upd), target=str(chat_id))
    return user_detail(chat_id)
