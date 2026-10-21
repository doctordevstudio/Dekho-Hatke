"""Broadcast engine: send a message (text/HTML + optional photo/video/file + optional URL button, or a "promote an
app" card with plan buttons) to many users, then edit / delete / re-send it later -- the changes are applied to the
messages already sitting in every user's chat.

Sender: one background thread per broadcast, ~20 messages/second (Telegram's limit is 30), resumable.
Audiences: all users of the main bot | people who bought one of a seller's apps (needs admin approval first).
"""
import logging
import re
import threading
import time

from bot import messages as m
from bot.tg import TelegramError, tg
from core import db, utils
from core.errors import ApiError
from services import catalog, notify, settings

log = logging.getLogger("broadcast")
RATE_SLEEP = 0.05
_threads = {}
_lock = threading.Lock()
KINDS = ("photo", "video", "document", "animation")


# ------------------------------------------------------------------ building
def clean_message(d):
    d = d or {}
    fmt = d.get("format", "html")
    if fmt not in ("html", "text"):
        raise ApiError("Format must be html or text.")
    text = utils.clean_text(d.get("text"), "Message", 3900, required=False)
    media = d.get("media") or {}
    kind, ref = str(media.get("kind") or ""), str(media.get("ref") or "").strip()
    if ref:
        if kind not in KINDS:
            raise ApiError("Unknown media type.")
        if ref.startswith(("http://", "https://")):
            ref = utils.clean_url(ref, "Media URL", True)
        if len(text) > 1024:
            raise ApiError("With a photo/video/file the message can be at most 1024 characters.")
    else:
        kind, ref = "", ""
    if not text and not ref:
        raise ApiError("Write a message or add media.")
    btn = d.get("button") or {}
    button = {}
    if btn.get("text") or btn.get("url"):
        button = {"text": utils.clean_text(btn.get("text"), "Button text", 40), "url": utils.clean_url(btn.get("url"), "Button URL", True)}
    return {"text": text, "format": fmt, "media": {"kind": kind, "ref": ref} if ref else {}, "button": button}


def markup_for(b):
    rows = []
    if b.get("plan_buttons"):
        rows.extend([[{"text": pb["text"], "callback_data": pb["data"]}] for pb in b["plan_buttons"]])
    btn = (b.get("message") or {}).get("button")
    if btn:
        rows.append([{"text": btn["text"], "url": btn["url"]}])
    return {"inline_keyboard": rows} if rows else None


def promote_payload(app_id):
    """Message + plan buttons that let users pick a plan right from the broadcast."""
    app = catalog.get_app(app_id)
    if not app or not catalog.purchasable(app):
        raise ApiError("Choose a live app that has plans.")
    plans = catalog.plans_for(app_id, "")
    if not plans:
        raise ApiError("This app has no plans yet.")
    lines = [f"🔥 <b>{m.esc(app['name'])}</b>"]
    if app.get("description"):
        lines.append(f"<i>{m.esc(app['description'])}</i>")
    if app.get("features"):
        lines.append("\n" + "\n".join(f"✨ {m.esc(f.strip(' -•'))}" for f in app["features"].splitlines() if f.strip())[:700])
    lines.append("\n👇 <b>Pick a plan and get your key instantly</b>")
    buttons = []
    for p in plans[:8]:
        label = utils.validity_label(p["validity_value"], p["validity_unit"])
        if p.get("kind") == "trial":
            text = f"🎁 FREE TRIAL · {label}"
        else:
            text = f"{'⭐ ' if p.get('is_best') else ''}{label} — {utils.inr(p['price'])}"
        buttons.append({"text": text, "data": f"plan:{p['id']}"})
    media = {}
    if app.get("image_url", "").startswith("http"):
        media = {"kind": "photo", "ref": app["image_url"]}
    return {"text": "\n".join(lines)[:1000] if media else "\n".join(lines), "format": "html", "media": media,
            "button": {}}, buttons


