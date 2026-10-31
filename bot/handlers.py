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
from config import Config
from bot.antispam import spam
from bot.context import MAIN
from bot.tg import EFFECT_PARTY, TelegramError
from core import admin_gate, db, utils
from core.audit import log_action
from core.errors import ApiError
from services import bans, botdesign, botui, catalog, delivery, faqs, media, notify, payments, referral, resellers, rewards, sellers, settings, shortener, tgchannel, tgfiles, users, wallet, wheel
from services import licenses as lic_svc

log = logging.getLogger("bot")
UTR_RE = re.compile(r"^\d{12}$")
UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z0-9.\-]{2,32}$")
_joined_until = {}  # (tenant, user_id) -> epoch until which the force-join check is cached
STATE_TTL = 3600


# ================================================================== infrastructure
# One update at a time per user (no races between a double tap and a text message) and ONE button action per tap burst:
# if a user hammers several buttons, only the first is executed, the rest are silently dropped.
CLICK_GAP = 0.9  # seconds after the last accepted click during which further clicks are ignored
_user_locks = {}
_locks_guard = threading.Lock()
_last_click = {}


def _lock_for(key):
    with _locks_guard:
        lk = _user_locks.get(key)
        if lk is None:
            if len(_user_locks) > 5000:
                for k in [k for k, v in _user_locks.items() if not v.locked()]:
                    _user_locks.pop(k, None)
                    _last_click.pop(k, None)
            lk = _user_locks[key] = threading.Lock()
        return lk


def reset_gates():
    """Tests: forget the click cool-downs."""
    _last_click.clear()


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


def screen(ctx, uid, text, markup=None, media=None, cq=None, effect=None, same_media=False, sk=None):
    """Show `text` as THE current screen: edit the tapped message in place when possible, otherwise send a new
    message and delete the previous screen. `media` = (kind, ref) puts a photo/video on top.
    `sk` = name of an editable screen (services/botdesign.py): the admin's / reseller's message goes on top and their picture is used."""
    if sk:
        text, dm = botdesign.decorate(ctx, sk, text)
        if dm:
            media = dm
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
            if media and c_media and (same_media or cur.get("img") == media[1]):
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
    db.set(f"/bot_scr/{key}", {"mid": new_mid, "img": media[1] if media and msg is not None and "photo" in msg else ""})
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
    screen(ctx, uid, text, m.kb(*rows), cq=cq, sk="join")
    return False


# ================================================================== onboarding / home
def locked_text(ctx):
    r = ctx.reseller or {}
    rich = settings.get_rich("reseller_due")
    return r.get("due_message") or rich["text"]


def is_locked(ctx):
    return bool(ctx.tenant) and resellers.is_forced(ctx.tenant)


def _seller_open(uid):
    return settings.feature("seller_signup") or sellers.seller_for_chat(uid) is not None


def kbd(ctx, uid):
    from services import collection
    return m.menu_keyboard(ctx.is_main, rewards_on(ctx), collection.is_collector(uid, ctx.tenant),
                           seller=ctx.is_main and _seller_open(uid), reseller=settings.feature("reseller_signup"),
                           refer=referral.enabled(ctx.tenant))


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


def home_markup(ctx, uid=None):
    refer_on = referral.enabled(ctx.tenant)
    btns = [("🛒 Shop", "buy"), ("💰 Wallet", "wallet")]
    if refer_on:
        btns.append(("🎁 Refer & Earn", "refer"))
    btns.append(("🔑 My Keys", "keys"))
    if rewards_on(ctx):
        btns += [("📅 Daily Check-in", "checkin"), ("🎡 Spin Wheel", "spin")]
    if ctx.is_main and uid is not None and _seller_open(uid):
        btns.append(("💼 Become Seller", "seller"))
    if ctx.is_main and settings.feature("reseller_signup"):
        btns.append(("🏪 Reseller", "reseller"))
    btns.append(("🛟 Support", "support"))
    if ctx.is_main and faqs.active():
        btns.append(("❓ FAQs", "faq:0"))
    return m.kb(*botui.grid(btns, botui.cols(ctx, "home")))


def show_home(ctx, uid, user, cq=None):
    if is_locked(ctx):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if ctx.is_main:
        s = settings.bot_settings()
        tmpl, fmt, image = s["welcome_message"], s["welcome_format"], s["welcome_image"]
    else:
        tmpl = (ctx.reseller or {}).get("welcome_message") or settings.DEFAULT_WELCOME
        fmt, image = "html", (ctx.reseller or {}).get("welcome_image") or ""
    first = (user.get("full_name") or "there").split()[0]
    text, mode = m.render_welcome(tmpl, fmt, {"first_name": first, "full_name": user.get("full_name"),
                                              "username": user.get("username"), "chat_id": uid})
    if not image and cq is None:  # a fresh /start shows the Dekho Hatke logo unless a custom welcome image is set
        image = delivery.BRAND_LOGO
    media = ("photo", image) if image else None
    if mode is None:  # plain-text welcome: screen() sends HTML, so escape it
        text = m.esc(text)
    screen(ctx, uid, text, home_markup(ctx, uid), media=media, cq=cq)


# ================================================================== purchase: categories -> apps -> plans
def show_categories(ctx, uid, cq=None):
    if is_locked(ctx):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    tree = catalog.tree(ctx.tenant)
    if not tree:
        return screen(ctx, uid, "📭 <b>Nothing to buy yet</b>\n\nNew apps are coming soon — check back later!",
                      m.kb([("🏠 Home", "home")]), cq=cq)
    btns = [(f"{c['icon']} {c['name']} ({len(c['apps'])})", f"cat:{c['id']}") for c in tree]
    rows = botui.grid(btns, botui.cols(ctx, "categories"))
    rows.append([("🏠 Home", "home")])
    screen(ctx, uid, "🛒 <b>Shop</b>\n" + m.LINE + "\n\nPick a category 👇", m.kb(*rows), cq=cq, sk="buy")


def show_category(ctx, uid, cat_id, cq=None):
    cat = next((c for c in catalog.tree(ctx.tenant) if c["id"] == cat_id), None)
    if not cat:
        return show_categories(ctx, uid, cq)
    medals = ["🥇", "🥈", "🥉"]
    rows = []
    for i, a in enumerate(cat["apps"]):
        has_trial = any(p.get("kind") == "trial" for p in catalog.plans_for(a["id"], ctx.tenant))
        badge = "🔥" if a.get("highlighted") else (medals[i] if i < 3 and int(a.get("sales_count", 0)) > 0 else "📱")
        rows.append((f"{badge} {a['name']}" + (" · 🎁 Free trial" if has_trial else ""), f"ap:{a['id']}"))
    rows = botui.grid(rows, botui.cols(ctx, "apps"))
    rows.append([("⬅️ Back", "buy"), ("🏠 Home", "home")])
    screen(ctx, uid, f"{cat['icon']} <b>{m.esc(cat['name'])}</b>\n" + m.LINE +
           "\n\n🔥 Featured and best sellers first — pick an app 👇", m.kb(*rows), cq=cq, sk="category")


