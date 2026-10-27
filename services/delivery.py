"""Sending stored media (Telegram file ids, Firebase images, URLs) through ANY bot.

A Telegram file_id only works for the bot that received the file. So:
* main bot            -> sends the id directly (the file keeps its name: game.apk).
* reseller bots       -> files that were sent to the bot by an admin / seller also live in the storage channel
                         (services/tgchannel.py); the reseller's bot COPIES that message to the customer (copyMessage:
                         done by Telegram itself -- nothing is downloaded or uploaded, no "Forwarded from", the name stays).
* anything else (small images such as logos, files from before the channel existed) falls back to the old way: the main
  bot downloads it (Bot API limit 20 MB) and the reseller's bot uploads it once, under its REAL file name, and remembers the id.
"""
import hashlib
import logging

from bot import messages as m
from bot.tg import TelegramError, tg
from core import db, utils
from services import media, tgchannel

log = logging.getLogger("delivery")
KIND_FILENAME = {"photo": "image.jpg", "video": "video.mp4", "document": "file.bin", "animation": "anim.mp4", "audio": "audio.mp3"}


def _cache_key(tenant, ref):
    # "v2": ids cached by older versions were uploaded as "file.bin" -- never reuse them
    return f"v2/{tenant}/{hashlib.sha256(ref.encode()).hexdigest()[:40]}"


def _new_file_id(msg, kind):
    if kind == "photo":
        return msg["photo"][-1]["file_id"]
    return (msg.get(kind) or msg.get("document") or msg.get("video") or {}).get("file_id", "")


BRAND_LOGO = "/static/img/logo-welcome.jpg"  # the Dekho Hatke logo shipped with the app (welcome message default)
_brand_bytes = []


def _local_bytes(ref):
    """Bytes for refs that live in our own database (/media/img/<id>) or the bundled brand logo."""
    if ref == BRAND_LOGO:
        if not _brand_bytes:
            import os
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "img", "logo-welcome.jpg")
            with open(path, "rb") as fh:
                _brand_bytes.append(fh.read())
        return _brand_bytes[0]
    if ref.startswith("/media/img/"):
        data, _ = media.get_media(ref.rsplit("/", 1)[-1])
        return data
    return None


def _id_problem(e):
    d = e.description.lower()
    return "file identifier" in d or "wrong file" in d or "wrong remote" in d or "file_id" in d


def send_ref(ctx, chat_id, kind, ref, caption=None, markup=None, effect=None, filename=None):
    """Send one stored asset. Raises TelegramError when it cannot be delivered. `filename` is the name to use if the file
    has to be uploaded (the APK of an app is called <app>.apk)."""
    ref = (ref or "").strip()
    if not ref:
        raise TelegramError("empty media reference")
    local = _local_bytes(ref)
    known = None if local is not None else tgchannel.index(ref)
    if known and known.get("mid") and not ctx.is_main:
        return tgchannel.copy_to_chat(ctx.tg, chat_id, {"chat": known["chat"], "mid": known["mid"]}, caption, markup)
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
    name = filename or (known or {}).get("name") or KIND_FILENAME.get(kind, "file.bin")
    msg = ctx.tg.send_media(kind, chat_id, bytes(data), caption, markup, effect=effect, filename=name)
    fid = _new_file_id(msg, kind)
    if fid:
        db.set(f"/file_cache/{key}", fid)
    return msg


def safe_send(ctx, chat_id, kind, ref, caption=None, markup=None, effect=None, filename=None):
    """Like send_ref but returns None instead of raising."""
    try:
        return send_ref(ctx, chat_id, kind, ref, caption, markup, effect, filename)
    except TelegramError as e:
        log.warning("could not deliver %s to %s via %s: %s", kind, chat_id, ctx.key, e)
        return None


def _apk_name(app):
    """File name for an uploaded APK: 'My App' -> 'My_App.apk'."""
    base = "".join(ch if ch.isalnum() else "_" for ch in str(app.get("name") or "app")).strip("_")[:40] or "app"
    return base + ".apk"


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
        r = safe_send(ctx, chat_id, "document", app["apk_id"], cap + "\n\n📲 Install the APK and paste your key.",
                      filename=_apk_name(app))
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
                           "Check the APK id in the panel and Settings → Channel & account (the bot must be in the storage channel).", client=ctx.tg)
    return sent
