"""All user-facing bot texts and keyboards live here (premium look: emoji, dividers, highlights)."""
import html

from core import utils

LINE = "━━━━━━━━━━━━━━━━━━"


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=False)


# ------------------------------------------------------------------ persistent menu
M_HOME, M_BUY, M_WALLET, M_REFER = "🏠 Home", "🛒 Purchase", "💰 Wallet", "🎁 Refer & Earn"
M_CHECKIN, M_SPIN, M_KEYS, M_SUPPORT, M_RESELLER = "📅 Daily Check-in", "🎡 Spin Wheel", "🔑 My Keys", "🛟 Support", "🏪 Reseller"
M_COLLECT = "💵 My Payment Collection"
M_SELLER = "💼 Become Seller"
MENU_ACTIONS = {M_COLLECT: "collect", "/collection": "collect", M_HOME: "home", M_BUY: "buy", M_WALLET: "wallet", M_REFER: "refer", M_CHECKIN: "checkin",
                M_SPIN: "spin", M_KEYS: "keys", M_SUPPORT: "support", M_RESELLER: "reseller", M_SELLER: "seller", "/seller": "seller",
                "/home": "home", "/help": "home", "/buy": "buy", "/apps": "buy", "/purchase": "buy",
                "/wallet": "wallet", "/refer": "refer", "/checkin": "checkin", "/spin": "spin", "/keys": "keys",
                "/support": "support", "/reseller": "reseller", "📱 Apps": "buy", "🔑 My License Key": "keys",
                "🛟 Customer Support": "support"}


