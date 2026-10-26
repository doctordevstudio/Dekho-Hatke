"""Shortener = the step-gate on the blog pages of the root domain.

A *shortener* (made in the panel) says: N pages, a top wait and a bottom wait per page, and what happens after the last
"Generate" click -- either **license** (the Telegram bot sends the key, used by Free plans) or **redirect** (the visitor is
sent to a URL, nobody is notified in Telegram).

Speed: every running link ("session") lives in memory and is the authority while it runs -- the gate API never waits for
the database or for Telegram.  A background flusher saves sessions and per-day counters to Firebase every ~2 s (write-behind),
Telegram messages and license delivery run on a small thread pool and are retried until they arrive.  A link that is not in
memory (after a restart) is loaded from Firebase on first use, so nothing is lost on redeploy.

Server-side timing (same rules as the original gate): the top wait must have passed before "top click", the bottom wait before
"bottom click", the whole step's time before "verify", and on the last step the real time since the first visit must exceed all
waits combined.

Firebase:  /shorteners/<sid>         the shortener definitions
           /sl_links/<sid>/<code>    one running / finished link
           /sl_stats/<sid>/<day>/<k> counters: started, opened, p<n> (page n verified), done, left<n> (quit after n pages)
           /sl_ud/<sid>/<chat>/<day> keys one person received today (daily limit)
           /shortener_cfg            site url + the pool of blog pages
"""
import copy
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from config import Config
from core import db, utils
from core.errors import ApiError

log = logging.getLogger("shortener")

SID_LEN = 9                                   # "s" + 8 hex; every link code starts with it
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
KINDS = ("license", "redirect")
KEEP_DONE = 600                               # seconds a finished link stays in memory (repeat calls answer instantly)
MAX_SESSIONS = 150_000                        # hard cap on links held in memory
MAX_TRIES = 8                                 # license delivery attempts before the admin is told

_S = {}                                       # code -> session (authoritative while running)
_BY_USER = defaultdict(set)                   # (sid, chat_id) -> codes that are active
_dirty = set()
_dlock = threading.Lock()
_LOCKS = [threading.Lock() for _ in range(128)]
_counts = defaultdict(int)                    # (sid, day, key) -> pending increment
_clock = threading.Lock()
_SH = {}                                      # sid -> (valid_until, shortener or None)
_CFG = [0.0, None]
_cooldown = {}
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sl")
_started = threading.Event()


def _lock(code):
    return _LOCKS[hash(code) % len(_LOCKS)]


def _mark(code):
    with _dlock:
        _dirty.add(code)


# =============================================================== configuration + shortener definitions
def get_config():
    """{'site_url', 'pages'}: the root-domain address the links point at and the pool of blog pages."""
    if _CFG[1] is not None and _CFG[0] > time.time():
        return _CFG[1]
    d = db.get("/shortener_cfg") or {}
    pages = [p for p in (d.get("pages") or []) if isinstance(p, str)]
    cfg = {"site_url": (d.get("site_url") or Config.SITE_URL).rstrip("/"), "pages": pages}
    _CFG[0], _CFG[1] = time.time() + 10, cfg
    return cfg


def clean_pages(raw):
    """Paths on the root domain, one per line (or a list): '/blog/post/'  or a full address on the same site."""
    if isinstance(raw, str):
        raw = re.split(r"[\n,]+", raw)
    out = []
    for item in raw or []:
        s = str(item or "").strip()
        if not s:
            continue
        if s.startswith(("http://", "https://")):
            s = urlparse(s).path or "/"
        if not s.startswith("/"):
            s = "/" + s
        s = s.split("?", 1)[0].split("#", 1)[0]
        if not re.fullmatch(r"/[A-Za-z0-9._~\-/%]*", s) or len(s) > 200:
            raise ApiError(f"Invalid page path: {item}")
        if s not in out:
            out.append(s)
    if len(out) > 200:
        raise ApiError("At most 200 pages.")
    return out


def save_config(site_url, pages):
    site = utils.clean_url(site_url, "Website address", required=True).rstrip("/")
    p = urlparse(site)
    if p.path not in ("", "/") or p.query:
        raise ApiError("Website address must be only the domain, e.g. https://dekhohatke.com")
    db.set("/shortener_cfg", {"site_url": site, "pages": clean_pages(pages), "updated_at": utils.now()})
    _CFG[1] = None
    return get_config()


