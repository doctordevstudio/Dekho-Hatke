"""Refer & earn for the main shop and for every reseller shop.

* A referral starts as *pending* when the friend joins (after the force-join check) and becomes *converted* when
  they buy something. The referrer then earns a % commission on every purchase, forever.
* Commission % : the store default (main: Settings -> Referral, reseller: their own setting) unless the admin /
  reseller set a custom % for that referrer.
* Commission lands in the referrer's *Refer Money* wallet bucket (withdrawable, or spendable on licenses).
"""
import logging

from bot import messages as m
from bot.tg import TelegramError
from core import db, utils
from core.errors import ApiError
from services import media, notify, resellers, users, wallet

log = logging.getLogger("referral")


# ------------------------------------------------------------------ settings
def enabled(tenant=""):
    """Refer & Earn on/off: the admin's global switch, and (for a reseller shop) the reseller's own switch."""
    from services import settings
    if not settings.feature("referral"):
        return False
    return bool((resellers.get(tenant) or {}).get("referral_enabled", True)) if tenant else True


def is_registered(suid):
    """The user confirmed Refer & Earn (or already had referrals before this step existed)."""
    return db.get(f"/refer_reg/{suid}") is not None or db.get(f"/referral_stats/{suid}") is not None


def register_referrer(suid):
    return db.set_if_absent(f"/refer_reg/{suid}", {"ts": utils.now()})


def default_percent(tenant=""):
    if tenant:
        r = resellers.get(tenant) or {}
        return float(r.get("referral_percent", resellers.DEFAULT_REF_PERCENT))
    try:
        return float((db.get("/settings/referral") or {}).get("percent", 10))
    except (TypeError, ValueError):
        return 10.0


def percent():  # main shop default (kept for the panel settings page)
    return default_percent("")


def parse_pct(v):
    try:
        p = round(float(v), 2)
    except (TypeError, ValueError):
        raise ApiError("Enter a valid percentage.")
    if not 0 < p <= 90:
        raise ApiError("Percentage must be between 0 and 90.")
    return p


def save_percent(v, tenant=""):
    p = parse_pct(v)
    if tenant:
        db.update(f"/resellers/{tenant}", {"referral_percent": p})
    else:
        db.set("/settings/referral/percent", p)


def custom_percent(suid):
    v = db.get(f"/referral_custom/{suid}")
    return float(v) if v is not None else None


def percent_for(suid, tenant=""):
    c = custom_percent(suid)
    return c if c is not None else default_percent(tenant)


def set_custom_percent(suid, value):
    """value None/'' removes the override."""
    if value in (None, ""):
        db.set(f"/referral_custom/{suid}", None)
        return None
    p = parse_pct(value)
    db.set(f"/referral_custom/{suid}", p)
    return p


def min_withdraw(tenant=""):
    base = wallet.settings()["min_withdraw"]
    if tenant:
        v = (resellers.get(tenant) or {}).get("min_withdraw")
        try:
            return float(v) if v else base
        except (TypeError, ValueError):
            return base
    return base


def fmt_pct(p):
    return f"{int(p)}" if float(p) == int(p) else f"{p:g}"


def link_for(bot_username, chat_id):
    return f"https://t.me/{bot_username}?start=ref_{chat_id}"


def stats_for(suid):
    s = db.get(f"/referral_stats/{suid}") or {}
    return {"count": int(s.get("count", 0)), "converted": int(s.get("converted", 0)),
            "earned": round(float(s.get("earned", 0)), 2)}


def _udict(u):
    return {"chat_id": str(u.get("chat_id", "")), "full_name": u.get("full_name", ""),
            "username": u.get("username", ""), "profile_image_url": u.get("profile_image_url", "")}


