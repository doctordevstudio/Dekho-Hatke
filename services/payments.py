"""Transaction lifecycle for every store (main shop + each reseller).

kinds:    license (buy a plan)  |  deposit (add money to the wallet)
statuses: pending -> verification -> success | failed (fail_type: expired | cancelled | rejected)
A license purchase can be paid from the wallet in whole (instant, no UPI) or in part (the rest by UPI).
Money held from the wallet is given back when a payment fails, expires or is rejected.
Nothing is ever deleted; every state change is stored on the txn.
"""
import logging

from bot import messages as m
from bot.context import MAIN, get_ctx
from bot.tg import TelegramError
from config import Config
from core import db, utils
from core.audit import log_action
from core.cache import cache
from core.errors import ApiError
from services import catalog, delivery, earnings, licenses, media, notify, resellers, users, wallet

log = logging.getLogger("payments")


# ------------------------------------------------------------------ helpers
def get_txn(txn_id):
    return db.get(f"/txns/{txn_id}")


def ctx_of(txn):
    return get_ctx(txn.get("reseller_id", "")) or MAIN


def _claim(txn_id, from_statuses, to_status):
    """Atomically move status; returns True only for the caller that won the race."""
    state = {"ok": False}

    def fn(cur):
        state["ok"] = False
        if cur in from_statuses:
            state["ok"] = True
            return to_status
        return cur

    db.transaction(f"/txns/{txn_id}/status", fn)
    return state["ok"]


def screenshot_url(txn):
    fid = (txn.get("screenshot") or {}).get("file_id")
    if not fid:
        return ""
    return f"/media/tg/{fid}" + (f"?b={txn['reseller_id']}" if txn.get("reseller_id") else "")


def uses_upi(txn):
    return bool(txn.get("upi_record_id"))


def public_txn(t):
    rem = max(0, t.get("expires_at", 0) - utils.now()) if t.get("status") == "pending" else 0
    wal = t.get("wallet") or {}
    return {
        "id": t["id"], "kind": t.get("kind", "license"), "status": t["status"], "fail_type": t.get("fail_type", ""),
        "fail_reason": t.get("fail_reason", ""), "amount": t["amount"], "plan_price": t.get("plan_price", t["amount"]),
        "wallet_used": round(float(wal.get("total", 0)), 2), "app_id": t.get("app_id", ""),
        "app_name": t.get("app_name") or ("Wallet deposit" if t.get("kind") == "deposit" else ""),
        "plan_label": t.get("plan_label", ""), "upi_id": t.get("upi_id", ""),
        "upi_record_id": t.get("upi_record_id", ""), "utr": t.get("utr", ""),
        "created_at": t["created_at"], "expires_at": t.get("expires_at", 0), "remaining": rem,
        "submitted_at": t.get("submitted_at", 0), "verified_at": t.get("verified_at", 0),
        "verified_by": t.get("verified_by", ""), "license_key": t.get("license_key", ""),
        "screenshot_url": screenshot_url(t), "user": t["user"], "reseller_id": t.get("reseller_id", ""),
        "seller_id": t.get("seller_id", ""),
    }


# ------------------------------------------------------------------ UPI selection (round robin per store)
def active_upis(tenant=""):
    rows = [u for u in db.get_list("/upis") if not u.get("deleted") and u.get("active", True)
            and (u.get("reseller_id") or "") == (tenant or "")]
    rows.sort(key=lambda u: u.get("created_at", 0))
    return rows


def plan_upi(plan, tenant=""):
    """The UPI the admin fixed for this plan (main shop only). None = random / round-robin."""
    uid = (plan or {}).get("upi_id")
    if tenant or not uid:
        return None
    u = db.get(f"/upis/{uid}")
    if u and not u.get("deleted") and u.get("active", True) and not u.get("reseller_id"):
        return u
    return None  # removed or switched off -> fall back to round-robin


def pick_upi(tenant=""):
    upis = active_upis(tenant)
    if not upis:
        return None
    if len(upis) == 1:
        return upis[0]
    counter = db.transaction(f"/meta/rr/{tenant or 'main'}", lambda cur: int(cur or 0) + 1)
    return upis[(int(counter) - 1) % len(upis)]


