"""Sending stored media (Telegram file ids, Firebase images, URLs) through ANY bot.

A Telegram file_id only works for the bot that received the file. The main bot therefore sends ids directly;
a reseller's bot first tries the id, and if Telegram refuses it we download the file with the main bot
(Bot API limit: 20 MB), upload it through the reseller bot once and remember the new id.
"""
import hashlib
import logging

from bot import messages as m
from bot.tg import TelegramError, tg
from core import db, utils
from services import media

log = logging.getLogger("delivery")
KIND_FILENAME = {"photo": "image.png", "video": "video.mp4", "document": "file.bin", "animation": "anim.mp4"}


def _cache_key(tenant, ref):
    return f"{tenant}/{hashlib.sha256(ref.encode()).hexdigest()[:40]}"


def _new_file_id(msg, kind):
    if kind == "photo":
        return msg["photo"][-1]["file_id"]
    return (msg.get(kind) or msg.get("document") or msg.get("video") or {}).get("file_id", "")


def _local_bytes(ref):
    """Bytes for refs that live in our own database (/media/img/<id>)."""
    if ref.startswith("/media/img/"):
        data, _ = media.get_media(ref.rsplit("/", 1)[-1])
        return data
    return None


def _id_problem(e):
    d = e.description.lower()
    return "file identifier" in d or "wrong file" in d or "wrong remote" in d or "file_id" in d


def send_ref(ctx, chat_id, kind, ref, caption=None, markup=None, effect=None):
    """Send one stored asset. Raises TelegramError when it cannot be delivered."""
    ref = (ref or "").strip()
    if not ref:
        raise TelegramError("empty media reference")
    local = _local_bytes(ref)
    key = _cache_key(ctx.key, ref)
    cached = db.get(f"/file_cache/{key}") if (local or not ctx.is_main) else None
    direct = cached or (None if local else ref)
    if direct:
        try:
            return ctx.tg.send_media(kind, chat_id, direct, caption, markup, effect=effect)
        except TelegramError as e:
            if not _id_problem(e) or (ctx.is_main and not local):
                raise
            if cached:
                db.set(f"/file_cache/{key}", None)  # stale id: re-upload below
    if local is None and ref.startswith(("http://", "https://")):
        raise TelegramError("could not send URL")
    data = local if local is not None else tg.download(ref)[0]  # the main bot owns the file id
    msg = ctx.tg.send_media(kind, chat_id, bytes(data), caption, markup, effect=effect,
                            filename=KIND_FILENAME.get(kind, "file.bin"))
    fid = _new_file_id(msg, kind)
    if fid:
        db.set(f"/file_cache/{key}", fid)
    return msg


def safe_send(ctx, chat_id, kind, ref, caption=None, markup=None, effect=None):
    """Like send_ref but returns None instead of raising."""
    try:
        return send_ref(ctx, chat_id, kind, ref, caption, markup, effect)
    except TelegramError as e:
        log.warning("could not deliver %s to %s via %s: %s", kind, chat_id, ctx.key, e)
        return None


def deliver_assets(ctx, chat_id, app, lic, plan_label=""):
    """After a successful purchase / trial: license key message, setup video and the APK."""
    sent = []
    from services import settings  # local import: avoid cycles

    cap = (f"✅ <b>{m.esc(app.get('name'))}</b>\n🔑 <code>{lic['key']}</code>\n⏳ {m.esc(plan_label or lic.get('validity_label'))}"
           f" · valid until {utils.fmt_dt(lic['expires_at'])}")
    if app.get("setup_video_id"):
        r = safe_send(ctx, chat_id, "video", app["setup_video_id"], "🎬 <b>How to set up</b>\nWatch this first, then install the app.")
        if r:
            sent.append(r["message_id"])
    if app.get("apk_id"):
        r = safe_send(ctx, chat_id, "document", app["apk_id"], cap + "\n\n📲 Install the APK and paste your key.")
        if r:
            sent.append(r["message_id"])
        else:
            note = ("⚠️ We could not send the APK file automatically. Please contact support and share your license key — "
                    "we will send it to you right away.")
            try:
                ctx.tg.send_message(chat_id, note)
            except TelegramError:
                pass
            from services import notify
            notify.send_to(notify.recipients(ctx.tenant),
                           f"⚠️ APK delivery failed for <b>{m.esc(app.get('name'))}</b> → user <code>{chat_id}</code>. "
                           "Check the APK id in the panel (for reseller bots the file must be ≤ 20 MB).", client=ctx.tg)
    return sent
