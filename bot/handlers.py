"""Telegram update handling for the main bot and every reseller bot (same code, different `ctx`).

Chat hygiene: every menu / button press *replaces* the previous screen (the old screen and the user's menu tap
are deleted), so the conversation never fills up. Valuable messages (payment cards, license keys, APKs, withdrawal
results) are "kept" and never removed.
"""
import hashlib
import logging
import re
import threading
import time

from bot import messages as m
from bot.antispam import spam
from bot.context import MAIN
from bot.tg import EFFECT_PARTY, TelegramError
from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import catalog, delivery, media, notify, payments, referral, resellers, rewards, sellers, settings, users, wallet, wheel
from services import licenses as lic_svc

log = logging.getLogger("bot")
UTR_RE = re.compile(r"^\d{12}$")
UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z0-9.\-]{2,32}$")
_joined_until = {}  # (tenant, user_id) -> epoch until which the force-join check is cached
STATE_TTL = 3600


# ================================================================== infrastructure
def skey(ctx, uid):
    return users.scoped(uid, ctx.tenant)


def get_state(ctx, uid):
    st = db.get(f"/bot_state/{skey(ctx, uid)}") or {}
    return st if utils.now() - int(st.get("ts", 0)) < STATE_TTL else {}


def set_state(ctx, uid, **kw):
    db.set(f"/bot_state/{skey(ctx, uid)}", {**kw, "ts": utils.now()})


def clear_state(ctx, uid):
    if db.get(f"/bot_state/{skey(ctx, uid)}") is not None:
        db.set(f"/bot_state/{skey(ctx, uid)}", None)


def send(ctx, chat_id, text, markup=None, mode="HTML", effect=None):
    """A message that stays in the chat."""
    try:
        return ctx.tg.send_message(chat_id, text, markup, parse_mode=mode, effect=effect)
    except TelegramError as e:
        log.info("send to %s failed: %s", chat_id, e)
        return None


def answer(ctx, cq, text=None, alert=False):
    ctx.tg.answer_callback(cq["id"], text, alert)


def screen(ctx, uid, text, markup=None, media=None, cq=None, effect=None, same_media=False):
    """Show `text` as THE current screen: edit the tapped message in place when possible, otherwise send a new
    message and delete the previous screen. `media` = (kind, ref) puts a photo/video on top."""
    key = skey(ctx, uid)
    cur = db.get(f"/bot_scr/{key}") or {}
    cmsg = (cq or {}).get("message")
    cmid = cmsg["message_id"] if cmsg else None
    c_media = bool(cmsg and any(k in cmsg for k in ("photo", "video", "animation", "document")))
    is_cur = bool(cmid) and cmid == cur.get("mid")
    if cmsg and (is_cur or not cur):
        try:
            if not media and not c_media:
                ctx.tg.edit_text(uid, cmid, text, markup)
                db.set(f"/bot_scr/{key}", {"mid": cmid})
                return cmid
            if media and c_media and same_media:
                ctx.tg.edit_caption(uid, cmid, text, markup)
                return cmid
        except TelegramError as e:
            if "not modified" in e.description.lower():
                return cmid
    msg = None
    if media:
        try:
            msg = delivery.send_ref(ctx, uid, media[0], media[1], text, markup, effect=effect)
        except TelegramError as e:
            log.info("screen media failed (%s) -> text only", e)
    if msg is None:
        try:
            msg = ctx.tg.send_message(uid, text, markup, effect=effect)
        except TelegramError as e:
            if "parse" in e.description.lower():
                msg = send(ctx, uid, re.sub(r"<[^>]+>", "", text), markup, mode=None)
            else:
                log.info("screen failed: %s", e)
                return None
    new_mid = msg["message_id"]
    for old in {cur.get("mid"), cmid if is_cur else None} - {None, new_mid}:
        ctx.tg.delete_message(uid, old)
    db.set(f"/bot_scr/{key}", {"mid": new_mid})
    return new_mid


def drop_screen(ctx, uid):
    cur = db.get(f"/bot_scr/{skey(ctx, uid)}") or {}
    if cur.get("mid"):
        ctx.tg.delete_message(uid, cur["mid"])
        db.set(f"/bot_scr/{skey(ctx, uid)}", None)


def purl(ctx, fid):
    return f"/media/tg/{fid}" + (f"?b={ctx.tenant}" if ctx.tenant else "")


def touch_user(ctx, frm):
    uid = str(frm["id"])
    u, is_new = users.upsert_user(uid, users.full_name_of(frm), frm.get("username") or "", source="bot", tenant=ctx.tenant)
    path = users.user_path(uid, ctx.tenant)
    if not u.get("profile_file_id") or utils.now() - int(u.get("profile_checked_at", 0)) > 7 * 86400:
        upd = {"profile_checked_at": utils.now()}
        try:
            fid = ctx.tg.profile_file_id(uid)
            if fid:
                upd.update(profile_file_id=fid, profile_image_url=purl(ctx, fid))
        except TelegramError:
            pass
        db.update(path, upd)
        u.update(upd)
    if is_new:
        notify.new_user(u, ctx.tenant, ctx.tg, "" if ctx.is_main else ctx.brand)
    u["_new"] = is_new
    return u


def is_admin_chat(chat_id):
    return str(chat_id) in notify.admin_chats()


# ================================================================== force join
def channels_for(ctx):
    return [c for c in settings.channels() if (c.get("reseller_id") or "") == ctx.tenant]


def missing_channels(ctx, uid, use_cache=True):
    chans = channels_for(ctx)
    if not chans:
        return []
    ck = (ctx.tenant, uid)
    if use_cache and _joined_until.get(ck, 0) > time.time():
        return []
    missing = []
    for ch in chans:
        try:
            mem = ctx.tg.get_chat_member(ch["chat_id"], uid)
            st = mem.get("status")
            if st in ("creator", "administrator", "member") or (st == "restricted" and mem.get("is_member")):
                db.set_if_absent(f"/channel_joins/{ch['id']}/{ctx.key}_{uid}", utils.now())  # only real joiners count
            else:
                missing.append(ch)
        except TelegramError as e:  # bot not admin / bad channel: don't lock users out
            log.warning("membership check failed for %s: %s", ch.get("title"), e)
    if not missing:
        _joined_until[ck] = time.time() + 300
    return missing


def gate(ctx, uid, cq=None):
    miss = missing_channels(ctx, uid)
    if not miss:
        return True
    rows = [[(f"📢 {c['title'][:30]}", "url:" + c["invite_link"])] for c in miss if c.get("invite_link")]
    rows.append([("✅ I've joined — Verify", "verify_join")])
    text = ("🔒 <b>Join to unlock the store</b>\n" + m.LINE + "\n\nPlease join "
            + ("all channels" if len(miss) > 1 else "our channel") + " below, then tap <b>Verify</b> 👇")
    screen(ctx, uid, text, m.kb(*rows), cq=cq)
    return False


# ================================================================== onboarding / home
def locked_text(ctx):
    r = ctx.reseller or {}
    rich = settings.get_rich("reseller_due")
    return r.get("due_message") or rich["text"]


def is_locked(ctx):
    return bool(ctx.tenant) and resellers.is_forced(ctx.tenant)


def kbd(ctx, uid):
    from services import collection
    return m.menu_keyboard(ctx.is_main, rewards_on(ctx), collection.is_collector(uid, ctx.tenant))


def menu_hint(ctx, uid):
    old = db.get(f"/bot_hint/{skey(ctx, uid)}")
    if old:
        ctx.tg.delete_message(uid, old)
    msg = send(ctx, uid, "👇 Use the menu below anytime", kbd(ctx, uid))
    if msg:
        db.set(f"/bot_hint/{skey(ctx, uid)}", msg["message_id"])


def rewards_on(ctx):
    return ctx.is_main and (rewards.settings("checkin")["enabled"] or rewards.settings("spin")["enabled"])


