"""Central configuration. Everything comes from environment variables."""
import hashlib
import os
import secrets

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _flag(name, default=False):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    ENV = os.getenv("APP_ENV", "production").lower()
    IS_DEV = ENV == "development"

    # ---- security ----
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_FROM_ENV = bool(os.getenv("SECRET_KEY"))
    COOKIE_SECURE = _flag("COOKIE_SECURE", not IS_DEV)
    SESSION_HOURS = _int("SESSION_HOURS", 8)
    REPLAY_PROTECTION = _flag("REPLAY_PROTECTION", True)  # signed requests (timestamp + one-time nonce + HMAC) on every panel write
    REPLAY_WINDOW = _int("REPLAY_WINDOW", 120)  # seconds a signed request stays valid

    # first admin (created automatically when the database has no admins)
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    # optional second admin (everything except deleting) -- created/updated from env on every start
    ADMIN2_USERNAME = os.getenv("ADMIN2_USERNAME", "")
    ADMIN2_PASSWORD = os.getenv("ADMIN2_PASSWORD", "")
    ADMIN2_CHAT_ID = os.getenv("ADMIN2_CHAT_ID", "")  # can also be set from the panel

    # ---- firebase ----
    FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
    FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    FIREBASE_CREDENTIALS_FILE = os.getenv("FIREBASE_CREDENTIALS_FILE", "serviceAccountKey.json")
    USE_MEMORY_DB = _flag("USE_MEMORY_DB", False)  # local testing only

    # ---- telegram ----
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")  # can also be set from the panel
    # ---- domain / Cloudflare (sub-domain mode) ----
    # ROOT_DOMAIN=dekhohatke.com turns it on: admin./seller./reseller./api. each serve ONLY their own panel,
    # and every other host (including the raw *.onrender.com address) gets a plain 404.
    ROOT_DOMAIN = (os.getenv("ROOT_DOMAIN") or "").strip().lower().strip(".")
    HOST_MODE = bool(ROOT_DOMAIN)
    ADMIN_HOST = (os.getenv("ADMIN_HOST") or (f"admin.{ROOT_DOMAIN}" if ROOT_DOMAIN else "")).strip().lower()
    SELLER_HOST = (os.getenv("SELLER_HOST") or (f"seller.{ROOT_DOMAIN}" if ROOT_DOMAIN else "")).strip().lower()
    RESELLER_HOST = (os.getenv("RESELLER_HOST") or (f"reseller.{ROOT_DOMAIN}" if ROOT_DOMAIN else "")).strip().lower()
    API_HOST = (os.getenv("API_HOST") or (f"api.{ROOT_DOMAIN}" if ROOT_DOMAIN else "")).strip().lower()
    HOSTS = ({ADMIN_HOST: "admin", SELLER_HOST: "seller", RESELLER_HOST: "reseller", API_HOST: "api"} if ROOT_DOMAIN else {})
    # Origin lock: Cloudflare adds this secret header to every request it forwards (Transform Rule). Without it the app
    # answers 404, so nobody can reach the Render address directly. Real visitor IPs are then read from CF-Connecting-IP.
    ORIGIN_SECRET = os.getenv("ORIGIN_SECRET", "").strip()
    ORIGIN_HEADER = "X-Origin-Auth"
    _scheme = "http" if IS_DEV else "https"

    BASE_URL = (os.getenv("BASE_URL") or (f"{_scheme}://{API_HOST}" if ROOT_DOMAIN else "") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")

    @classmethod
    def panel_url(cls, kind):
        """Public address of a panel ('admin' | 'seller' | 'reseller')."""
        if cls.HOST_MODE:
            return f"{cls._scheme}://{getattr(cls, kind.upper() + '_HOST')}"
        return cls.BASE_URL + {"admin": "", "seller": "/seller", "reseller": "/reseller"}[kind] if cls.BASE_URL else ""

    @classmethod
    def panel_home(cls, kind):
        """Path of a panel's home page on its own address (sub-domain mode: always '/')."""
        if cls.HOST_MODE:
            return "/"
        return {"admin": "/", "seller": "/seller", "reseller": "/reseller"}[kind]

    @classmethod
    def panel_login(cls, kind):
        if kind == "admin":
            return "/login"
        return "/" if cls.HOST_MODE else "/" + kind

    @classmethod
    def api_base(cls, fallback=""):
        """Base URL that app developers put in their apps (the api. sub-domain in sub-domain mode)."""
        return f"{cls._scheme}://{cls.API_HOST}" if cls.HOST_MODE else fallback.rstrip("/")
    _ws = os.getenv("WEBHOOK_SECRET") or (SECRET_KEY + BOT_TOKEN)
    WEBHOOK_PATH_SECRET = hashlib.sha256(("path" + _ws).encode()).hexdigest()[:40]
    WEBHOOK_HEADER_SECRET = hashlib.sha256(("hdr" + _ws).encode()).hexdigest()[:40]

    @classmethod
    def path_secret(cls, tenant=""):
        """Secret URL part of a bot webhook. tenant '' = main bot, otherwise a reseller id."""
        base = os.getenv("WEBHOOK_SECRET") or (cls.SECRET_KEY + cls.BOT_TOKEN)
        return hashlib.sha256(("path" + tenant + base).encode()).hexdigest()[:40]

    @classmethod
    def header_secret(cls, tenant=""):
        base = os.getenv("WEBHOOK_SECRET") or (cls.SECRET_KEY + cls.BOT_TOKEN)
        return hashlib.sha256(("hdr" + tenant + base).encode()).hexdigest()[:40]

    # ---- business rules ----
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
    TXN_TTL_SECONDS = _int("TXN_TTL_SECONDS", 600)  # 10 minutes
    MAX_TXN_PER_DAY = _int("MAX_TXN_PER_DAY", 5)
    PAGE_SIZE = 20
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 50)  # broadcast media; other uploads are capped lower in code

    # ---- rate limits (requests / seconds) ----
    API_IP_LIMIT = (_int("API_IP_LIMIT", 60), 60)  # per IP per minute
    API_KEY_LIMIT = (_int("API_LICENSE_LIMIT", 20), 60)  # per license key per minute
    API_BAD_LIMIT = (_int("API_BAD_LIMIT", 10), 60)  # invalid-license guesses per IP per minute
    ADMIN_LIMIT = (_int("ADMIN_LIMIT", 600), 60)
    LOGIN_LIMIT = (10, 60)
