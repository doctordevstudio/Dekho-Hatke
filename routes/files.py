"""Admin side of the file registry: the files THIS admin sent to the bot (and their ids) -- nobody else's."""
from flask import g, jsonify, request

from core.errors import ApiError
from services import settings as bot_settings
from services import tgfiles
from . import bp

KINDS = {"apk": ("apk",), "video": ("video",), "photo": ("photo",)}


def my_chat():
    s = bot_settings.bot_settings()
    return {"admin": s["admin_chat_id"], "admin2": s["admin2_chat_id"]}.get(g.admin["role"], "")


@bp.get("/my-files")
def my_files():
    if g.admin["role"] not in ("admin", "admin2"):
        raise ApiError("View-only accounts cannot see file ids.", 403)
    chat = my_chat()
    return jsonify(items=[tgfiles.public(r) for r in tgfiles.mine(chat, KINDS.get(request.args.get("kind", "")), 40)] if chat else [],
                   chat_linked=bool(chat))