def shortener(sid):
    """The shortener or None (unknown / deleted). Cached 5 s so a panel edit reaches the gate within seconds."""
    hit = _SH.get(sid)
    if hit and hit[0] > time.time():
        return hit[1]
    d = db.get(f"/shorteners/{sid}") if re.fullmatch(r"s[0-9a-f]{8}", sid or "") else None
    if d and d.get("deleted"):
        d = None
    if d:
        d.setdefault("id", sid)
    if len(_SH) > 4000:
        _SH.clear()
    _SH[sid] = (time.time() + 5, d)
    return d


def forget(sid=None):
    _SH.pop(sid, None) if sid else _SH.clear()


def all_shorteners(include_deleted=False):
    rows = db.get_list("/shorteners")
    rows = [r for r in rows if include_deleted or not r.get("deleted")]
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows


def usable_for_plans():
    """Shorteners a Free plan may use: the ones that generate a license key and are switched on."""
    return [r for r in all_shorteners() if r.get("kind") == "license" and r.get("active", True)]


def plan_ready(plan):
    """A Free (short link) plan is shown / bought only while its shortener exists, is on, and generates license keys."""
    sh = shortener(str(plan.get("shorturl_id") or ""))
    return bool(sh and sh.get("kind") == "license" and sh.get("active", True))


def _num(d, key, lo, hi, default, label):
    raw = d.get(key)
    if raw in (None, ""):
        return default
    try:
        v = int(float(str(raw).strip()))
    except ValueError:
        raise ApiError(f"{label} must be a number.")
    if not lo <= v <= hi:
        raise ApiError(f"{label} must be between {lo} and {hi}.")
    return v


def clean_input(d, current=None):
    """Validate the create / edit form. Returns the fields to store."""
    cur = current or {}
    kind = str(d.get("kind") or cur.get("kind") or "license")
    if kind not in KINDS:
        raise ApiError("Choose what the shortener does: generate a license key or redirect.")
    if current and kind != cur.get("kind"):
        raise ApiError("The type (license key / redirect) cannot be changed after creation. Create a new shortener instead.")
    top_min = _num(d, "top_min", 0, 600, 15, "Top wait (min)")
    top_max = _num(d, "top_max", 0, 600, top_min, "Top wait (max)")
    bot_min = _num(d, "bottom_min", 0, 600, 10, "Bottom wait (min)")
    bot_max = _num(d, "bottom_max", 0, 600, bot_min, "Bottom wait (max)")
    if top_max < top_min or bot_max < bot_min:
        raise ApiError("The maximum wait cannot be smaller than the minimum.")
    out = {
        "name": utils.clean_text(d.get("name"), "Shortener name", 60),
        "kind": kind,
        "pages": _num(d, "pages", 1, 15, 3, "Total pages"),
        "top_min": top_min, "top_max": top_max, "bottom_min": bot_min, "bottom_max": bot_max,
        "ttl_min": _num(d, "ttl_min", 5, 1440, 45, "Link validity (minutes)"),
        "pool": clean_pages(d.get("pool") if "pool" in d else cur.get("pool")),
        "note": utils.clean_text(d.get("note"), "Note", 200, required=False),
        "active": bool(d.get("active", cur.get("active", True))),
    }
    if kind == "redirect":
        out["redirect_url"] = utils.clean_url(d.get("redirect_url"), "Redirect URL", required=True)
        out.update(notify_pages=False, max_active=0, cooldown=0, daily_limit=0)
    else:
        out["redirect_url"] = ""
        out["notify_pages"] = bool(d.get("notify_pages", cur.get("notify_pages", True)))
        out["max_active"] = _num(d, "max_active", 1, 20, 3, "Open links per user")
        out["cooldown"] = _num(d, "cooldown", 0, 3600, 5, "Seconds between links")
        out["daily_limit"] = _num(d, "daily_limit", 0, 500, 0, "Keys per user per day (0 = unlimited)")
    if not (out["pool"] or get_config()["pages"]):
        raise ApiError("Add the blog pages first (Shortener → Pages & website), or enter pages for this shortener.")
    return out


def create(d, actor):
    fields = clean_input(d)
    sid = "s" + secrets.token_hex(4)
    rec = {"id": sid, **fields, "created_at": utils.now(), "created_by": actor}
    if fields["kind"] == "redirect":
        rec["pub"] = sid + secrets.token_urlsafe(9)
    db.set(f"/shorteners/{sid}", rec)
    forget(sid)
    return rec


def update(sid, d):
    cur = db.get(f"/shorteners/{sid}")
    if not cur or cur.get("deleted"):
        raise ApiError("Shortener not found.", 404)
    fields = clean_input(d, cur)
    db.update(f"/shorteners/{sid}", {**fields, "updated_at": utils.now()})
    forget(sid)
    return {**cur, **fields}


