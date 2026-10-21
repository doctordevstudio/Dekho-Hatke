"""Broadcast + Promote app (admin). Seller broadcasts also show up here, waiting for approval."""
from flask import g, jsonify, request

from bot.tg import TelegramError
from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import broadcast, catalog, notify
from . import body, bp


def _row(b):
    out = broadcast.public(b)
    if b.get("kind") == "promote":
        a = catalog.get_app(b.get("app_id", "")) or {}
        out["app_name"] = a.get("name", "")
    return out


@bp.get("/broadcasts")
def list_broadcasts():
    status = request.args.get("status", "")
    rows = broadcast.listing()
    if status:
        rows = [b for b in rows if b["status"] == status]
    page, meta = utils.paginate(rows, request.args.get("page"))
    pending = sum(1 for b in broadcast.listing() if b["status"] == "pending_approval")
    return jsonify(items=[_row(b) for b in page], pending_approval=pending,
                   audience=broadcast.audience_count("admin", {"type": "all"}), **meta)


@bp.post("/broadcasts")
def create_broadcast():
    d = body()
    message = broadcast.clean_message(d.get("message"))
    b = broadcast.create("admin", "Admin", message, {"type": "all"}, actor=g.admin["username"])
    if d.get("send", True):
        broadcast.start(b["id"])
    log_action("broadcast.created", (message["text"] or "[media]")[:80], target=b["id"])
    return jsonify(_row(broadcast.get(b["id"]))), 201


@bp.post("/broadcasts/promote")
def promote():
    d = body()
    message, buttons = broadcast.promote_payload(str(d.get("app_id", "")))
    extra = utils.clean_text(d.get("text"), "Message", 400, False)
    if extra:
        message["text"] = (extra + "\n\n" + message["text"])[:1000]
    b = broadcast.create("admin", "Admin", message, {"type": "all"}, kind="promote", app_id=d["app_id"],
                         plan_buttons=buttons, actor=g.admin["username"])
    broadcast.start(b["id"])
    log_action("broadcast.promote", catalog.get_app(d["app_id"])["name"], target=b["id"])
    return jsonify(_row(broadcast.get(b["id"]))), 201


@bp.post("/broadcasts/test")
def test_broadcast():
    """Send the message only to the admin chat so it can be checked first."""
    message = broadcast.clean_message(body().get("message"))
    chats = notify.admin_chats()
    if not chats:
        raise ApiError("Set the Admin Chat ID in Settings first.")
    try:
        broadcast._send_one({"message": message, "owner": "admin", "plan_buttons": []}, chats[0])
    except TelegramError as e:
        raise ApiError(f"Telegram rejected the message: {e.description}")
    return jsonify(ok=True)


@bp.patch("/broadcasts/<bid>")
def edit_broadcast(bid):
    out = broadcast.edit(bid, body().get("message"), g.admin["username"])
    log_action("broadcast.edited", "", target=bid)
    return jsonify(out)


@bp.post("/broadcasts/<bid>/rebroadcast")
def rebroadcast(bid):
    d = body()
    broadcast.rebroadcast(bid, bool(d.get("delete_old")), d.get("message"))
    log_action("broadcast.rebroadcast", "delete old" if d.get("delete_old") else "", target=bid)
    return jsonify(ok=True)


@bp.post("/broadcasts/<bid>/stop")
def stop(bid):
    broadcast.stop(bid)
    log_action("broadcast.stopped", "", target=bid)
    return jsonify(ok=True)


@bp.post("/broadcasts/<bid>/approve")
def approve_broadcast(bid):
    broadcast.approve(bid, g.admin["username"])
    log_action("broadcast.approved", "", target=bid)
    return jsonify(ok=True)


@bp.post("/broadcasts/<bid>/reject")
def reject_broadcast(bid):
    broadcast.reject(bid, body().get("reason"), g.admin["username"])
    log_action("broadcast.rejected", "", target=bid)
    return jsonify(ok=True)


@bp.delete("/broadcasts/<bid>")
def delete_broadcast(bid):
    broadcast.soft_delete(bid, g.admin["username"], delete_messages=request.args.get("messages", "1") != "0")
    log_action("broadcast.deleted", "messages removed from chats" if request.args.get("messages", "1") != "0" else "", target=bid)
    return jsonify(ok=True)