def finish_onboarding(ctx, uid, user):
    """Runs once the user passed the force-join gate: register a pending referral, say who referred them."""
    key = skey(ctx, uid)
    ref = db.get(f"/pending_ref/{key}")
    if ref:
        db.set(f"/pending_ref/{key}", None)
        who = referral.register(ctx, ref, user)
        if who:
            send(ctx, uid, f"🤝 You were referred by <b>{m.esc(who.get('full_name'))}</b>. Welcome aboard!")


def home_markup(ctx):
    rows = [[("🛒 Purchase", "buy"), ("💰 Wallet", "wallet")], [("🎁 Refer & Earn", "refer"), ("🔑 My Keys", "keys")]]
    if rewards_on(ctx):
        rows.append([("📅 Daily Check-in", "checkin"), ("🎡 Spin Wheel", "spin")])
    rows.append([("🛟 Support", "support")])
    return m.kb(*rows)


def show_home(ctx, uid, user, cq=None):
    if is_locked(ctx):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if ctx.is_main:
        s = settings.bot_settings()
        tmpl, fmt, image = s["welcome_message"], s["welcome_format"], s["welcome_image"]
    else:
        tmpl = (ctx.reseller or {}).get("welcome_message") or settings.DEFAULT_WELCOME
        fmt, image = "html", ""
    first = (user.get("full_name") or "there").split()[0]
    text, mode = m.render_welcome(tmpl, fmt, {"first_name": first, "full_name": user.get("full_name"),
                                              "username": user.get("username"), "chat_id": uid})
    media = ("photo", image) if image else None
    if mode is None:  # plain-text welcome: screen() sends HTML, so escape it
        text = m.esc(text)
    screen(ctx, uid, text, home_markup(ctx), media=media, cq=cq)


# ================================================================== purchase: categories -> apps -> plans
def show_categories(ctx, uid, cq=None):
    if is_locked(ctx):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    tree = catalog.tree(ctx.tenant)
    if not tree:
        return screen(ctx, uid, "📭 <b>Nothing to buy yet</b>\n\nNew apps are coming soon — check back later!",
                      m.kb([("🏠 Home", "home")]), cq=cq)
    btns = [(f"{c['icon']} {c['name']} ({len(c['apps'])})", f"cat:{c['id']}") for c in tree]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([("🏠 Home", "home")])
    screen(ctx, uid, "🛒 <b>Purchase</b>\n" + m.LINE + "\n\nPick a category 👇", m.kb(*rows), cq=cq)


def show_category(ctx, uid, cat_id, cq=None):
    cat = next((c for c in catalog.tree(ctx.tenant) if c["id"] == cat_id), None)
    if not cat:
        return show_categories(ctx, uid, cq)
    medals = ["🥇", "🥈", "🥉"]
    rows = []
    for i, a in enumerate(cat["apps"]):
        has_trial = ctx.is_main and any(p.get("kind") == "trial" for p in catalog.plans_for(a["id"], ""))
        badge = medals[i] if i < 3 and int(a.get("sales_count", 0)) > 0 else "📱"
        rows.append([(f"{badge} {a['name']}" + (" · 🎁 Free trial" if has_trial else ""), f"ap:{a['id']}")])
    rows.append([("⬅️ Back", "buy"), ("🏠 Home", "home")])
    screen(ctx, uid, f"{cat['icon']} <b>{m.esc(cat['name'])}</b>\n" + m.LINE +
           "\n\n🏆 Best sellers first — pick an app 👇", m.kb(*rows), cq=cq)


def _app_and_plans(ctx, app_id):
    app = catalog.get_app(app_id)
    if not app or not catalog.purchasable(app):
        return None, []
    return app, catalog.plans_for(app_id, ctx.tenant)


def show_app(ctx, uid, app_id, selected=None, cq=None):
    app, plans = _app_and_plans(ctx, app_id)
    if not app or not plans:
        return show_categories(ctx, uid, cq)
    rows = [[(m.plan_label(p, selected == p["id"]), f"pl:{p['id']}")] for p in plans]
    sel = next((p for p in plans if p["id"] == selected), None)
    if sel:
        rows.append([("✅ Continue — " + utils.validity_label(sel["validity_value"], sel["validity_unit"]),
                      f"pc:{sel['id']}")])
    rows.append([("⬅️ Back", f"cat:{app.get('category_id') or catalog.OTHER_ID}"), ("🏠 Home", "home")])
    users_now = _active_users(app_id)
    text = m.app_caption(app, plans, users_now, selected)
    media = None
    if app.get("video_id"):
        media = ("video", app["video_id"])
    elif app.get("image_url"):
        media = ("photo", app["image_url"] if app["image_url"].startswith(("http", "/media/img/")) else None)
        media = media if media[1] else None
    screen(ctx, uid, text, m.kb(*rows), media=media, cq=cq, same_media=bool(selected) or True)


_au_cache = {}


def _active_users(app_id):
    hit = _au_cache.get(app_id)
    if hit and hit[0] > time.time():
        return hit[1]
    n = catalog.active_users(app_id)
    _au_cache[app_id] = (time.time() + 90, n)
    return n


def plan_continue(ctx, user, plan_id, cq):
    uid = user["chat_id"]
    q = payments.quote(ctx, user, plan_id)
    if not q:
        return screen(ctx, uid, "That plan is no longer available.", m.kb([("🛒 Purchase", "buy")]), cq=cq)
    if is_locked(ctx):
        return show_categories(ctx, uid, cq)
    if not q["trial"] and q["wallet"]["total"] > 0:
        usable = min(q["wallet"]["total"], q["price"])
        return screen(ctx, uid, m.wallet_question(q), m.kb(
            [(f"👛 Use wallet (−{utils.inr(usable)})", f"pw:{plan_id}:1")],
            [(f"🏦 Pay {utils.inr(q['price'])} by UPI", f"pw:{plan_id}:0")],
            [("⬅️ Back", f"ap:{q['app']['id']}")]), cq=cq)
    confirm_order(ctx, user, plan_id, False, cq)


def confirm_order(ctx, user, plan_id, use_wallet, cq):
    uid = user["chat_id"]
    q = payments.quote(ctx, user, plan_id)
    if not q:
        return screen(ctx, uid, "That plan is no longer available.", m.kb([("🛒 Purchase", "buy")]), cq=cq)
    wal = min(q["wallet"]["total"], q["price"]) if use_wallet and not q["trial"] else 0
    cash = round(q["price"] - wal, 2)
    screen(ctx, uid, m.confirm_text(q, use_wallet, (wal, cash)),
           m.kb([("✅ Yes", f"by:{plan_id}:{1 if use_wallet else 0}"), ("❌ No", f"ap:{q['app']['id']}")]), cq=cq)


def do_buy(ctx, user, plan_id, use_wallet, cq):
    uid = user["chat_id"]
    status, txn = payments.create_license_txn(ctx, user, plan_id, use_wallet)
    back = m.kb([("🛒 Purchase", "buy"), ("🏠 Home", "home")])
    if status == "in_verification":
        return screen(ctx, uid, "⏳ <b>Your payment is in verification</b>\n" + m.LINE +
                      f"\n\n🧾 Txn: <code>{txn['id']}</code>\nPlease wait 2-3 hours. You'll get your key here once approved.",
                      m.kb([("🏠 Home", "home")]), cq=cq)
    if status == "daily_limit":
        return screen(ctx, uid, f"🚫 You reached today's limit of <b>{payments.Config.MAX_TXN_PER_DAY}</b> payment requests. "
                                "Try again tomorrow.", back, cq=cq)
    if status == "no_upi":
        notify.send_to(notify.recipients(ctx.tenant), "⚠️ <b>No active UPI ID</b> — a user tried to pay but payments are "
                       "off. Add/enable a UPI in the panel.", client=ctx.tg)
        return screen(ctx, uid, "⚠️ Payments are temporarily unavailable. Please try again later.", back, cq=cq)
    if status == "locked":
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if status == "unavailable":
        return screen(ctx, uid, "That plan is no longer available.", back, cq=cq)
    if status == "trial_used":
        return screen(ctx, uid, "🎁 You've already used the free trial for this app.\nLoved it? Grab a full plan 👇",
                      m.kb([("🛒 See plans", f"ap:{(catalog.get_plan(plan_id) or {}).get('app_id', '')}")]), cq=cq)
    if status == "done":
        drop_screen(ctx, uid)
        send(ctx, uid, "✅ Done! Use the menu below to continue.", kbd(ctx, uid))
        return
    if status == "existing":
        screen(ctx, uid, "ℹ️ You already have an open payment request — here it is 👇", cq=cq)
        return payments.send_card(ctx, txn)
    drop_screen(ctx, uid)
    payments.send_card(ctx, txn)
    payments.notify_created(txn)


