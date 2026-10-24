"""Blocked users: the bot never answers them (no reply, no button, nothing). Applies to the main bot and every reseller bot.
Stored at /banned/<chat_id>; reads go through the db read cache, so checking every update is cheap."""
from core import db, utils
from core.errors import ApiError


def get(chat_id):
    return db.get(f"/banned/{chat_id}") if chat_id else None


def is_banned(chat_id):
    return db.get(f"/banned/{chat_id}") is not None


def ban(chat_id, reason, actor, name="", source="panel"):
    chat_id = utils.clean_chat_id(chat_id)
    from bot.handlers import is_admin_chat
    if is_admin_chat(chat_id):
        raise ApiError("You cannot block an admin.")
    reason = utils.clean_text(reason, "Reason", 300)
    rec = {"chat_id": chat_id, "reason": reason, "by": actor, "ts": utils.now(), "name": name, "source": source}
    db.set(f"/banned/{chat_id}", rec)
    db.set(f"/users/{chat_id}/banned", True) if db.get(f"/users/{chat_id}") else None
    return rec


def unban(chat_id, actor):
    chat_id = utils.clean_chat_id(chat_id)
    if not get(chat_id):
        raise ApiError("That user is not blocked.", 404)
    db.set(f"/banned/{chat_id}", None)
    if db.get(f"/users/{chat_id}"):
        db.set(f"/users/{chat_id}/banned", None)


def listing():
    rows = db.get_list("/banned")
    rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return rows