def remove(sid, actor):
    cur = db.get(f"/shorteners/{sid}")
    if not cur or cur.get("deleted"):
        raise ApiError("Shortener not found.", 404)
    users = [p for p in db.get_list("/plans") if not p.get("deleted") and p.get("shorturl_id") == sid]
    if users:
        raise ApiError(f"{len(users)} plan(s) still use this shortener. Change or delete those plans first.")
    db.update(f"/shorteners/{sid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor})
    forget(sid)
    return cur


# =============================================================== link building
def pool_for(sh):
    return sh.get("pool") or get_config()["pages"] or ["/"]


def step_url(code, step):
    return f"{step['path']}?t={code}"


def full_url(code, step):
    return get_config()["site_url"] + step_url(code, step)


def public_link(sh):
    """The link an admin shares for a redirect shortener (every visitor gets their own run)."""
    if sh.get("kind") != "redirect" or not sh.get("pub"):
        return ""
    return get_config()["site_url"] + pool_for(sh)[0] + "?t=" + sh["pub"]


def build_steps(sh):
    pool = pool_for(sh)
    out, last = [], None
    for _ in range(int(sh["pages"])):
        choices = [p for p in pool if p != last] or pool
        last = secrets.choice(choices)
        out.append({"path": last, "top": _rand(sh["top_min"], sh["top_max"]), "bottom": _rand(sh["bottom_min"], sh["bottom_max"])})
    return out


def _rand(lo, hi):
    return lo if hi <= lo else lo + secrets.randbelow(int(hi - lo) + 1)


# =============================================================== counters (write-behind)
def _bump(sid, key, ts=None, n=1):
    with _clock:
        _counts[(sid, utils.day_key(ts), key)] += n


# =============================================================== sessions
def _new_session(sh, *, ip="", user=None, plan=None, tenant=""):
    if len(_S) >= MAX_SESSIONS:
        _sweep(force=True)
        if len(_S) >= MAX_SESSIONS:
            return None
    t = time.time()
    code = sh["id"] + secrets.token_urlsafe(16)
    s = {"code": code, "sid": sh["id"], "kind": sh["kind"], "steps": build_steps(sh), "cur": 0, "status": "active",
         "created": t, "expires": t + int(sh.get("ttl_min", 45)) * 60, "nonce": secrets.token_urlsafe(12), "spent": 0,
         "flags": 0, "last": t, "pt": [], "ip": ip, "tenant": tenant, "name": sh.get("name", "")}
    if user:
        s.update(chat_id=str(user["chat_id"]), full_name=user.get("full_name", ""), username=user.get("username", ""))
    if plan:
        s["plan_id"] = plan["id"]
    _S[code] = s
    if user:
        _BY_USER[(sh["id"], s["chat_id"])].add(code)
    _bump(sh["id"], "started")
    return s


def _alive(s):
    return s.get("status") == "active" and time.time() <= s["expires"]


def load(code):
    """The running session for a code, from memory or (after a restart) Firebase. None if the code is not ours."""
    if not code or not CODE_RE.match(code):
        return None
    s = _S.get(code)
    if s:
        return s
    sid = code[:SID_LEN]
    if not shortener(sid):
        return None
    d = db.get(f"/sl_links/{sid}/{code}")
    if not d or not d.get("steps"):
        return None
    d.setdefault("pt", [])
    d["steps"] = list(d["steps"]) if not isinstance(d["steps"], dict) else [d["steps"][k] for k in sorted(d["steps"], key=int)]
    s = _S.setdefault(code, d)
    if s.get("chat_id") and s.get("status") == "active":
        _BY_USER[(sid, s["chat_id"])].add(code)
    return s


def _state(s):
    """Everything the page needs, from the SERVER clock (survives reloads)."""
    i, t = s["cur"], time.time()
    st = s["steps"][i]
    if s.get("bottom_click"):
        phase, remaining = "bottom", max(0, st["bottom"] - (t - s["bottom_click"]))
    elif s.get("top_click"):
        phase, remaining = "scroll", 0
    else:
        phase, remaining = "top", max(0, st["top"] - (t - s["step_loaded"]))
    if phase == "bottom" and remaining <= 0:
        phase = "verify"
    return {"ok": True, "step": i + 1, "total": len(s["steps"]), "last": i == len(s["steps"]) - 1, "top": st["top"],
            "bottom": st["bottom"], "phase": phase, "remaining": round(remaining, 2), "n": s["nonce"],
            "turnstile": Config.TURNSTILE_SITEKEY or None, "bot": bot_url(s)}


_BAD = {"ok": False}
_RESYNC = {"ok": False, "err": "resync"}
_norm = lambda p: str(p or "").split("?", 1)[0].rstrip("/")  # noqa: E731


def _suspicious(s, sig):
    if sig.get("wd") or sig.get("trusted") is False:
        s["flags"] = int(s.get("flags", 0)) + 1
        _mark(s["code"])
        return True
    return False


def api_init(code, path, ip=""):
    sh = shortener(str(code or "")[:SID_LEN]) if code else None
    if sh and sh.get("kind") == "redirect" and sh.get("active", True) and hmac_eq(sh.get("pub", ""), code):
        if _rate(f"mk:{ip}", 40, 600):               # one visitor cannot flood the server with new runs
            return {"ok": False, "err": "slow_down"}
        s = _new_session(sh, ip=ip)
        return {"ok": True, "redirect": step_url(s["code"], s["steps"][0])} if s else _BAD
    s = load(code)
    if not s or not _alive(s):
        return _BAD
    with _lock(s["code"]):
        if not _alive(s):
            return _BAD
        cur = s["steps"][s["cur"]]
        if _norm(path) != _norm(cur["path"]):         # opened on the wrong page -> send to the current step page
            return {"ok": True, "redirect": step_url(s["code"], cur)}
        t = time.time()
        if not s.get("step_loaded"):
            s["step_loaded"] = t
        if not s.get("first_visit"):
            s["first_visit"] = t
            _bump(s["sid"], "opened", t)
        s["nonce"] = secrets.token_urlsafe(12)
        s["last"] = t
        out = _state(s)
    _mark(s["code"])
    return out


def api_event(code, ev, nonce, sig):
    s = load(code)
    if not s or not _alive(s) or not s.get("step_loaded"):
        return _BAD
    sig = sig if isinstance(sig, dict) else {}
    with _lock(s["code"]):
        if not _alive(s) or not s.get("step_loaded"):
            return _BAD
        if not hmac_eq(str(nonce or ""), s.get("nonce") or "x"):
            return _RESYNC
        st, t = s["steps"][s["cur"]], time.time()
        if _suspicious(s, sig):
            return _RESYNC
        if ev == "top_click":
            if s.get("top_click") or t - s["step_loaded"] < st["top"] - Config.GATE_GRACE:
                return _RESYNC
            s["top_click"] = t
        elif ev == "bottom_click":
            if (not s.get("top_click") or s.get("bottom_click") or not sig.get("bottom")
                    or t - s["top_click"] < Config.GATE_MIN_SCROLL_GAP):
                return _RESYNC
            s["bottom_click"] = t
        else:
            return _BAD
        s["nonce"] = secrets.token_urlsafe(12)
        s["last"] = t
        out = _state(s)
    _mark(s["code"])
    return out


def api_verify(code, nonce, sig, cf="", ip=""):
    """'Verify' on pages 1..N-1 and 'Generate' on the last one."""
    s = load(code)
    if not s or not _alive(s) or not s.get("step_loaded"):
        return _BAD
    sig = sig if isinstance(sig, dict) else {}
    if Config.TURNSTILE_SECRET and not turnstile_ok(cf, ip):   # network call: done before the session lock is taken
        return {"ok": False, "err": "captcha"}
    with _lock(s["code"]):
        if not _alive(s) or not s.get("step_loaded"):
            return _BAD
        if not hmac_eq(str(nonce or ""), s.get("nonce") or "x"):
            return _RESYNC
        if _suspicious(s, sig):
            return _RESYNC
        i, t = s["cur"], time.time()
        st, steps = s["steps"][i], s["steps"]
        if not s.get("top_click") or not s.get("bottom_click"):
            return _RESYNC
        if t - s["step_loaded"] < st["top"] + st["bottom"] - Config.GATE_GRACE:     # the whole step
            return _RESYNC
        if t - s["bottom_click"] < st["bottom"] - Config.GATE_GRACE:                 # the bottom wait
            return _RESYNC
        spent = float(s.get("spent", 0)) + (t - s["step_loaded"])
        last = i == len(steps) - 1
        page = i + 1
        if not last:
            s["pt"].append(t)
            s.update(cur=i + 1, spent=spent, last=t, nonce=secrets.token_urlsafe(12))
            for k in ("step_loaded", "top_click", "bottom_click"):
                s.pop(k, None)
            nxt = step_url(s["code"], steps[i + 1])
            out = {"ok": True, "done": False, "next": nxt}
            notify = s["kind"] == "license" and bool((shortener(s["sid"]) or {}).get("notify_pages", True))
        else:
            required = sum(x["top"] + x["bottom"] for x in steps)
            if t - s["first_visit"] < required - Config.GATE_GRACE or spent < required - Config.GATE_GRACE * len(steps):
                return _RESYNC
            s["pt"].append(t)
            s.update(status="done", done_at=t, spent=spent, last=t)
            _BY_USER[(s["sid"], s.get("chat_id", ""))].discard(s["code"])
            notify = False
            if s["kind"] == "license":
                s["txn_id"] = _txn_id()
                out = {"ok": True, "done": True, "bot": bot_url(s)}
            else:
                url = (shortener(s["sid"]) or {}).get("redirect_url", "")
                s["redirect"] = url
                # not "done": the page simply follows `next`/`redirect` to the target address
                out = {"ok": True, "done": False, "final": True, "next": url, "redirect": url}
    _bump(s["sid"], f"p{page}", t)
    if last:
        _bump(s["sid"], "done", t)
        if s["kind"] == "license":
            _pool.submit(_finish, s["code"])
    elif notify:
        _pool.submit(_notify_page, s["code"], page)
    _mark(s["code"])
    return out


def hmac_eq(a, b):
    import hmac
    return hmac.compare_digest(str(a).encode("utf-8", "ignore"), str(b).encode("utf-8", "ignore"))


def turnstile_ok(token, ip=""):
    if not Config.TURNSTILE_SECRET:
        return True
    try:
        r = requests.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                          data={"secret": Config.TURNSTILE_SECRET, "response": token or "", "remoteip": ip}, timeout=8).json()
        return bool(r.get("success"))
    except Exception:
        return False


