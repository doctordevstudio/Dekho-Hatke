"""Authentication helpers, CSRF, rate limiting and login lockout."""
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque

from flask import request, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from core import db, utils
from core.cache import cache
from core.errors import ApiError

PBKDF = "pbkdf2:sha256:600000"
_DUMMY_HASH = generate_password_hash("not-a-real-password", method=PBKDF)


def hash_password(p):
    return generate_password_hash(p, method=PBKDF)


def verify_password(stored_hash, password):
    return check_password_hash(stored_hash, password)


def burn_password_check(password):
    """Constant-ish time work for unknown usernames (prevents user enumeration by timing)."""
    check_password_hash(_DUMMY_HASH, password)


def password_problem(p):
    if len(p) < 10:
        return "Password must be at least 10 characters."
    if len(p) > 128:
        return "Password is too long."
    if not re.search(r"[A-Za-z]", p) or not re.search(r"\d", p):
        return "Password must contain letters and numbers."
    return None


def client_ip():
    return request.remote_addr or "0.0.0.0"


# ----------------------------------------------------------------- sessions / csrf
def current_admin():
    aid = session.get("aid")
    if not aid:
        return None
    hit = cache.get(f"admin:{aid}")
    if hit is None:
        adm = db.get(f"/admins/{aid}")
        hit = (adm,)
        cache.set(f"admin:{aid}", hit, 15)
    adm = hit[0]
    if not adm or adm.get("deleted") or not adm.get("active", True):
        return None
    if adm.get("session_version", 1) != session.get("sv"):
        return None
    return adm


def current_reseller():
    """The logged-in reseller (separate session keys from admins)."""
    rid = session.get("rid")
    if not rid:
        return None
    hit = cache.get(f"reseller_sess:{rid}")
    if hit is None:
        hit = (db.get(f"/resellers/{rid}"),)
        cache.set(f"reseller_sess:{rid}", hit, 15)
    r = hit[0]
    if not r or r.get("deleted") or not r.get("active", True):
        return None
    if r.get("session_version", 1) != session.get("rsv"):
        return None
    return r


def current_seller():
    """The logged-in seller (own session keys)."""
    sid = session.get("sid")
    if not sid:
        return None
    from services import sellers
    s = sellers.current(sid)
    if not s or s.get("session_version", 1) != session.get("ssv"):
        return None
    return s


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_valid(token):
    return bool(token) and hmac.compare_digest(str(token), str(session.get("csrf", "")))


def public_admin(adm):
    return {"id": adm["id"], "username": adm["username"], "role": adm["role"]}


# ----------------------------------------------------------------- rate limiting
class RateLimiter:
    """In-memory sliding window limiter (single-instance deployments such as Render free)."""

    def __init__(self):
        self._h = {}
        self._blocks = {}
        self._l = threading.Lock()
        self._gc_at = time.time()

    def hit(self, key, limit, window):
        """Count a hit. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        with self._l:
            if self._blocks.get(key, 0) > now:
                return False, int(self._blocks[key] - now) + 1
            dq, _ = self._h.setdefault(key, (deque(), window))
            while dq and dq[0] <= now - window:
                dq.popleft()
            if len(dq) >= limit:
                return False, max(1, int(dq[0] + window - now) + 1)
            dq.append(now)
            if now - self._gc_at > 120:
                self._gc(now)
            return True, 0

    def block(self, key, seconds):
        with self._l:
            self._blocks[key] = time.time() + seconds

    def _gc(self, now):
        self._gc_at = now
        for k in [k for k, (dq, w) in self._h.items() if not dq or dq[-1] <= now - w]:
            self._h.pop(k, None)
        for k in [k for k, t in self._blocks.items() if t <= now]:
            self._blocks.pop(k, None)


limiter = RateLimiter()


def rate_limit_or_429(key, limit_window, message="Too many requests. Slow down."):
    ok, retry = limiter.hit(key, *limit_window)
    if not ok:
        e = ApiError(message, 429, "RATE_LIMITED")
        e.retry_after = retry
        raise e


# ----------------------------------------------------------------- login lockout (persisted)
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 900
LOGIN_LOCK = 900


def _lock_key(kind, ident):
    return f"/security/login/{kind}_{hashlib.sha256(ident.encode()).hexdigest()[:24]}"


def login_locked_for(username, ip):
    now = utils.now()
    worst = 0
    for key in (_lock_key("u", username.lower()), _lock_key("ip", ip)):
        rec = db.get(key) or {}
        worst = max(worst, int(rec.get("locked_until", 0)) - now)
    return max(0, worst)


def login_failed(username, ip):
    now = utils.now()
    for key in (_lock_key("u", username.lower()), _lock_key("ip", ip)):
        rec = db.get(key) or {}
        if now - int(rec.get("first_ts", 0)) > LOGIN_WINDOW:
            rec = {"count": 0, "first_ts": now, "locked_until": 0}
        rec["count"] = int(rec.get("count", 0)) + 1
        # IP bucket is more generous (shared NATs) than the per-username bucket
        limit = LOGIN_MAX_FAILS if "/u_" in key else LOGIN_MAX_FAILS * 4
        if rec["count"] >= limit:
            rec["locked_until"] = now + LOGIN_LOCK
            rec["count"] = 0
            rec["first_ts"] = now
        db.set(key, rec)


def login_succeeded(username, ip):
    for key in (_lock_key("u", username.lower()),):
        db.set(key, {"count": 0, "first_ts": 0, "locked_until": 0})
