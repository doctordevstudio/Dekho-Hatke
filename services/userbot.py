"""The linked personal Telegram account (MTProto, through Telethon).

What it is for: the account owns / administers the storage channel and puts every bot (the main bot and each reseller's bot)
into that channel, so that any bot can later COPY a stored APK / video straight from the channel to a customer
(Telegram copies it on its own servers: nothing is downloaded or uploaded again, no "Forwarded from" label, and the file
name -- game.apk -- stays intact).

Security rules
* The login (phone -> code -> 2-step password) runs only on the server. The phone number and the password are kept in memory
  while the login is in progress and are never stored.
* Only the Telethon session string is stored (Firebase path /private/tg_account), ENCRYPTED with a key derived from SECRET_KEY,
  so the link survives redeploys. No API ever returns it, nor the phone number: panels only learn "linked / not linked".
* Only the Super Admin may unlink (that also logs the session out on Telegram's side).
Needs TG_API_ID + TG_API_HASH (my.telegram.org) and a fixed SECRET_KEY in the environment.
"""
import asyncio
import base64
import concurrent.futures
import hashlib
import logging
import re
import threading
import time

from config import Config
from core import db, utils

log = logging.getLogger("userbot")
PATH = "/private/tg_account"
LOGIN_TTL = 600  # seconds a half-finished login (code sent, not yet confirmed) is kept