# ------------------------------------------------------------------ recipients
def recipients_for(b):
    aud = b.get("audience") or {"type": "all"}
    if aud["type"] == "all":
        return [u["chat_id"] for u in db.get_list("/users") if u.get("chat_id") and not u.get("blocked")]
    ids = set(aud.get("app_ids") or [])
    seen = []
    for lic in licenses_for_apps(b["owner"], ids):
        if lic["chat_id"] not in seen and lic["chat_id"] != "test" and not lic.get("reseller_id"):
            seen.append(lic["chat_id"])
    return seen


def licenses_for_apps(seller_id, app_ids):
    from services import licenses
    mine = {a["id"] for a in catalog.live("/apps") if a.get("seller_id") == seller_id}
    wanted = (set(app_ids) & mine) if app_ids else mine
    return [l for l in licenses.all_licenses() if l["app_id"] in wanted]


def audience_count(owner, aud):
    return len(recipients_for({"audience": aud, "owner": owner}))


# ------------------------------------------------------------------ CRUD
def create(owner, owner_name, message, audience, kind="message", app_id="", plan_buttons=None, needs_approval=False,
           actor=""):
    bid = utils.new_id("bc")
    b = {"id": bid, "owner": owner, "owner_name": owner_name, "kind": kind, "app_id": app_id,
         "message": message, "plan_buttons": plan_buttons or [], "audience": audience, "run": 0,
         "status": "pending_approval" if needs_approval else "draft", "created_at": utils.now(), "created_by": actor,
         "total": 0, "sent": 0, "failed": 0, "blocked": 0}
    db.set(f"/broadcasts/{bid}", b)
    return b


def get(bid):
    b = db.get(f"/broadcasts/{bid}")
    if not b or b.get("deleted"):
        raise ApiError("Broadcast not found.", 404)
    return b


def public(b):
    return {k: b.get(k) for k in ("id", "owner", "owner_name", "kind", "app_id", "message", "plan_buttons", "audience",
                                  "run", "status", "created_at", "total", "sent", "failed", "blocked", "error",
                                  "started_at", "finished_at", "edited_at", "reject_reason", "deleted_msgs")}


def listing(owner=None):
    rows = [b for b in db.get_list("/broadcasts") if not b.get("deleted")]
    if owner is not None:
        rows = [b for b in rows if b.get("owner") == owner]
    rows.sort(key=lambda b: b["created_at"], reverse=True)
    return rows


def approve(bid, actor):
    b = get(bid)
    if b["status"] != "pending_approval":
        raise ApiError("This broadcast is not waiting for approval.")
    db.update(f"/broadcasts/{bid}", {"approved_by": actor, "status": "draft"})
    start(bid)
    if b["owner"] != "admin":
        from services import sellers
        s = sellers.get(b["owner"])
        if s:
            sellers.tell(s, "✅ Broadcast approved", "Your broadcast is being delivered to your customers now.")


def reject(bid, reason, actor):
    b = get(bid)
    if b["status"] != "pending_approval":
        raise ApiError("This broadcast is not waiting for approval.")
    reason = utils.clean_text(reason, "Reason", 300)
    db.update(f"/broadcasts/{bid}", {"status": "rejected", "reject_reason": reason, "decided_by": actor})
    from services import sellers
    s = sellers.get(b["owner"])
    if s:
        sellers.tell(s, "❌ Broadcast rejected", f"Reason: {reason}")


# ------------------------------------------------------------------ sending
def _text_of(b):
    msg = b["message"]
    text = msg.get("text", "")
    if b["owner"] != "admin" and b.get("owner_name"):
        head = f"📣 <b>{m.esc(b['owner_name'])}</b>\n\n" if msg.get("format") != "text" else f"📣 {b['owner_name']}\n\n"
        text = head + text
    return text


def _send_one(b, chat):
    msg = b["message"]
    text = _text_of(b)
    mode = None if msg.get("format") == "text" else "HTML"
    markup = markup_for(b)
    md = msg.get("media") or {}
    if md.get("ref"):
        from services import delivery
        from bot.context import MAIN
        local = md["ref"].startswith("/media/img/")
        if local:
            return delivery.send_ref(MAIN, chat, md["kind"], md["ref"], text or None, markup)
        return tg.send_media(md["kind"], chat, md["ref"], text or None, markup, parse_mode=mode)
    return tg.send_message(chat, text, markup, parse_mode=mode)


