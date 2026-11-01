"""The storage channel: every APK / video / file that an admin or a seller sends to the bot is copied there once.

Why: a Telegram file id works for one bot only, which is why reseller bots used to download the APK and upload it again
(slow, limited to 20 MB, and the upload lost its name -> "file.bin"). Now any bot copies the stored message from the
channel to the customer with `copyMessage`: Telegram does it server-side, the file keeps its real name (game.apk), there is no
"Forwarded from" label, and nothing is downloaded or uploaded. The linked personal account (services/userbot.py) puts each
bot into the channel.

Records: /settings/tg_channel = {chat_id, title, username}   /tg_ix/<sha1(file_id)> = {name, kind, chat, mid, ...}
"""
import hashlib
import logging
import threading

from bot.tg import TelegramError, client_for, tg
from core import db, utils
from core.cache import cache
from services import userbot

log = logging.getLogger("tgchannel")
SETTING = "/settings/tg_channel"
MIRROR_KINDS = ("apk", "video", "document", "animation", "audio")  # photos stay on the old (small, fast) path


def ix_key(file_id):
    return hashlib.sha1(str(file_id).encode()).hexdigest()[:32]


def index(file_id):
    """What we know about a stored file id (name, kind, channel copy) or None."""
    return db.get(f"/tg_ix/{ix_key(file_id)}") if file_id else None


def remember(file_id, kind, name, size, mime):
    db.update(f"/tg_ix/{ix_key(file_id)}", {"file_id": file_id, "kind": kind, "name": str(name)[:80], "size": int(size or 0),
                                              "mime": mime or "", "ts": utils.now()})


def settings():
    """{chat_id, title, username} or None when no channel is configured."""
    s = db.get(SETTING) or {}
    return s if s.get("chat_id") else None


def enabled():
    return settings() is not None


def public():
    s = settings()
    return {"chat_id": s["chat_id"], "title": s.get("title", ""), "username": s.get("username", "")} if s else None


def _access_problem(e):
    d = e.description.lower()
    return any(x in d for x in ("chat not found", "not enough rights", "no rights", "not a member", "forbidden", "kicked",
                                "message to copy not found", "need administrator", "have no rights"))


# ------------------------------------------------------------------ choosing the channel
def save_channel(ref, actor):
    """Check the chat with the linked account, remember it, then let the account bring every bot in. -> (info, bot results)."""
    ref = str(ref or "").strip()
    if not ref:
        db.set(SETTING, None)
        cache.clear()
        return None, []
    info = userbot.resolve_chat(ref)
    db.set(SETTING, {"chat_id": info["chat_id"], "title": info["title"], "username": info["username"],
                     "set_by": actor, "set_at": utils.now()})
    cache.clear()
    return info, grant_all()


# ------------------------------------------------------------------ bots <-> channel
def _bots():
    """[(label, client)] -- the main bot and every active reseller's bot."""
    from services import resellers
    out = []
    if tg.token:
        out.append(("main bot", tg))
    for r in resellers.all_resellers():
        if r.get("bot_token") and not r.get("deleted") and r.get("active", True):
            out.append((r.get("bot_name") or r.get("name") or r["id"], client_for(r["bot_token"])))
    return out


def grant(client):
    """Let one bot into the channel. -> (ok, note). Never raises."""
    s = settings()
    if not s:
        return False, "no channel set"
    try:
        uname = client.get_me().get("username")
        if not uname:
            return False, "the bot has no username"
        return userbot.grant_bot(s["username"] and "@" + s["username"] or s["chat_id"], uname)
    except userbot.UserbotError as e:
        return False, e.message
    except TelegramError as e:
        return False, e.description


def grant_all():
    out = []
    for label, client in _bots():
        ok, note = grant(client)
        out.append({"bot": label, "ok": bool(ok), "note": note})
    return out


def grant_in_background(client):
    """A reseller just saved a bot token: bring that bot into the channel (best effort, off the request thread)."""
    if not enabled() or not userbot.linked():
        return
    threading.Thread(target=lambda: grant(client), daemon=True, name="tg-grant").start()


# ------------------------------------------------------------------ storing and delivering
def mirror_upload(client, from_chat, message_id, file_id):
    """Copy the message the sender just posted to the bot into the channel and remember where it is. -> (ok, why)."""
    s = settings()
    if not s:
        return False, "no channel"
    payload = {"chat_id": s["chat_id"], "from_chat_id": from_chat, "message_id": message_id}
    try:
        try:
            r = client.call("copyMessage", payload)
        except TelegramError as e:
            if not _access_problem(e):
                raise
            ok, note = grant(client)  # the main bot is not in the channel yet: bring it in, then once more
            if not ok:
                return False, note
            r = client.call("copyMessage", payload)
    except TelegramError as e:
        log.warning("channel copy failed: %s", e)
        return False, e.description
    db.update(f"/tg_ix/{ix_key(file_id)}", {"chat": s["chat_id"], "mid": int(r["message_id"])})
    return True, ""


def ensure_copy(file_id, kind_hint="document", force=False):
    """Make sure the channel holds a copy of `file_id`, and return its record {chat, mid}. Raises TelegramError.

    A copy normally appears when an admin / seller sends the file to the bot. When it is missing (file sent before the
    channel was set, the copy failed, the channel message was deleted or the channel was changed) the MAIN bot -- the
    owner of the file id -- simply sends the file into the channel by id (any size, nothing is downloaded) and we
    remember that message. Reseller bots can then copy it."""
    s = settings()
    if not s:
        raise TelegramError("no storage channel is set (Settings → Channel & account)")
    rec = index(file_id) or {}
    if rec.get("mid") and rec.get("chat") == s["chat_id"] and not force:
        return rec
    kind = rec.get("kind") or kind_hint or "document"
    kind = "document" if kind in ("apk", "file") else kind
    if kind not in ("video", "document", "animation", "audio", "photo"):
        kind = "document"
    try:
        try:
            msg = tg.send_media(kind, s["chat_id"], file_id)
        except TelegramError as e:
            if not _access_problem(e):
                raise
            ok, note = grant(tg)  # the main bot itself is not (admin) in the channel yet
            if not ok:
                raise TelegramError(f"main bot cannot post in the channel: {note}")
            msg = tg.send_media(kind, s["chat_id"], file_id)
    except TelegramError as e:
        if "wrong file" in e.description.lower() or "file identifier" in e.description.lower():
            raise TelegramError("this file id was not issued by the main bot (send the file to the main bot again and use the new id)")
        raise
    out = {"chat": s["chat_id"], "mid": int(msg["message_id"])}
    db.update(f"/tg_ix/{ix_key(file_id)}", {**out, "file_id": file_id, "kind": rec.get("kind") or kind, "ts": utils.now()})
    return {**rec, **out}


def copy_to_chat(client, chat_id, rec, caption=None, markup=None):
    """Send the channel copy to a customer through `client` (any bot). Raises TelegramError."""
    payload = {"chat_id": chat_id, "from_chat_id": rec["chat"], "message_id": rec["mid"], "reply_markup": markup}
    if caption:
        payload.update(caption=caption, parse_mode="HTML")
    try:
        return client.call("copyMessage", payload)
    except TelegramError as e:
        if not _access_problem(e):
            raise
        ok, note = grant(client)  # this bot is not in the channel (yet): bring it in and retry once
        if not ok:
            raise TelegramError(f"channel access: {note}")
        return client.call("copyMessage", payload)