def menu_keyboard(is_main=True, rewards=True, collector=False, seller=False, reseller=True, refer=True):
    rows = [[{"text": M_HOME}, {"text": M_BUY}], [{"text": M_WALLET}] + ([{"text": M_REFER}] if refer else [])]
    if is_main and rewards:
        rows.append([{"text": M_CHECKIN}, {"text": M_SPIN}])
    rows.append([{"text": M_KEYS}, {"text": M_SUPPORT}])
    if is_main and (seller or reseller):
        rows.append(([{"text": M_SELLER}] if seller else []) + ([{"text": M_RESELLER}] if reseller else []))
    if collector:
        rows.append([{"text": M_COLLECT}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True,
            "input_field_placeholder": "Choose from the menu 👇"}


def kb(*rows):
    """kb([("text", "callback"), ("Open", "url:https://...")], ...)"""
    return {"inline_keyboard": [[{"text": t, **({"url": d[4:]} if d.startswith("url:") else {"callback_data": d})}
                                 for t, d in row] for row in rows if row]}


def user_block(u, username=True):
    """`username=False` hides the @username (UPI owners / resellers never get it)."""
    out = f"👤 <b>{esc(u.get('full_name'))}</b>\n"
    if username:
        uname = f"@{esc(u.get('username'))}" if u.get("username") else "—"
        out += f"🔗 Username: {uname}\n"
    return out + f"🆔 Chat ID: <code>{esc(u.get('chat_id'))}</code>"


def render_welcome(template, fmt, user):
    """Fill {first_name} {full_name} {username} {chat_id}. Returns (text, parse_mode)."""
    html_mode = fmt != "text"
    clean = (lambda v: esc(v)) if html_mode else (lambda v: str(v or ""))
    text = template
    for k in ("first_name", "full_name", "username", "chat_id"):
        text = text.replace("{" + k + "}", clean(user.get(k, "")))
    return text, ("HTML" if html_mode else None)


def fill(template, fmt, **vals):
    """Fill {placeholders} in an admin-edited message. Returns (text, parse_mode)."""
    html_mode = fmt != "text"
    text = template
    for k, v in vals.items():
        text = text.replace("{" + k + "}", esc(v) if html_mode and k not in ("link",) else str(v))
    return text, ("HTML" if html_mode else None)


def mmss(seconds):
    m, s = divmod(max(0, int(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def pct(p):
    return f"{int(p)}" if float(p) == int(p) else f"{p:g}"


# ------------------------------------------------------------------ purchase
def plan_label(p, selected=False):
    """Button text for a plan. `p` comes from catalog.plans_for (has price, per_day, save_pct)."""
    label = utils.validity_label(p["validity_value"], p["validity_unit"])
    if p.get("kind") == "trial":
        txt = f"🎁 FREE TRIAL · {label}"
    elif p.get("kind") == "short":
        txt = f"🆓 {label} — FREE"
    else:
        txt = f"{'⭐ ' if p.get('is_best') else ''}{label} — {utils.inr(p['price'])}"
        if p.get("save_pct"):
            txt += f" · save {p['save_pct']}%"
    return ("🟢 " + txt) if selected else txt


def app_caption(app, plans, users_now=0, selected=None):
    feats = [f.strip(" -•*") for f in (app.get("features") or "").splitlines() if f.strip()][:6]
    out = [f"🔥 <b>{esc(app['name'])}</b>", LINE]
    if app.get("description"):
        out.append(f"<i>{esc(app['description'][:160])}</i>")
    if feats:
        out.append("\n".join(f"✨ {esc(f[:70])}" for f in feats))
    if users_now >= 5:
        out.append(f"👥 <b>{users_now}</b> people are using it right now")
    best = next((p for p in plans if p.get("is_best")), None)
    if best:
        out.append(f"⭐ <b>Best value:</b> {esc(utils.validity_label(best['validity_value'], best['validity_unit']))} "
                   f"— only {esc(utils.inr(best['per_day']))}/day")
    if any(p.get("kind") == "trial" for p in plans):
        out.append("🎁 <b>Try it free</b> before you buy!")
    out.append("\n👇 <b>Choose your plan</b>")
    return "\n".join(out)[:1020]


def confirm_text(q, use_wallet=False, bd_preview=(0, 0)):
    """The order summary shown right after a plan is tapped. q from payments.quote()."""
    app = q["app"]
    lines = ["🧾 <b>Confirm your order</b>", LINE, f"📱 App: <b>{esc(app['name'])}</b>",
             f"📦 Plan: <b>{esc(q['label'])}</b>" + (" 🎁 FREE TRIAL" if q["trial"] else "")]
    if q.get("short"):
        sh = q.get("sh") or {}
        n = int(sh.get("pages", 1))
        lines += ["💰 Price: <b>FREE 🆓</b>", "",
                  f"Unlock it by completing <b>{n} page{'s' if n != 1 else ''}</b> on our website. I'll message you after every "
                  "page and send your <b>license key</b>, the <b>APK</b> and the <b>setup video</b> as soon as you finish.",
                  "", "Do you want to continue?"]
    elif q["trial"]:
        lines += ["💰 Price: <b>FREE</b>", "", "You'll instantly get your <b>license key</b>, the <b>APK</b> and the "
                                                "<b>setup video</b>.", "", "Start your free trial?"]
    else:
        lines += [f"💰 Price: <b>{esc(utils.inr(q['price']))}</b>", "", "Do you want to purchase it?"]
    return "\n".join(lines)


def payment_options(q):
    """Shown after 'Yes' when the user has wallet money: choose how to pay."""
    b = q["wallet"]
    parts = []
    if b["bonus"]:
        parts.append(f"⚡ Bonus: <b>{esc(utils.inr(b['bonus']))}</b>")
    if b["deposit"]:
        parts.append(f"💳 Deposit: <b>{esc(utils.inr(b['deposit']))}</b>")
    if b["refer"]:
        parts.append(f"🎁 Refer money: <b>{esc(utils.inr(b['refer']))}</b>")
    return ("💳 <b>Choose how to pay</b>\n" + LINE + f"\n📱 {esc(q['app']['name'])} — {esc(q['label'])}\n"
            f"💰 Price: <b>{esc(utils.inr(q['price']))}</b>\n\n👛 Your wallet:\n" + "\n".join(parts) +
            "\n\n💡 Paying from the wallet means you pay less (or nothing) by UPI.")


def wallet_question(q):  # kept for older callers
    return payment_options(q)


def payment_caption(txn, note=""):
    rem = txn["expires_at"] - utils.now()
    dep = txn.get("kind") == "deposit"
    lines = ["💳 <b>Wallet Deposit</b>" if dep else "💳 <b>Payment Request</b>", LINE]
    if not dep:
        lines += [f"📱 App: <b>{esc(txn['app_name'])}</b>", f"📦 Plan: <b>{esc(txn['plan_label'])}</b>"]
        used = float((txn.get("wallet") or {}).get("total", 0))
        if used:
            lines += [f"💰 Price: <b>{esc(utils.inr(txn.get('plan_price', txn['amount'])))}</b>",
                      f"👛 Wallet used: <b>-{esc(utils.inr(used))}</b>"]
    lines += [f"💵 Pay now: <b>{esc(utils.inr(txn['amount']))}</b>", "",
              f"🏦 UPI ID: <code>{esc(txn['upi_id'])}</code>", f"🧾 Txn ID: <code>{esc(txn['id'])}</code>",
              f"⏳ Time left: <b>{mmss(rem)}</b> (till {utils.fmt_time(txn['expires_at'])})", "",
              f"Scan the QR or pay exactly <b>{esc(utils.inr(txn['amount']))}</b> to the UPI ID, then tap <b>I have Paid</b>."]
    if note:
        lines += ["", note]
    return "\n".join(lines)


def card_markup(txn_id):
    return kb([("✅ I have Paid", f"paid:{txn_id}"), ("❌ Cancel", f"cancel:{txn_id}")],
              [("🔄 Refresh timer", f"ref:{txn_id}")])


def license_done_text(txn, lic, wallet_total=0):
    head = {"trial": "🎁 <b>Your free trial is ready!</b>", "wallet": "✅ <b>Order complete!</b>",
            "shorturl": "🎉 <b>All steps verified — your free key is ready!</b>"}.get(txn.get("via"), "🎉 <b>Payment verified!</b>")
    lines = [head, LINE,
             f"📱 <b>{esc(txn['app_name'])}</b>", f"🔑 Your license key:\n<code>{lic['key']}</code>", "",
             f"📦 Plan: {esc(txn['plan_label'])}", f"⏳ Valid until: {utils.fmt_dt(lic['expires_at'])}"]
    if wallet_total:
        lines.append(f"👛 Paid from wallet: {esc(utils.inr(wallet_total))}")
    lines += ["", "Tap the key to copy it. Your APK and setup video are coming right below 👇"]
    return "\n".join(lines)


def deposit_done_text(txn, bal):
    return ("✅ <b>Deposit successful!</b>\n" + LINE + f"\n💳 Added: <b>{esc(utils.inr(txn['amount']))}</b>\n"
            f"👛 Wallet total: <b>{esc(utils.inr(bal['total']))}</b>\n\nUse it on your next purchase 🛒")


# ------------------------------------------------------------------ messages to admins / owners
def admin_created_text(txn, username=True):
    dep = txn.get("kind") == "deposit"
    return ((("💳 <b>New wallet deposit</b>\n\n" if dep else "🆕 <b>New payment created</b>\n\n")) +
            ("" if dep else f"📱 App: <b>{esc(txn['app_name'])}</b> — {esc(txn['plan_label'])}\n") +
            f"💰 Amount: <b>{esc(utils.inr(txn['amount']))}</b>"
            + (f" (plan {esc(utils.inr(txn.get('plan_price')))}, wallet {esc(utils.inr((txn.get('wallet') or {}).get('total', 0)))})"
               if (txn.get("wallet") or {}).get("total") else "") + "\n"
            f"🏦 UPI: <code>{esc(txn['upi_id'])}</code>\n🧾 Txn: <code>{esc(txn['id'])}</code>\n"
            f"⏳ Expires: {utils.fmt_time(txn['expires_at'])}\n\n" + user_block(txn["user"], username))


def admin_proof_caption(txn, status_line="", username=True):
    dep = txn.get("kind") == "deposit"
    return ("📸 <b>Payment proof submitted</b>\n\n"
            f"🧾 Txn: <code>{esc(txn['id'])}</code>\n🔢 UTR: <code>{esc(txn.get('utr', ''))}</code>\n"
            + ("💳 Wallet deposit\n" if dep else f"📱 App: <b>{esc(txn['app_name'])}</b> — {esc(txn['plan_label'])}\n")
            + f"💰 Amount: <b>{esc(utils.inr(txn['amount']))}</b>\n🏦 UPI: <code>{esc(txn['upi_id'])}</code>\n\n"
            + user_block(txn["user"], username) + (f"\n\n{status_line}" if status_line else ""))


def admin_wallet_sale_text(txn, username=True):
    return ("👛 <b>Sale paid from wallet</b>\n\n"
            f"📱 {esc(txn['app_name'])} — {esc(txn['plan_label'])}\n💰 {esc(utils.inr(txn.get('plan_price')))}\n\n"
            + user_block(txn["user"], username))


def admin_buttons(txn_id):
    return kb([("✅ Verified", f"av:{txn_id}"), ("❌ Fail", f"af:{txn_id}")])