def _user_dict(user):
    return {"chat_id": str(user["chat_id"]), "full_name": user.get("full_name", ""),
            "username": user.get("username", ""), "profile_image_url": user.get("profile_image_url", ""),
            "profile_file_id": user.get("profile_file_id", "")}


def _new_id():
    txn_id = utils.gen_txn_id()
    while db.get(f"/txns/{txn_id}") is not None:
        txn_id = utils.gen_txn_id()
    return txn_id


def open_txn(ctx, user):
    """('in_verification'|'existing', txn) if the user already has an open UPI payment in this store."""
    uid, now = str(user["chat_id"]), utils.now()
    mine = [t for t in db.query_equal("/txns", "user_id", uid) if (t.get("reseller_id") or "") == ctx.tenant]
    verif = [t for t in mine if t["status"] == "verification"]
    if verif:
        return "in_verification", verif[0], mine
    pend = [t for t in mine if t["status"] == "pending" and t["expires_at"] > now]
    if pend:
        return "existing", pend[0], mine
    return None, None, mine


def _daily_limit_hit(mine):
    today = utils.day_key()
    return sum(1 for t in mine if utils.day_key(t["created_at"]) == today and uses_upi(t)) >= Config.MAX_TXN_PER_DAY


# ------------------------------------------------------------------ quote (what the confirm screen shows)
def quote(ctx, user, plan_id):
    """Everything the bot needs to show before the user confirms. None if unavailable."""
    plan = catalog.get_plan(plan_id)
    app = catalog.get_app(plan["app_id"]) if plan else None
    if not plan or not app or not catalog.purchasable(app) or plan.get("disabled"):
        return None
    price, enabled = catalog.price_for(plan, ctx.tenant)
    if not enabled:
        return None
    if plan.get("kind") == "short":
        from services import shortener
        if ctx.tenant or not shortener.plan_ready(plan):
            return None
    if ctx.tenant:  # a reseller can hide apps / categories / plans and switch free trials on or off for their own bot
        if plan.get("kind") == "trial" and not resellers.trial_enabled(ctx.tenant, app["id"]):
            return None
        hid = resellers.hidden(ctx.tenant)
        if app["id"] in (hid.get("apps") or {}) or (app.get("category_id") or "other") in (hid.get("categories") or {}):
            return None
    bal = wallet.balance(users.scoped(user["chat_id"], ctx.tenant))
    sh = None
    if plan.get("kind") == "short":
        from services import shortener
        sh = shortener.shortener(str(plan.get("shorturl_id") or ""))
    return {"plan": plan, "app": app, "price": price, "trial": plan.get("kind") == "trial",
            "short": plan.get("kind") == "short", "sh": sh, "wallet": bal,
            "label": utils.validity_label(plan["validity_value"], plan["validity_unit"])}


