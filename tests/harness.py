"""Shared test harness: in-memory DB, fake Telegram (records every call), stub `qrcode` when not installed."""
import io
import os
import sys
import types

os.environ.update(APP_ENV="development", USE_MEMORY_DB="1", ADMIN_USERNAME="boss", ADMIN_PASSWORD="TestPassw0rd123",
                  BOT_TOKEN="123456:fakefakefakefakefakefakefakefakefake", ADMIN_CHAT_ID="999", SECRET_KEY="test-secret",
                  ADMIN2_USERNAME="boss2", ADMIN2_PASSWORD="TestPassw0rd456", ADMIN2_CHAT_ID="998",
                  BASE_URL="https://shop.example.com")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

try:  # the real library is used in production; tests only need something that makes a PNG
    import qrcode  # noqa: F401
except ImportError:
    class _Img:
        def save(self, buf, format="PNG"):
            Image.new("RGB", (330, 330), "white").save(buf, format)

    class _QR:
        last_data = None

        def __init__(self, **kw): pass

        def add_data(self, data):
            _QR.last_data = data

        def make(self, fit=True): pass

        def make_image(self, **kw): return _Img()

    mod = types.ModuleType("qrcode")
    mod.QRCode = _QR
    mod.constants = types.SimpleNamespace(ERROR_CORRECT_M=0)
    sys.modules["qrcode"] = mod
    sys.modules["qrcode.constants"] = mod.constants

from bot import tg as tgmod  # noqa: E402

CALLS = []
_mid = [100]
FAIL = {"getChatMember": None}


def fake_call(self, method, payload=None, files=None, timeout=20):
    CALLS.append((method, payload or {}, self.token, files))
    _mid[0] += 1
    if method in ("sendMessage", "editMessageText", "editMessageCaption", "editMessageMedia"):
        return {"message_id": _mid[0], "chat": {"id": (payload or {}).get("chat_id")}}
    if method == "sendPhoto":
        return {"message_id": _mid[0], "photo": [{"file_id": f"PHOTOID{_mid[0]:05d}XXXXXXXX"}]}
    if method == "sendDocument":
        return {"message_id": _mid[0], "document": {"file_id": f"DOCID{_mid[0]:05d}XXXXXXXXXX"}}
    if method == "sendVideo":
        return {"message_id": _mid[0], "video": {"file_id": f"VIDID{_mid[0]:05d}XXXXXXXXXX"}}
    if method == "copyMessage":
        return {"message_id": _mid[0]}
    if method == "getMe":
        return {"id": 1, "username": "main_bot" if self.token == os.environ["BOT_TOKEN"] else "reseller_bot", "first_name": "Bot"}
    if method == "getChat":
        return {"id": -1001, "title": "Chan", "username": "chan"}
    if method == "getChatMember":
        return {"status": "administrator" if (payload or {}).get("user_id") == 1 else "member"}
    if method == "getUserProfilePhotos":
        return {"total_count": 1, "photos": [[{"file_id": "PROFILEFILE000002"}]]}
    if method == "getWebhookInfo":
        return {"pending_update_count": 0}
    return True


# --- panel writes are signed (anti-replay): the test client signs them exactly like the browser does
import hashlib  # noqa: E402
import hmac  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

_orig_open = FlaskClient.open


def _signed_open(self, *args, **kwargs):
    headers = kwargs.get("headers")
    method = str(kwargs.get("method", "GET")).upper()
    path = args[0] if args and isinstance(args[0], str) else ""
    if headers and "X-CSRF-Token" in headers and method in ("POST", "PUT", "PATCH", "DELETE") and "X-Req-Sig" not in headers \
            and not os.environ.get("TEST_NO_SIGN"):
        ts, nonce = str(int(time.time())), secrets.token_hex(12)
        clean = path.split("?")[0]
        sig = hmac.new(headers["X-CSRF-Token"].encode(), f"{method}|{clean}|{ts}|{nonce}".encode(), hashlib.sha256).hexdigest()
        kwargs["headers"] = {**headers, "X-Req-Ts": ts, "X-Req-Nonce": nonce, "X-Req-Sig": sig}
    return _orig_open(self, *args, **kwargs)


FlaskClient.open = _signed_open

tgmod.Telegram.call = fake_call
tgmod.Telegram.download = lambda self, fid: (b"x" * 100, "jpg")


def sent(method="sendMessage", contains="", chat=None):
    return [p for m, p, *_ in CALLS if m == method and contains in str(p) and (chat is None or str(p.get("chat_id")) == str(chat))]


def png(size=300, color="white"):
    b = io.BytesIO()
    im = Image.new("RGB", (size, size), color)
    for i in range(0, size, 7):  # some pixel variety so it is > 1 KB
        im.putpixel((i % size, (i * 3) % size), (i % 255, 10, 200))
    im.save(b, "PNG")
    return b.getvalue()


def ok(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        raise SystemExit(f"FAILED: {label}")


def msg(uid, text=None, photo=None, name="Rahul", doc=None, video=None):
    m = {"message_id": 1, "from": {"id": uid, "first_name": name, "username": f"u{uid}"},
         "chat": {"id": uid, "type": "private"}}
    if doc:  # (file_id, file_name, size)
        m["document"] = {"file_id": doc[0], "file_name": doc[1], "file_size": doc[2], "mime_type": "application/octet-stream"}
    if video:  # (file_id, size)
        m["video"] = {"file_id": video[0], "file_size": video[1], "mime_type": "video/mp4", "file_name": "v.mp4"}
    if text is not None:
        m["text"] = text
    if photo:
        m["photo"] = [{"file_id": photo, "file_unique_id": "uniq" + photo, "file_size": 100}]
    return {"update_id": id(m) % 10 ** 9, "message": m}


def cbq(uid, data, name="Rahul", mid=7):
    return {"update_id": id(data) % 10 ** 9 + 1, "callback_query": {
        "id": "cb1", "from": {"id": uid, "first_name": name, "username": f"u{uid}"}, "data": data,
        "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}}}


def login_admin(app, user="boss", pw="TestPassw0rd123"):
    from core import admin_gate
    c = app.test_client()
    assert c.get("/login").status_code == 401, "the sign-in page must be hidden without the bot's link"
    assert c.get(f"/go/{admin_gate.make_token(999)}").status_code == 302  # the link the bot hands the admin
    page = c.get("/login").get_data(as_text=True)
    csrf = page.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/login", data={"username": user, "password": pw, "csrf_token": csrf})
    assert r.status_code == 302, r.status_code
    me = c.get("/admin/api/me").get_json()
    return c, {"X-CSRF-Token": me["csrf"]}, me


def bot_file(uid, kind, fid, name="game.apk", size=5000, process=None):
    """Simulate a seller / admin sending a file to the bot. Returns the text of the bot's reply (it contains the file id)."""
    from bot import handlers
    from bot.antispam import spam
    spam._s.clear()
    CALLS.clear()
    u = msg(uid, doc=(fid, name, size)) if kind == "doc" else msg(uid, video=(fid, size))
    (process or handlers.process_update)(u)
    return " ".join(str(c[1].get("text", "")) for c in CALLS if c[0] == "sendMessage" and str(c[1].get("chat_id")) == str(uid))