def _app_and_plans(ctx, app_id):
    app = catalog.get_app(app_id)
    if not app or not catalog.purchasable(app):
        return None, []
    return app, catalog.plans_for(app_id, ctx.tenant)


def show_app(ctx, uid, app_id, selected=None, cq=None):
    app, plans = _app_and_plans(ctx, app_id)
    if not app or not plans:
        return show_categories(ctx, uid, cq)
    rows = botui.grid([(m.plan_label(p), f"pl:{p['id']}") for p in plans], botui.cols(ctx, "plans"))  # tapping a plan goes straight to the confirmation
    rows.append([("⬅️ Back", f"cat:{app.get('category_id') or catalog.OTHER_ID}"), ("🏠 Home", "home")])
    users_now = _active_users(app_id)
    text = m.app_caption(app, plans, users_now)
    media = None
    if app.get("video_id"):
        media = ("video", app["video_id"])
    elif app.get("image_url"):
        media = ("photo", app["image_url"] if app["image_url"].startswith(("http", "/media/img/")) else None)
        media = media if media[1] else None
    screen(ctx, uid, text, m.kb(*rows), media=media, cq=cq, same_media=True)


_au_cache = {}


def _active_users(app_id):
    hit = _au_cache.get(app_id)
    if hit and hit[0] > time.time():
        return hit[1]
    n = catalog.active_users(app_id)
    _au_cache[app_id] = (time.time() + 90, n)
    return n


def _gone(ctx, uid, cq):
    return screen(ctx, uid, "That plan is no longer available.", m.kb([("🛒 Shop", "buy")]), cq=cq)


def confirm_plan(ctx, user, plan_id, cq):
    """A plan was tapped: show the order summary with Yes / No right away (no second price selection)."""
    uid = user["chat_id"]
    q = payments.quote(ctx, user, plan_id)
    if not q:
        return _gone(ctx, uid, cq)
    if is_locked(ctx):
        return show_categories(ctx, uid, cq)
    screen(ctx, uid, m.confirm_text(q),
           m.kb([("✅ Yes", f"py:{plan_id}"), ("❌ No", f"ap:{q['app']['id']}")]), cq=cq, sk="confirm")


def proceed_plan(ctx, user, plan_id, cq):
    """'Yes' was tapped: a Free short-link plan starts its steps, a trial is claimed, otherwise the user picks how to pay
    (wallet / UPI -- the choice is only shown when a wallet balance exists)."""
    uid = user["chat_id"]
    q = payments.quote(ctx, user, plan_id)
    if not q:
        return _gone(ctx, uid, cq)
    if is_locked(ctx):
        return show_categories(ctx, uid, cq)
    if q["short"]:
        return start_short(ctx, user, q, cq)
    if "crypto" in q["methods"]:  # more than one way to pay (or crypto only): the user chooses
        return show_methods(ctx, user, q, cq)
    return show_upi_options(ctx, user, q, cq)


def _wallet_usable(q):
    use, _cash = q["split"]
    return not q["trial"] and q["wallet"]["total"] > 0 and use > 0 and "upi" in q["methods"]


def show_upi_options(ctx, user, q, cq):
    """UPI path: with wallet money the user picks wallet(+UPI) or all UPI; otherwise straight to the UPI request."""
    uid, plan_id = user["chat_id"], q["plan"]["id"]
    use, cash = q["split"]
    if _wallet_usable(q):
        back = f"py:{plan_id}" if "crypto" in q["methods"] else f"ap:{q['app']['id']}"
        return screen(ctx, uid, m.payment_options(q), m.kb(
            [(f"👛 Wallet −{utils.inr(use)}" + (f" + UPI {utils.inr(cash)}" if cash > 0 else " (no UPI needed)"), f"by:{plan_id}:1")],
            [(f"🏦 Pay all {utils.inr(q['price'])} by UPI", f"by:{plan_id}:0")],
            [("📖 How to pay by UPI", f"how:upi:{plan_id}")],
            [("⬅️ Back", back)]), cq=cq, sk="pay_options")
    do_buy(ctx, user, plan_id, False, cq)


def show_methods(ctx, user, q, cq):
    """UPI (manual verification) or Crypto (auto-verified), plus the how-to-pay guides."""
    uid, pid = user["chat_id"], q["plan"]["id"]
    rows = []
    if "upi" in q["methods"]:
        rows.append([("🏦 Pay with UPI", f"pu:{pid}")])
    rows.append([("🪙 Pay with Crypto (XMR) · ✅ Auto-verified", f"cr:{pid}")])
    rows.append([("📖 UPI guide", f"how:upi:{pid}"), ("📖 Crypto guide", f"how:crypto:{pid}")])
    rows.append([("⬅️ Back", f"ap:{q['app']['id']}"), ("🏠 Home", "home")])
    screen(ctx, uid, m.methods_text(q), m.kb(*rows), cq=cq, sk="methods")


def show_how(ctx, uid, kind, plan_id, cq):
    """The step-by-step guide. From the chooser it replaces the screen; from a payment card it arrives as its own message."""
    text = m.how_crypto_text() if kind == "crypto" else m.how_upi_text()
    sk = "how_crypto" if kind == "crypto" else "how_upi"
    if plan_id:
        return screen(ctx, uid, text, m.kb([("⬅️ Back", f"py:{plan_id}")], [("🏠 Home", "home")]), cq=cq, sk=sk)
    top = botdesign.top_text(ctx, sk)
    send(ctx, uid, (top + "\n\n" if top else "") + text, m.kb([("✖️ Close", "xclose")]))


def do_crypto(ctx, user, plan_id, cq):
    do_buy(ctx, user, plan_id, False, cq, method="crypto")