# ------------------------------------------------------------------ registering a referral
def register(ctx, referrer_chat, user):
    """`user` just started this store's bot for the first time through `referrer_chat`'s link.
    Returns the referrer's user record (so the bot can say "you were referred by <Name>") or None."""
    referrer_chat, uid = str(referrer_chat), str(user["chat_id"])
    if referrer_chat == uid or not enabled(ctx.tenant):
        return None
    ref_user = users.get_user(referrer_chat, ctx.tenant)
    if not ref_user:
        return None
    rsid = users.scoped(referrer_chat, ctx.tenant)
    if not is_registered(rsid):  # only people who confirmed Refer & Earn can refer
        return None
    rec = {"referred_id": uid, "referrer_id": referrer_chat, "referrer_sid": rsid, "joined_at": utils.now(),
           "status": "pending", "purchases": 0, "earned": 0, "user": _udict(user)}
    if not db.set_if_absent(f"/referrals/{rsid}/{uid}", rec):
        return None
    db.update(users.user_path(uid, ctx.tenant), {"referred_by": referrer_chat})
    count = db.transaction(f"/referral_stats/{rsid}/count", lambda c: int(c or 0) + 1)
    pct = percent_for(rsid, ctx.tenant)
    notify.to_chat(referrer_chat,
                   "👥 <b>You got a new referral!</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                   f"🙋 <b>{m.esc(user.get('full_name'))}</b> joined using your link.\n"
                   f"⏳ Pending — you'll earn <b>{fmt_pct(pct)}%</b> as soon as they buy a plan.\n\n"
                   f"🔢 Total referrals: <b>{count}</b>", client=ctx.tg)
    from core.audit import log_action
    log_action("referral.joined", f"{user.get('full_name')} via {referrer_chat}", target=uid, source="bot",
               actor="bot")
    return ref_user


# ------------------------------------------------------------------ commission
def on_purchase(ctx, txn, sale_amount):
    """Pay the referrer their commission for a verified purchase (once per txn)."""
    uid = str(txn["user_id"])
    if not enabled(ctx.tenant):
        return None
    user = users.get_user(uid, ctx.tenant) or {}
    ref = user.get("referred_by")
    cash = float(sale_amount or 0)
    if not ref or cash <= 0:
        return None
    rsid = users.scoped(ref, ctx.tenant)
    pct = percent_for(rsid, ctx.tenant)
    commission = round(cash * pct / 100, 2)
    if commission <= 0:
        return None
    row = {"id": txn["id"], "ts": utils.now(), "referrer": rsid, "referrer_chat": str(ref), "referred": uid,
           "txn_id": txn["id"], "reseller_id": ctx.tenant, "app_name": txn.get("app_name", ""),
           "plan_label": txn.get("plan_label", ""), "paid": round(cash, 2), "percent": pct,
           "commission": commission, "user": _udict(user)}
    if not db.set_if_absent(f"/referral_earnings/{txn['id']}", row):
        return None  # already paid for this txn
    wallet.credit(rsid, "refer", commission, "refer_commission", txn["id"],
                  f"{user.get('full_name', '')} bought {txn.get('app_name', '')}")
    prev = db.get(f"/referrals/{rsid}/{uid}") or {}
    first = prev.get("status") != "converted"
    db.update(f"/referrals/{rsid}/{uid}", {"status": "converted", "converted_at": prev.get("converted_at") or utils.now(),
                                           "purchases": int(prev.get("purchases", 0)) + 1,
                                           "earned": round(float(prev.get("earned", 0)) + commission, 2)})
    if first:
        db.transaction(f"/referral_stats/{rsid}/converted", lambda c: int(c or 0) + 1)
    db.transaction(f"/referral_stats/{rsid}/earned", lambda c: round(float(c or 0) + commission, 2))
    bal = wallet.balance(rsid)
    notify.to_chat(ref, "💸 <b>Commission earned!</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                        f"🙋 <b>{m.esc(user.get('full_name'))}</b> purchased a premium plan\n"
                        f"📱 App: <b>{m.esc(txn.get('app_name'))}</b>\n📦 Plan: <b>{m.esc(txn.get('plan_label'))}</b>\n\n"
                        f"🎉 Your <b>{fmt_pct(pct)}%</b> commission: <b>{m.esc(utils.inr(commission))}</b>\n"
                        f"💰 Refer Money balance: <b>{m.esc(utils.inr(bal['refer']))}</b>\n\n"
                        "You earn this every time they buy — for life! ♾️",
                   client=ctx.tg, effect="5046509860389126442")
    return commission