def _is_parse_error(e):
    return "parse" in e.description.lower() or "entities" in e.description.lower()


def start(bid, resume=False):
    with _lock:
        t = _threads.get(bid)
        if t and t.is_alive():
            return False
        b = get(bid)
        run = int(b.get("run", 0)) + (0 if resume else 1)
        db.update(f"/broadcasts/{bid}", {"status": "sending", "run": run,
                                         **({} if resume else {"sent": 0, "failed": 0, "blocked": 0, "error": "",
                                                               "started_at": utils.now(), "finished_at": 0})})
        th = threading.Thread(target=_worker, args=(bid, run), daemon=True, name=f"broadcast-{bid}")
        _threads[bid] = th
        th.start()
        return True


def resume_pending():
    """Called by the housekeeping loop: restart broadcasts that were interrupted by a restart."""
    for b in db.query_equal("/broadcasts", "status", "sending"):
        with _lock:
            t = _threads.get(b["id"])
            alive = t and t.is_alive()
        if not alive:
            start(b["id"], resume=True)


def _worker(bid, run):
    try:
        b = get(bid)
        chats = recipients_for(b)
        done = db.get(f"/broadcast_msgs/{bid}") or {}
        todo = [c for c in chats if (done.get(c) or {}).get("run") != run]
        db.update(f"/broadcasts/{bid}", {"total": len(chats)})
        sent = sum(1 for c in chats if (done.get(c) or {}).get("run") == run)
        failed = int(b.get("failed", 0))
        blocked = int(b.get("blocked", 0))
        for i, chat in enumerate(todo):
            if db.get(f"/broadcasts/{bid}/status") != "sending":
                return  # cancelled / deleted while running
            try:
                res = _send_one(b, chat)
                db.set(f"/broadcast_msgs/{bid}/{chat}", {"mid": res["message_id"], "run": run, "ts": utils.now(),
                                                         "media": bool((b["message"].get("media") or {}).get("ref"))})
                sent += 1
            except TelegramError as e:
                if e.retry_after:
                    time.sleep(min(e.retry_after + 1, 30))
                    try:
                        res = _send_one(b, chat)
                        db.set(f"/broadcast_msgs/{bid}/{chat}", {"mid": res["message_id"], "run": run, "ts": utils.now(),
                                                                 "media": bool((b["message"].get("media") or {}).get("ref"))})
                        sent += 1
                        continue
                    except TelegramError as e2:
                        e = e2
                if e.blocked:
                    blocked += 1
                    db.update(f"/users/{chat}", {"blocked": True}) if db.get(f"/users/{chat}") else None
                elif _is_parse_error(e) and sent == 0 and i == 0:
                    db.update(f"/broadcasts/{bid}", {"status": "failed", "error": "Invalid HTML in the message: " + e.description[:120]})
                    return
                else:
                    failed += 1
            if i % 10 == 0:
                db.update(f"/broadcasts/{bid}", {"sent": sent, "failed": failed, "blocked": blocked})
            time.sleep(RATE_SLEEP)
        db.update(f"/broadcasts/{bid}", {"sent": sent, "failed": failed, "blocked": blocked, "status": "done",
                                         "finished_at": utils.now()})
        _tell_owner(b, sent, len(chats))
    except Exception:
        log.exception("broadcast %s crashed", bid)
        db.update(f"/broadcasts/{bid}", {"status": "failed", "error": "Server error while sending"})


def _tell_owner(b, sent, total):
    if b["owner"] == "admin":
        return
    from services import sellers
    s = sellers.get(b["owner"])
    if s:
        sellers.tell(s, "📣 Broadcast delivered", f"Delivered to {sent} of {total} customers.")