# ------------------------------------------------------------------ create: license
def create_license_txn(ctx, user, plan_id, use_wallet=False):
    """Returns (status, txn).
    status: ok | done | existing | in_verification | daily_limit | no_upi | unavailable | trial_used | locked"""
    q = quote(ctx, user, plan_id)
    if not q:
        return "unavailable", None
    if ctx.tenant and resellers.is_forced(ctx.tenant):
        return "locked", None
    plan, app, price = q["plan"], q["app"], q["price"]
    uid, suid = str(user["chat_id"]), users.scoped(user["chat_id"], ctx.tenant)
    state, open_t, mine = open_txn(ctx, user)
    if state:
        return state, open_t
    if q["short"]:
        return "unavailable", None  # Free (short link) plans are started with start_short, never as a payment
    if q["trial"]:
        return _claim_trial(ctx, user, suid, app, plan)
    if _daily_limit_hit(mine):
        return "daily_limit", None

    txn_id = _new_id()
    bd = wallet.reserve(suid, price, txn_id) if use_wallet and price > 0 else \
        {"bonus": 0.0, "deposit": 0.0, "refer": 0.0, "rewards": [], "total": 0.0}
    cash = round(price - bd["total"], 2)
    upi = None
    if cash > 0:
        upi = plan_upi(plan, ctx.tenant) or pick_upi(ctx.tenant)
        if not upi:
            wallet.release(suid, bd, txn_id)
            return "no_upi", None
    now = utils.now()
    txn = {
        "id": txn_id, "kind": "license", "user_id": uid, "suid": suid, "reseller_id": ctx.tenant,
        "status": "pending", "amount": cash, "plan_price": price, "wallet": bd,
        "app_id": app["id"], "app_name": app["name"], "plan_id": plan["id"], "seller_id": app.get("seller_id", ""),
        "plan_label": q["label"], "validity_value": plan["validity_value"], "validity_unit": plan["validity_unit"],
        "upi_record_id": upi["id"] if upi else "", "upi_id": upi["upi_id"] if upi else "",
        "upi_name": (upi or {}).get("name", ""), "created_at": now,
        "expires_at": now + Config.TXN_TTL_SECONDS, "user": _user_dict(user),
    }
    db.set(f"/txns/{txn_id}", txn)
    if cash <= 0:  # the wallet covered everything: no UPI step at all
        txn["via"] = "wallet"
        db.update(f"/txns/{txn_id}", {"via": "wallet"})
        done = _complete(ctx, txn, "wallet", "bot")
        return "done", done
    return "ok", txn


def _claim_trial(ctx, user, suid, app, plan):
    if ctx.tenant and not resellers.trial_enabled(ctx.tenant, app["id"]):
        return "unavailable", None
    if not db.set_if_absent(f"/trial_used/{suid}/{app['id']}", {"ts": utils.now(), "plan_id": plan["id"]}):
        return "trial_used", None
    txn_id = _new_id()
    now = utils.now()
    label = utils.validity_label(plan["validity_value"], plan["validity_unit"])
    txn = {"id": txn_id, "kind": "license", "user_id": str(user["chat_id"]), "suid": suid, "reseller_id": ctx.tenant,
           "status": "pending", "amount": 0, "plan_price": 0, "wallet": {"total": 0}, "trial": True, "via": "trial",
           "app_id": app["id"], "app_name": app["name"], "plan_id": plan["id"], "seller_id": app.get("seller_id", ""),
           "plan_label": label + " (free trial)", "validity_value": plan["validity_value"],
           "validity_unit": plan["validity_unit"], "upi_record_id": "", "upi_id": "", "created_at": now,
           "expires_at": now, "user": _user_dict(user)}
    db.set(f"/txns/{txn_id}", txn)
    done = _complete(ctx, txn, "trial", "bot")
    notify.to_admins("🎁 <b>Free trial claimed</b>\n\n"
                     f"📱 {m.esc(app['name'])} — {m.esc(label)}\n\n" + m.user_block(done["user"]))
    log_action("trial.claimed", f"{app['name']} / {label}", target=txn_id, source="bot", actor=f"user:{user['chat_id']}")
    return "done", done


def issue_short_license(ctx, user, plan, txn_id, shortener_name=""):
    """The user finished every step of a shortener: record a free order and hand over key + APK + video.
    Safe to call again with the same txn_id (a retry never makes a second key)."""
    old = get_txn(txn_id)
    if old and old.get("status") == "success":
        return old
    app = catalog.get_app(plan["app_id"])
    if not app:
        raise ApiError("App not found.", 404)
    if not old:
        suid = users.scoped(user["chat_id"], ctx.tenant)
        now = utils.now()
        label = utils.validity_label(plan["validity_value"], plan["validity_unit"])
        old = {"id": txn_id, "kind": "license", "user_id": str(user["chat_id"]), "suid": suid, "reseller_id": ctx.tenant,
               "status": "pending", "amount": 0, "plan_price": 0, "wallet": {"total": 0}, "short": True, "via": "shorturl",
               "shortener": shortener_name, "app_id": app["id"], "app_name": app["name"], "plan_id": plan["id"],
               "seller_id": app.get("seller_id", ""), "plan_label": label + " (free · short link)",
               "validity_value": plan["validity_value"], "validity_unit": plan["validity_unit"], "upi_record_id": "",
               "upi_id": "", "created_at": now, "expires_at": now, "user": _user_dict(user)}
        db.set(f"/txns/{txn_id}", old)
    done = _complete(ctx, old, "shorturl", "shortener")
    log_action("shorturl.key", f"{app['name']} / {old['plan_label']} via {shortener_name}", target=txn_id, source="bot",
               actor=f"user:{user['chat_id']}")
    return done


