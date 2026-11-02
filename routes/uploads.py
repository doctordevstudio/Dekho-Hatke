"""Uploads used by the panel editors: images for rich messages / apps, and media for broadcasts."""
import io

from flask import g, jsonify, request

from bot.tg import TelegramError, tg
from core import utils
from core.errors import ApiError
from services import media, notify
from . import both, bp

BROADCAST_LIMIT = 49 * 1024 * 1024


@both("/upload/image", methods=["POST"])
def upload_image():
    f = request.files.get("file")
    if not f:
        raise ApiError("Choose an image.")
    raw = f.read(2 * 1024 * 1024 + 1)
    ok, why = media.verify_real_image(raw, 2 * 1024 * 1024, 16)
    if not ok:
        raise ApiError("That is not a valid JPG / PNG / WEBP image (max 2 MB).")
    owner = (g.admin or {}).get("username") or (g.reseller or {}).get("login_username") or "panel"
    mid = media.save_image(raw, "panel_image", owner)
    return jsonify(url=f"/media/img/{mid}")


@bp.post("/broadcast/upload")
def broadcast_upload():
    """Photo / video / file for a broadcast. The bot hands it to Telegram once (sent to the admin chat) and the
    broadcast then reuses the returned file id for every recipient."""
    f = request.files.get("file")
    if not f or not f.filename:
        raise ApiError("Choose a file.")
    data = f.read(BROADCAST_LIMIT + 1)
    if not data:
        raise ApiError("That file is empty.")
    if len(data) > BROADCAST_LIMIT:
        raise ApiError("Files can be at most 49 MB (Telegram's bot limit is 50 MB).")
    name = "".join(c if c.isalnum() or c in "._- " else "_" for c in f.filename)[:80] or "file"
    chats = notify.admin_chats()
    if not chats:
        raise ApiError("Set the Admin Chat ID in Settings first — uploads are handed to Telegram through that chat.")
    ok_img, _ = media.verify_real_image(data, BROADCAST_LIMIT, 16)
    if ok_img:
        kind, field = "photo", "photo"
    elif name.lower().endswith((".mp4", ".mov", ".webm")) and data[4:8] == b"ftyp" or name.lower().endswith(".mp4"):
        kind, field = "video", "video"
    elif name.lower().endswith(".gif"):
        kind, field = "animation", "animation"
    else:
        kind, field = "document", "document"
    last = None
    for chat in chats:
        try:
            msg = tg.send_media(kind, chat, (name, io.BytesIO(data)), "📥 Broadcast upload (preview)")
            fid = msg["photo"][-1]["file_id"] if kind == "photo" else msg[field]["file_id"]
            return jsonify(kind=kind, ref=fid, name=name, size=len(data))
        except TelegramError as e:
            last = e
    raise ApiError(f"Telegram refused the file: {last.description if last else 'unknown error'}")