def start_short(ctx, user, q, cq):
    """Free plan unlocked by a short link: create the user's own link and hand it over."""
    uid = user["chat_id"]
    status, s, sh = shortener.start_for_user(ctx, user, q["plan"])
    back = m.kb([("🛒 Shop", "buy"), ("🏠 Home", "home")])
    if status == "unavailable":
        return _gone(ctx, uid, cq)
    if status == "daily_limit":
        return screen(ctx, uid, f"🚫 You reached today's limit of <b>{int(sh.get('daily_limit', 0))}</b> free key"
                                f"{'s' if int(sh.get('daily_limit', 0)) != 1 else ''} for this plan. Come back tomorrow!", back, cq=cq)
    if status == "cooldown":
        return screen(ctx, uid, "⏳ Please wait a few seconds before creating another link.", back, cq=cq)
    link = shortener.full_url(s["code"], s["steps"][0])
    total = len(s["steps"])
    mins = int(sh.get("ttl_min", 45))
    head = "ℹ️ <b>You already have an open link</b>" if status == "existing" else "🔗 <b>Your link is ready!</b>"
    text = (f"{head}\n{m.LINE}\n\n📱 <b>{m.esc(q['app']['name'])}</b> — {m.esc(q['label'])} · <b>FREE</b>\n\n"
            f"Complete all <b>{total} page{'s' if total != 1 else ''}</b> on the website. I'll message you after every page, and "
            f"your <b>license key</b> arrives here the moment the last step is done.\n\n⏱ Valid for {mins} minutes.")
    drop_screen(ctx, uid)
    send(ctx, uid, text, m.kb([("🚀 Open link", "url:" + link)], [("🏠 Home", "home")]))


def plan_continue(ctx, user, plan_id, cq):  # old "Continue" buttons still in chats
    confirm_plan(ctx, user, plan_id, cq)


def do_buy(ctx, user, plan_id, use_wallet, cq, method="upi"):
    uid = user["chat_id"]
    status, txn = payments.create_license_txn(ctx, user, plan_id, use_wallet, method)
    back = m.kb([("🛒 Shop", "buy"), ("🏠 Home", "home")])
    if status == "in_verification":
        return screen(ctx, uid, "⏳ <b>Your payment is in verification</b>\n" + m.LINE +
                      f"\n\n🧾 Txn: <code>{txn['id']}</code>\nPlease wait 2-3 hours. You'll get your key here once approved.",
                      m.kb([("🏠 Home", "home")]), cq=cq)
    if status == "daily_limit":
        return screen(ctx, uid, f"🚫 You reached today's limit of <b>{botui.max_txn_per_day(ctx.tenant)}</b> payment requests. "
                                "Try again tomorrow.", back, cq=cq)
    if status == "crypto_down":
        return screen(ctx, uid, "⚠️ Crypto payments are temporarily unavailable. Please pay with UPI or try again in a few minutes.",
                      m.kb([("⬅️ Back", f"py:{plan_id}")], [("🏠 Home", "home")]), cq=cq)
    if status == "no_upi":
        notify.send_to(notify.recipients(ctx.tenant), "⚠️ <b>No active UPI ID</b> — a user tried to pay but payments are "
                       "off. Add/enable a UPI in the panel.", client=ctx.tg)
        return screen(ctx, uid, "⚠️ Payments are temporarily unavailable. Please try again later.", back, cq=cq)
    if status == "locked":
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if status == "unavailable":
        return screen(ctx, uid, "That plan is no longer available.", back, cq=cq)
    if status == "min_upi":
        return screen(ctx, uid, f"ℹ️ UPI payments start at <b>{utils.inr(payments.Config.MIN_UPI_PAYMENT)}</b>. Your wallet balance just "
                                "changed — please pick the plan again.", back, cq=cq)
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
    if txn.get("via") != "crypto":  # crypto is verified by the chain: admins hear about it when it is paid
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
    if res == "ok":
        botui.react(ctx, uid, "proof")
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


_xchk_at = {}


def on_crypto_check(ctx, uid, txn_id, cq, msg):
    """'Check payment' on a crypto card: ask the wallet right now (at most every 8 s per order)."""
    from services import xmr
    txn = payments.get_txn(txn_id)
    if not txn or txn["user_id"] != str(uid) or txn.get("via") != "crypto":
        return answer(ctx, cq, "Order not found.", True)
    if txn["status"] == "success":
        return answer(ctx, cq, "✅ Already paid — your key was sent.", True)
    if txn["status"] != "pending":
        return answer(ctx, cq, "This payment request is closed.", True)
    now = time.time()
    if now - _xchk_at.get(txn_id, 0) < 8:
        return answer(ctx, cq, f"⏳ {m.mmss(txn['expires_at'] - utils.now())} left — checked a moment ago.")
    _xchk_at[txn_id] = now
    try:
        info = xmr.check_invoice(txn["crypto"])
    except ApiError:
        return answer(ctx, cq, "Could not check right now — it is checked automatically every few seconds.", True)
    if info["paid"]:
        answer(ctx, cq, "✅ Payment confirmed!")
        return payments.complete_crypto(txn_id, info)
    if info["seen_pico"]:
        db.update(f"/txns/{txn_id}/crypto", {"seen_pico": info["seen_pico"], "conf": info["conf"], "txids": info["txids"]})
        answer(ctx, cq, "👀 Payment detected — waiting for confirmation. Your key arrives automatically.", True)
    else:
        answer(ctx, cq, f"Nothing received yet. ⏳ {m.mmss(txn['expires_at'] - utils.now())} left.", True)
    txn = payments.get_txn(txn_id)
    try:
        if msg.get("photo"):
            ctx.tg.edit_caption(uid, msg["message_id"], m.crypto_caption(txn), m.crypto_markup(txn_id))
        else:
            ctx.tg.edit_text(uid, msg["message_id"], m.crypto_caption(txn), m.crypto_markup(txn_id))
    except TelegramError:
        pass


# ================================================================== FAQs (10 per page)
def show_faq(ctx, user, page=0, cq=None):
    uid = user["chat_id"]
    rows = faqs.active()
    if not rows:
        return screen(ctx, uid, "❓ <b>FAQs</b>\n" + m.LINE + "\n\nNo questions yet — check back soon!",
                      m.kb([("🏠 Home", "home")]), cq=cq, sk="faq")
    size = botui.faq_page_size()
    pages = max(1, -(-len(rows) // size))
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * size:(page + 1) * size]
    btns = botui.grid([(f"{page * size + i + 1}. {r['question'][:52]}", f"fq:{r['id']}:{page}") for i, r in enumerate(chunk)],
                      botui.cols(ctx, "faq"))
    screen(ctx, uid, f"❓ <b>FAQs</b>\n{m.LINE}\n\nTap a question to see the answer 👇",
           m.kb(*btns, *_pager("faq", page, pages), [("🏠 Home", "home")]), cq=cq, sk="faq")


def show_faq_answer(ctx, user, faq_id, page, cq=None):
    uid = user["chat_id"]
    f = faqs.get(faq_id)
    if not f:
        return show_faq(ctx, user, page, cq)
    faqs.record_view(f, user)
    body = f["answer"] if f.get("format") != "text" else m.esc(f["answer"])
    screen(ctx, uid, f"❓ <b>{m.esc(f['question'])}</b>\n{m.LINE}\n\n{body}"[:3900],
           m.kb([("⬅️ Back to questions", f"faq:{page}")], [("🏠 Home", "home")]), cq=cq)


# ================================================================== wallet
PAGE = 5   # every bot list (history, referrals, license keys, collection) shows 5 per page


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
              f"➕ Minimum deposit: {m.esc(utils.inr(st['min_deposit']))}"]
    if _crypto_deposit_ok(ctx):
        from services import xmr
        lines.append(f"🪙 Crypto deposit rate: <b>1 XMR = {m.esc(utils.inr(xmr.fixed_rate()))}</b> <i>(fixed)</i>")
    lines += [
              f"💸 Minimum withdrawal: {m.esc(utils.inr(referral.min_withdraw(ctx.tenant)))} <i>(Refer Money only)</i>"]
    screen(ctx, uid, "\n".join(lines), m.kb(
        [("➕ Deposit Money", "dep"), ("💸 Withdraw", "wdr")],
        [("🧾 Deposit history", "hist:dep:0"), ("📤 Withdraw history", "hist:wdr:0")],
        [("🛒 Shop", "buy"), ("🏠 Home", "home")]), cq=cq, sk="wallet")