# ================================================================== payment proof
def on_paid(ctx, uid, txn_id, cq):
    txn = payments.get_txn(txn_id)
    if not txn or txn["user_id"] != str(uid):
        return answer(ctx, cq, "Transaction not found.", True)
    if txn["status"] == "verification":
        return answer(ctx, cq, "Already submitted — it's in verification.", True)
    if txn["status"] != "pending" or txn["expires_at"] <= utils.now():
        payments.expire_due()
        return answer(ctx, cq, "This payment request has expired. Please start again.", True)
    set_state(ctx, uid, name="await_screenshot", txn_id=txn_id)
    answer(ctx, cq)
    send(ctx, uid, "📸 <b>Upload your payment screenshot</b>\n" + m.LINE +
         "\n\nSend the screenshot of the successful payment as a photo.\nSend /cancel to go back.")


def on_screenshot(ctx, user, msg, st):
    uid = user["chat_id"]
    if msg.get("photo"):
        ph = msg["photo"][-1]
        fid, uniq, size = ph["file_id"], ph["file_unique_id"], ph.get("file_size", 0)
    elif (msg.get("document") or {}).get("mime_type", "") in ("image/jpeg", "image/png", "image/webp"):
        d = msg["document"]
        fid, uniq, size = d["file_id"], d["file_unique_id"], d.get("file_size", 0)
    else:
        return send(ctx, uid, "Please send the payment <b>screenshot as a photo</b> (JPG / PNG) or /cancel.")
    if size and size > 8 * 1024 * 1024:
        return send(ctx, uid, "That image is too large (max 8 MB).")
    try:
        data, _ = ctx.tg.download(fid)
    except TelegramError:
        return send(ctx, uid, "Couldn't read that image. Please try again.")
    # Strict check: the bytes must decode as a real JPEG/PNG/WEBP (a renamed file is not enough)
    ok, why = media.verify_real_image(data)
    if not ok:
        log_action("txn.fake_image", why, target=st.get("txn_id", ""), source="telegram", actor=f"user:{uid}")
        return send(ctx, uid, "🚫 <b>That is not a valid screenshot.</b>\nPlease send a real photo / screenshot of the "
                              "payment (JPG or PNG). Renamed or edited files are not accepted.")
    shot = {"file_id": fid, "unique_id": uniq, "sha256": hashlib.sha256(data).hexdigest()}
    if payments.find_duplicate(shot, "", st["txn_id"]) == "image":
        return send(ctx, uid, "🚫 This screenshot was already used for another payment. Please send the correct one.")
    set_state(ctx, uid, name="await_utr", txn_id=st["txn_id"], shot=shot)
    send(ctx, uid, "✅ Screenshot received.\n\nNow send the <b>12-digit UTR / reference number</b> of the payment.")


def on_utr(ctx, user, text, st):
    uid = user["chat_id"]
    utr = re.sub(r"\s", "", text)
    if not UTR_RE.match(utr):
        return send(ctx, uid, "❌ The UTR must be exactly <b>12 digits</b>. Please send it again (or /cancel).")
    res = payments.submit_proof(st["txn_id"], uid, st["shot"], utr)
    if res == "image":
        clear_state(ctx, uid)
        return send(ctx, uid, "🚫 This screenshot was already used for another payment.")
    if res == "utr":
        return send(ctx, uid, "🚫 This UTR was already used for another payment. Check it and send the correct one.")
    clear_state(ctx, uid)
    if res == "expired":
        return send(ctx, uid, "⌛ This payment request expired. Please start a new purchase.")
    if res != "ok":
        return send(ctx, uid, "Something went wrong with this transaction. Please start again.")
    send(ctx, uid, "🕒 <b>Payment verification is pending</b>\n" + m.LINE +
         f"\n\n🧾 Txn: <code>{st['txn_id']}</code>\n🔢 UTR: <code>{utr}</code>\n\n"
         "Please wait <b>2-3 hours</b> for manual verification. You'll receive your key here.")


def on_cancel_txn(ctx, uid, txn_id, cq):
    txn = payments.get_txn(txn_id)
    if not txn or txn["user_id"] != str(uid):
        return answer(ctx, cq, "Transaction not found.", True)
    try:
        payments.fail_txn(txn_id, "Cancelled by user", "user", "telegram", "cancelled", ("pending",))
        clear_state(ctx, uid)
        answer(ctx, cq, "Payment cancelled.")
    except ApiError as e:
        answer(ctx, cq, e.message, True)


def on_refresh(ctx, uid, txn_id, cq, msg):
    txn = payments.get_txn(txn_id)
    if not txn or txn["user_id"] != str(uid) or txn["status"] != "pending":
        return answer(ctx, cq, "This request is no longer open.", True)
    answer(ctx, cq, f"⏳ {m.mmss(txn['expires_at'] - utils.now())} remaining")
    try:
        if msg.get("photo"):
            ctx.tg.edit_caption(uid, msg["message_id"], m.payment_caption(txn), m.card_markup(txn_id))
        else:
            ctx.tg.edit_text(uid, msg["message_id"], m.payment_caption(txn), m.card_markup(txn_id))
    except TelegramError:
        pass


# ================================================================== wallet
PAGE = 10


def _pager(prefix, page, pages):
    """◀️ 2/5 ▶️ row for callback data like  hist:dep:<page>"""
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(("◀️ Prev", f"{prefix}:{page - 1}"))
    row.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        row.append(("Next ▶️", f"{prefix}:{page + 1}"))
    return [row]


