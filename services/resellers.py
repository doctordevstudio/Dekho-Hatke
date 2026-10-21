"""Resellers: own Telegram bot, own UPI, own prices, own customers. The shop owner (admin) earns a commission on
every sale and can force a reseller into maintenance until the dues are settled."""
import re

from bot.tg import TelegramError, client_for
from config import Config
from core import db, security, utils
from core.cache import cache, cached
from core.errors import ApiError
from services import media, stats

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")
DEFAULT_COMMISSION = 10.0
DEFAULT_REF_PERCENT = 5.0


def get(rid):
    r = db.get(f"/resellers/{rid}") if rid else None
    return r if r and not r.get("deleted") else None


def get_or_404(rid):
    r = get(rid)
    if not r:
        raise ApiError("Reseller not found.", 404)
    return r


def all_resellers():
    rows = [r for r in db.get_list("/resellers") if not r.get("deleted")]
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows


def mask_token(t):
    return (t.split(":")[0] + ":••••••••") if t else ""


def public(r, with_due=False):
    out = {"id": r["id"], "name": r.get("name", ""), "username": r.get("username", ""),
           "chat_id": r.get("chat_id", ""), "avatar_url": f"/media/img/{r['avatar_media_id']}" if r.get("avatar_media_id") else "",
           "bot_name": r.get("bot_name", ""), "bot_username": r.get("bot_username", ""),
           "bot_token": mask_token(r.get("bot_token", "")), "note": r.get("note", ""),
           "login_username": r.get("login_username", ""), "active": r.get("active", True),
           "commission_pct": float(r.get("commission_pct", DEFAULT_COMMISSION)),
           "due_limit": float(r.get("due_limit", 0) or 0),
           "referral_percent": float(r.get("referral_percent", DEFAULT_REF_PERCENT)),
           "support_username": r.get("support_username", ""), "min_withdraw": float(r.get("min_withdraw", 0) or 0),
           "created_at": r.get("created_at", 0)}
    if with_due:
        out["due"] = due_info(r)
    return out


# ------------------------------------------------------------------ money
def entries(rid):
    return stats.ledger(rid)


def period_entries(r):
    if r.get("period_start"):  # after a settlement: only sales strictly after it count
        start = int(r["period_start"])
        return [e for e in entries(r["id"]) if e.get("ts", 0) > start]
    start = int(r.get("created_at", 0))
    return [e for e in entries(r["id"]) if e.get("ts", 0) >= start]


def due_info(r):
    rows = period_entries(r)
    earned = round(sum(e.get("amount", 0) for e in rows), 2)
    commission = round(sum(e.get("commission", 0) for e in rows), 2)
    limit = float(r.get("due_limit", 0) or 0)
    return {"earned": earned, "commission": commission, "limit": limit, "sales": len(rows),
            "period_start": int(r.get("period_start") or r.get("created_at", 0)),
            "forced": bool(limit and earned >= limit), "commission_pct": float(r.get("commission_pct", DEFAULT_COMMISSION))}


def is_forced(rid):
    """True when this reseller's store is locked because the dues must be cleared first."""
    hit = cache.get(("forced", rid))
    if hit is not None:
        return hit[0]
    r = get(rid)
    val = bool(r and due_info(r)["forced"])
    cache.set(("forced", rid), (val,), 15)
    return val


def settle(rid, actor, note=""):
    r = get_or_404(rid)
    info = due_info(r)
    sid = utils.ts_id()
    db.update("/", {f"reseller_settlements/{rid}/{sid}": {"id": sid, "ts": utils.now(), "earned": info["earned"],
                                                           "commission": info["commission"], "sales": info["sales"],
                                                           "by": actor, "note": note[:200]},
                    f"resellers/{rid}/period_start": utils.now()})
    cache.clear()
    return info


def settlements(rid):
    rows = db.get_list(f"/reseller_settlements/{rid}")
    rows.sort(key=lambda x: x["ts"], reverse=True)
    return rows


def totals(lo, hi):
    """Admin overview: resellers, their earnings and the admin's commission in a period."""
    rows = [e for e in db.get_list("/earnings") if e.get("reseller_id") and lo <= e.get("ts", 0) <= hi]
    return {"sales": len(rows), "earned": round(sum(e["amount"] for e in rows), 2),
            "commission": round(sum(e.get("commission", 0) for e in rows), 2),
            "resellers": len({e["reseller_id"] for e in rows})}


# ------------------------------------------------------------------ create / update
def _unique_login(login, keep_rid=None):
    lower = login.lower()
    owner = db.get(f"/reseller_usernames/{lower}")
    if owner and owner != keep_rid:
        raise ApiError("That login username is already taken.")
    return lower


def _check_token(token, keep_rid=None):
    if not TOKEN_RE.match(token):
        raise ApiError("That does not look like a Telegram bot token.")
    if token == Config.BOT_TOKEN:
        raise ApiError("That is the main bot's token. Create a separate bot for the reseller in @BotFather.")
    for r in all_resellers():
        if r.get("bot_token") == token and r["id"] != keep_rid:
            raise ApiError("Another reseller already uses this bot token.")
    try:
        me = client_for(token).get_me(refresh=True)
    except TelegramError as e:
        raise ApiError(f"Telegram rejected the bot token: {e}")
    return me