def _crypto_deposit_ok(ctx):
    if not ctx.is_main:
        return False
    try:
        from services import xmr
        return xmr.deposit_ready()
    except Exception:  # noqa: BLE001
        return False


def start_deposit(ctx, user, cq, method=""):
    st = wallet.settings()
    uid = user["chat_id"]
    if not method and _crypto_deposit_ok(ctx):  # two ways to add money: let the user choose
        from services import xmr
        answer(ctx, cq)
        return screen(ctx, uid, "➕ <b>Deposit Money</b>\n" + m.LINE + "\n\nHow do you want to add money?\n\n"
                      "🏦 <b>UPI</b> — pay by UPI, an admin verifies your screenshot.\n"
                      f"🪙 <b>Crypto (Monero)</b> — automatic. Credited at a <b>fixed rate: 1 XMR = {m.esc(utils.inr(xmr.fixed_rate()))}</b>, "
                      "whatever the market does.",
                      m.kb([("🏦 UPI", "dep:upi"), ("🪙 Crypto (XMR)", "dep:crypto")], [("⬅️ Back", "wallet")]), cq=cq, sk="deposit")
    method = "crypto" if method == "crypto" and _crypto_deposit_ok(ctx) else "upi"
    set_state(ctx, uid, name="await_deposit", method=method)
    answer(ctx, cq)
    extra = "\n🪙 Paid in Monero at the fixed rate shown in your wallet. Your wallet is credited automatically." if method == "crypto" else ""
    screen(ctx, uid, "➕ <b>Deposit Money</b>\n" + m.LINE +
           f"\n\nSend the amount you want to add (minimum <b>{m.esc(utils.inr(st['min_deposit']))}</b>).{extra}\n"
           "Send /cancel to go back.", m.kb([("⬅️ Back", "wallet")]), cq=cq, sk="deposit")


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
    method = (get_state(ctx, uid) or {}).get("method", "upi")
    clear_state(ctx, uid)
    status, txn = payments.create_deposit_txn(ctx, user, amount, method)
    if status == "crypto_down":
        return send(ctx, uid, "⚠️ Crypto deposits are temporarily unavailable (or the amount is below the crypto minimum). "
                              "Try UPI or a different amount.")
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
               m.kb([("🛒 Shop", "buy"), ("🏠 Home", "home")]), cq=cq)
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
           m.kb([("🎁 Claim now", "cin_go")], [("🏠 Home", "home")]), cq=cq, sk="checkin")


def show_spin(ctx, user, cq=None):
    st = _reward_gate(ctx, user, "spin", cq)
    if not st:
        return
    screen(ctx, user["chat_id"], "🎡 <b>Spin the Wheel</b>\n" + m.LINE +
           f"\n\nOne free spin every day! Win between <b>{m.esc(utils.inr(st['min']))}</b> and "
           f"<b>{m.esc(utils.inr(st['max']))}</b>.\n⏳ Bonus is valid for <b>{st['expire_minutes']} minutes</b>.",
           m.kb([("🎡 Spin now!", "spin_go")], [("🏠 Home", "home")]), cq=cq, sk="spin")


def _won_text(kind, reward):
    until = utils.fmt_dt(reward["expires_at"])
    return (f"🎉 <b>You won {m.esc(utils.inr(reward['amount']))}!</b>\n" + m.LINE +
            f"\n\n💰 Added to your wallet as <b>bonus</b>.\n⏳ Valid for <b>{reward['expire_minutes']} minutes</b> (until {until}).\n\n"
            "🛒 Use it on any purchase before it expires!")


CHECKIN_FRAMES = ["🎁", "🎁✨", "🎁✨🎊", "📦💫", "📦💥", "💰🎉"]


def _animate_checkin(ctx, uid, cq):
    """A short frame animation (edits one message) while the reward is 'opened'."""
    mid = None
    try:
        mid = screen(ctx, uid, "📅 <b>Checking you in…</b>\n" + m.LINE + "\n\n▱▱▱▱▱▱\n\n🎁", None, cq=cq)
        if not mid:
            return
        for i, frame in enumerate(CHECKIN_FRAMES, start=1):
            time.sleep(0.45)
            bar = "▰" * i + "▱" * (len(CHECKIN_FRAMES) - i)
            ctx.tg.edit_text(uid, mid, f"📅 <b>Opening your daily reward…</b>\n{m.LINE}\n\n{bar}\n\n{frame}", None)
    except Exception as e:  # a nicety: never lose the reward because of it
        log.info("check-in animation failed: %s", e)


def do_checkin(ctx, user, cq):
    uid = user["chat_id"]
    status, reward = rewards.claim(skey(ctx, uid), "checkin", user)
    if status != "ok":
        return show_checkin(ctx, user, cq)
    answer(ctx, cq, "🎉 Claimed!")
    _animate_checkin(ctx, uid, cq)
    drop_screen(ctx, uid)
    send(ctx, uid, "📅 <b>Daily Check-in done!</b>\n\n" + _won_text("checkin", reward),
         m.kb([("🛒 Shop now", "buy"), ("💰 Wallet", "wallet")]), effect=EFFECT_PARTY)
    botui.react(ctx, uid, "checkin")
    botui.celebrate(ctx, uid, "checkin")
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
        values, win = wheel.pick_values(reward["amount"], st["min"], st["max"], decimals=st.get("decimals", False))
        gif = wheel.render_gif(values, win)
        anim = ctx.tg.send_media("animation", uid, gif, "🎡 <b>Spinning…</b> good luck! 🍀", filename="spin.gif")
        time.sleep(4.6)  # the GIF spins ~2s and then rests on the winning slice
    except Exception as e:  # animation is a nicety: never lose the reward because of it
        log.info("spin animation failed: %s", e)
    send(ctx, uid, "🎡 <b>Spin complete!</b>\n\n" + _won_text("spin", reward),
         m.kb([("🛒 Shop now", "buy"), ("💰 Wallet", "wallet")]), effect=EFFECT_PARTY)
    if anim:  # keep the wheel on screen a moment after the result, then remove it
        botui.later(ctx, uid, anim["message_id"], botui.get(ctx.tenant)["gifs"]["spin_delete_after"])
    botui.react(ctx, uid, "spin")
    log_action("spin.claimed", utils.inr(reward["amount"]), target=str(uid), source="bot", actor=f"user:{uid}")