# ------------------------------------------------------------------ create: wallet deposit
def create_deposit_txn(ctx, user, amount):
    """('ok'|'existing'|'in_verification'|'daily_limit'|'no_upi', txn)"""
    state, open_t, mine = open_txn(ctx, user)
    if state:
        return state, open_t
    if _daily_limit_hit(mine):
        return "daily_limit", None
    upi = pick_upi(ctx.tenant)
    if not upi:
        return "no_upi", None
    txn_id, now = _new_id(), utils.now()
    txn = {"id": txn_id, "kind": "deposit", "user_id": str(user["chat_id"]),
           "suid": users.scoped(user["chat_id"], ctx.tenant), "reseller_id": ctx.tenant, "status": "pending",
           "amount": amount, "plan_price": amount, "wallet": {"total": 0}, "app_id": "", "app_name": "Wallet deposit",
           "plan_id": "", "plan_label": "Deposit", "upi_record_id": upi["id"], "upi_id": upi["upi_id"],
           "upi_name": upi.get("name", ""), "created_at": now, "expires_at": now + Config.TXN_TTL_SECONDS,
           "user": _user_dict(user)}
    db.set(f"/txns/{txn_id}", txn)
    return "ok", txn


# ------------------------------------------------------------------ payment card + admin notifications
def _qr_photo(upi, txn):
    """The payment QR. A *universal* UPI QR is generated for every payment (UPI id, payee name and the exact amount
    are pre-filled, so any UPI app opens ready to pay). The uploaded scanner image is only the fallback for when
    generation fails. Returns a (filename, bytes) tuple or None."""
    try:
        return ("qr.png", media.make_upi_qr(upi["upi_id"], upi.get("name"), txn["amount"], txn["id"]))
    except Exception as e:  # missing library, bad data ... never block a payment
        log.warning("QR generation failed (%s) -> using the uploaded scanner", e)
    if upi.get("scanner_media_id"):
        data, _ = media.get_media(upi["scanner_media_id"])
        if data:
            return ("scanner.png", data)
    return None


def send_card(ctx, txn):
    upi = db.get(f"/upis/{txn['upi_record_id']}") or {"upi_id": txn["upi_id"]}
    photo = _qr_photo(upi, txn)
    if photo:
        msg = ctx.tg.send_photo(txn["user_id"], photo, m.payment_caption(txn), m.card_markup(txn["id"]))
    else:  # no QR at all: the UPI id in the text still lets the user pay
        msg = ctx.tg.send_message(txn["user_id"], m.payment_caption(txn), m.card_markup(txn["id"]))
    db.update(f"/txns/{txn['id']}", {"user_msg_id": msg["message_id"], "card_kind": "photo" if photo else "text"})
    return msg


def txn_chats(txn):
    return notify.recipients(txn.get("reseller_id", ""), db.get(f"/upis/{txn.get('upi_record_id', '')}/telegram_chat_id"))


def can_moderate(chat_id, txn):
    return str(chat_id) in set(txn_chats(txn))


def _show_username(chat):
    """Only the super admin / 2nd admin get the customer's @username; UPI owners and resellers do not."""
    return str(chat) in set(notify.admin_chats())


def notify_created(txn):
    ctx = ctx_of(txn)
    sent = {}
    for chat in txn_chats(txn):
        sent.update(notify.send_to([chat], m.admin_created_text(txn, _show_username(chat)),
                                   photo=txn["user"].get("profile_file_id") if _show_username(chat) else None,
                                   client=ctx.tg))
    if sent:
        db.update(f"/txns/{txn['id']}", {f"created_msgs/{c}": mid for c, mid in sent.items()})


