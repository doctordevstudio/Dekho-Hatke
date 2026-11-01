"""Daily check-in + spin wheel rewards.

A claim creates a time-limited *bonus* credit (expires after N minutes, user is warned M minutes before).
Each user can claim each reward once per local day; it resets at 12:00 AM (TIMEZONE).
Bonus credit can only be spent on license purchases (see wallet.reserve).
"""
import logging
import random
from datetime import timedelta

from core import db, utils
from core.errors import ApiError

log = logging.getLogger("rewards")
KINDS = ("checkin", "spin")
LABEL = {"checkin": "Daily Check-in", "spin": "Spin Wheel"}
DEFAULTS = {"enabled": True, "min": 1, "max": 10, "expire_minutes": 60, "notify_before": 10, "decimals": False}


def settings(kind):
    s = db.get(f"/settings/rewards/{kind}") or {}
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS}}
    out["enabled"] = bool(out["enabled"])
    out["decimals"] = bool(out["decimals"])
    return out


def save_settings(kind, d):
    mn = utils.parse_amount(d.get("min", 0), allow_zero=True)
    mx = utils.parse_amount(d.get("max"))
    if mx < mn:
        raise ApiError("Maximum must be at least the minimum.")
    try:
        exp = int(d.get("expire_minutes"))
        before = int(d.get("notify_before", 0))
    except (TypeError, ValueError):
        raise ApiError("Minutes must be whole numbers.")
    if exp < 1 or exp > 10080:
        raise ApiError("Expiry must be between 1 and 10080 minutes.")
    if before < 0 or before >= exp:
        raise ApiError("The reminder must be earlier than the expiry (0 = no reminder).")
    db.update(f"/settings/rewards/{kind}", {"enabled": bool(d.get("enabled")), "decimals": bool(d.get("decimals")), "min": mn, "max": mx,
                                            "expire_minutes": exp, "notify_before": before})


def roll(st):
    """Random reward between min and max. With 'decimals' on (or a decimal min / max) it has up to 2 decimal places."""
    lo, hi = float(st["min"]), float(st["max"])
    if not st.get("decimals") and lo == int(lo) and hi == int(hi):
        return random.randint(int(lo), int(hi))
    v = round(random.uniform(lo, hi), 2)
    return int(v) if v == int(v) else v


def seconds_to_reset():
    n = utils.local_dt()
    tomorrow = (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - n).total_seconds())