def sweep_rewards():
    """Called from the housekeeping loop: warn about bonuses that are about to expire."""
    def tell(r):
        left = max(1, (int(r["expires_at"]) - utils.now()) // 60)
        MAIN.tg.send_message(r["user_id"], "⏰ <b>Your bonus is about to expire!</b>\n" + m.LINE +
                             f"\n\n💰 <b>{m.esc(utils.inr(r['remaining']))}</b> expires in about <b>{left} min</b>.\n"
                             "Use it now on a purchase before it's gone 👇",
                             m.kb([("🛒 Shop now", "buy")]))
    return rewards.sweep(tell)


# ================================================================== refer & earn
def _bot_username(ctx):
    try:
        return ctx.tg.get_me().get("username", "")
    except TelegramError:
        return (ctx.reseller or {}).get("bot_username", "")


def _strip_link(tmpl):
    """The intro never shows the link: drop any line of the (admin-editable) message that contains {link}."""
    tmpl = re.sub(r"[^\n]*\{link\}[^\n]*\n?", "", tmpl)
    return re.sub(r"[^\n]*[Ll]ink\s*:\s*\n", "", tmpl)


def show_refer(ctx, user, cq=None):
    """First time: intro message (no link) + Confirm. After confirming, the user is registered and goes straight to
    their dashboard with the link."""
    uid = user["chat_id"]
    if not referral.enabled(ctx.tenant):
        return screen(ctx, uid, "🎁 <b>Refer &amp; Earn</b> is not available right now.", m.kb([("🏠 Home", "home")]), cq=cq)
    sid = skey(ctx, uid)
    if referral.is_registered(sid):
        return show_refer_dashboard(ctx, user, cq)
    rich = settings.get_rich("referral")
    if ctx.tenant:  # a reseller's bot uses the reseller's own message / picture (Bot screens), never the admin's
        t_text, t_fmt, t_img = botdesign.override(ctx, "refer")
        rich = {"text": t_text or settings.RICH_DEFAULTS["referral"], "format": t_fmt if t_text else "html", "image": t_img}
    pct = referral.percent_for(sid, ctx.tenant)
    s = referral.stats_for(sid)
    text, _mode = m.fill(_strip_link(rich["text"]), rich["format"], percent=m.pct(pct), count=s["count"],
                         earned=utils.inr(s["earned"]), link="")
    if rich["format"] == "text":
        text = m.esc(text)
    text += "\n\n👇 <b>Tap Confirm to join Refer &amp; Earn</b> — your personal link appears right after."
    media = ("photo", rich["image"]) if rich["image"] else None
    screen(ctx, uid, text[:1020] if media else text, m.kb([("✅ Confirm — register me", "refer_go")], [("🏠 Home", "home")]),
           media=media, cq=cq)


def do_refer_confirm(ctx, user, cq):
    uid = user["chat_id"]
    if not referral.enabled(ctx.tenant):
        return show_refer(ctx, user, cq)
    sid = skey(ctx, uid)
    if referral.register_referrer(sid):  # first confirmation
        answer(ctx, cq, "✅ You are registered!")
        screen(ctx, uid, "✅ <b>You are registered!</b>\n" + m.LINE + "\n\n🎉 Welcome to <b>Refer &amp; Earn</b>.\n"
               "⏳ Creating your personal link…", cq=cq)
        time.sleep(1.1)
        log_action("referral.registered", "", target=str(uid), source="bot", actor=f"user:{uid}")
        return show_refer_dashboard(ctx, user, None)
    answer(ctx, cq)
    show_refer_dashboard(ctx, user, cq)


def show_refer_dashboard(ctx, user, cq=None):
    uid = user["chat_id"]
    sid = skey(ctx, uid)
    if not referral.is_registered(sid):
        return show_refer(ctx, user, cq)
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
    img = botdesign.override(ctx, "refer")[2] if ctx.tenant else settings.get_rich("referral")["image"]
    screen(ctx, uid, text, m.kb([("📤 Share my link", share)], [("👥 My referrals", "rl:0")],
                                [("💸 Withdraw", "wdr"), ("🏠 Home", "home")]),
           media=("photo", img) if img else None, cq=cq)


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
def show_keys(ctx, user, cq=None, page=0):
    uid = user["chat_id"]
    rows = [l for l in db.query_equal("/licenses", "chat_id", str(uid))
            if not l.get("deleted") and (l.get("reseller_id") or "") == ctx.tenant]
    rows.sort(key=lambda l: l.get("created_at", 0), reverse=True)
    if not rows:
        return screen(ctx, uid, "🔑 <b>My License Keys</b>\n" + m.LINE + "\n\nYou don't have a license yet.",
                      m.kb([("🛒 Shop", "buy"), ("🏠 Home", "home")]), cq=cq, sk="keys")
    icon = {"active": "🟢 Active", "expired": "🔴 Expired", "revoked": "⛔ Revoked"}
    parts, buttons = [], []
    chunk, page, pages = _page_of(rows, page)
    for l in chunk:
        st = lic_svc.status_of(l)
        lim = lic_svc.limit_of(l)
        if lim:
            left = "⚠️ <b>Limit finished</b> — buy more in Shop" if lim["remaining"] <= 0 else f"🔢 Left: <b>{lim['remaining']:,}</b> of {lim['total']:,} requests"
            forever = l["expires_at"] > utils.now() + 20 * 365 * 86400
            parts.append(f"📱 <b>{m.esc(l.get('app_name'))}</b> — {m.esc(l.get('validity_label'))}\n<code>{l['key']}</code>\n"
                         f"{icon[st]}{'' if forever else ' · until ' + utils.fmt_dt(l['expires_at'])}\n{left}")
        else:
            parts.append(f"📱 <b>{m.esc(l.get('app_name'))}</b> — {m.esc(l.get('validity_label'))}\n"
                         f"<code>{l['key']}</code>\n{icon[st]} · until {utils.fmt_dt(l['expires_at'])}\n"
                         f"📲 Device: {'linked' if l.get('device_id') else 'not linked yet'}")
        if st == "active":  # a download button for every live key
            buttons.append([(f"📥 Download APK · {(l.get('app_name') or 'App')[:22]}", f"dl:{l['key']}")])
    head = f"🔑 <b>My License Keys</b> ({len(rows)})\n" + m.LINE + "\n\n"
    foot = "\n\nTap a key to copy it." + (f"\nPage {page + 1}/{pages}" if pages > 1 else "")
    screen(ctx, uid, head + "\n\n".join(parts) + foot,
           m.kb(*buttons, *_pager("keys", page, pages), [("🛒 Shop", "buy"), ("🏠 Home", "home")]), cq=cq, sk="keys")


def send_apk_for_key(ctx, user, key, cq):
    """Download APK button under a live license key: sends the setup video and the APK again."""
    uid = user["chat_id"]
    lic = db.get(f"/licenses/{key}") if re.fullmatch(r"[A-Za-z0-9]{18}", key or "") else None
    if (not lic or lic.get("deleted") or str(lic.get("chat_id")) != str(uid) or (lic.get("reseller_id") or "") != ctx.tenant
            or lic_svc.status_of(lic) != "active"):
        return answer(ctx, cq, "This key is not active any more.", True)
    app = catalog.get_app(lic["app_id"])
    if not app or not app.get("apk_id"):
        return answer(ctx, cq, "The APK is not available right now. Contact support.", True)
    answer(ctx, cq, "📥 Sending…")
    delivery.deliver_assets(ctx, uid, app, lic, lic.get("validity_label"))


def show_support(ctx, user, cq=None):
    name = settings.bot_settings()["support_username"] if ctx.is_main else (ctx.reseller or {}).get("support_username", "")
    custom = ("" if ctx.is_main else (ctx.reseller or {}).get("support_text", "")).strip()  # reseller's own text / HTML
    if name or custom:
        body = custom or "Need help? Our team is one tap away."
        rows = ([[("💬 Contact Support", f"url:https://t.me/{name}")]] if name else []) + [[("🏠 Home", "home")]]
        return screen(ctx, user["chat_id"], "🛟 <b>Customer Support</b>\n" + m.LINE + "\n\n" + body, m.kb(*rows), cq=cq, sk="support")
    screen(ctx, user["chat_id"], "🛟 Support is not configured yet. Please try again later.", m.kb([("🏠 Home", "home")]), cq=cq)


def show_reseller_info(ctx, user, cq=None):
    if not settings.feature("reseller_signup"):
        return screen(ctx, user["chat_id"], "🏪 <b>Reseller</b>\n" + m.LINE + "\n\nReseller applications are closed right now. Please check back later.",
                      m.kb([("🏠 Home", "home")]), cq=cq)
    rich = settings.get_rich("reseller")
    support = settings.bot_settings()["support_username"]
    text = rich["text"] if rich["format"] != "text" else m.esc(rich["text"])
    rows = [[("💬 Contact admin", f"url:https://t.me/{support}")]] if support else []
    media = ("photo", rich["image"]) if rich["image"] else None
    screen(ctx, user["chat_id"], text[:1020] if media else text, m.kb(*rows, [("🏠 Home", "home")]), media=media, cq=cq)


def _panel_url():
    return Config.panel_url("seller")


def show_seller(ctx, user, cq=None):
    """Become Seller: explain the program, then one button creates the account (username = Telegram chat id)."""
    uid = user["chat_id"]
    mine = sellers.seller_for_chat(uid)
    url = _panel_url()
    if mine:
        rows = ([[("🌐 Open Seller Panel", f"url:{url}")]] if url else []) + [[("🔁 Reset my password", "sl_reset")], [("🏠 Home", "home")]]
        return screen(ctx, uid, "💼 <b>You are a seller</b>\n" + m.LINE + f"\n\n👤 Username: <code>{uid}</code>\n"
                      "🔑 Your password was shown when you joined. Forgot it? Tap <b>Reset my password</b> — you get a new one instantly.",
                      m.kb(*rows), cq=cq, sk="seller")
    if not settings.feature("seller_signup"):
        return screen(ctx, uid, "💼 <b>Become Seller</b>\n" + m.LINE + "\n\nSeller sign-ups are closed right now. Please check back later.",
                      m.kb([("🏠 Home", "home")]), cq=cq)
    screen(ctx, uid, "💼 <b>Become a Seller</b>\n" + m.LINE + "\n\nBuild your own app and let our shop sell it for you.\n\n"
           "🧩 Your own <b>license API</b> (endpoint + key)\n📤 Publish apps with plans, <b>free trials</b>, videos &amp; APK — just <b>send the files to this bot</b> and paste the id it gives you\n"
           "🧪 <b>Live APK testing</b> before review — device lock, revoke &amp; maintenance\n💰 Earn on <b>every sale</b> and withdraw by UPI\n\n"
           "👇 Tap below — your Seller Panel account is created instantly. Your <b>username is your Telegram ID</b> and a strong "
           "password is generated for you.",
           m.kb([("✅ Become Seller", "sl_go")], [("🏠 Home", "home")]), cq=cq, sk="seller")


def do_become_seller(ctx, user, cq, reset=False):
    uid = user["chat_id"]
    try:
        s, pw, created = sellers.create_from_bot(uid, user.get("full_name"), user.get("username", ""),
                                                 user.get("profile_file_id", ""))
    except ApiError as e:
        answer(ctx, cq, e.message, True)
        return show_seller(ctx, user, cq)
    answer(ctx, cq, "✅ Account ready!" if created else "🔑 New password created")
    url = _panel_url()
    log_action("seller.joined_bot" if created else "seller.password_reset_bot", s["name"], target=s["id"], source="bot", actor=f"user:{uid}")
    drop_screen(ctx, uid)
    send(ctx, uid, ("🎉 <b>Welcome, seller!</b>\n" if created else "🔑 <b>New password created</b>\n") + m.LINE +
         (f"\n\n🌐 Panel: {url}" if url else "\n\n🌐 Open the <b>/seller</b> page of the shop website") +
         f"\n👤 Username: <code>{uid}</code>\n🔑 Password: <code>{pw}</code>\n\n"
         "⚠️ <b>Save this password now.</b> It is shown only once. Change it anytime in Seller Panel → Settings; "
         "lost it? Tap <b>Become Seller</b> again to get a new one.\n\n"
         "📎 <b>Sending files:</b> send your <b>.apk</b> (as a file) and your <b>.mp4 videos</b> (as a video) to this bot. It replies with "
         "an id that only you can use — paste it in <b>Publish App</b>.",
         m.kb(*([[("🌐 Open Seller Panel", f"url:{url}")]] if url else []), [("🏠 Home", "home")]))


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
                  ("pending", "Pending payments by UPI id"), ("block", "Block a user (asks for a reason)"),
                  ("unblock", "Unblock a user"), ("blocked", "List blocked users")]