# ------------------------------------------------------------------ panel views (tenant = '' for admin, rid for reseller)
def _stat_rows(tenant):
    stats = db.get("/referral_stats") or {}
    pre = f"{tenant}~" if tenant else ""
    return {k: v for k, v in stats.items() if (k.startswith(pre) if tenant else "~" not in k)}


def overview(tenant=""):
    stats = _stat_rows(tenant)
    reg = sum(int(v.get("count", 0)) for v in stats.values())
    conv = sum(int(v.get("converted", 0)) for v in stats.values())
    earned = round(sum(float(v.get("earned", 0)) for v in stats.values()), 2)
    pending_wd = [w for w in db.query_equal("/withdrawals", "status", "pending") if (w.get("reseller_id") or "") == tenant]
    return {"referrers": len(stats), "registered": reg, "converted": conv, "pending": reg - conv, "earned": earned,
            "pending_withdrawals": len(pending_wd),
            "pending_withdraw_amount": round(sum(w["amount"] for w in pending_wd), 2),
            "default_percent": default_percent(tenant), "min_withdraw": min_withdraw(tenant)}


def referrer_rows(tenant="", q=""):
    q = (q or "").strip().lower().lstrip("@")
    stats = _stat_rows(tenant)
    all_users = {u["chat_id"]: u for u in (db.get_list(f"/r_users/{tenant}") if tenant else db.get_list("/users")) if u.get("chat_id")}
    rows = []
    for sid, s in stats.items():
        chat = users.chat_of(sid)
        u = all_users.get(chat) or {"chat_id": chat, "full_name": chat}
        if q and q not in chat and q not in (u.get("full_name") or "").lower() and q not in (u.get("username") or "").lower():
            continue
        w = db.get(f"/wallets/{sid}") or {}
        cp = custom_percent(sid)
        rows.append({"chat_id": chat, "sid": sid, "full_name": u.get("full_name", ""), "username": u.get("username", ""),
                     "profile_image_url": u.get("profile_image_url", ""), "count": int(s.get("count", 0)),
                     "converted": int(s.get("converted", 0)), "earned": round(float(s.get("earned", 0)), 2),
                     "balance": round(float(w.get("refer", 0)), 2), "custom_percent": cp,
                     "percent": cp if cp is not None else default_percent(tenant)})
    rows.sort(key=lambda r: (-r["count"], -r["earned"]))
    return rows


def referrer_detail(tenant, chat_id):
    sid = users.scoped(chat_id, tenant)
    refs = db.get_list(f"/referrals/{sid}")
    earn = db.query_equal("/referral_earnings", "referrer", sid)
    by_user = {}
    for e in earn:
        by_user.setdefault(e["referred"], []).append(e)
    out = []
    for r in sorted(refs, key=lambda r: r.get("joined_at", 0), reverse=True):
        purchases = sorted(by_user.get(r["referred_id"], []), key=lambda e: e["ts"], reverse=True)
        out.append({**r["user"], "status": r.get("status", "pending"), "joined_at": r.get("joined_at", 0),
                    "purchases": [{"ts": e["ts"], "app_name": e["app_name"], "plan_label": e["plan_label"],
                                   "paid": e["paid"], "percent": e["percent"], "commission": e["commission"],
                                   "txn_id": e["txn_id"]} for e in purchases],
                    "earned": round(sum(e["commission"] for e in purchases), 2)})
    return out


# ------------------------------------------------------------------ withdrawals
def request_withdrawal(ctx, user, amount, upi_id):
    """Returns the withdrawal record. Refer money is taken out of the wallet immediately."""
    amount = round(float(amount), 2)
    mw = min_withdraw(ctx.tenant)
    if amount < mw:
        raise ApiError(f"Minimum withdrawal is {utils.inr(mw)}.")
    wid = utils.new_id("wd")
    suid = users.scoped(user["chat_id"], ctx.tenant)
    if not wallet.debit(suid, "refer", amount, "withdraw", wid, f"Withdraw to {upi_id}"):
        raise ApiError("You don't have that much Refer Money.")
    rec = {"id": wid, "user_id": str(user["chat_id"]), "suid": suid, "reseller_id": ctx.tenant, "amount": amount,
           "upi_id": upi_id, "status": "pending", "created_at": utils.now(),
           "user": {**_udict(user), "profile_file_id": user.get("profile_file_id", "")}}
    db.set(f"/withdrawals/{wid}", rec)
    return rec


