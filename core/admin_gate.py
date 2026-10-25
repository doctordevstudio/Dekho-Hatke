"""Signed, expiring links to the admin sign-in page.

The admin panel is not discoverable: opening the panel address directly shows "Unauthorised". A super admin or 2nd admin taps
"Admin Login" in the bot's menu, the bot answers with a link  <panel>/go/<token>  that is valid for ADMIN_LINK_MINUTES
(30 by default). Opening it unlocks the sign-in page for that browser (until the link would have expired); the usual
username + password sign-in still follows. The token is  <chat>.<expires>.<nonce>.<hmac>  -- tamper-proof, not guessable,
and only accepted while that chat id is still an admin chat.
"""
import hashlib
import hmac
import secrets
import time

from flask import session

from config import Config


def _sig(payload):
    return hmac.new(Config.SECRET_KEY.encode(), ("gate|" + payload).encode(), hashlib.sha256).hexdigest()[:32]


def make_token(chat_id, now=None):
    exp = int(now if now is not None else time.time()) + Config.ADMIN_LINK_MINUTES * 60
    payload = f"{int(chat_id)}.{exp}.{secrets.token_hex(6)}"
    return f"{payload}.{_sig(payload)}"


def link_for(chat_id):
    base = Config.panel_url("admin") or Config.BASE_URL
    return f"{base}/go/{make_token(chat_id)}"


def check_token(token, now=None):
    """(chat_id, expires_at) for a good, unexpired token of a current admin chat; otherwise None."""
    try:
        chat, exp, nonce, sig = str(token).split(".")
        payload = f"{int(chat)}.{int(exp)}.{nonce}"
        exp = int(exp)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sig(payload)):
        return None
    if exp <= (now if now is not None else time.time()):
        return None
    from services import notify
    if str(int(chat)) not in notify.admin_chats():
        return None
    return int(chat), exp


def unlock(expires_at):
    session["gate"] = int(expires_at)


def unlocked():
    """May this browser see the admin sign-in page?"""
    if not Config.ADMIN_GATE:
        return True
    try:
        return int(session.get("gate", 0)) > time.time()
    except (TypeError, ValueError):
        return False
