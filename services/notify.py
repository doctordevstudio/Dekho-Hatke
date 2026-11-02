"""Notifications to admins / UPI owners / resellers / sellers (all through Telegram)."""
import logging

from bot import messages as m
from bot.tg import TelegramError, tg
from core import db, utils
from services import settings

log = logging.getLogger("notify")


def admin_chats():
    s = settings.bot_settings()
    return [c for c in (s["admin_chat_id"], s["admin2_chat_id"]) if c]


def refresh_admin_menus(old_chats=()):
    """Admin chat ids changed: give "Admin Login" to the new ones and take it from the ones that were removed."""
    now = admin_chats()
    for c in old_chats:
        if c and c not in now:
            tg.drop_admin_menu(c)
    tg.set_start_menu(now)


def recipients(tenant="", *extra):
    """Who hears about payments. Main shop: UPI owner + super admin + 2nd admin. Reseller shop: UPI owner + the
    reseller. `extra` chat ids (e.g. the UPI owner's) come first."""
    base = admin_chats()
    if tenant:
        from bot.context import get_reseller
        r = get_reseller(tenant) or {}
        base = [str(r.get("chat_id") or "")]
    seen, out = set(), []
    for c in (*extra, *base):
        c = str(c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _clients(client):
    out = [client or tg]
    if tg not in out:
        out.append(tg)  # fall back to the main bot (the person may only have started that one)
    return out


def send_to(chats, text, photo=None, reply_markup=None, reply_to=None, client=None):
    """Send to many chats. Returns {chat_id: message_id} for those that succeeded.
    `reply_to` is a {chat_id: message_id} map used to thread the message under an earlier one."""
    sent = {}
    for chat in chats:
        rt = (reply_to or {}).get(chat)
        last_err = None
        for c in _clients(client):
            try:
                if photo:
                    try:
                        msg = c.send_photo(chat, photo, text[:1024], reply_markup, rt)
                    except TelegramError as e:
                        log.info("photo to %s failed (%s), falling back to text", chat, e)
                        msg = c.send_message(chat, text, reply_markup, reply_to=rt)
                else:
                    msg = c.send_message(chat, text, reply_markup, reply_to=rt)
                sent[chat] = msg["message_id"]
                _reached(chat)
                break
            except TelegramError as e:
                log.info("notify %s failed: %s", chat, e)
                last_err = e
        else:
            _unreachable(chat, last_err)
    return sent


_BAD = set()


def _reached(chat):
    if chat in _BAD:
        _BAD.discard(chat)
        db.set(f"/notify_status/{chat}", None)


def _unreachable(chat, err):
    """Remember that an admin chat could not be reached (the panel shows why: usually 'press Start in the bot')."""
    why = getattr(err, "description", "") or str(err or "")
    _BAD.add(chat)
    try:
        db.set(f"/notify_status/{chat}", {"error": why[:160], "ts": utils.now()})
    except Exception:  # noqa: BLE001
        log.exception("could not record notify status")


def status_of(chat):
    """{'ok': bool, 'error': str} for the last alert sent to this admin chat."""
    row = db.get(f"/notify_status/{chat}") if chat else None
    return {"ok": not row, "error": (row or {}).get("error", ""), "ts": (row or {}).get("ts", 0)}


def to_admins(text, photo=None, reply_markup=None):
    return send_to(admin_chats(), text, photo=photo, reply_markup=reply_markup)


def new_user(user, tenant="", client=None, brand=""):
    """A new user started a bot: ONLY the super admin and the 2nd admin hear about it (never resellers or sellers).
    Sent through the main bot, because the admins talk to that one (reseller bots are named in the heading)."""
    head = "🙋 <b>New user started the bot</b>" + (f" · {m.esc(brand)}" if brand else (" · reseller bot" if tenant else ""))
    for chat in admin_chats():
        send_to([chat], head + "\n\n" + m.user_block(user, username=True),
                photo=(user.get("profile_file_id") or None) if not tenant else None)


def to_chat(chat_id, text, client=None, markup=None, effect=None):
    """One message to one person; never raises."""
    for c in _clients(client):
        try:
            return c.send_message(chat_id, text, markup, effect=effect)
        except TelegramError as e:
            log.info("message to %s failed: %s", chat_id, e)
    return None