# ------------------------------------------------------------------ proof
def find_duplicate(shot, utr, txn_id):
    """A screenshot / UTR already attached to a txn that is in verification or success is a duplicate.
    (Failed txns release their image/UTR so an honest user can retry.)"""
    def live(ref_id):
        return bool(ref_id) and ref_id != txn_id and db.get(f"/txns/{ref_id}/status") in ("verification", "success")

    if live(db.get(f"/image_hashes/{shot['sha256']}")) or live(db.get(f"/image_hashes/u_{shot['unique_id']}")):
        return "image"
    if utr and live(db.get(f"/utr_index/{utr}")):
        return "utr"
    return None


def submit_proof(txn_id, user_id, shot, utr):
    """Returns 'ok' | 'expired' | 'invalid' | 'image' | 'utr'."""
    txn = get_txn(txn_id)
    if not txn or txn["user_id"] != str(user_id) or txn["status"] != "pending" or not uses_upi(txn):
        return "invalid"
    if txn["expires_at"] <= utils.now():
        expire_due()
        return "expired"
    dup = find_duplicate(shot, utr, txn_id)
    if dup:
        return dup
    if not _claim(txn_id, {"pending"}, "verification"):
        return "invalid"
    shot = dict(shot, url=f"/media/tg/{shot['file_id']}")
    db.update("/", {
        f"txns/{txn_id}/utr": utr, f"txns/{txn_id}/screenshot": shot, f"txns/{txn_id}/submitted_at": utils.now(),
        f"utr_index/{utr}": txn_id, f"image_hashes/{shot['sha256']}": txn_id,
        f"image_hashes/u_{shot['unique_id']}": txn_id,
    })
    txn = get_txn(txn_id)
    ctx = ctx_of(txn)
    created = txn.get("created_msgs") or {}
    # The photo is a file id of the *customer's* bot, so reseller UPI chats get it re-uploaded when needed.
    sent = {}
    for chat in txn_chats(txn):
        try:
            msg = delivery.send_ref(ctx, chat, "photo", shot["file_id"],
                                    m.admin_proof_caption(txn, "", _show_username(chat)), m.admin_buttons(txn_id))
            sent[chat] = msg["message_id"]
        except TelegramError:
            r = notify.send_to([chat], m.admin_proof_caption(txn, "", _show_username(chat)),
                               reply_markup=m.admin_buttons(txn_id), reply_to=created, client=ctx.tg)
            sent.update(r)
    if sent:
        db.update(f"/txns/{txn_id}", {f"proof_msgs/{c}": mid for c, mid in sent.items()})
    cache.clear()
    log_action("txn.proof_submitted", f"UTR {utr}", target=txn_id, source="telegram", actor=f"user:{user_id}")
    return "ok"


# ------------------------------------------------------------------ resolution
def _edit_proof_messages(txn, status_line):
    ctx = ctx_of(txn)
    for chat, mid in (txn.get("proof_msgs") or {}).items():
        for client in (ctx.tg, MAIN.tg):
            try:
                client.edit_caption(chat, mid, m.admin_proof_caption(txn, status_line, _show_username(chat)),
                                    reply_markup={"inline_keyboard": []})
                break
            except TelegramError as e:
                log.info("edit proof msg failed: %s", e)


def _thread_note(txn, text):
    notify.send_to(list((txn.get("created_msgs") or {}).keys()) or txn_chats(txn), text,
                   reply_to=txn.get("created_msgs"), client=ctx_of(txn).tg)


def verify_txn(txn_id, actor_name, source="panel"):
    txn = get_txn(txn_id)
    if not txn:
        raise ApiError("Transaction not found.", 404)
    if not _claim(txn_id, {"verification"}, "success"):
        raise ApiError(f"Transaction is already {get_txn(txn_id)['status']}.", 409)
    txn = get_txn(txn_id)
    db.update(f"/txns/{txn_id}", {"verified_at": utils.now(), "verified_by": actor_name, "verified_via": source})
    txn.update(verified_at=utils.now(), verified_by=actor_name, verified_via=source)
    ctx = ctx_of(txn)
    out = _complete(ctx, txn, actor_name, source, already_success=True)
    _edit_proof_messages(out, f"✅ <b>Verified</b> by {m.esc(actor_name)}")
    log_action("txn.verified", f"{out.get('app_name') or 'Wallet deposit'} / {utils.inr(out['amount'])}", target=txn_id,
               source=source, actor=actor_name)
    return licenses.get_license(out["license_key"]) if out.get("license_key") else out