def _page_of(rows, page):
    pages = max(1, -(-len(rows) // PAGE))
    page = max(0, min(int(page), pages - 1))
    return rows[page * PAGE:(page + 1) * PAGE], page, pages


def show_wallet(ctx, user, cq=None):
    uid = user["chat_id"]
    b = wallet.balance(skey(ctx, uid))
    st = wallet.settings()
    lines = ["💰 <b>My Wallet</b>", m.LINE, f"💳 Deposit Money: <b>{m.esc(utils.inr(b['deposit']))}</b>",
             f"🎁 Refer Money: <b>{m.esc(utils.inr(b['refer']))}</b>"]
    if b["bonus"]:
        left = max(0, rewards.next_expiry(skey(ctx, uid)) - utils.now())
        lines.append(f"⚡ Bonus: <b>{m.esc(utils.inr(b['bonus']))}</b> · ⏰ expires in {rewards.fmt_wait(left)}")
    lines += [m.LINE, f"✨ Usable on purchases: <b>{m.esc(utils.inr(b['total']))}</b>", "",
              f"➕ Minimum deposit: {m.esc(utils.inr(st['min_deposit']))}",
              f"💸 Minimum withdrawal: {m.esc(utils.inr(referral.min_withdraw(ctx.tenant)))} <i>(Refer Money only)</i>"]
    screen(ctx, uid, "\n".join(lines), m.kb(
        [("➕ Deposit Money", "dep"), ("💸 Withdraw", "wdr")],
        [("🧾 Deposit history", "hist:dep:0"), ("📤 Withdraw history", "hist:wdr:0")],
        [("🛒 Purchase", "buy"), ("🏠 Home", "home")]), cq=cq)


def start_deposit(ctx, user, cq):
    st = wallet.settings()
    set_state(ctx, user["chat_id"], name="await_deposit")
    answer(ctx, cq)
    screen(ctx, user["chat_id"], "➕ <b>Deposit Money</b>\n" + m.LINE +
           f"\n\nSend the amount you want to add (minimum <b>{m.esc(utils.inr(st['min_deposit']))}</b>).\n"
           "Send /cancel to go back.", m.kb([("⬅️ Back", "wallet")]), cq=cq)


def on_deposit_amount(ctx, user, text):
    uid = user["chat_id"]
    st = wallet.settings()
    try:
        amount = utils.parse_amount(text.replace("₹", "").replace(",", "").strip())
    except ApiError:
        return send(ctx, uid, "Please send a valid amount, e.g. <code>100</code> (or /cancel).")
    if amount < st["min_deposit"]:
        return send(ctx, uid, f"The minimum deposit is <b>{m.esc(utils.inr(st['min_deposit']))}</b>. Send a higher amount.")
    if amount > 100000:
        return send(ctx, uid, "That amount is too large. Please send a smaller amount.")
    clear_state(ctx, uid)
    status, txn = payments.create_deposit_txn(ctx, user, amount)
    if status == "in_verification":
        return send(ctx, uid, f"⏳ You already have a payment in verification (<code>{txn['id']}</code>). Please wait for it first.")
    if status == "daily_limit":
        return send(ctx, uid, "🚫 You reached today's limit of payment requests. Try again tomorrow.")
    if status == "no_upi":
        notify.send_to(notify.recipients(ctx.tenant), "⚠️ <b>No active UPI ID</b> — a user tried to deposit.", client=ctx.tg)
        return send(ctx, uid, "⚠️ Payments are temporarily unavailable. Please try again later.")
    drop_screen(ctx, uid)
    payments.send_card(ctx, txn)
    if status == "ok":
        payments.notify_created(txn)


def start_withdraw(ctx, user, cq):
    uid = user["chat_id"]
    b = wallet.balance(skey(ctx, uid))
    mw = referral.min_withdraw(ctx.tenant)
    answer(ctx, cq)
    if b["refer"] < mw:
        return screen(ctx, uid, "💸 <b>Withdraw</b>\n" + m.LINE +
                      f"\n\nYou can withdraw your <b>Refer Money</b> once it reaches <b>{m.esc(utils.inr(mw))}</b>.\n"
                      f"Your Refer Money: <b>{m.esc(utils.inr(b['refer']))}</b>\n\nInvite friends to earn more 🎁",
                      m.kb([("🎁 Refer & Earn", "refer"), ("⬅️ Back", "wallet")]), cq=cq)
    set_state(ctx, uid, name="await_wd_upi")
    screen(ctx, uid, "💸 <b>Withdraw Refer Money</b>\n" + m.LINE +
           f"\n\n💰 Available: <b>{m.esc(utils.inr(b['refer']))}</b>\n\nSend your <b>UPI ID</b> (like <code>name@bank</code>).\n"
           "Send /cancel to go back.", m.kb([("⬅️ Back", "wallet")]), cq=cq)


def on_wd_upi(ctx, user, text):
    upi = text.strip()
    if not UPI_RE.match(upi):
        return send(ctx, user["chat_id"], "❌ That doesn't look like a UPI ID. Send it like <code>name@bank</code> (or /cancel).")
    set_state(ctx, user["chat_id"], name="await_wd_amount", upi=upi)
    b = wallet.balance(skey(ctx, user["chat_id"]))
    send(ctx, user["chat_id"], f"🏦 UPI: <code>{m.esc(upi)}</code>\n\nNow send the <b>amount</b> to withdraw "
                               f"(min {m.esc(utils.inr(referral.min_withdraw(ctx.tenant)))}, max {m.esc(utils.inr(b['refer']))}).")


def on_wd_amount(ctx, user, text, st):
    uid = user["chat_id"]
    try:
        amount = utils.parse_amount(text.replace("₹", "").replace(",", "").strip())
        rec = referral.request_withdrawal(ctx, user, amount, st["upi"])
    except ApiError as e:
        return send(ctx, uid, f"⚠️ {m.esc(e.message)}\nSend another amount (or /cancel).")
    clear_state(ctx, uid)
    referral.notify_new_withdrawal(ctx, rec)
    send(ctx, uid, "✅ <b>Withdrawal requested</b>\n" + m.LINE + f"\n\n💰 Amount: <b>{m.esc(utils.inr(rec['amount']))}</b>\n"
                   f"🏦 UPI: <code>{m.esc(rec['upi_id'])}</code>\n🧾 ID: <code>{rec['id']}</code>\n\n"
                   "We'll notify you here as soon as it's approved.", kbd(ctx, uid))
    log_action("withdraw.requested", f"{utils.inr(rec['amount'])} → {rec['upi_id']}", target=rec["id"],
               source="bot", actor=f"user:{uid}")


def show_history(ctx, user, kind, page, cq):
    uid = user["chat_id"]
    if kind == "dep":
        rows = [t for t in db.query_equal("/txns", "user_id", str(uid))
                if t.get("kind") == "deposit" and (t.get("reseller_id") or "") == ctx.tenant]
        rows.sort(key=lambda t: t["created_at"], reverse=True)
        title, icon = "🧾 <b>Deposit history</b>", {"success": "✅", "failed": "❌", "verification": "🕒", "pending": "⏳"}
        chunk, page, pages = _page_of(rows, page)
        body = "\n".join(f"{icon.get(t['status'], '•')} <b>{m.esc(utils.inr(t['amount']))}</b> · {utils.fmt_dt(t['created_at'])}"
                         + (f"\n    ↳ {m.esc(t.get('fail_reason') or t.get('fail_type', ''))}" if t["status"] == "failed" else "")
                         for t in chunk)
    else:
        rows = [w for w in db.query_equal("/withdrawals", "user_id", str(uid)) if (w.get("reseller_id") or "") == ctx.tenant]
        rows.sort(key=lambda w: w["created_at"], reverse=True)
        title, icon = "📤 <b>Withdraw history</b>", {"approved": "✅", "rejected": "❌", "pending": "🕒"}
        chunk, page, pages = _page_of(rows, page)
        body = "\n".join(f"{icon.get(w['status'], '•')} <b>{m.esc(utils.inr(w['amount']))}</b> → <code>{m.esc(w['upi_id'])}</code>"
                         f" · {utils.fmt_dt(w['created_at'])}"
                         + (f"\n    ↳ {m.esc(w['reason'])}" if w.get("reason") else "") for w in chunk)
    text = f"{title}\n{m.LINE}\n\n" + (body or "Nothing here yet.") + (f"\n\nPage {page + 1}/{pages}" if pages > 1 else "")
    screen(ctx, uid, text, m.kb(*_pager(f"hist:{kind}", page, pages), [("⬅️ Wallet", "wallet"), ("🏠 Home", "home")]), cq=cq)


# ================================================================== daily check-in & spin wheel
def _reward_gate(ctx, user, kind, cq):
    """Returns the settings if the user may claim now, otherwise shows why and returns None."""
    uid = user["chat_id"]
    st = rewards.settings(kind)
    label = rewards.LABEL[kind]
    if not st["enabled"]:
        screen(ctx, uid, f"😴 <b>{label}</b> is not available right now.", m.kb([("🏠 Home", "home")]), cq=cq)
        return None
    if rewards.claimed_today(skey(ctx, uid), kind):
        wait = rewards.fmt_wait(rewards.seconds_to_reset())
        screen(ctx, uid, f"✅ <b>{label}</b>\n" + m.LINE + "\n\nYou already claimed today's reward.\n"
                         f"⏳ Come back in <b>{wait}</b> — it resets at <b>12:00 AM</b>.\n\n"
                         "💡 Don't forget to use your bonus on a purchase!",
               m.kb([("🛒 Purchase", "buy"), ("🏠 Home", "home")]), cq=cq)
        return None
    return st


def show_checkin(ctx, user, cq=None):
    st = _reward_gate(ctx, user, "checkin", cq)
    if not st:
        return
    screen(ctx, user["chat_id"], "📅 <b>Daily Check-in</b>\n" + m.LINE +
           f"\n\nCheck in every day and win a random bonus between <b>{m.esc(utils.inr(st['min']))}</b> and "
           f"<b>{m.esc(utils.inr(st['max']))}</b>!\n⏳ Bonus is valid for <b>{st['expire_minutes']} minutes</b> — "
           "use it on any purchase.",
           m.kb([("🎁 Claim now", "cin_go")], [("🏠 Home", "home")]), cq=cq)


def show_spin(ctx, user, cq=None):
    st = _reward_gate(ctx, user, "spin", cq)
    if not st:
        return
    screen(ctx, user["chat_id"], "🎡 <b>Spin the Wheel</b>\n" + m.LINE +
           f"\n\nOne free spin every day! Win between <b>{m.esc(utils.inr(st['min']))}</b> and "
           f"<b>{m.esc(utils.inr(st['max']))}</b>.\n⏳ Bonus is valid for <b>{st['expire_minutes']} minutes</b>.",
           m.kb([("🎡 Spin now!", "spin_go")], [("🏠 Home", "home")]), cq=cq)


def _won_text(kind, reward):
    until = utils.fmt_dt(reward["expires_at"])
    return (f"🎉 <b>You won {m.esc(utils.inr(reward['amount']))}!</b>\n" + m.LINE +
            f"\n\n💰 Added to your wallet as <b>bonus</b>.\n⏳ Valid for <b>{reward['expire_minutes']} minutes</b> (until {until}).\n\n"
            "🛒 Use it on any purchase before it expires!")


def do_checkin(ctx, user, cq):
    uid = user["chat_id"]
    status, reward = rewards.claim(skey(ctx, uid), "checkin", user)
    if status != "ok":
        return show_checkin(ctx, user, cq)
    answer(ctx, cq, "🎉 Claimed!")
    drop_screen(ctx, uid)
    send(ctx, uid, "📅 <b>Daily Check-in done!</b>\n\n" + _won_text("checkin", reward),
         m.kb([("🛒 Purchase now", "buy"), ("💰 Wallet", "wallet")]), effect=EFFECT_PARTY)
    log_action("checkin.claimed", utils.inr(reward["amount"]), target=str(uid), source="bot", actor=f"user:{uid}")


def do_spin(ctx, user, cq):
    uid = user["chat_id"]
    status, reward = rewards.claim(skey(ctx, uid), "spin", user)
    if status != "ok":
        return show_spin(ctx, user, cq)
    answer(ctx, cq, "🎡 Good luck!")
    drop_screen(ctx, uid)
    st = rewards.settings("spin")
    anim = None
    try:
        values, win = wheel.pick_values(reward["amount"], st["min"], st["max"])
        gif = wheel.render_gif(values, win)
        anim = ctx.tg.send_media("animation", uid, gif, "🎡 <b>Spinning…</b> good luck! 🍀", filename="spin.gif")
        time.sleep(4.6)  # the GIF spins ~2s and then rests on the winning slice
    except Exception as e:  # animation is a nicety: never lose the reward because of it
        log.info("spin animation failed: %s", e)
    send(ctx, uid, "🎡 <b>Spin complete!</b>\n\n" + _won_text("spin", reward),
         m.kb([("🛒 Purchase now", "buy"), ("💰 Wallet", "wallet")]), effect=EFFECT_PARTY)
    if anim:
        ctx.tg.delete_message(uid, anim["message_id"])
    log_action("spin.claimed", utils.inr(reward["amount"]), target=str(uid), source="bot", actor=f"user:{uid}")


def sweep_rewards():
    """Called from the housekeeping loop: warn about bonuses that are about to expire."""
    def tell(r):
        left = max(1, (int(r["expires_at"]) - utils.now()) // 60)
        MAIN.tg.send_message(r["user_id"], "⏰ <b>Your bonus is about to expire!</b>\n" + m.LINE +
                             f"\n\n💰 <b>{m.esc(utils.inr(r['remaining']))}</b> expires in about <b>{left} min</b>.\n"
                             "Use it now on a purchase before it's gone 👇",
                             m.kb([("🛒 Purchase now", "buy")]))
    return rewards.sweep(tell)


# ================================================================== refer & earn
def _bot_username(ctx):
    try:
        return ctx.tg.get_me().get("username", "")
    except TelegramError:
        return (ctx.reseller or {}).get("bot_username", "")


def show_refer(ctx, user, cq=None):
    """Intro (admin-editable message + image) with a Confirm button that opens the personal dashboard."""
    uid = user["chat_id"]
    rich = settings.get_rich("referral")
    sid = skey(ctx, uid)
    pct = referral.percent_for(sid, ctx.tenant)
    s = referral.stats_for(sid)
    text, _mode = m.fill(rich["text"], rich["format"], percent=m.pct(pct), count=s["count"], earned=utils.inr(s["earned"]),
                         link=referral.link_for(_bot_username(ctx), uid))
    if rich["format"] == "text":
        text = m.esc(text)
    media = ("photo", rich["image"]) if rich["image"] else None
    screen(ctx, uid, text[:1020] if media else text, m.kb([("✅ Confirm — show my link", "refer_go")], [("🏠 Home", "home")]),
           media=media, cq=cq)


def show_refer_dashboard(ctx, user, cq=None):
    uid = user["chat_id"]
    sid = skey(ctx, uid)
    s = referral.stats_for(sid)
    pct = referral.percent_for(sid, ctx.tenant)
    link = referral.link_for(_bot_username(ctx), uid)
    b = wallet.balance(sid)
    text = ("🎁 <b>Your Refer &amp; Earn</b>\n" + m.LINE + f"\n\n🔗 <b>Your link</b>\n<code>{link}</code>\n\n"
            f"👥 Total referrals: <b>{s['count']}</b>  (✅ {s['converted']} bought)\n"
            f"💰 Total earned: <b>{m.esc(utils.inr(s['earned']))}</b>\n"
            f"👛 Refer Money balance: <b>{m.esc(utils.inr(b['refer']))}</b>\n\n"
            f"♾️ You earn <b>{m.pct(pct)}%</b> on <b>every</b> purchase your friends make — for life.")
    share = f"url:https://t.me/share/url?url={link}&text=Get%20premium%20license%20keys%20here%20%F0%9F%94%A5"
    screen(ctx, uid, text, m.kb([("📤 Share my link", share)], [("👥 My referrals", "rl:0")],
                                [("💸 Withdraw", "wdr"), ("🏠 Home", "home")]), cq=cq)


def show_referrals(ctx, user, page, cq):
    uid = user["chat_id"]
    rows = sorted(db.get_list(f"/referrals/{skey(ctx, uid)}"), key=lambda r: r.get("joined_at", 0), reverse=True)
    chunk, page, pages = _page_of(rows, page)
    buttons = [[(f"{'✅' if r.get('status') == 'converted' else '⏳'} {r['user'].get('full_name', '')[:28] or 'User'}",
                 f"rd:{r['referred_id']}:0")] for r in chunk]
    text = ("👥 <b>My referrals</b>\n" + m.LINE + "\n\n" + (f"Total: <b>{len(rows)}</b> · tap a name for details\n"
            "✅ bought a plan   ⏳ joined, not bought yet" if rows else "Nobody joined with your link yet.\nShare it and start earning 🚀"))
    screen(ctx, uid, text, m.kb(*buttons, *_pager("rl", page, pages), [("⬅️ Back", "refer_go")]), cq=cq)


def show_referral_detail(ctx, user, ref_chat, page, cq):
    uid = user["chat_id"]
    sid = skey(ctx, uid)
    rec = db.get(f"/referrals/{sid}/{ref_chat}")
    if not rec:
        return show_referrals(ctx, user, 0, cq)
    earn = [e for e in db.query_equal("/referral_earnings", "referrer", sid) if e["referred"] == str(ref_chat)]
    earn.sort(key=lambda e: e["ts"], reverse=True)
    chunk, page, pages = _page_of(earn, page)
    lines = [f"🙋 <b>{m.esc(rec['user'].get('full_name') or 'User')}</b>", m.LINE,
             "Status: " + ("✅ <b>Bought a plan</b>" if rec.get("status") == "converted" else "⏳ <b>Not bought yet</b>"),
             f"💰 Earned from this friend: <b>{m.esc(utils.inr(sum(e['commission'] for e in earn)))}</b>", ""]
    lines += [f"📱 <b>{m.esc(e['app_name'])}</b> — {m.esc(e['plan_label'])}\n    💸 Commission: <b>{m.esc(utils.inr(e['commission']))}</b>"
              f" ({m.pct(e['percent'])}%)" for e in chunk] or ["No purchases yet — you'll earn when they buy."]
    screen(ctx, uid, "\n".join(lines), m.kb(*_pager(f"rd:{ref_chat}", page, pages), [("⬅️ Back", "rl:0")]), cq=cq)


# ================================================================== keys / support / reseller info
def show_keys(ctx, user, cq=None):
    uid = user["chat_id"]
    rows = [l for l in db.query_equal("/licenses", "chat_id", str(uid))
            if not l.get("deleted") and (l.get("reseller_id") or "") == ctx.tenant]
    rows.sort(key=lambda l: l.get("created_at", 0), reverse=True)
    if not rows:
        return screen(ctx, uid, "🔑 <b>My License Keys</b>\n" + m.LINE + "\n\nYou don't have a license yet.",
                      m.kb([("🛒 Purchase", "buy"), ("🏠 Home", "home")]), cq=cq)
    icon = {"active": "🟢 Active", "expired": "🔴 Expired", "revoked": "⛔ Revoked"}
    parts = []
    for l in rows[:8]:
        st = lic_svc.status_of(l)
        parts.append(f"📱 <b>{m.esc(l.get('app_name'))}</b> — {m.esc(l.get('validity_label'))}\n"
                     f"<code>{l['key']}</code>\n{icon[st]} · until {utils.fmt_dt(l['expires_at'])}\n"
                     f"📲 Device: {'linked' if l.get('device_id') else 'not linked yet'}")
    screen(ctx, uid, "🔑 <b>My License Keys</b>\n" + m.LINE + "\n\n" + "\n\n".join(parts) + "\n\nTap a key to copy it.",
           m.kb([("🛒 Purchase", "buy"), ("🏠 Home", "home")]), cq=cq)


def show_support(ctx, user, cq=None):
    name = settings.bot_settings()["support_username"] if ctx.is_main else (ctx.reseller or {}).get("support_username", "")
    custom = ("" if ctx.is_main else (ctx.reseller or {}).get("support_text", "")).strip()  # reseller's own text / HTML
    if name or custom:
        body = custom or "Need help? Our team is one tap away."
        rows = ([[("💬 Contact Support", f"url:https://t.me/{name}")]] if name else []) + [[("🏠 Home", "home")]]
        return screen(ctx, user["chat_id"], "🛟 <b>Customer Support</b>\n" + m.LINE + "\n\n" + body, m.kb(*rows), cq=cq)
    screen(ctx, user["chat_id"], "🛟 Support is not configured yet. Please try again later.", m.kb([("🏠 Home", "home")]), cq=cq)


def show_reseller_info(ctx, user, cq=None):
    rich = settings.get_rich("reseller")
    support = settings.bot_settings()["support_username"]
    text = rich["text"] if rich["format"] != "text" else m.esc(rich["text"])
    rows = [[("💬 Contact admin", f"url:https://t.me/{support}")]] if support else []
    media = ("photo", rich["image"]) if rich["image"] else None
    screen(ctx, user["chat_id"], text[:1020] if media else text, m.kb(*rows, [("🏠 Home", "home")]), media=media, cq=cq)


# ================================================================== "My Payment Collection" (UPI owners)
def show_collection(ctx, user, cq=None):
    from services import collection
    uid = user["chat_id"]
    ids = collection.owned_upi_ids(uid, ctx.tenant)
    if not ids:
        return screen(ctx, uid, "💵 No UPI ID is linked to this chat.", m.kb([("🏠 Home", "home")]), cq=cq)
    mon, cnt = collection.money(ids, ctx.tenant), collection.counts(ids, ctx.tenant)
    text = ("💵 <b>My Payment Collection</b>\n" + m.LINE +
            f"\n\n📅 Today: <b>{m.esc(utils.inr(mon['today']['amount']))}</b> ({mon['today']['count']})"
            f"\n🕘 Yesterday: <b>{m.esc(utils.inr(mon['yesterday']['amount']))}</b> ({mon['yesterday']['count']})"
            f"\n🏆 All time: <b>{m.esc(utils.inr(mon['all']['amount']))}</b> ({mon['all']['count']})\n\n"
            f"⏳ Pending: <b>{cnt['pending']}</b>   ❌ Failed: <b>{cnt['failed']}</b>   ✅ Success: <b>{cnt['success']}</b>")
    screen(ctx, uid, text, m.kb(
        [(f"⏳ Pending ({cnt['pending']})", "col:pending:0"), (f"❌ Failed ({cnt['failed']})", "col:failed:0")],
        [(f"✅ Success ({cnt['success']})", "col:success:0")], [("🔄 Refresh", "collect"), ("🏠 Home", "home")]), cq=cq)


def show_collection_list(ctx, user, bucket, page, cq):
    from services import collection
    uid = user["chat_id"]
    ids = collection.owned_upi_ids(uid, ctx.tenant)
    if not ids or bucket not in collection.BUCKETS:
        return show_collection(ctx, user, cq)
    rows = collection.rows(ids, bucket, ctx.tenant)
    chunk, page, pages = _page_of(rows, page)
    body = "\n\n".join(
        f"<b>{page * PAGE + i + 1}. {m.esc(t['user'].get('full_name'))}</b>\n   💰 {m.esc(utils.inr(t['amount']))}"
        f" · 🆔 <code>{m.esc(t['user'].get('chat_id'))}</code>\n   🧾 <code>{t['id']}</code> · {utils.fmt_dt(t['created_at'])}"
        + (" · 🕒 verifying" if t["status"] == "verification" else "") for i, t in enumerate(chunk))
    text = f"{collection.LABEL[bucket]} <b>payments</b> ({len(rows)})\n{m.LINE}\n\n" + (body or "Nothing here.")
    screen(ctx, uid, text[:3900], m.kb(*_pager(f"col:{bucket}", page, pages), [("⬅️ Back", "collect")]), cq=cq)


# ================================================================== admin chat tools (super admin / 2nd admin)
ADMIN_COMMANDS = [("help", "All admin commands"), ("send", "Send a message to a chat id"),
                  ("broadcast", "Broadcast to all users (preview + confirm)"),
                  ("pending", "Pending payments by UPI id")]
ADMIN_HELP = (
    "🛠 <b>Admin commands</b>\n" + m.LINE + "\n\n"
    "/help — show this list\n\n"
    "/send <code>chat_id message</code> — send a message to one user\n"
    "   e.g. <code>/send 123456789 Your key is ready!</code>\n\n"
    "/broadcast — send a message to <b>all users</b>. Send the command, then send your message (text, or a photo / "
    "video / file with a caption). You'll see a <b>preview</b>, how many users will receive it, and "
    "<b>Confirm</b> / <b>Cancel</b> buttons.\n\n"
    "/pending — total pending payments grouped by UPI id\n\n"
    "/cancel — stop what you are doing\n\n"
    "📎 <b>File IDs:</b> send me any photo, video, APK or file and I reply with its <b>file id</b> — paste it in the "
    "panel (Apps → Video ID / Setup video ID / App (APK) ID, or Seller → Submissions).\n\n"
    "👤 <b>User commands:</b> /start /buy /wallet /refer /checkin /spin /keys /support")


def admin_command(ctx, user, cmd, text, msg):
    """Returns True if the message was an admin command."""
    uid = user["chat_id"]
    if cmd == "/help":
        send(ctx, uid, ADMIN_HELP)
        return True
    if cmd == "/send":
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not re.fullmatch(r"-?\d{3,16}", parts[1]):
            send(ctx, uid, "Usage: <code>/send chat_id message</code>\nExample: <code>/send 123456789 Hello!</code>")
            return True
        try:
            ctx.tg.send_message(parts[1], m.esc(parts[2]))
            send(ctx, uid, f"✅ Sent to <code>{parts[1]}</code>.")
            log_action("bot.send", f"to {parts[1]}", target=parts[1], source="telegram", actor=f"admin:{uid}", role="admin")
        except TelegramError as e:
            send(ctx, uid, f"❌ Could not send: {m.esc(e.description)}")
        return True
    if cmd == "/pending":
        from services import collection
        groups = collection.pending_by_upi()
        if not groups:
            send(ctx, uid, "✅ <b>No pending payments.</b>")
            return True
        tot_n = sum(g["awaiting_payment"] + g["verification"] for g in groups)
        tot_a = round(sum(g["awaiting_amount"] + g["verification_amount"] for g in groups), 2)
        lines = ["⏳ <b>Pending payments</b>", m.LINE, f"Total: <b>{tot_n}</b> payments · <b>{m.esc(utils.inr(tot_a))}</b>", ""]
        for g in groups:
            lines.append(f"🏦 <code>{m.esc(g['upi_id'])}</code>" + (" · reseller" if g["reseller_id"] else ""))
            lines.append(f"   🕒 In verification: <b>{g['verification']}</b> · {m.esc(utils.inr(g['verification_amount']))}")
            lines.append(f"   ⏳ Waiting to pay: <b>{g['awaiting_payment']}</b> · {m.esc(utils.inr(g['awaiting_amount']))}")
        send(ctx, uid, "\n".join(lines))
        return True
    if cmd == "/broadcast":
        set_state(ctx, uid, name="bc_compose")
        send(ctx, uid, "📣 <b>New broadcast</b>\n" + m.LINE + "\n\nSend the message now — text, or a photo / video / file with a "
                       "caption. Formatting (bold, italic, links) is kept.\n\nSend /cancel to abort.")
        return True
    return False


def message_to_broadcast(msg):
    """Convert an admin's Telegram message into a broadcast message dict."""
    from bot import richtext
    from services import broadcast
    text = msg.get("text") or msg.get("caption") or ""
    ents = msg.get("entities") or msg.get("caption_entities") or []
    media = {}
    if msg.get("photo"):
        media = {"kind": "photo", "ref": msg["photo"][-1]["file_id"]}
    elif msg.get("video"):
        media = {"kind": "video", "ref": msg["video"]["file_id"]}
    elif msg.get("animation"):
        media = {"kind": "animation", "ref": msg["animation"]["file_id"]}
    elif msg.get("document"):
        media = {"kind": "document", "ref": msg["document"]["file_id"]}
    elif not text:
        raise ApiError("Send text, a photo, a video or a file.")
    return broadcast.clean_message({"text": richtext.to_html(text, ents), "format": "html", "media": media})


def on_broadcast_compose(ctx, user, msg):
    from services import broadcast
    uid = user["chat_id"]
    try:
        message = message_to_broadcast(msg)
    except ApiError as e:
        return send(ctx, uid, f"⚠️ {m.esc(e.message)}\nSend another message or /cancel.")
    aud = {"type": "all"}
    n = broadcast.audience_count("admin", aud)
    try:  # preview exactly as users will see it
        broadcast._send_one({"message": message, "owner": "admin", "plan_buttons": []}, uid)
    except TelegramError as e:
        return send(ctx, uid, f"⚠️ Telegram rejected this message: {m.esc(e.description)}\nFix it and send again, or /cancel.")
    set_state(ctx, uid, name="bc_confirm", message=message, n=n)
    send(ctx, uid, f"👆 <b>Preview</b>\n{m.LINE}\n\n👥 This will be sent to <b>{n}</b> user{'s' if n != 1 else ''}.\n\nSend it now?",
         m.kb([("✅ Confirm & send", "bcgo"), ("❌ Cancel", "bcno")]))


def on_broadcast_button(ctx, cq, go):
    from services import broadcast
    uid = cq["from"]["id"]
    st = get_state(ctx, uid)
    if st.get("name") != "bc_confirm":
        return answer(ctx, cq, "This broadcast is no longer pending.", True)
    clear_state(ctx, uid)
    if not go:
        answer(ctx, cq, "Cancelled")
        return ctx.tg.edit_text(uid, cq["message"]["message_id"], "❌ Broadcast cancelled.")
    b = broadcast.create("admin", "Admin", st["message"], {"type": "all"}, actor=f"telegram:{uid}")
    broadcast.start(b["id"])
    answer(ctx, cq, "🚀 Sending…")
    ctx.tg.edit_text(uid, cq["message"]["message_id"],
                     f"🚀 <b>Broadcast started</b> to <b>{st['n']}</b> users.\nTrack it in the panel → Broadcast "
                     f"(<code>{b['id']}</code>).")
    log_action("broadcast.started", f"{st['n']} users (from Telegram)", target=b["id"], source="telegram",
               actor=f"admin:{uid}", role="admin")


def admin_file_id(ctx, uid, msg):
    """An admin sent media: reply with its file id (so it can be pasted in the panel)."""
    found = None
    for kind, label, hint in (("video", "Video", "Apps → <b>Video ID</b> / <b>Setup video ID</b>"),
                              ("animation", "GIF", "a broadcast / message media field"),
                              ("document", "File / APK", "Apps → <b>App (APK) ID</b>, or Seller → Submissions → APK id"),
                              ("audio", "Audio", "a broadcast media field"),
                              ("video_note", "Video note", ""), ("voice", "Voice", ""), ("sticker", "Sticker", "")):
        if msg.get(kind):
            found = (label, msg[kind]["file_id"], hint, msg[kind].get("file_size"))
            break
    if msg.get("photo"):
        ph = msg["photo"][-1]
        found = ("Photo", ph["file_id"], "a message image field (welcome / referral / broadcast)", ph.get("file_size"))
    if not found:
        return False
    label, fid, hint, size = found
    send(ctx, uid, f"📎 <b>{label} file id</b>\n{m.LINE}\n\n<code>{fid}</code>\n\n👆 Tap to copy."
         + (f"\nPaste it in {hint}." if hint else "")
         + (f"\n📦 Size: {size / 1048576:.1f} MB" if size else ""))
    return True


# ================================================================== dispatch
def run_action(ctx, user, action, cq=None):
    uid = user["chat_id"]
    if action == "support":
        return show_support(ctx, user, cq)
    if action == "home":
        return show_home(ctx, uid, user, cq)
    if action == "reseller" and ctx.is_main:
        return show_reseller_info(ctx, user, cq)
    if action == "collect":
        return show_collection(ctx, user, cq)
    if is_locked(ctx):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if action == "buy":
        return show_categories(ctx, uid, cq)
    return {"wallet": show_wallet, "refer": show_refer, "checkin": show_checkin, "spin": show_spin,
            "keys": show_keys}[action](ctx, user, cq)


def on_start(ctx, user, frm, text):
    uid = user["chat_id"]
    clear_state(ctx, uid)
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if arg.startswith("sl_") and ctx.is_main:  # a seller connecting their Telegram to the seller panel
        s = sellers.link_telegram(arg[3:], frm, user.get("profile_file_id", ""))
        if s:
            return send(ctx, uid, f"✅ <b>Telegram connected!</b>\nHi <b>{m.esc(s['name'])}</b> — you'll now get app reviews, "
                                  "sales and withdrawal updates here.", kbd(ctx, uid))
        return send(ctx, uid, "⚠️ This link expired. Open the seller panel and tap <b>Connect Telegram</b> again.")
    if arg.startswith("ref_") and arg[4:].isdigit() and user.get("_new"):
        db.set(f"/pending_ref/{skey(ctx, uid)}", arg[4:])
    menu_hint(ctx, uid)
    if gate(ctx, uid):
        finish_onboarding(ctx, uid, user)
        show_home(ctx, uid, user)


def process_update(update, ctx=MAIN):
    if "message" in update:
        on_message(ctx, update["message"])
    elif "callback_query" in update:
        on_callback(ctx, update["callback_query"])


def finish_fail(ctx, msg, st):
    frm, chat = msg["from"], msg["chat"]["id"]
    reason = (msg.get("text") or "").strip()
    if reason == "/cancel":
        clear_state(ctx, frm["id"])
        return send(ctx, chat, "Okay, nothing changed.")
    try:
        payments.fail_txn(st["txn_id"], reason, actor_name(frm), "telegram")
        send(ctx, chat, f"❌ Txn <code>{st['txn_id']}</code> marked as failed. The user was notified.")
    except ApiError as e:
        send(ctx, chat, f"⚠️ {m.esc(e.message)}")
    clear_state(ctx, frm["id"])


def actor_name(frm):
    return (f"@{frm['username']}" if frm.get("username") else frm.get("first_name", "admin")) + f" (tg:{frm['id']})"


def on_admin_button(ctx, cq, action, txn_id):
    chat_id, frm = cq["message"]["chat"]["id"], cq["from"]
    txn = payments.get_txn(txn_id)
    if not txn or not payments.can_moderate(chat_id, txn):
        return answer(ctx, cq, "You are not allowed to do this.", True)
    if txn["status"] != "verification":
        return answer(ctx, cq, f"Already {txn['status']}.", True)
    if action == "av":
        try:
            payments.verify_txn(txn_id, actor_name(frm), "telegram")
            answer(ctx, cq, "Verified ✅ — sent to the user.")
        except ApiError as e:
            answer(ctx, cq, e.message, True)
    else:
        set_state(ctx, frm["id"], name="fail_reason", txn_id=txn_id, chat_id=str(chat_id))
        answer(ctx, cq)
        try:
            ctx.tg.send_message(chat_id, f"✍️ Enter the <b>reason</b> for failing txn <code>{txn_id}</code>:",
                                {"force_reply": True, "selective": True, "input_field_placeholder": "Reason…"},
                                reply_to=cq["message"]["message_id"])
        except TelegramError:
            pass


def on_message(ctx, msg):
    frm, chat = msg.get("from") or {}, msg["chat"]
    if not frm or frm.get("is_bot"):
        return
    uid = frm["id"]
    if chat["type"] != "private":  # groups: only the admin's "fail reason" reply is handled
        st = get_state(ctx, uid)
        if st.get("name") == "fail_reason" and msg.get("text") and str(chat["id"]) == st.get("chat_id"):
            finish_fail(ctx, msg, st)
        return
    allowed, warn, wait = spam.check((ctx.key, uid))
    if not allowed:
        if warn:
            send(ctx, uid, f"🚫 <b>Slow down!</b> Too many messages. Try again in {wait}s.")
        return
    user = touch_user(ctx, frm)
    text = (msg.get("text") or "").strip()
    st = get_state(ctx, uid)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    admin = ctx.is_main and is_admin_chat(uid)

    if cmd == "/start":
        return on_start(ctx, user, frm, text)
    if cmd == "/cancel":
        clear_state(ctx, uid)
        return send(ctx, uid, "Cancelled. Use the menu below to continue.", kbd(ctx, uid))
    if admin and cmd in ("/help", "/send", "/broadcast", "/pending"):
        clear_state(ctx, uid)
        return admin_command(ctx, user, cmd, text, msg)

    action = m.MENU_ACTIONS.get(text)
    if action:
        clear_state(ctx, uid)
        ctx.tg.delete_message(uid, msg["message_id"])  # keep the chat clean: the menu tap disappears
        if action != "support" and action != "collect" and not gate(ctx, uid):
            return
        return run_action(ctx, user, action)

    name = st.get("name")
    if name == "fail_reason":
        return finish_fail(ctx, msg, st)
    if name == "bc_compose" and admin:
        return on_broadcast_compose(ctx, user, msg)
    if name == "await_screenshot":
        return on_screenshot(ctx, user, msg, st)
    if name == "await_utr":
        if text:
            return on_utr(ctx, user, text, st)
        return send(ctx, uid, "Please send the 12-digit UTR as text (or /cancel).")
    if name == "await_deposit" and text:
        return on_deposit_amount(ctx, user, text)
    if name == "await_wd_upi" and text:
        return on_wd_upi(ctx, user, text)
    if name == "await_wd_amount" and text:
        return on_wd_amount(ctx, user, text, st)
    if admin and admin_file_id(ctx, uid, msg):
        return
    send(ctx, uid, "Use the menu below 👇", kbd(ctx, uid))


def on_callback(ctx, cq):
    frm, msg = cq["from"], cq.get("message")
    if not msg or frm.get("is_bot"):
        return
    uid, data = frm["id"], cq.get("data", "")
    allowed, warn, wait = spam.check((ctx.key, uid))
    if not allowed:
        return answer(ctx, cq, f"Slow down ✋ ({wait}s)")
    if data.startswith(("av:", "af:")):
        return on_admin_button(ctx, cq, data[:2], data[3:])
    if data in ("bcgo", "bcno"):
        if ctx.is_main and is_admin_chat(uid):
            return on_broadcast_button(ctx, cq, data == "bcgo")
        return answer(ctx, cq, "Not allowed.", True)
    if msg["chat"]["type"] != "private":
        return answer(ctx, cq)
    user = touch_user(ctx, frm)
    head, _, arg = data.partition(":")
    if head == "noop":
        return answer(ctx, cq)
    if head == "verify_join":
        _joined_until.pop((ctx.tenant, uid), None)
        miss = missing_channels(ctx, uid, use_cache=False)
        if miss:
            return answer(ctx, cq, "You haven't joined: " + ", ".join(c["title"] for c in miss)[:150], True)
        answer(ctx, cq, "Verified ✅")
        finish_onboarding(ctx, uid, user)
        return show_home(ctx, uid, user, cq)
    if head in ("support", "collect", "col"):
        answer(ctx, cq)
        if head == "col":
            bucket, _, page = arg.partition(":")
            return show_collection_list(ctx, user, bucket, int(page or 0), cq)
        return run_action(ctx, user, head, cq)
    if not gate(ctx, uid, cq):
        return answer(ctx, cq)
    if head not in ("paid", "cancel", "ref", "cin_go", "spin_go", "dep", "wdr"):
        answer(ctx, cq)

    if head in ("home", "buy", "wallet", "refer", "checkin", "spin", "keys", "reseller"):
        return run_action(ctx, user, head, cq)
    if is_locked(ctx) and head in ("cat", "ap", "pl", "pc", "pw", "by"):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if head == "cat":
        show_category(ctx, uid, arg, cq)
    elif head == "ap":
        show_app(ctx, uid, arg, None, cq)
    elif head == "pl":
        p = catalog.get_plan(arg)
        show_app(ctx, uid, p["app_id"], arg, cq) if p else show_categories(ctx, uid, cq)
    elif head == "pc":
        plan_continue(ctx, user, arg, cq)
    elif head == "pw":
        pid, _, w = arg.partition(":")
        confirm_order(ctx, user, pid, w == "1", cq)
    elif head == "by":
        pid, _, w = arg.partition(":")
        do_buy(ctx, user, pid, w == "1", cq)
    elif head == "paid":
        on_paid(ctx, uid, arg, cq)
    elif head == "cancel":
        on_cancel_txn(ctx, uid, arg, cq)
    elif head == "ref":
        on_refresh(ctx, uid, arg, cq, msg)
    elif head == "dep":
        start_deposit(ctx, user, cq)
    elif head == "wdr":
        start_withdraw(ctx, user, cq)
    elif head == "hist":
        kind, _, page = arg.partition(":")
        show_history(ctx, user, kind, int(page or 0), cq)
    elif head == "cin_go":
        do_checkin(ctx, user, cq)
    elif head == "spin_go":
        do_spin(ctx, user, cq)
    elif head == "refer_go":
        show_refer_dashboard(ctx, user, cq)
    elif head == "rl":
        show_referrals(ctx, user, int(arg or 0), cq)
    elif head == "rd":
        rc, _, page = arg.partition(":")
        show_referral_detail(ctx, user, rc, int(page or 0), cq)