# ---- tiny sliding-window limiter (per ip) -------------------------------------------------------------
_hits = {}
_hlock = threading.Lock()


def _rate(key, limit, per=60):
    """True when `key` already did `limit` things in the last `per` seconds."""
    t = time.time()
    with _hlock:
        dq = _hits.get(key)
        if dq is None:
            if len(_hits) > 40_000:
                for k in [k for k, v in _hits.items() if not v or v[-1] < t - 600]:
                    _hits.pop(k, None)
            dq = _hits[key] = deque()
        while dq and dq[0] < t - per:
            dq.popleft()
        if len(dq) >= limit:
            return True
        dq.append(t)
        return False


def rate_limited(ip):
    return _rate("gate:" + (ip or "?"), Config.GATE_IP_LIMIT, 60)


# =============================================================== bot side
_bot_names = {}
_bot_retry = {}


def _ctx(s):
    from bot.context import MAIN, get_ctx
    return get_ctx(s.get("tenant", "")) or MAIN


def bot_url(s=None):
    """https://t.me/<bot> for the page's final 'open the bot' button. Looked up once per bot, never blocks a request."""
    tenant = (s or {}).get("tenant", "")
    name = _bot_names.get(tenant)
    if not name and time.time() >= _bot_retry.get(tenant, 0):
        _bot_retry[tenant] = time.time() + 60        # at most one background lookup per minute per bot
        _pool.submit(_lookup_bot, tenant, s)
    return f"https://t.me/{name}" if name else None