class UserbotError(Exception):
    """A problem that can be shown to the admin as it is."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ session encryption
def _fernet():
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("tg-session|" + Config.SECRET_KEY).encode()).digest())
    return Fernet(key)


def _seal(text):
    return _fernet().encrypt(text.encode()).decode()


def _unseal(token):
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise UserbotError("The saved Telegram session cannot be read (SECRET_KEY changed?). Link the account again.")


# ------------------------------------------------------------------ Telethon, driven from ordinary threads
_FRIENDLY = {
    "PhoneNumberInvalidError": "That phone number is not valid. Use the international format, e.g. +919876543210.",
    "PhoneNumberBannedError": "Telegram has banned that phone number.",
    "PhoneNumberFloodError": "Too many attempts for that number. Try again later.",
    "PhoneCodeInvalidError": "That code is wrong. Check the code Telegram sent to the account.",
    "PhoneCodeEmptyError": "Enter the code Telegram sent to the account.",
    "PhoneCodeExpiredError": "That code has expired. Start again to get a new one.",
    "PasswordHashInvalidError": "Wrong 2-step verification password.",
    "ApiIdInvalidError": "TG_API_ID / TG_API_HASH are not valid. Check them on my.telegram.org.",
    "AuthKeyUnregisteredError": "The linked account was logged out. Link it again.",
    "SessionRevokedError": "The linked account was logged out. Link it again.",
    "UserDeactivatedError": "That Telegram account is deactivated.",
    "ChatAdminRequiredError": "The linked account must be the owner or an admin with the “Add admins” right in that chat.",
    "UserNotMutualContactError": "Telegram will not let this account add that bot. Add the bot to the chat by hand.",
    "UserPrivacyRestrictedError": "Telegram will not let this account add that bot. Add the bot to the chat by hand.",
    "BotsTooMuchError": "That chat already has the maximum number of bots.",
    "AdminsTooMuchError": "That chat already has the maximum number of admins.",
    "ChannelPrivateError": "The linked account cannot see that chat. Join it with the account first.",
    "UsernameNotOccupiedError": "No chat or bot with that username exists.",
    "UsernameInvalidError": "That username is not valid.",
}


def _explain(e):
    if isinstance(e, UserbotError):
        return e.message
    name = type(e).__name__
    if name == "FloodWaitError":
        return f"Telegram asks to wait {getattr(e, 'seconds', 60)} seconds before trying again."
    return _FRIENDLY.get(name) or f"Telegram error ({name}). Try again."


class TelethonBackend:
    """One Telethon client on its own asyncio thread. Every public method is synchronous."""

    def __init__(self):
        self._loop = None
        self._lock = threading.Lock()
        self._client = None       # the live, logged-in client
        self._pending = None      # {"client", "phone", "hash", "ts", "step"} while a login is in progress

    # ---- plumbing
    def _ensure_loop(self):
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, daemon=True, name="telethon").start()
                self._loop = loop
            return self._loop

    def _run(self, coro, timeout=60):
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise UserbotError("Telegram did not answer in time. Try again.")
        except UserbotError:
            raise
        except Exception as e:  # Telethon RPC errors, network errors ...
            log.info("telethon call failed: %s", type(e).__name__)
            raise UserbotError(_explain(e))

    async def _new_client(self, session=""):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        return TelegramClient(StringSession(session), Config.TG_API_ID, Config.TG_API_HASH, receive_updates=False,
                              device_model="Dekho Hatke server", app_version="1.0", system_version="Linux")

    # ---- login (phone -> code -> password)
    def pending_step(self):
        p = self._pending
        if not p or time.time() - p["ts"] > LOGIN_TTL:
            return ""
        return p.get("step", "")

    async def _drop_pending(self):
        p, self._pending = self._pending, None
        if p:
            try:
                await p["client"].disconnect()
            except Exception:
                pass

    def start_login(self, phone):
        async def go():
            await self._drop_pending()
            c = await self._new_client()
            await c.connect()
            try:
                sent = await c.send_code_request(phone)
            except Exception:
                await c.disconnect()
                raise
            self._pending = {"client": c, "phone": phone, "hash": sent.phone_code_hash, "ts": time.time(), "step": "code"}
        self._run(go())

    def _need_pending(self):
        p = self._pending
        if not p or time.time() - p["ts"] > LOGIN_TTL:
            raise UserbotError("The login timed out. Start again.")
        return p

    def submit_code(self, code):
        """-> ('done', session_string) or ('password', None)."""
        async def go():
            p = self._need_pending()
            try:
                await p["client"].sign_in(phone=p["phone"], code=code, phone_code_hash=p["hash"])
            except Exception as e:
                if type(e).__name__ == "SessionPasswordNeededError":
                    p["step"] = "password"
                    return "password", None
                raise
            return "done", await self._finish(p)
        return self._run(go())

    def submit_password(self, password):
        async def go():
            p = self._need_pending()
            await p["client"].sign_in(password=password)
            return await self._finish(p)
        return self._run(go())

    async def _finish(self, p):
        session = p["client"].session.save()
        old, self._client, self._pending = self._client, p["client"], None
        if old is not None:
            try:
                await old.disconnect()
            except Exception:
                pass
        return session

    def cancel_login(self):
        self._run(self._drop_pending())

    # ---- the linked session
    def connected(self):
        c = self._client
        return bool(c is not None and c.is_connected())

    def connect(self, session):
        """Use a stored session. False when Telegram says it is no longer logged in."""
        async def go():
            c = await self._new_client(session)
            await c.connect()
            if not await c.is_user_authorized():
                await c.disconnect()
                return False
            old, self._client = self._client, c
            if old is not None:
                try:
                    await old.disconnect()
                except Exception:
                    pass
            return True
        return self._run(go())

    async def _entity(self, ref):
        c = self._client
        ref = str(ref).strip()
        if "t.me/" in ref:
            ref = ref.split("t.me/")[-1].strip("/").split("/")[0].split("?")[0]
        target = int(ref) if re.fullmatch(r"-?\d+", ref) else ref
        try:
            return await c.get_entity(target)
        except Exception as first:
            if type(first).__name__ in ("FloodWaitError", "AuthKeyUnregisteredError"):
                raise
        await c.get_dialogs(limit=500)  # a fresh session knows no chats yet; this loads them
        try:
            return await c.get_entity(target)
        except Exception:
            raise UserbotError("Could not find that chat. Make sure the linked account has joined it "
                               "(for a private channel use its numeric id, like -1001234567890).")

    def resolve(self, ref):
        async def go():
            from telethon import utils as tutils
            from telethon.tl.types import Channel
            ent = await self._entity(ref)
            if not isinstance(ent, Channel):
                raise UserbotError("Use a channel or a supergroup (an old-style small group cannot be used).")
            try:
                perms = await self._client.get_permissions(ent, "me")
            except Exception:
                raise UserbotError("The linked account is not a member of that chat. Join it with the account first.")
            return {"chat_id": str(tutils.get_peer_id(ent)), "title": ent.title or "", "username": ent.username or "",
                    "broadcast": bool(ent.broadcast), "admin": bool(perms.is_admin or perms.is_creator),
                    "can_add_admins": bool(perms.is_creator or getattr(perms, "add_admins", False))}
        return self._run(go())

    def grant_bot(self, chat_ref, bot_username):
        """Make sure a bot can read / copy from the chat. Returns (ok, note)."""
        async def go():
            from telethon.tl.functions.channels import EditAdminRequest, InviteToChannelRequest
            from telethon.tl.types import ChatAdminRights
            c = self._client
            ent = await self._entity(chat_ref)
            bot = await c.get_entity(bot_username.lstrip("@"))
            try:
                perms = await c.get_permissions(ent, bot)
                if perms.is_admin or perms.is_creator or not getattr(ent, "broadcast", False):
                    return True, "already has access"
            except Exception:
                pass  # not a member yet
            if getattr(ent, "broadcast", False):
                await c(EditAdminRequest(channel=ent, user_id=bot, admin_rights=ChatAdminRights(post_messages=True), rank=""))
                return True, "added as admin"
            await c(InviteToChannelRequest(ent, [bot]))
            return True, "added to the group"
        return self._run(go())

    def logout(self):
        async def go():
            c, self._client = self._client, None
            if c is None:
                return
            try:
                await c.log_out()
            except Exception:
                try:
                    await c.disconnect()
                except Exception:
                    pass
        self._run(go(), 20)


# ------------------------------------------------------------------ service layer (what routes and the bot call)
_backend = None
_conn_lock = threading.Lock()


def backend():
    global _backend
    if _backend is None:
        _backend = TelethonBackend()
    return _backend


def configured():
    return bool(Config.TG_API_ID and Config.TG_API_HASH)


def record():
    return db.get(PATH) or {}


def linked():
    r = record()
    return bool(r.get("session")) and not r.get("expired")


def status():
    """Everything a panel may learn about the account: nothing but yes / no."""
    r = record()
    return {"configured": configured(), "linked": bool(r.get("session")) and not r.get("expired"),
            "expired": bool(r.get("session")) and bool(r.get("expired")), "step": backend().pending_step() if _backend else ""}


def _need_ready():
    if not configured():
        raise UserbotError("TG_API_ID and TG_API_HASH are not set on the server (get them at my.telegram.org → API development tools).")
    if not Config.SECRET_KEY_FROM_ENV:
        raise UserbotError("SECRET_KEY is not set on the server. It encrypts the saved session; set it first.")


def start(phone):
    _need_ready()
    phone = re.sub(r"[\s\-()]", "", str(phone or ""))
    if not re.fullmatch(r"\+?\d{7,15}", phone):
        raise UserbotError("Enter the phone number with the country code, e.g. +919876543210.")
    backend().start_login(phone if phone.startswith("+") else "+" + phone)


def _store(session, actor):
    db.set(PATH, {"session": _seal(session), "linked_at": utils.now(), "linked_by": actor, "expired": False})


def submit_code(code, actor):
    """-> 'password' when 2-step verification is on, else 'done'."""
    _need_ready()
    code = re.sub(r"\D", "", str(code or ""))
    if not 4 <= len(code) <= 8:
        raise UserbotError("Enter the numeric code Telegram sent to the account.")
    step, session = backend().submit_code(code)
    if step == "done":
        _store(session, actor)
    return step


def submit_password(password, actor):
    _need_ready()
    if not password:
        raise UserbotError("Enter the 2-step verification password.")
    _store(backend().submit_password(str(password)), actor)
    return "done"


def cancel():
    if _backend:
        backend().cancel_login()


def _live():
    """The backend, connected with the stored session (restored after a redeploy)."""
    _need_ready()
    r = record()
    if not r.get("session"):
        raise UserbotError("No Telegram account is linked yet.")
    b = backend()
    with _conn_lock:
        if not b.connected():
            if not b.connect(_unseal(r["session"])):
                db.update(PATH, {"expired": True})
                raise UserbotError("The linked account was logged out from Telegram. Link it again.")
    return b


def resolve_chat(ref):
    return _live().resolve(ref)


def grant_bot(chat_ref, bot_username):
    return _live().grant_bot(chat_ref, bot_username)


def unlink():
    """Forget the account and end its session on Telegram's side."""
    if record().get("session") and configured():
        try:
            _live().logout()
        except UserbotError:
            pass  # already logged out / unreadable: just forget it
    db.set(PATH, None)
