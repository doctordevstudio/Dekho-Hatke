"""Leaderboard: top 50 by balance. Tabs: Referral (refer money) and Reseller (their balance with the shop)."""
from flask import jsonify, request

from core import db
from services import resellers, users
from . import bp


@bp.get("/leaderboard")
def leaderboard():
    tab = request.args.get("tab", "referral")
    if tab == "reseller":
        rows = []
        for r in resellers.all_resellers():
            info = resellers.due_info(r)
            earned = round(sum(e.get("amount", 0) for e in resellers.entries(r["id"])), 2)
            rows.append({"id": r["id"], "full_name": r["name"], "username": r.get("username", ""),
                         "chat_id": r.get("chat_id", ""), "profile_image_url": f"/media/img/{r['avatar_media_id']}" if r.get("avatar_media_id") else "",
                         "balance": earned, "due": info["commission"]})
        rows.sort(key=lambda x: -x["balance"])
        return jsonify(items=rows[:50], tab=tab)
    pool = {u["chat_id"]: u for u in db.get_list("/users") if u.get("chat_id")}
    rows = []
    for sid, w in (db.get("/wallets") or {}).items():
        if "~" in sid or float(w.get("refer", 0)) <= 0:
            continue
        u = pool.get(sid, {"chat_id": sid, "full_name": sid})
        rows.append({"chat_id": sid, "full_name": u.get("full_name", ""), "username": u.get("username", ""),
                     "profile_image_url": u.get("profile_image_url", ""), "balance": round(float(w["refer"]), 2)})
    rows.sort(key=lambda x: -x["balance"])
    return jsonify(items=rows[:50], tab=tab)