def _complete(ctx, txn, actor_name, source, already_success=False):
    """The payment is good: wallet credit (deposit) or license + earnings + referral + seller credit + delivery."""
    txn_id = txn["id"]
    if not already_success:
        if not _claim(txn_id, {"pending", "verification"}, "success"):
            return get_txn(txn_id)
        db.update(f"/txns/{txn_id}", {"verified_at": utils.now(), "verified_by": actor_name, "verified_via": source})
    cache.clear()
    u = txn["user"]
    bd = txn.get("wallet") or {}
    suid = txn.get("suid") or users.scoped(txn["user_id"], txn.get("reseller_id", ""))
    cash = float(txn["amount"])
    if txn.get("kind") == "deposit":
        wallet.credit(suid, "deposit", cash, "deposit", txn_id, "UPI deposit verified")
        earnings.record_upi(upi_id=txn["upi_record_id"], amount=cash, kind="deposit", user=u, txn_id=txn_id,
                            reseller_id=txn.get("reseller_id", ""))
        out = get_txn(txn_id)
        _send_user(ctx, txn["user_id"], m.deposit_done_text(out, wallet.balance(suid)), effect=True)
        return out

    app = catalog.get_app(txn["app_id"]) or {"id": txn["app_id"], "name": txn["app_name"]}
    wallet.confirm(bd)
    deposit_part = float(bd.get("deposit", 0))
    sale = round(cash + deposit_part, 2)  # real money received for this sale
    lic = licenses.create_license(
        app=app, chat_id=u["chat_id"], full_name=u["full_name"], username=u.get("username", ""),
        profile_image_url=u.get("profile_image_url", ""), profile_file_id=u.get("profile_file_id", ""),
        amount=txn.get("plan_price", cash), validity_value=txn["validity_value"], validity_unit=txn["validity_unit"],
        source="shorturl" if txn.get("short") else ("trial" if txn.get("trial") else "bot"), txn_id=txn_id, plan_id=txn["plan_id"],
        upi_id=txn.get("upi_record_id", ""), reseller_id=txn.get("reseller_id", ""), record_earning=False)
    reseller = ctx.reseller or {}
    if sale > 0:
        earnings.record(app=app, amount=sale, source="bot", user=lic, license_key=lic["key"], txn_id=txn_id,
                        upi_id=txn.get("upi_record_id", ""), plan_id=txn["plan_id"], label=lic["validity_label"],
                        reseller_id=txn.get("reseller_id", ""),
                        commission_pct=float(reseller.get("commission_pct", resellers.DEFAULT_COMMISSION)) if ctx.tenant else 0)
    if cash > 0:
        earnings.record_upi(upi_id=txn["upi_record_id"], amount=cash, kind="license", user=u, txn_id=txn_id,
                            app_id=app["id"], app_name=app.get("name", ""), reseller_id=txn.get("reseller_id", ""))
    db.update(f"/txns/{txn_id}", {"license_key": lic["key"]})
    if app.get("id"):
        db.transaction(f"/apps/{app['id']}/sales_count", lambda c: int(c or 0) + 1)
    out = get_txn(txn_id)

    # side effects that must never undo the sale
    for fn, args in ((_seller_credit, (out, app, lic)), (_referral_commission, (ctx, out, sale))):
        try:
            fn(*args)
        except Exception:
            log.exception("post-sale step %s failed for %s", fn.__name__, txn_id)
    _close_user_card(ctx, out)
    _send_user(ctx, out["user_id"], m.license_done_text(out, lic, wallet_total=float(bd.get("total", 0))), effect=True)
    try:
        delivery.deliver_assets(ctx, out["user_id"], app, lic, out.get("plan_label", ""))
    except Exception:
        log.exception("asset delivery failed for %s", txn_id)
    if out.get("via") == "wallet":
        for chat in txn_chats(out):
            notify.send_to([chat], m.admin_wallet_sale_text(out, _show_username(chat)), client=ctx.tg)
    return out