def notify_new_withdrawal(ctx, rec):
    u = rec["user"]
    text = ("💸 <b>New withdrawal request</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Amount: <b>{m.esc(utils.inr(rec['amount']))}</b>\n🏦 UPI: <code>{m.esc(rec['upi_id'])}</code>\n"
            f"🧾 ID: <code>{rec['id']}</code>\n\n" + m.user_block(u, username=not ctx.tenant) +
            "\n\nOpen the panel → <b>Referral → Withdrawals</b> to approve or reject.")
    notify.send_to(notify.recipients(ctx.tenant), text, photo=u.get("profile_file_id") or None, client=ctx.tg)


def pending_withdrawals(tenant=""):
    return [w for w in db.query_equal("/withdrawals", "status", "pending") if (w.get("reseller_id") or "") == tenant]


def list_withdrawals(tenant="", status=""):
    rows = [w for w in db.get_list("/withdrawals") if (w.get("reseller_id") or "") == tenant]
    if status:
        rows = [w for w in rows if w["status"] == status]
    rows.sort(key=lambda w: w["created_at"], reverse=True)
    return rows


def resolve_withdrawal(wid, approve, reason, photo_bytes, actor, source="panel", tenant=None):
    """Approve or reject. Reason + optional photo are sent to the user. Rejected money goes back to the wallet."""
    from bot.context import MAIN, get_ctx
    rec = db.get(f"/withdrawals/{wid}")
    if not rec or (tenant is not None and (rec.get("reseller_id") or "") != tenant):
        raise ApiError("Withdrawal not found.", 404)
    reason = utils.clean_text(reason, "Reason", 300, required=not approve)
    new = "approved" if approve else "rejected"
    state = {"ok": False}

    def fn(cur):
        state["ok"] = cur == "pending"
        return new if state["ok"] else cur

    db.transaction(f"/withdrawals/{wid}/status", fn)
    if not state["ok"]:
        raise ApiError(f"This withdrawal is already {db.get(f'/withdrawals/{wid}/status')}.", 409)
    upd = {"resolved_at": utils.now(), "resolved_by": actor, "reason": reason}
    if photo_bytes:
        upd["proof_media_id"] = media.save_image(photo_bytes, "withdraw_proof", actor)
    db.update(f"/withdrawals/{wid}", upd)
    if not approve:
        wallet.credit(rec.get("suid") or rec["user_id"], "refer", rec["amount"], "withdraw_refund", wid,
                      f"Rejected: {reason}"[:200])
    ctx = get_ctx(rec.get("reseller_id", "")) or MAIN
    head = "✅ <b>Withdrawal approved</b>" if approve else "❌ <b>Withdrawal rejected</b>"
    text = (f"{head}\n━━━━━━━━━━━━━━━━━━\n\n💰 Amount: <b>{m.esc(utils.inr(rec['amount']))}</b>\n"
            f"🏦 UPI: <code>{m.esc(rec['upi_id'])}</code>\n🧾 ID: <code>{wid}</code>\n"
            + (f"📝 Note: {m.esc(reason)}\n" if reason else "")
            + ("" if approve else "\n💰 The amount was returned to your <b>Refer Money</b>."))
    try:
        if photo_bytes:
            ctx.tg.send_media("photo", rec["user_id"], photo_bytes, text[:1024], filename="proof.png",
                              effect="5046509860389126442" if approve else None)
        else:
            ctx.tg.send_message(rec["user_id"], text, effect="5046509860389126442" if approve else None)
    except TelegramError as e:
        log.info("could not notify user of withdrawal: %s", e)
    from core.audit import log_action
    log_action("withdraw.approved" if approve else "withdraw.rejected", f"{utils.inr(rec['amount'])} → {rec['upi_id']}",
               target=wid, source=source, actor=actor)
    return db.get(f"/withdrawals/{wid}")