ADMIN_HELP = (
    "🛠 <b>Admin commands</b>\n" + m.LINE + "\n\n"
    "/help — show this list\n\n"
    "/send <code>chat_id message</code> — send a message to one user\n"
    "   e.g. <code>/send 123456789 Your key is ready!</code>\n\n"
    "/broadcast — send a message to <b>all users</b>. Send the command, then send your message (text, or a photo / "
    "video / file with a caption). You'll see a <b>preview</b>, how many users will receive it, and "
    "<b>Confirm</b> / <b>Cancel</b> buttons.\n\n"
    "/pending — total pending payments grouped by UPI id\n\n"
    "/block <code>chat_id</code> — block a user. I ask for the <b>reason</b>; a blocked user gets <b>no reply at all</b>\n"
    "/unblock <code>chat_id</code> — let them back in\n/blocked — list blocked users\n\n"
    "/adminlogin — a private sign-in link for the admin panel (valid 30 minutes). Also in the ≡ menu.\n\n"
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
    if cmd == "/adminlogin":
        if not Config.ADMIN_GATE:
            send(ctx, uid, "The admin panel sign-in page is open (ADMIN_GATE is off on this server).")
            return True
        link = admin_gate.link_for(uid)
        send(ctx, uid, "🔐 <b>Admin login</b>\n" + m.LINE + f"\n\nTap the button to open the sign-in page. The link works for "
             f"<b>{Config.ADMIN_LINK_MINUTES} minutes</b> and is only for you — don't forward it.",
             m.kb([("🔐 Open admin login", f"url:{link}")]))
        log_action("auth.entrance_link_issued", "", source="telegram", actor=f"admin:{uid}", role="admin")
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
    if cmd == "/block":
        parts = text.split()
        if len(parts) != 2 or not re.fullmatch(r"\d{3,16}", parts[1]):
            send(ctx, uid, "Usage: <code>/block chat_id</code>\nExample: <code>/block 123456789</code>")
            return True
        target = parts[1]
        if is_admin_chat(target):
            send(ctx, uid, "⚠️ You cannot block an admin.")
            return True
        if bans.is_banned(target):
            send(ctx, uid, f"ℹ️ <code>{target}</code> is already blocked.")
            return True
        u = users.get_user(target, "") or {}
        set_state(ctx, uid, name="block_reason", target=target, who=u.get("full_name", ""))
        send(ctx, uid, f"🚫 <b>Block user</b>\n{m.LINE}\n\n🆔 <code>{target}</code>" + (f"\n👤 {m.esc(u.get('full_name'))}" if u else "")
             + "\n\n✍️ Send the <b>reason</b> now (or /cancel).")
        return True
    if cmd == "/unblock":
        parts = text.split()
        if len(parts) != 2 or not re.fullmatch(r"\d{3,16}", parts[1]):
            send(ctx, uid, "Usage: <code>/unblock chat_id</code>")
            return True
        try:
            bans.unban(parts[1], f"telegram:{uid}")
            send(ctx, uid, f"✅ <code>{parts[1]}</code> is unblocked.")
            log_action("user.unbanned", "", target=parts[1], source="telegram", actor=f"admin:{uid}", role="admin")
        except ApiError as e:
            send(ctx, uid, f"⚠️ {m.esc(e.message)}")
        return True
    if cmd == "/blocked":
        rows = bans.listing()
        if not rows:
            send(ctx, uid, "✅ Nobody is blocked.")
            return True
        send(ctx, uid, f"🚫 <b>Blocked users</b> ({len(rows)})\n{m.LINE}\n\n" + "\n".join(
            f"• <code>{r['chat_id']}</code> {m.esc(r.get('name') or '')} — {m.esc(r.get('reason', ''))}" for r in rows[:30]))
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


ADMIN_HINTS = {"video": "Apps → <b>Video ID</b> / <b>Setup video ID</b>",
               "apk": "Apps → <b>App (APK) ID</b>, or Seller → Submissions → APK id",
               "document": "Apps → <b>App (APK) ID</b> (or a message media field)",
               "photo": "a message image field (welcome / referral / broadcast)",
               "animation": "a broadcast / message media field", "audio": "a broadcast media field"}
SELLER_HINTS = {"apk": "your seller panel → <b>Publish App</b> → step 4, <b>APK file id</b>",
                "video": "your seller panel → <b>Publish App</b> → step 2, <b>Preview video id</b> (or <b>Setup video id</b>)"}


def media_file_id(ctx, uid, msg, admin):
    """An admin or a seller sent media to the bot: reply with its Telegram file id and remember who sent it.
    The id goes only to the sender, and only the sender can use it (sellers attach ids from their own list)."""
    found = tgfiles.classify(msg)
    if not found:
        return False
    kind, fid, name, size, mime = found
    role = "admin" if admin else "seller"
    if role == "seller":
        if kind not in ("apk", "video"):
            send(ctx, uid, "📎 Send me your <b>.apk</b> as a file, or your preview / setup <b>video</b> (.mp4) as a video, "
                           "and I will reply with its id.")
            return True
        why = tgfiles.problem("apk" if kind == "apk" else "preview_video", kind, name, size, mime, sellers.settings()["max_apk_mb"])
        if why:
            send(ctx, uid, f"⚠️ {m.esc(why)}")
            return True
    stored = ""
    if ctx.is_main:
        tgfiles.register(uid, role, kind, fid, name, size, mime)
        stored = _store_in_channel(ctx, uid, msg, role, kind, fid, name)
    hint = (SELLER_HINTS if role == "seller" else ADMIN_HINTS).get(kind, "")
    label = tgfiles.LABEL.get(kind, "File")
    send(ctx, uid, f"📎 <b>{label} id</b>\n{m.LINE}\n\n<code>{fid}</code>\n\n👆 Tap to copy. Only you can see and use this id."
         + (f"\nPaste it in {hint}." if hint else "")
         + (f"\n📦 {m.esc(name)} · {size / 1048576:.1f} MB" if size else f"\n📦 {m.esc(name)}") + stored)
    return True


def _store_in_channel(ctx, uid, msg, role, kind, fid, name):
    """Copy the file into the storage channel (so every reseller bot can deliver it without uploading it again).
    Returns a line for the sender; admins are told when a seller's file could not be stored."""
    if kind not in tgchannel.MIRROR_KINDS or not tgchannel.enabled():
        return ""
    ok, why = tgchannel.mirror_upload(ctx.tg, uid, msg["message_id"], fid)
    if ok:
        return "\n☁️ Saved to the storage channel." if role == "admin" else ""
    log.warning("channel copy of %s failed: %s", fid[:12], why)
    if role == "seller":
        notify.to_admins(f"⚠️ A seller's file <b>{m.esc(name)}</b> could not be copied to the storage channel: {m.esc(why)}")
        return "\n⚠️ Saved, but the admin has to check the storage channel before resellers can deliver it."
    return f"\n⚠️ Could not copy it to the storage channel: {m.esc(why)}"


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
    if action == "faq" and ctx.is_main:
        return show_faq(ctx, user, 0, cq)
    if is_locked(ctx):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if action == "buy":
        return show_categories(ctx, uid, cq)
    if action == "seller" and ctx.is_main:
        return show_seller(ctx, user, cq)
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
    if ctx.is_main and is_admin_chat(uid):
        try:  # the ≡ menu of an admin: /start + Admin Login
            ctx.tg.set_commands(ctx.tg.ADMIN_MENU, chat_id=uid)
        except TelegramError:
            pass
    if gate(ctx, uid):
        finish_onboarding(ctx, uid, user)
        show_home(ctx, uid, user)


def _is_click(update):
    """A button press: an inline-keyboard tap (except the admin's Verify / Fail buttons) or a tap on the persistent menu."""
    cq = update.get("callback_query")
    if cq:
        return not str(cq.get("data", "")).startswith(("av:", "af:"))
    msg = update.get("message") or {}
    return (msg.get("chat") or {}).get("type") == "private" and (msg.get("text") or "").strip() in m.MENU_ACTIONS


def process_update(update, ctx=MAIN):
    part = update.get("message") or update.get("callback_query") or {}
    uid = (part.get("from") or {}).get("id")
    if uid is not None and bans.is_banned(uid):
        return  # blocked users get no reply at all
    if uid is None:
        return _dispatch(update, ctx)
    key = (ctx.key, uid)
    with _lock_for(key):
        if not _is_click(update):
            return _dispatch(update, ctx)
        if time.time() - _last_click.get(key, 0) < CLICK_GAP:  # a second button in the same burst: ignore it
            try:
                if "callback_query" in update:
                    ctx.tg.answer_callback(update["callback_query"]["id"])
                else:
                    ctx.tg.delete_message(update["message"]["chat"]["id"], update["message"]["message_id"])
            except TelegramError:
                pass
            return None
        try:
            return _dispatch(update, ctx)
        finally:
            _last_click[key] = time.time()


def _dispatch(update, ctx):
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
    botui.remember_message(ctx, uid, msg["message_id"])
    text = (msg.get("text") or "").strip()
    st = get_state(ctx, uid)
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    admin = ctx.is_main and is_admin_chat(uid)

    if cmd == "/start":
        return on_start(ctx, user, frm, text)
    if cmd == "/cancel":
        clear_state(ctx, uid)
        return send(ctx, uid, "Cancelled. Use the menu below to continue.", kbd(ctx, uid))
    if admin and cmd in ("/help", "/send", "/broadcast", "/pending", "/block", "/unblock", "/blocked", "/adminlogin"):
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
    if name == "block_reason" and admin and text:
        target = st.get("target", "")
        clear_state(ctx, uid)
        try:
            bans.ban(target, text, f"telegram:{uid}", name=st.get("who", ""), source="telegram")
            send(ctx, uid, f"🚫 <code>{target}</code> is now <b>blocked</b>. They will get no replies.\nReason: {m.esc(text)}")
            log_action("user.banned", text[:200], target=target, source="telegram", actor=f"admin:{uid}", role="admin")
        except ApiError as e:
            send(ctx, uid, f"⚠️ {m.esc(e.message)}")
        return
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
    if ctx.is_main and (admin or sellers.seller_by_chat(uid)) and media_file_id(ctx, uid, msg, admin):
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
    if head not in ("paid", "cancel", "ref", "xchk", "cin_go", "spin_go", "dep", "wdr", "refer_go", "sl_go", "sl_reset", "dl"):
        answer(ctx, cq)

    if head == "keys" and arg:  # keys:<page>
        if is_locked(ctx):
            return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
        return show_keys(ctx, user, cq, int(arg) if str(arg).isdigit() else 0)
    if head in ("home", "buy", "wallet", "refer", "checkin", "spin", "keys", "reseller", "seller"):
        return run_action(ctx, user, head, cq)
    if is_locked(ctx) and head in ("cat", "ap", "pl", "plan", "pc", "py", "pw", "by", "pu", "cr"):
        return screen(ctx, uid, "🛠 <b>Store under maintenance</b>\n" + m.LINE + "\n\n" + locked_text(ctx), cq=cq)
    if head == "cat":
        show_category(ctx, uid, arg, cq)
    elif head == "ap":
        show_app(ctx, uid, arg, None, cq)
    elif head in ("pl", "plan", "pc"):  # "plan:<id>" comes from promotion broadcasts; "pc" = old Continue buttons
        confirm_plan(ctx, user, arg, cq)
    elif head == "py":
        proceed_plan(ctx, user, arg, cq)
    elif head == "pu":  # UPI chosen in the method chooser
        q = payments.quote(ctx, user, arg)
        show_upi_options(ctx, user, q, cq) if q else _gone(ctx, uid, cq)
    elif head == "cr" and ctx.is_main:
        do_crypto(ctx, user, arg, cq)
    elif head == "how":
        kind, _, pid = arg.partition(":")
        show_how(ctx, uid, "crypto" if kind == "crypto" else "upi", pid, cq)
    elif head == "xclose":
        ctx.tg.delete_message(uid, msg["message_id"])
    elif head == "xchk" and ctx.is_main:
        on_crypto_check(ctx, uid, arg, cq, msg)
    elif head == "faq" and ctx.is_main:
        show_faq(ctx, user, int(arg) if str(arg).isdigit() else 0, cq)
    elif head == "fq" and ctx.is_main:
        fid, _, page = arg.partition(":")
        show_faq_answer(ctx, user, fid, int(page) if str(page).isdigit() else 0, cq)
    elif head == "pw":  # old wallet-question buttons
        pid, _, w = arg.partition(":")
        do_buy(ctx, user, pid, w == "1", cq)
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
        start_deposit(ctx, user, cq, arg)
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
        do_refer_confirm(ctx, user, cq)
    elif head == "sl_go" and ctx.is_main:
        do_become_seller(ctx, user, cq)
    elif head == "sl_reset" and ctx.is_main:
        do_become_seller(ctx, user, cq, reset=True)
    elif head == "dl":
        send_apk_for_key(ctx, user, arg, cq)
    elif head == "rl":
        show_referrals(ctx, user, int(arg or 0), cq)
    elif head == "rd":
        rc, _, page = arg.partition(":")
        show_referral_detail(ctx, user, rc, int(page or 0), cq)