def save(d, rid=None, avatar_bytes=None, actor="", admin=True):
    """Create (rid=None) or update a reseller. `admin=False` limits the fields a reseller may change themselves."""
    cur = get_or_404(rid) if rid else {}
    upd = {}

    def has(k):
        return k in d and (d[k] is not None)

    if admin:
        if has("name") or not rid:
            upd["name"] = utils.clean_text(d.get("name"), "Name", 60)
        if has("username") or not rid:
            upd["username"] = utils.clean_username(d.get("username"))
        if has("chat_id") or not rid:
            upd["chat_id"] = utils.clean_chat_id(d.get("chat_id"), "Chat ID")
        if has("note") or not rid:
            upd["note"] = utils.clean_text(d.get("note"), "Note", 400, False)
        if has("commission_pct") or not rid:
            try:
                c = round(float(d.get("commission_pct", DEFAULT_COMMISSION)), 2)
            except (TypeError, ValueError):
                raise ApiError("Enter a valid commission percentage.")
            if not 0 <= c <= 100:
                raise ApiError("Commission must be between 0 and 100.")
            upd["commission_pct"] = c
        if has("due_limit") or not rid:
            upd["due_limit"] = utils.parse_amount(d.get("due_limit") or 0, allow_zero=True)
        if has("active"):
            upd["active"] = bool(d.get("active"))
        if has("due_message"):
            upd["due_message"] = utils.clean_text(d.get("due_message"), "Maintenance message", 1000, False)
    if has("bot_name") or not rid:
        upd["bot_name"] = utils.clean_text(d.get("bot_name"), "Bot name", 60, False) or upd.get("name", cur.get("name", ""))
    if has("support_username"):
        upd["support_username"] = utils.clean_username(d.get("support_username"))
    if has("referral_percent"):
        try:
            p = round(float(d.get("referral_percent")), 2)
        except (TypeError, ValueError):
            raise ApiError("Enter a valid referral percentage.")
        if not 0 <= p <= 90:
            raise ApiError("Referral percentage must be between 0 and 90.")
        upd["referral_percent"] = p
    if has("min_withdraw"):
        upd["min_withdraw"] = utils.parse_amount(d.get("min_withdraw") or 0, allow_zero=True)
    token = str(d.get("bot_token") or "").strip()
    if token and "•" not in token and token != cur.get("bot_token"):
        me = _check_token(token, rid)
        upd["bot_token"] = token
        upd["bot_username"] = me.get("username", "")
        if not upd.get("bot_name") and not cur.get("bot_name"):
            upd["bot_name"] = me.get("first_name", "")
    elif not rid:
        raise ApiError("Bot token is required.")
    login = str(d.get("login_username") or "").strip()
    pw = str(d.get("password") or "")
    new_login_lower = None
    if admin or login:
        if (login or not rid) and (admin):
            if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", login):
                raise ApiError("Login username: 3-32 letters, numbers, . _ -")
            new_login_lower = _unique_login(login, rid)
            upd["login_username"] = login
            upd["login_lower"] = new_login_lower
    if pw or not rid:
        problem = security.password_problem(pw)
        if problem:
            raise ApiError(problem)
        upd["password_hash"] = security.hash_password(pw)
        upd["session_version"] = int(cur.get("session_version", 1)) + 1
    if avatar_bytes:
        upd["avatar_media_id"] = media.save_image(avatar_bytes, "reseller_avatar", actor)

    if not rid:
        rid = utils.new_id("rsl")
        upd.update(id=rid, created_at=utils.now(), created_by=actor, active=upd.get("active", True),
                   session_version=upd.get("session_version", 1), period_start=utils.now())
        db.set(f"/resellers/{rid}", upd)
    else:
        db.update(f"/resellers/{rid}", upd)
    if new_login_lower:
        old = cur.get("login_lower")
        if old and old != new_login_lower:
            db.set(f"/reseller_usernames/{old}", None)
        db.set(f"/reseller_usernames/{new_login_lower}", rid)
    cache.clear()
    r = get(rid)
    if "bot_token" in upd:
        try:
            register_webhook(r)
        except TelegramError:
            pass
    return r


def register_webhook(r):
    if not (Config.BASE_URL and r.get("bot_token")):
        return False
    client_for(r["bot_token"]).set_webhook(f"{Config.BASE_URL}/telegram/{Config.path_secret(r['id'])}/{r['id']}",
                                           Config.header_secret(r["id"]))
    return True


def soft_delete(rid, actor):
    r = get_or_404(rid)
    db.update(f"/resellers/{rid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor, "active": False,
                                    "session_version": int(r.get("session_version", 1)) + 1})
    if r.get("login_lower"):
        db.set(f"/reseller_usernames/{r['login_lower']}", None)
    try:
        if r.get("bot_token"):
            client_for(r["bot_token"]).call("deleteWebhook", {})
    except TelegramError:
        pass
    cache.clear()


# ------------------------------------------------------------------ prices (resellers may only raise them)
def prices(rid):
    return db.get(f"/reseller_prices/{rid}") or {}


def set_price(rid, plan, price=None, enabled=True):
    base = plan["amount"]
    cur = prices(rid).get(plan["id"]) or {}
    upd = {"enabled": bool(enabled)}
    if price is not None and price != "":
        p = utils.parse_amount(price)
        if p < base:
            raise ApiError(f"You can only increase the price — the minimum is {utils.inr(base)}.")
        upd["price"] = p
    elif "price" not in cur:
        upd["price"] = base
    db.update(f"/reseller_prices/{rid}/{plan['id']}", upd)
    cache.clear()


@cached(30)
def counts():
    cats = {c["id"] for c in db.get_list("/categories") if not c.get("deleted")}
    apps = [a for a in db.get_list("/apps") if not a.get("deleted") and a.get("status", "live") == "live"]
    plans = [p for p in db.get_list("/plans") if not p.get("deleted") and p.get("kind") != "trial"]
    return {"categories": len(cats), "apps": len(apps), "plans": len(plans)}
