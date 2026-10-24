"""Telegram Bot API client (plain HTTPS). One instance per bot token (main bot + one per reseller)."""
import json
import logging
import threading

import requests

from config import Config

log = logging.getLogger("tg")

# Animated full-screen message effects (private chats only). Used sparingly for "wow" moments.
EFFECT_PARTY = "5046509860389126442"   # 🎉
EFFECT_FIRE = "5104841245755180586"    # 🔥
EFFECT_LIKE = "5107584321108051014"    # 👍
EFFECT_HEART = "5044134455711629726"   # ❤️

MEDIA_METHODS = {"photo": ("sendPhoto", "photo"), "video": ("sendVideo", "video"),
                 "document": ("sendDocument", "document"), "animation": ("sendAnimation", "animation"),
                 "audio": ("sendAudio", "audio")}


class TelegramError(Exception):
    def __init__(self, description, code=None, retry_after=None):
        super().__init__(description)
        self.description = description
        self.code = code
        self.retry_after = retry_after

    @property
    def blocked(self):
        d = self.description.lower()
        return self.code == 403 or "blocked" in d or "deactivated" in d or "chat not found" in d


def _encode(payload):
    return {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (payload or {}).items()
            if v is not None}


class Telegram:
    def __init__(self, token=None):
        self._token = token
        self.session = requests.Session()
        self._me = None
        self._lock = threading.Lock()

    @property
    def token(self):
        return self._token if self._token is not None else Config.BOT_TOKEN

    def call(self, method, payload=None, files=None, timeout=20):
        if not self.token:
            raise TelegramError("BOT_TOKEN is not configured")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        payload = {k: v for k, v in (payload or {}).items() if v is not None}
        try:
            if files:
                r = self.session.post(url, data=_encode(payload), files=files, timeout=max(timeout, 60))
            else:
                r = self.session.post(url, json=payload, timeout=timeout)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise TelegramError(f"network error: {e}")
        if not data.get("ok"):
            raise TelegramError(data.get("description", "unknown error"), data.get("error_code"),
                                (data.get("parameters") or {}).get("retry_after"))
        return data["result"]

    def _effectful(self, method, payload, effect, files=None):
        """Try with an animated message effect; if Telegram refuses it, send normally."""
        if effect:
            try:
                return self.call(method, {**payload, "message_effect_id": effect}, files=files)
            except TelegramError as e:
                if "effect" not in e.description.lower() and "message_effect" not in e.description.lower():
                    raise
        return self.call(method, payload, files=files)

    @staticmethod
    def _reply(reply_to):
        return {"message_id": int(reply_to), "allow_sending_without_reply": True} if reply_to else None

    # ------------------------------------------------------------------ sending
    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML", reply_to=None, effect=None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode or None, "reply_markup": reply_markup,
                   "reply_parameters": self._reply(reply_to), "link_preview_options": {"is_disabled": True}}
        return self._effectful("sendMessage", payload, effect)

    def send_media(self, kind, chat_id, media, caption=None, reply_markup=None, reply_to=None, parse_mode="HTML",
                   effect=None, filename=None):
        """`media` is a file_id / URL string, or raw bytes (uploaded under `filename`)."""
        method, field = MEDIA_METHODS[kind]
        payload = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode if caption else None,
                   "reply_markup": reply_markup, "reply_parameters": self._reply(reply_to)}
        if kind == "video":
            payload["supports_streaming"] = True
        files = None
        if isinstance(media, tuple):  # (filename, bytes)
            files = {field: media}
        elif isinstance(media, (bytes, bytearray)):
            files = {field: (filename or "file.bin", bytes(media))}
        else:
            payload[field] = media
        return self._effectful(method, payload, effect, files=files)

    def send_photo(self, chat_id, photo, caption=None, reply_markup=None, reply_to=None, parse_mode="HTML", effect=None):
        return self.send_media("photo", chat_id, photo, caption, reply_markup, reply_to, parse_mode, effect)

    def edit_text(self, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
        return self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                             "parse_mode": parse_mode, "reply_markup": reply_markup,
                                             "link_preview_options": {"is_disabled": True}})

    def edit_caption(self, chat_id, message_id, caption, reply_markup=None, parse_mode="HTML"):
        return self.call("editMessageCaption", {"chat_id": chat_id, "message_id": message_id,
                                                "caption": caption, "parse_mode": parse_mode,
                                                "reply_markup": reply_markup})

    def edit_media(self, chat_id, message_id, kind, media, caption=None, reply_markup=None, parse_mode="HTML"):
        return self.call("editMessageMedia", {
            "chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup,
            "media": {"type": kind, "media": media, "caption": caption, "parse_mode": parse_mode if caption else None}})

    def edit_markup(self, chat_id, message_id, reply_markup):
        return self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                    "reply_markup": reply_markup or {"inline_keyboard": []}})

    def delete_message(self, chat_id, message_id):
        try:
            return self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}, timeout=8)
        except TelegramError:
            return False

    def answer_callback(self, cb_id, text=None, alert=False):
        try:
            return self.call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text,
                                                     "show_alert": alert or None}, timeout=8)
        except TelegramError:
            return None

    # ------------------------------------------------------------------ info
    def get_chat_member(self, chat_id, user_id):
        return self.call("getChatMember", {"chat_id": chat_id, "user_id": int(user_id)}, timeout=10)

    def get_chat(self, chat_id):
        return self.call("getChat", {"chat_id": chat_id}, timeout=10)

    def get_me(self, refresh=False):
        if self._me is None or refresh:
            self._me = self.call("getMe", timeout=10)
        return self._me

    def profile_file_id(self, user_id):
        r = self.call("getUserProfilePhotos", {"user_id": int(user_id), "limit": 1}, timeout=10)
        if r.get("total_count") and r.get("photos"):
            sizes = r["photos"][0]
            return sizes[min(1, len(sizes) - 1)]["file_id"]
        return ""

    def download(self, file_id):
        """Return (bytes, extension). The bot token never leaves the server. Bot API limit: 20 MB."""
        info = self.call("getFile", {"file_id": file_id}, timeout=15)
        path = info.get("file_path", "")
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        try:
            r = self.session.get(url, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            raise TelegramError(f"download failed: {e}")
        return r.content, path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"

    # ------------------------------------------------------------------ webhook
    def set_webhook(self, url, secret):
        return self.call("setWebhook", {"url": url, "secret_token": secret,
                                        "allowed_updates": ["message", "callback_query"],
                                        "max_connections": 20, "drop_pending_updates": False})

    def set_commands(self, commands, chat_id=None):
        """commands = [(name, description)]. chat_id limits them to one chat (used for the admin commands)."""
        payload = {"commands": [{"command": c, "description": d} for c, d in commands]}
        if chat_id:
            payload["scope"] = {"type": "chat", "chat_id": int(chat_id)}
        return self.call("setMyCommands", payload, timeout=10)

    START_ONLY = [("start", "Start the bot")]

    def set_start_menu(self, admin_chats=()):
        """The ≡ menu button shows ONLY /start (for everybody, admins included): default scope = /start,
        and any per-chat command lists left over from older versions are removed. Never raises."""
        try:
            self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in self.START_ONLY]}, timeout=10)
            for chat in admin_chats:
                try:
                    self.call("deleteMyCommands", {"scope": {"type": "chat", "chat_id": int(chat)}}, timeout=10)
                except (TelegramError, ValueError):
                    pass
            self.call("setChatMenuButton", {"menu_button": {"type": "commands"}}, timeout=10)
            return True
        except TelegramError:
            return False

    def webhook_info(self):
        return self.call("getWebhookInfo", timeout=10)


tg = Telegram()  # the main bot (BOT_TOKEN)
_clients = {}
_clients_lock = threading.Lock()


def client_for(token):
    """Shared client per bot token (reseller bots)."""
    if not token or token == Config.BOT_TOKEN:
        return tg
    with _clients_lock:
        c = _clients.get(token)
        if c is None:
            c = _clients[token] = Telegram(token)
        return c
