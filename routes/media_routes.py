"""Admin-only image proxy. Telegram files are fetched server-side so the bot token never reaches the browser."""
import re

from flask import Blueprint, Response, abort, request

from bot.context import get_ctx
from bot.tg import TelegramError, tg
from core import db, security
from services import media

media_bp = Blueprint("media", __name__, url_prefix="/media")
FILE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{10,250}$")
MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def _authed():
    """Any logged-in panel user (admin, reseller or seller) may load panel images."""
    return bool(security.current_admin() or security.current_reseller() or security.current_seller())


def _send(data, mime):
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@media_bp.get("/tg/<file_id>")
def telegram_file(file_id):
    if not _authed():
        abort(401)
    if not FILE_ID_RE.match(file_id):
        abort(404)
    client = tg
    rid = request.args.get("b", "")  # file ids belong to the bot that received them (reseller bots)
    if rid:
        admin, reseller = security.current_admin(), security.current_reseller()
        if not admin and not (reseller and reseller["id"] == rid):
            abort(403)
        ctx = get_ctx(rid)
        if not ctx:
            abort(404)
        client = ctx.tg
    try:
        data, ext = client.download(file_id)
    except TelegramError:
        abort(404)
    return _send(data, MIME.get(ext, "image/jpeg"))


@media_bp.get("/img/<media_id>")
def stored_image(media_id):
    if not _authed():
        abort(401)
    data, mime = media.get_media(media_id) if re.fullmatch(r"med_[a-f0-9]{12}", media_id) else (None, None)
    if not data:
        abort(404)
    return _send(data, mime)


@media_bp.get("/upi/<upi_id>")
def upi_scanner(upi_id):
    admin, reseller = security.current_admin(), security.current_reseller()
    if not admin and not reseller:
        abort(401)
    u = db.get(f"/upis/{upi_id}")
    if not u or u.get("deleted") or not u.get("scanner_media_id"):
        abort(404)
    if reseller and (u.get("reseller_id") or "") != reseller["id"]:
        abort(404)
    data, mime = media.get_media(u["scanner_media_id"])
    if not data:
        abort(404)
    return _send(data, mime)
