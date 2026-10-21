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


def msg(uid, text=None, photo=None, name="Rahul", doc=None):
    m = {"message_id": 1, "from": {"id": uid, "first_name": name, "username": f"u{uid}"},
         "chat": {"id": uid, "type": "private"}}
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
    c = app.test_client()
    page = c.get("/login").get_data(as_text=True)
    csrf = page.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/login", data={"username": user, "password": pw, "csrf_token": csrf})
    assert r.status_code == 302, r.status_code
    me = c.get("/admin/api/me").get_json()
    return c, {"X-CSRF-Token": me["csrf"]}, me