# ------------------------------------------------------------------ edit / delete / rebroadcast (reflected in chats)
def _for_each_msg(bid, fn, status_label):
    msgs = db.get(f"/broadcast_msgs/{bid}") or {}
    ok = bad = 0
    for chat, info in msgs.items():
        try:
            fn(chat, info)
            ok += 1
        except TelegramError as e:
            if "not modified" not in e.description.lower():
                bad += 1
        time.sleep(RATE_SLEEP)
    return ok, bad


def edit(bid, new_message, actor):
    """Apply a new text/caption/button to the messages already delivered."""
    b = get(bid)
    if b["status"] in ("sending",):
        raise ApiError("Wait until the broadcast has finished sending.")
    msg = clean_message(new_message)
    old_md = (b["message"].get("media") or {})
    if bool(msg["media"]) != bool(old_md) or (msg["media"] and msg["media"]["kind"] != old_md.get("kind")):
        raise ApiError("You can change the text and button of a sent broadcast. To change or add media, use Re-broadcast.")
    b = {**b, "message": msg}
    db.update(f"/broadcasts/{bid}", {"message": msg, "edited_at": utils.now()})
    text = _text_of(b)
    mode = None if msg.get("format") == "text" else "HTML"
    markup = markup_for(b) or {"inline_keyboard": []}

    def work():
        def one(chat, info):
            if info.get("media"):
                if msg["media"].get("ref") != old_md.get("ref"):
                    tg.edit_media(chat, info["mid"], msg["media"]["kind"], msg["media"]["ref"], text, markup, parse_mode=mode)
                else:
                    tg.edit_caption(chat, info["mid"], text, markup, parse_mode=mode)
            else:
                tg.edit_text(chat, info["mid"], text, markup, parse_mode=mode)
        ok, bad = _for_each_msg(bid, one, "edit")
        db.update(f"/broadcasts/{bid}", {"edit_result": {"ok": ok, "failed": bad, "ts": utils.now()}})

    threading.Thread(target=work, daemon=True, name=f"bc-edit-{bid}").start()
    return public(get(bid))


def delete_sent(bid):
    """Delete the messages from every user's chat (keeps the broadcast record)."""
    b = get(bid)
    if b["status"] == "sending":
        raise ApiError("Wait until the broadcast has finished sending, or stop it first.")

    def work():
        ok, _bad = _for_each_msg(bid, lambda chat, info: tg.call("deleteMessage", {"chat_id": chat, "message_id": info["mid"]}, timeout=8), "delete")
        db.set(f"/broadcast_msgs/{bid}", None)
        db.update(f"/broadcasts/{bid}", {"deleted_msgs": ok, "sent": 0})

    threading.Thread(target=work, daemon=True, name=f"bc-del-{bid}").start()


def soft_delete(bid, actor, delete_messages=True):
    b = get(bid)
    if b["status"] == "sending":
        db.update(f"/broadcasts/{bid}", {"status": "cancelled"})
    if delete_messages:
        try:
            delete_sent(bid)
        except ApiError:
            pass
    db.update(f"/broadcasts/{bid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor})


def stop(bid):
    b = get(bid)
    if b["status"] != "sending":
        raise ApiError("It is not sending.")
    db.update(f"/broadcasts/{bid}", {"status": "cancelled", "finished_at": utils.now()})


def rebroadcast(bid, delete_old, new_message=None):
    b = get(bid)
    if b["status"] == "sending":
        raise ApiError("Wait until the broadcast has finished sending.")
    if new_message:
        db.update(f"/broadcasts/{bid}", {"message": clean_message(new_message)})
    db.update(f"/broadcasts/{bid}", {"status": "queued"})

    def go():
        if delete_old:
            for chat, info in (db.get(f"/broadcast_msgs/{bid}") or {}).items():
                tg.delete_message(chat, info["mid"])
                time.sleep(RATE_SLEEP)
            db.set(f"/broadcast_msgs/{bid}", None)
        start(bid)

    threading.Thread(target=go, daemon=True, name=f"bc-re-{bid}").start()
