import math
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from config import Config
from core.errors import ApiError

TZ = ZoneInfo(Config.TIMEZONE)
FAR_FUTURE = 4102444800  # year 2100
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_TXN_ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"


# ------------------------------------------------------------------ ids / time
def now():
    return int(time.time())


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ts_id():
    """Time-ordered id (used for logs / ledger)."""
    return f"{int(time.time() * 1000):013d}_{secrets.token_hex(3)}"


def gen_license_key():
    return "".join(secrets.choice(_KEY_ALPHABET) for _ in range(18))


def gen_txn_id():
    return "TXN" + "".join(secrets.choice(_TXN_ALPHABET) for _ in range(10))


def gen_api_key():
    return secrets.token_urlsafe(32)


def local_dt(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), TZ)


def day_key(ts=None):
    return local_dt(ts).strftime("%Y-%m-%d")


def fmt_dt(ts):
    return local_dt(ts).strftime("%d %b %Y, %I:%M %p") if ts else "-"


def fmt_time(ts):
    return local_dt(ts).strftime("%I:%M:%S %p")


def _parse_day(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").replace(tzinfo=TZ)
    except ValueError:
        raise ApiError("Invalid date. Use YYYY-MM-DD.")


def resolve_range(name, start=None, end=None):
    """Return (start_ts, end_ts) inclusive for today / yesterday / all / custom (local timezone)."""
    t0 = local_dt().replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "today":
        a, b = t0, t0 + timedelta(days=1)
    elif name == "yesterday":
        a, b = t0 - timedelta(days=1), t0
    elif name == "all":
        return 0, FAR_FUTURE
    elif name == "custom":
        if not start or not end:
            raise ApiError("Pick a start and end date.")
        a = _parse_day(start)
        b = _parse_day(end) + timedelta(days=1)
        if b <= a:
            raise ApiError("End date must be after start date.")
        if (b - a).days > 400:
            raise ApiError("Custom range is limited to 400 days.")
    else:
        raise ApiError("Unknown range.")
    return int(a.timestamp()), int(b.timestamp()) - 1


def days_in_range(start_ts, end_ts):
    out, d = [], local_dt(start_ts).replace(hour=0, minute=0, second=0, microsecond=0)
    last = local_dt(min(end_ts, now() + 86400))
    while d <= last and len(out) < 401:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ validation
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(v, label, maxlen=100, required=True):
    s = _CTRL.sub("", str(v if v is not None else "")).strip()
    if required and not s:
        raise ApiError(f"{label} is required.")
    if len(s) > maxlen:
        raise ApiError(f"{label} is too long (max {maxlen}).")
    return s


def clean_url(v, label="URL", required=False):
    s = clean_text(v, label, 500, required)
    if not s:
        return ""
    p = urlparse(s)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ApiError(f"{label} must start with http:// or https://")
    return s


def clean_username(v):
    s = clean_text(v, "Username", 64, required=False).lstrip("@")
    if s and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", s):
        raise ApiError("Telegram username may contain only letters, numbers and underscore.")
    return s


def clean_chat_id(v, label="Chat ID"):
    s = clean_text(v, label, 20)
    if not re.fullmatch(r"-?\d{3,16}", s):
        raise ApiError(f"{label} must be a number.")
    return s


def parse_amount(v, allow_zero=False):
    try:
        a = round(float(v), 2)
    except (TypeError, ValueError):
        raise ApiError("Enter a valid amount.")
    if not math.isfinite(a) or a < 0 or (a == 0 and not allow_zero) or a > 1_000_000:
        raise ApiError("Enter a valid amount.")
    return int(a) if a == int(a) else a


_UNIT_ALIASES = {"hour": "hours", "hours": "hours", "hourly": "hours", "h": "hours",
                 "day": "days", "days": "days", "daily": "days", "d": "days",
                 "year": "years", "years": "years", "yearly": "years", "y": "years"}
_UNIT_SECONDS = {"hours": 3600, "days": 86400, "years": 365 * 86400}


def parse_validity(value, unit):
    u = _UNIT_ALIASES.get(str(unit).strip().lower())
    if not u:
        raise ApiError("Validity unit must be hours, days or years.")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ApiError("Validity must be a whole number.")
    if n < 1 or n > (100 if u == "years" else 36500):
        raise ApiError("Validity must be between 1 and %d." % (100 if u == "years" else 36500))
    return n, u


def validity_seconds(value, unit):
    return int(value) * _UNIT_SECONDS[unit]


def validity_label(value, unit):
    n = int(value)
    base = unit[:-1] if n == 1 else unit
    return f"{n} {base.capitalize()}"


def inr(amount):
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return "₹0"
    return f"₹{int(a):,}" if a == int(a) else f"₹{a:,.2f}"


def paginate(items, page, per=None):
    per = per or Config.PAGE_SIZE
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    total = len(items)
    pages = max(1, math.ceil(total / per))
    page = min(page, pages)
    return items[(page - 1) * per: page * per], {"page": page, "pages": pages, "total": total, "per": per}