def fmt_wait(sec):
    h, m = divmod(max(0, sec) // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def claimed_today(uid, kind):
    return db.get(f"/reward_claims/{kind}/{utils.day_key()}/{uid}") is not None


def claim(uid, kind, user):
    """Returns ('ok', reward) | ('claimed', None) | ('disabled', None). Safe against double taps."""
    st = settings(kind)
    if not st["enabled"]:
        return "disabled", None
    rid = utils.new_id("rw")
    if not db.set_if_absent(f"/reward_claims/{kind}/{utils.day_key()}/{uid}", rid):
        return "claimed", None
    amount = roll(st)
    now = utils.now()
    exp = now + st["expire_minutes"] * 60
    reward = {"id": rid, "kind": kind, "user_id": str(uid), "amount": amount, "remaining": amount,
              "created_at": now, "expires_at": exp, "day": utils.day_key(),
              "notify_at": exp - st["notify_before"] * 60 if st["notify_before"] else 0, "notified": False,
              "expire_minutes": st["expire_minutes"],
              "user": {"chat_id": str(uid), "full_name": user.get("full_name", ""),
                       "username": user.get("username", ""), "profile_image_url": user.get("profile_image_url", "")}}
    db.set(f"/rewards/{rid}", reward)
    return "ok", reward


# ------------------------------------------------------------------ spending
def _mine(uid):
    return db.query_equal("/rewards", "user_id", str(uid))


def active_rewards(uid):
    now = utils.now()
    rows = [r for r in _mine(uid) if r.get("expires_at", 0) > now and float(r.get("remaining", 0)) > 0]
    rows.sort(key=lambda r: r["expires_at"])
    return rows


def active_bonus(uid):
    return round(sum(float(r["remaining"]) for r in active_rewards(uid)), 2)


def next_expiry(uid):
    rows = active_rewards(uid)
    return rows[0]["expires_at"] if rows else 0


def spend(uid, need, ref):
    """Take up to `need` from active rewards, soonest-to-expire first. Returns [{'id','amount','use'}]."""
    taken, left = [], round(float(need), 2)
    for r in active_rewards(uid):
        if left <= 0:
            break
        got = {"v": 0.0}

        def fn(cur, left=left, got=got):
            cur = float(cur or 0)
            got["v"] = round(min(cur, left), 2)
            return round(cur - got["v"], 2)

        db.transaction(f"/rewards/{r['id']}/remaining", fn)
        if got["v"] > 0:
            use = utils.new_id("use")
            db.set(f"/rewards/{r['id']}/uses/{use}", {"ts": utils.now(), "amount": got["v"], "txn_id": ref,
                                                      "status": "held"})
            taken.append({"id": r["id"], "amount": got["v"], "use": use})
            left = round(left - got["v"], 2)
    return taken


def confirm_use(rid, use):
    if use:
        db.update(f"/rewards/{rid}/uses/{use}", {"status": "used"})


def restore(rid, amount, use=""):
    db.transaction(f"/rewards/{rid}/remaining", lambda cur: round(float(cur or 0) + float(amount), 2))
    r = db.get(f"/rewards/{rid}") or {}
    upd = {}
    if r.get("expires_at", 0) < utils.now() + 300:
        upd["expires_at"] = utils.now() + 300  # grace so a failed payment never silently burns the reward
        upd["notified"] = True
    if upd:
        db.update(f"/rewards/{rid}", upd)
    if use:
        db.update(f"/rewards/{rid}/uses/{use}", {"status": "refunded"})


# ------------------------------------------------------------------ expiry reminders
def sweep(notify):
    """Warn users shortly before their bonus expires. `notify(reward)` sends the message."""
    now = utils.now()
    sent = 0
    for r in db.query_range("/rewards", "notify_at", now - 172800, now):
        if r.get("notified") or not r.get("notify_at"):
            continue
        if r.get("expires_at", 0) <= now or float(r.get("remaining", 0)) <= 0:
            db.update(f"/rewards/{r['id']}", {"notified": True})  # nothing left to warn about
            continue
        db.update(f"/rewards/{r['id']}", {"notified": True})
        try:
            notify(r)
            sent += 1
        except Exception:
            log.exception("reward reminder failed")
    return sent


# ------------------------------------------------------------------ admin statistics
def status_of(r, now=None):
    now = now or utils.now()
    rem = float(r.get("remaining", 0))
    if r.get("expires_at", 0) <= now:
        return "expired" if rem > 0 else "used"
    return "valid" if rem > 0 else "used"


def stats(kind, lo, hi):
    now = utils.now()
    rows = [r for r in db.query_range("/rewards", "created_at", lo, hi) if r.get("kind") == kind]
    claimed = round(sum(float(r["amount"]) for r in rows), 2)
    valid = round(sum(float(r["remaining"]) for r in rows if status_of(r, now) == "valid"), 2)
    expired = round(sum(float(r["remaining"]) for r in rows if status_of(r, now) == "expired"), 2)
    used = round(sum(float(r["amount"]) - float(r["remaining"]) for r in rows), 2)
    users = {r["user_id"] for r in rows}
    used_users = {r["user_id"] for r in rows if float(r["amount"]) - float(r["remaining"]) > 0}
    return {"claims": len(rows), "claimed_users": len(users), "claimed_amount": claimed, "valid_amount": valid,
            "expired_amount": expired, "used_amount": used, "used_users": len(used_users), "rows": rows}