def _seller_credit(txn, app, lic):
    if app.get("seller_id"):
        from services import sellers
        sellers.credit_sale(app, txn, lic)


def _referral_commission(ctx, txn, sale):
    from services import referral
    referral.on_purchase(ctx, txn, sale)


def _send_user(ctx, chat_id, text, effect=False):
    try:
        ctx.tg.send_message(chat_id, text, effect="5046509860389126442" if effect else None)
    except TelegramError as e:
        log.info("could not DM %s: %s", chat_id, e)


def _close_user_card(ctx, txn):
    mid = txn.get("user_msg_id")
    if mid:
        ctx.tg.delete_message(txn["user_id"], mid)


def fail_txn(txn_id, reason, actor_name, source="panel", fail_type="rejected", from_statuses=("verification",)):
    txn = get_txn(txn_id)
    if not txn:
        raise ApiError("Transaction not found.", 404)
    reason = utils.clean_text(reason, "Reason", 300, required=fail_type == "rejected")
    if not _claim(txn_id, set(from_statuses), "failed"):
        raise ApiError(f"Transaction is already {get_txn(txn_id)['status']}.", 409)
    db.update(f"/txns/{txn_id}", {"fail_type": fail_type, "fail_reason": reason, "failed_at": utils.now(),
                                  "failed_by": actor_name})
    txn = get_txn(txn_id)
    cache.clear()
    ctx = ctx_of(txn)
    wal = txn.get("wallet") or {}
    if wal.get("total"):
        wallet.release(txn.get("suid") or txn["user_id"], wal, txn_id)  # held wallet money goes back
    refunded = "\n\n💰 The wallet money used for this order was returned to your wallet." if wal.get("total") else ""
    if fail_type == "rejected":
        _send_user(ctx, txn["user_id"],
                   "❌ <b>Payment verification failed</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                   f"🧾 Txn: <code>{txn_id}</code>\n📝 Reason: {m.esc(reason)}{refunded}\n\n"
                   "You can try again from <b>Purchase</b> in the menu.")
        _edit_proof_messages(txn, f"❌ <b>Failed</b> by {m.esc(actor_name)}\n📝 {m.esc(reason)}")
        log_action("txn.rejected", reason, target=txn_id, source=source, actor=actor_name)
    else:
        label = "expired" if fail_type == "expired" else "cancelled by user"
        _close_user_card(ctx, txn)
        _send_user(ctx, txn["user_id"], f"⛔ <b>Payment {label}.</b>{refunded}")
        _thread_note(txn, f"⛔ Txn <code>{txn_id}</code> {label}.")
        key = users.scoped(txn["user_id"], txn.get("reseller_id", ""))
        if (db.get(f"/bot_state/{key}") or {}).get("txn_id") == txn_id:
            db.set(f"/bot_state/{key}", None)  # user was mid-upload for a txn that just closed
    return txn


def expire_due():
    """Mark pending txns whose 10 minutes are over as failed(expired). Safe to call often."""
    now = utils.now()
    n = 0
    for t in db.query_equal("/txns", "status", "pending"):
        if t.get("expires_at", 0) <= now and uses_upi(t):
            try:
                fail_txn(t["id"], "Payment window expired", "system", source="system",
                         fail_type="expired", from_statuses=("pending",))
                n += 1
            except ApiError:
                pass
    return n


# ------------------------------------------------------------------ search
def search(q, tenant=None):
    q = q.strip()
    found = {}
    up = q.upper()
    if up.startswith("TXN"):
        t = get_txn(up)
        if t:
            found[t["id"]] = t
    if q.isdigit():
        tid = db.get(f"/utr_index/{q}")
        if tid and get_txn(tid):
            found[tid] = get_txn(tid)
        if not found and len(q) != 12:
            for t in db.query_equal("/txns", "user_id", q):
                found[t["id"]] = t
    rows = [t for t in found.values() if uses_upi(t)]
    if tenant is not None:
        rows = [t for t in rows if (t.get("reseller_id") or "") == tenant]
    return rows