def _lookup_bot(tenant, s):
    try:
        from bot.handlers import _bot_username
        name = _bot_username(_ctx(s or {"tenant": tenant}))
        if name:
            _bot_names[tenant] = name
    except Exception:
        log.exception("bot name lookup failed")


def _send(ctx, chat_id, text, markup=None):
    """Telegram send with a few retries. A blocked / unknown chat is not retried."""
    from bot.tg import TelegramError
    for attempt in range(4):
        try:
            return ctx.tg.send_message(chat_id, text, markup)
        except TelegramError as e:
            if e.blocked or e.code in (400, 403):
                log.info("shortener notice to %s not delivered: %s", chat_id, e)
                return None
            time.sleep(min(float(e.retry_after or 0), 5) or 1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def _notify_page(code, page):
    """'Page 1 completed -- 2 pages remaining' with a button to the next page."""
    s = _S.get(code)
    if not s or not s.get("chat_id"):
        return
    total = len(s["steps"])
    left = total - page
    ctx = _ctx(s)
    nxt = full_url(code, s["steps"][page]) if page < total else ""
    from bot import messages as m
    text = (f"✅ <b>Page {page} completed</b>\n{m.LINE}\n\n📄 <b>{left}</b> page{'s' if left != 1 else ''} remaining "
            f"(page {page + 1} of {total} is next).\n\nTap below to continue 👇")
    r = _send(ctx, s["chat_id"], text, m.kb([("➡️ Continue to page " + str(page + 1), "url:" + nxt)]) if nxt else None)
    if r:
        old = s.get("pm")
        s["pm"] = r["message_id"]
        _mark(code)
        if old:
            ctx.tg.delete_message(s["chat_id"], old)   # keep the chat tidy: only the newest progress notice stays


def _txn_id():
    from services import payments
    return payments._new_id()


def _finish(code):
    """Give the user the license key (idempotent; retried by the sweeper until it works)."""
    s = _S.get(code) or load(code)
    if not s or s.get("delivered"):
        return
    with _lock(code):
        if s.get("delivered") or s.get("busy"):
            return
        s["busy"] = True
        s["tries"] = int(s.get("tries", 0)) + 1
    ok = False
    try:
        from bot import messages as m
        from services import catalog, payments, users
        ctx = _ctx(s)
        plan = catalog.get_plan(s.get("plan_id", ""))
        user = users.get_user(s["chat_id"], s.get("tenant", "")) or {
            "chat_id": s["chat_id"], "full_name": s.get("full_name", ""), "username": s.get("username", "")}
        user.setdefault("chat_id", s["chat_id"])
        if s.get("pm"):
            ctx.tg.delete_message(s["chat_id"], s["pm"])
            s.pop("pm", None)
        if not plan:
            _send(ctx, s["chat_id"], "⚠️ All steps are verified, but this plan was removed. Please contact support.")
            s["error"] = "plan_removed"
            ok = True
        else:
            txn = payments.issue_short_license(ctx, user, plan, s["txn_id"], s.get("name", ""))
            s["lic_key"] = (txn or {}).get("license_key", "")
            ok = bool(s["lic_key"])
            if ok:
                db.transaction(f"/sl_ud/{s['sid']}/{s['chat_id']}/{utils.day_key()}", lambda c: int(c or 0) + 1)
        if ok:
            s["delivered"] = True
        elif s["tries"] >= MAX_TRIES:
            _alert_admin(s, m)
    except Exception:
        log.exception("license delivery failed for %s", code)
        if s["tries"] >= MAX_TRIES:
            try:
                from bot import messages as m
                _alert_admin(s, m)
            except Exception:
                pass
    finally:
        s["busy"] = False
        s["next_try"] = time.time() + 20 * s["tries"]
        _mark(code)


def _alert_admin(s, m):
    if s.get("alerted"):
        return
    s["alerted"] = True
    from services import notify
    notify.to_admins(f"⚠️ <b>Shortener key not delivered</b>\n\nUser <code>{m.esc(s.get('chat_id'))}</code> finished every step of "
                     f"<b>{m.esc(s.get('name'))}</b> but the license could not be created after {MAX_TRIES} tries.\n"
                     f"Link: <code>{m.esc(s['code'][-8:])}</code>")


def start_for_user(ctx, user, plan):
    """Called by the bot when a user confirms a Free (short link) plan.
    Returns (status, session, shortener):  ok | existing | cooldown | daily_limit | unavailable"""
    sh = shortener(str(plan.get("shorturl_id") or ""))
    if not sh or sh.get("kind") != "license" or not sh.get("active", True):
        return "unavailable", None, sh
    sid, chat = sh["id"], str(user["chat_id"])
    # make sure links that survived a restart are counted
    mine = []
    for c in list(_BY_USER.get((sid, chat), ())):
        s = _S.get(c)
        if s and _alive(s) and s.get("plan_id") == plan["id"] and s.get("tenant", "") == ctx.tenant:
            mine.append(s)
    if len(mine) >= int(sh.get("max_active", 3)):
        return "existing", max(mine, key=lambda x: x["created"]), sh
    limit = int(sh.get("daily_limit", 0))
    if limit and int(db.get(f"/sl_ud/{sid}/{chat}/{utils.day_key()}") or 0) >= limit:
        return "daily_limit", None, sh
    key = (sid, chat)
    if time.time() - _cooldown.get(key, 0) < int(sh.get("cooldown", 5)):
        return "cooldown", None, sh
    s = _new_session(sh, user=user, plan=plan, tenant=ctx.tenant)
    if not s:
        return "unavailable", None, sh
    _cooldown[key] = time.time()
    if len(_cooldown) > 20000:
        _cooldown.clear()
    db.set(f"/sl_links/{sid}/{s['code']}", s)       # saved right away: a redeploy must not lose a link the user just got
    return "ok", s, sh


# =============================================================== background: flush + sweep
def flush():
    """Write pending counters and changed sessions to Firebase."""
    with _clock:
        counts = dict(_counts)
        _counts.clear()
    with _dlock:
        codes = list(_dirty)
        _dirty.clear()
    jobs = []
    for (sid, day, key), n in counts.items():
        jobs.append(("c", sid, day, key, n))
    for code in codes:
        jobs.append(("s", code))

    def run(job):
        try:
            if job[0] == "c":
                _, sid, day, key, n = job
                db.transaction(f"/sl_stats/{sid}/{day}/{key}", lambda c: int(c or 0) + n)
            else:
                s = _S.get(job[1])
                if s:
                    with _lock(job[1]):
                        snap = copy.deepcopy(s)
                    snap.pop("busy", None)
                    db.set(f"/sl_links/{snap['sid']}/{snap['code']}", snap)
            return None
        except Exception:
            log.exception("shortener flush failed")
            return job

    failed = [j for j in db.pmap(run, jobs) if j]
    for j in failed:                                  # keep them for the next round
        if j[0] == "c":
            with _clock:
                _counts[(j[1], j[2], j[3])] += j[4]
        else:
            _mark(j[1])
    return len(jobs) - len(failed)


def _sweep(force=False):
    t = time.time()
    for code, s in list(_S.items()):
        try:
            if s.get("status") == "active" and t > s["expires"]:
                with _lock(code):
                    if s.get("status") != "active":
                        continue
                    s["status"] = "expired"
                    s["left_at"] = t
                    _BY_USER[(s["sid"], s.get("chat_id", ""))].discard(code)
                _bump(s["sid"], f"left{int(s['cur'])}", s.get("last", t))
                if s.get("pm") and s.get("chat_id"):
                    _pool.submit(_ctx(s).tg.delete_message, s["chat_id"], s["pm"])
                _mark(code)
            elif (s.get("status") == "done" and s["kind"] == "license" and not s.get("delivered") and not s.get("busy")
                  and int(s.get("tries", 0)) < MAX_TRIES and t >= s.get("next_try", 0)):
                _pool.submit(_finish, code)
            elif s.get("status") in ("done", "expired") and (force or t - (s.get("done_at") or s.get("left_at") or t) > KEEP_DONE):
                if code not in _dirty and (s["kind"] != "license" or s.get("delivered") or s.get("status") == "expired"
                                           or int(s.get("tries", 0)) >= MAX_TRIES):
                    _S.pop(code, None)
        except Exception:
            log.exception("shortener sweep failed for %s", code)


def _recover():
    """After a restart: load the links that were still running so they keep working and get counted when they lapse."""
    for sh in all_shorteners():
        try:
            for d in db.query_equal(f"/sl_links/{sh['id']}", "status", "active"):
                d.setdefault("pt", [])
                if d.get("code") and d["code"] not in _S and d.get("steps"):
                    _S[d["code"]] = d
                    if d.get("chat_id"):
                        _BY_USER[(sh["id"], d["chat_id"])].add(d["code"])
            for d in db.query_equal(f"/sl_links/{sh['id']}", "status", "done"):   # finished but key not yet delivered
                if d.get("kind") == "license" and not d.get("delivered") and d.get("code") and d["code"] not in _S:
                    d.setdefault("pt", [])
                    _S[d["code"]] = d
        except Exception:
            log.exception("shortener recovery failed for %s", sh.get("id"))


def start():
    """Start the flusher / sweeper (once per process)."""
    if _started.is_set():
        return
    _started.set()

    def loop():
        time.sleep(4)
        try:
            _recover()
        except Exception:
            log.exception("shortener recover")
        n = 0
        while True:
            time.sleep(2)
            n += 1
            try:
                flush()
                if n % 8 == 0:
                    _sweep()
            except Exception:
                log.exception("shortener loop")

    threading.Thread(target=loop, daemon=True, name="shortener").start()


# =============================================================== statistics
def _merged_stats(sid):
    """{day: {key: n}} from Firebase plus what has not been flushed yet."""
    data = copy.deepcopy(db.get(f"/sl_stats/{sid}") or {})
    with _clock:
        for (s, day, key), n in _counts.items():
            if s == sid:
                data.setdefault(day, {})[key] = int(data.get(day, {}).get(key, 0)) + n
    return data


def _total(data, days=None):
    out = defaultdict(int)
    for day, row in data.items():
        if days is not None and day not in days:
            continue
        for k, v in row.items():
            out[k] += int(v or 0)
    return out


def _shape(tot, pages):
    """Funnel numbers from raw counters."""
    done = tot.get("done", 0)
    funnel = []
    for n in range(1, pages + 1):
        funnel.append({"page": n, "completed": tot.get(f"p{n}", 0), "left_after": tot.get(f"left{n}", 0) if n < pages else 0})
    return {"started": tot.get("started", 0), "opened": tot.get("opened", 0), "done": done,
            "left_total": sum(tot.get(f"left{n}", 0) for n in range(0, pages)),
            "left_before_page1": tot.get("left0", 0), "funnel": funnel,
            "rate": round(done * 100 / tot["started"], 1) if tot.get("started") else 0}


def stats_for(sh, lo, hi):
    data = _merged_stats(sh["id"])
    days = None if lo <= 0 else set(utils.days_in_range(lo, hi))
    out = _shape(_total(data, days), int(sh["pages"]))
    out["active_now"], out["active_by_page"] = live_progress(sh["id"])
    return out


def quick_counts(sh):
    """Successful completions for today / yesterday / all time (the numbers on every shortener card)."""
    data = _merged_stats(sh["id"])
    t0, t1 = utils.resolve_range("today"), utils.resolve_range("yesterday")
    out = {}
    for name, (a, b) in (("today", t0), ("yesterday", t1)):
        out[name] = _total(data, set(utils.days_in_range(a, b))).get("done", 0)
    out["all"] = _total(data).get("done", 0)
    out["started_all"] = _total(data).get("started", 0)
    out["active_now"], _ = live_progress(sh["id"])
    return out


def live_progress(sid):
    by = defaultdict(int)
    n = 0
    for s in list(_S.values()):
        if s["sid"] == sid and _alive(s):
            n += 1
            by[int(s["cur"])] += 1
    return n, dict(by)


def _public(s):
    steps = s.get("steps") or []
    pt = s.get("pt") or []
    st = s.get("status", "active")
    if st == "active" and time.time() > s.get("expires", 0):
        st = "expired"
    cur = int(s.get("cur", 0))
    return {"id": s["code"][-8:], "kind": s.get("kind", ""), "status": st, "page": len(steps) if st == "done" else cur + 1,
            "completed_pages": len(pt), "total": len(steps),
            "user": {"chat_id": s.get("chat_id", ""), "full_name": s.get("full_name", ""), "username": s.get("username", "")},
            "created": s.get("created", 0), "opened": s.get("first_visit", 0), "last": s.get("last", 0),
            "done_at": s.get("done_at", 0), "spent": round(float(s.get("spent", 0)), 1), "flags": int(s.get("flags", 0)),
            "license_key": s.get("lic_key", ""), "delivered": bool(s.get("delivered")), "tries": int(s.get("tries", 0)),
            "error": s.get("error", ""), "redirect": s.get("redirect", ""),
            "pages": [{"path": x.get("path", ""), "top": x.get("top", 0), "bottom": x.get("bottom", 0),
                       "done_at": pt[i] if i < len(pt) else 0} for i, x in enumerate(steps)]}


def sessions(sid, status="", page=1, q=""):
    """Recent links of one shortener (newest 400), memory first because it is fresher than Firebase."""
    rows = {d["code"]: d for d in db.query_last(f"/sl_links/{sid}", "created", 400) if d.get("code")}
    for s in list(_S.values()):
        if s["sid"] == sid:
            rows[s["code"]] = s
    items = [_public(s) for s in rows.values() if s.get("steps")]
    if status in ("active", "done", "expired"):
        items = [x for x in items if x["status"] == status]
    elif status == "left":
        items = [x for x in items if x["status"] == "expired"]
    q = (q or "").strip().lower()
    if q:
        items = [x for x in items if q in x["user"]["chat_id"].lower() or q in x["user"]["full_name"].lower()
                 or q in x["user"]["username"].lower() or q in x["license_key"].lower() or q in x["id"].lower()]
    items.sort(key=lambda x: x["created"], reverse=True)
    return utils.paginate(items, page)


def reset_for_tests():
    _S.clear(); _BY_USER.clear(); _dirty.clear(); _counts.clear(); _SH.clear(); _hits.clear(); _cooldown.clear(); _CFG[1] = None
    _bot_names.clear(); _bot_retry.clear()
