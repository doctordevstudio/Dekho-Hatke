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

    # ---- admin entrance ----
    # The admin / super-admin sign-in page is invisible ("Unauthorised") unless the visitor came through a signed link that the
    # bot hands out (menu -> Admin Login). The link works for ADMIN_LINK_MINUTES. ADMIN_GATE=0 turns this off (local dev only).
    ADMIN_GATE = _flag("ADMIN_GATE", True)
    ADMIN_LINK_MINUTES = _int("ADMIN_LINK_MINUTES", 30)

    # ---- telegram ----
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    # my.telegram.org -> API development tools. Needed only for linking a personal account (Settings -> Channel & account).
    TG_API_ID = _int("TG_API_ID", _int("API_ID", 0))
    TG_API_HASH = (os.getenv("TG_API_HASH") or os.getenv("API_HASH") or "").strip()
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")  # can also be set from the panel
    # ---- shortener (step-gate on the blog pages of the root domain) ----
    # The blog pages load /assets/gate.js, which calls /api/init|event|verify|ping|health through your Cloudflare worker.
    # The worker sends X-DH-Proxy: <PROXY_SECRET> and X-DH-IP: <real visitor ip>. Without PROXY_SECRET the gate API stays closed.
    SITE_URL = (os.getenv("SITE_URL") or "https://dekhohatke.com").strip().rstrip("/")
    PROXY_SECRET = os.getenv("PROXY_SECRET", "").strip()
    TURNSTILE_SITEKEY = os.getenv("TURNSTILE_SITEKEY", "").strip()
    TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET", "").strip()
    # Direct mode: the blog pages call this server straight from the visitor's browser (CORS), no Cloudflare worker needed.
    # Only requests whose Origin is your website are answered. 0 = only the worker (X-DH-Proxy) may call.
    GATE_DIRECT = os.getenv("GATE_DIRECT", "1").strip() not in ("0", "false", "no", "")
    GATE_ORIGINS = os.getenv("GATE_ORIGINS", "").strip()             # extra allowed sites, comma separated (default: SITE_URL + www.)
    GATE_GRACE = float(os.getenv("GATE_GRACE", "0.35"))              # timer / network jitter allowed on every timing check (s)
    GATE_MIN_SCROLL_GAP = float(os.getenv("GATE_MIN_SCROLL_GAP", "1.2"))  # min seconds between the top and the bottom click
    MIN_UPI_PAYMENT = float(os.getenv("MIN_UPI_PAYMENT", "10"))  # a UPI payment request is never smaller than this (wallet + UPI orders)
    GATE_IP_LIMIT = _int("GATE_IP_LIMIT", 90)                        # gate API calls per visitor ip per minute
    # ---- domain / Cloudflare (two custom domains: Render's free plan allows two) ----
    # ROOT_DOMAIN=dekhohatke.com turns it on:
    #   drdev.<domain>  -> ALL panels: admin ( / and /login ), /seller, /reseller   (PANEL_HOST)
    #   api.<domain>    -> the license verification API only (/api/v1/*)            (API_HOST)
    #   the raw *.onrender.com address -> ONLY the Telegram webhooks (/telegram/*) and /healthz; everything else is 404
    ROOT_DOMAIN = (os.getenv("ROOT_DOMAIN") or "").strip().lower().strip(".")
    HOST_MODE = bool(ROOT_DOMAIN)
    PANEL_HOST = (os.getenv("PANEL_HOST") or (f"drdev.{ROOT_DOMAIN}" if ROOT_DOMAIN else "")).strip().lower()
    API_HOST = (os.getenv("API_HOST") or (f"api.{ROOT_DOMAIN}" if ROOT_DOMAIN else "")).strip().lower()
    HOSTS = ({PANEL_HOST: "panel", API_HOST: "api"} if ROOT_DOMAIN else {})
    # Origin lock: Cloudflare adds this secret header to every request it forwards for the two custom domains (Transform
    # Rule). Without it the app answers 404, so nobody can reach those hosts around Cloudflare. Real visitor IPs are then
    # read from CF-Connecting-IP. Telegram's webhook calls the Render address directly (it cannot send the header); those
    # requests are protected by their own secret URL + secret header.
    ORIGIN_SECRET = os.getenv("ORIGIN_SECRET", "").strip()
    ORIGIN_HEADER = "X-Origin-Auth"
    _scheme = "http" if IS_DEV else "https"

    # Where Telegram calls the bots: the Render address itself (RENDER_EXTERNAL_URL is set by Render automatically).
    # NOTE: once a custom domain is added, Render's RENDER_EXTERNAL_URL can become that custom domain (it shows it as the
    # "primary URL"), but Telegram must call the plain onrender.com address -> prefer RENDER_EXTERNAL_HOSTNAME, and set BASE_URL by hand if in doubt.
    _render_host = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip()
    BASE_URL = (os.getenv("BASE_URL") or (f"https://{_render_host}" if _render_host else "") or os.getenv("RENDER_EXTERNAL_URL")
                or (f"{_scheme}://{PANEL_HOST}" if ROOT_DOMAIN else "")).rstrip("/")

    @classmethod
    def webhook_problem(cls):
        """Why Telegram's webhooks cannot work with the current BASE_URL, or ''. In two-domain mode the webhook must use the raw
        onrender.com address: the custom domains only answer Cloudflare."""
        if cls.HOST_MODE and cls.BASE_URL:
            host = cls.BASE_URL.split("://", 1)[-1].split("/")[0].lower()
            if host in cls.HOSTS:
                return (f"BASE_URL / RENDER_EXTERNAL_URL is {host}, one of your custom domains. Set the environment variable BASE_URL to the "
                        "service's own address (https://<service>.onrender.com) so Telegram can reach the bots.")
        return ""

    @classmethod
    def panel_url(cls, kind):
        """Public address of a panel ('admin' | 'seller' | 'reseller')."""
        base = f"{cls._scheme}://{cls.PANEL_HOST}" if cls.HOST_MODE else cls.BASE_URL
        return base + {"admin": "", "seller": "/seller", "reseller": "/reseller"}[kind] if base else ""

    @classmethod
    def panel_home(cls, kind):
        """Path of a panel's home page: admin at '/', the others under their own path (all on the same address)."""
        return {"admin": "/", "seller": "/seller", "reseller": "/reseller"}[kind]

    @classmethod
    def panel_login(cls, kind):
        return {"admin": "/login", "seller": "/seller", "reseller": "/reseller"}[kind]

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

    # ---- Monero (XMR) crypto payments (Settings -> Crypto, Super Admin only) ----
    # XMR_KEYPASS is the password the wallet seed is encrypted with before it goes to Firebase. Set it in Render -> Environment
    # BEFORE creating the wallet and never change it afterwards (the seed could not be decrypted any more).
    XMR_KEYPASS = os.getenv("XMR_KEYPASS", "")
    XMR_NODE = os.getenv("XMR_NODE", "node.moneroworld.com:18089").strip()       # public remote node (no local daemon)
    XMR_NODES = os.getenv("XMR_NODES", "").strip()                                # extra nodes, comma separated: fallbacks + payment cross-check
    XMR_NODE_ANY_CERT = os.getenv("XMR_NODE_ANY_CERT", "1").strip() not in ("0", "false", "no", "")  # public nodes often use self-signed TLS
    XMR_WALLET_RPC_BIN = os.getenv("XMR_WALLET_RPC_BIN", "").strip()              # default: ./bin/monero-wallet-rpc, then PATH
    XMR_RPC_PORT = _int("XMR_RPC_PORT", 18083)                                    # wallet-rpc listens on 127.0.0.1 only
    XMR_MIN_USD = float(os.getenv("XMR_MIN_USD", "0.10"))                         # smallest crypto order (USD)
    XMR_WINDOW_MINUTES = _int("XMR_WINDOW_MINUTES", 60)                           # an invoice is payable for this long
    XMR_GRACE_MINUTES = _int("XMR_GRACE_MINUTES", 30)                             # extra time ONLY when a payment is already seen in the mempool
    XMR_CONFIRMATIONS = _int("XMR_CONFIRMATIONS", 1)
    XMR_TOLERANCE_PCT = float(os.getenv("XMR_TOLERANCE_PCT", "0.5"))              # exchange withdrawal fees: accept this much under the quote
    XMR_POLL_SECONDS = _int("XMR_POLL_SECONDS", 20)
    XMR_PRICE_TTL = _int("XMR_PRICE_TTL", 60)
    XMR_MAX_INVOICES_PER_DAY = _int("XMR_MAX_INVOICES_PER_DAY", 10)
    DEFAULT_INR_PER_USD = float(os.getenv("DEFAULT_INR_PER_USD", "85"))           # until you set the rate in Settings -> Crypto
    PAGE_SIZE = 20
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 50)  # broadcast media; other uploads are capped lower in code

    # ---- rate limits (requests / seconds) ----
    API_IP_LIMIT = (_int("API_IP_LIMIT", 60), 60)  # per IP per minute
    API_KEY_LIMIT = (_int("API_LICENSE_LIMIT", 20), 60)  # per license key per minute
    API_BAD_LIMIT = (_int("API_BAD_LIMIT", 10), 60)  # invalid-license guesses per IP per minute
    ADMIN_LIMIT = (_int("ADMIN_LIMIT", 600), 60)
    LOGIN_LIMIT = (10, 60)
