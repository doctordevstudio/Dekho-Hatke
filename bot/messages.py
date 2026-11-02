"""All user-facing bot texts and keyboards live here (premium look: emoji, dividers, highlights)."""
import html

from core import utils
from services import texts as T

LINE = "━━━━━━━━━━━━━━━━━━"


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=False)


# ------------------------------------------------------------------ persistent menu
M_HOME, M_BUY, M_WALLET, M_REFER = "🏠 Home", "🛒 Shop", "💰 Wallet", "🎁 Refer & Earn"
M_CHECKIN, M_SPIN, M_KEYS, M_SUPPORT, M_RESELLER = "📅 Daily Check-in", "🎡 Spin Wheel", "🔑 My Keys", "🎧 Support", "🏪 Reseller"
M_COLLECT = "💵 My Payment Collection"
M_HIDE = "🙈 Hide menu"
M_SELLER = "💼 Become Seller"
MENU_ACTIONS = {M_COLLECT: "collect", "/collection": "collect", M_HOME: "home", M_BUY: "buy", M_WALLET: "wallet", M_REFER: "refer", M_CHECKIN: "checkin",
                M_SPIN: "spin", M_KEYS: "keys", M_SUPPORT: "support", M_RESELLER: "reseller", M_SELLER: "seller", "/seller": "seller",
                "/home": "home", "/help": "home", "/buy": "buy", "/apps": "buy", "/purchase": "buy",
                "/wallet": "wallet", "/refer": "refer", "/checkin": "checkin", "/spin": "spin", "/keys": "keys",
                "/support": "support", "/faq": "faq", "/faqs": "faq", "❓ FAQs": "faq", "/reseller": "reseller", "📱 Apps": "buy", "🔑 My License Key": "keys",
                "🛟 Customer Support": "support", "🛟 Support": "support", "🎧 Customer Support": "support",
                M_HIDE: "menu_off", "/hide": "menu_off", "/menu": "menu_on"}


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
    from services.settings import modern_welcome
    text = modern_welcome(template)
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
    """Button text for a plan. `p` comes from catalog.plans_for (has price, per_day, save_pct). The wording is editable
    (Settings -> Bot messages -> Plan buttons) and can be overridden per plan (`button_text`)."""
    label = p.get("text") or utils.validity_label(p["validity_value"], p["validity_unit"])
    custom = (p.get("button_text") or "").strip()
    if p.get("kind") == "trial":
        txt = T.fill(custom, label=label) if custom else T.t("btn_plan_trial", label=label)
    elif p.get("kind") == "short":
        txt = T.fill(custom, label=label) if custom else T.t("btn_plan_short", label=label)
    else:
        inr = utils.inr(p["price"])
        usd = f"${float(p['amount']):g}" if p.get("currency") == "USD" else (f"${float(p['usd']):.2f}" if p.get("usd") else "")
        price = f"${float(p['amount']):g} (≈ {inr})" if p.get("currency") == "USD" else inr
        vals = {"star": "⭐ " if p.get("is_best") else "", "label": label, "price": price, "inr": inr, "usd": usd,
                "save": f" · save {p['save_pct']}%" if p.get("save_pct") else "", "requests": f"{int(p.get('requests') or 0):,}", "credits": f"{int(p.get('requests') or 0):,}",
                "expiry": "" if p.get("no_expiry") else f" · valid {utils.validity_label(p['validity_value'], p['validity_unit'])}"}
        if int(p.get("requests") or 0) > 0:
            txt = T.fill(custom, **vals) if custom else T.t("btn_plan_limit", **vals)
        else:
            txt = T.fill(custom, **vals) if custom else T.t("btn_plan_time", **vals)
    return ("🟢 " + txt) if selected else txt


def app_caption(app, plans, users_now=0, selected=None, provider=""):
    feats = [f.strip(" -•*") for f in (app.get("features") or "").splitlines() if f.strip()][:6]
    out = [T.t("app_title", name=esc(app["name"])), LINE]
    if provider:
        out.append(provider_line(provider))
    if app.get("description"):
        out.append(f"<i>{esc(app['description'][:160])}</i>")
    if feats:
        out.append("\n".join(f"✨ {esc(f[:70])}" for f in feats))
    if users_now >= 5:
        out.append(T.t("app_users", count=users_now))
    best = next((p for p in plans if p.get("is_best")), None)
    if best:
        out.append(T.t("app_best", validity=esc(utils.validity_label(best['validity_value'], best['validity_unit'])),
                       per_day=esc(utils.inr(best['per_day']))))
    if any(p.get("kind") == "trial" for p in plans):
        out.append(T.t("app_trial"))
    out.append(T.t("app_choose"))
    return "\n".join(out)[:1020]


def price_text(q):
    """₹ price, with the dollar equivalent when crypto is possible (or the plan is priced in dollars)."""
    inr = esc(utils.inr(q["price"]))
    if q.get("currency") == "USD":
        return f"${float(q['plan']['amount']):g} (≈ {inr})"
    if "crypto" in (q.get("methods") or []) and q.get("usd"):
        return f"{inr} (≈ ${q['usd']:.2f})"
    return inr


def provider_line(name):
    return T.t("app_provider", provider=esc(name)) if name else ""


def confirm_text(q, use_wallet=False, bd_preview=(0, 0)):
    """The order summary shown right after a plan is tapped. q from payments.quote()."""
    app = q["app"]
    if q.get("short"):
        n = int((q.get("sh") or {}).get("pages", 1))
        body = T.t("confirm_short", pages=n, s="" if n == 1 else "s")
    elif q["trial"]:
        body = T.t("confirm_trial")
    else:
        body = T.t("confirm_paid", price=price_text(q), provider_line=(provider_line(q["provider"]) + "\n") if q.get("provider") else "")
    return T.t("confirm_order", app=esc(app["name"]), plan=esc(q["label"]), trial_tag=" 🎁 FREE TRIAL" if q["trial"] else "", body=body)


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
    use, cash = q.get("split") or (min(b["total"], q["price"]), 0)
    note = ""
    if 0 < cash and use < min(b["total"], q["price"]):
        note = T.t("wallet_split_note", cash=esc(utils.inr(cash)), use=esc(utils.inr(use)))
    upi_part = T.t("wallet_upi_part", cash=esc(utils.inr(cash))) if cash else T.t("wallet_no_upi")
    return T.t("wallet_options", app=esc(q["app"]["name"]), plan=esc(q["label"]), price=esc(utils.inr(q["price"])),
               wallet="\n".join(parts), use=esc(utils.inr(use)), upi_part=upi_part, note=note)


def methods_text(q):
    """Shown after 'Yes' when there is more than one way to pay."""
    parts = []
    if "upi" in q["methods"]:
        parts.append(T.t("method_upi"))
    if "crypto" in q["methods"]:
        parts.append(T.t("method_crypto"))
    return T.t("methods", app=esc(q["app"]["name"]), plan=esc(q["label"]), price=price_text(q),
               provider_line=(provider_line(q["provider"]) + "\n") if q.get("provider") else "", methods="\n".join(parts))


def how_upi_text():
    return T.t("how_upi")


def how_crypto_text():
    return T.t("how_crypto")


def _extra(lines):
    return ("\n\n" + "\n".join(lines)) if lines else ""


def crypto_deposit_caption(txn, note=""):
    c = txn["crypto"]
    out = T.t("card_crypto_deposit", amount=esc(utils.inr(txn["amount"])), rate=esc(utils.inr(float(c.get("fixed_inr_rate") or 0))),
              xmr=esc(c["xmr"]), address=esc(c["address"]), txn=esc(txn["id"]), left=mmss(txn["expires_at"] - utils.now()),
              till=utils.fmt_time(txn["expires_at"]), confirmations=int(c.get("confirmations", 1)))
    tail = (["👀 <b>Payment detected — waiting for confirmation…</b>"] if c.get("seen_pico") else []) + ([note] if note else [])
    return out + _extra(tail)


def crypto_caption(txn, note=""):
    if txn.get("kind") == "deposit":
        return crypto_deposit_caption(txn, note)
    c = txn["crypto"]
    out = T.t("card_crypto", app=esc(txn["app_name"]), plan=esc(txn["plan_label"]), price=f"${float(c['usd']):.2f}",
              inr=esc(utils.inr(txn["amount"])), provider_line=(provider_line(txn["provider"]) + "\n") if txn.get("provider") else "",
              xmr=esc(c["xmr"]), address=esc(c["address"]), txn=esc(txn["id"]), left=mmss(txn["expires_at"] - utils.now()),
              till=utils.fmt_time(txn["expires_at"]), confirmations=int(c.get("confirmations", 1)))
    tail = (["👀 <b>Payment detected — waiting for confirmation…</b>"] if c.get("seen_pico") else []) + ([note] if note else [])
    return out + _extra(tail)


def crypto_markup(txn_id):
    return kb([("🔄 Check payment", f"xchk:{txn_id}"), ("❌ Cancel", f"cancel:{txn_id}")],
              [("📖 How to pay", "how:crypto")])


def wallet_question(q):  # kept for older callers
    return payment_options(q)


def payment_caption(txn, note=""):
    dep = txn.get("kind") == "deposit"
    base = dict(amount=esc(utils.inr(txn["amount"])), upi=esc(txn["upi_id"]), txn=esc(txn["id"]), left=mmss(txn["expires_at"] - utils.now()),
                till=utils.fmt_time(txn["expires_at"]))
    if dep:
        out = T.t("card_upi_deposit", **base)
    else:
        used = float((txn.get("wallet") or {}).get("total", 0))
        wl = (f"💰 Price: <b>{esc(utils.inr(txn.get('plan_price', txn['amount'])))}</b>\n👛 Wallet used: <b>-{esc(utils.inr(used))}</b>\n") if used else ""
        pl = (provider_line(txn["provider"]) + "\n") if txn.get("provider") else ""
        out = T.t("card_upi", app=esc(txn["app_name"]), plan=esc(txn["plan_label"]), wallet_lines=wl, provider_line=pl, **base)
    return out + (("\n\n" + note) if note else "")


def card_markup(txn_id):
    return kb([("✅ I have Paid", f"paid:{txn_id}"), ("❌ Cancel", f"cancel:{txn_id}")],
              [("🔄 Refresh timer", f"ref:{txn_id}"), ("📖 How to pay", "how:upi")])


def license_done_text(txn, lic, wallet_total=0):
    head = T.t({"trial": "license_head_trial", "wallet": "license_head_wallet", "shorturl": "license_head_short",
                "crypto": "license_head_crypto"}.get(txn.get("via"), "license_head_paid"))
    forever = lic["expires_at"] > utils.now() + 20 * 365 * 86400
    if lic.get("limit_total"):
        validity = T.t("validity_limit", requests=f"{int(lic['limit_total']):,}", credits=f"{int(lic['limit_total']):,}",
                       expiry="" if forever else f"\n⏳ Valid until: {utils.fmt_dt(lic['expires_at'])}")
    else:
        validity = T.t("validity_time", expires=utils.fmt_dt(lic["expires_at"]))
    return T.t("license_done", head=head, app=esc(txn["app_name"]), key=lic["key"], plan=esc(txn["plan_label"]),
               validity_line=validity + "\n", wallet_line=(f"👛 Paid from wallet: {esc(utils.inr(wallet_total))}\n" if wallet_total else ""),
               provider_line=provider_line(txn.get("provider")))


def deposit_done_text(txn, bal):
    if txn.get("via") == "crypto":
        from services import xmr
        got = xmr.pico_to_xmr((txn.get("crypto") or {}).get("received_pico", 0))
        return T.t("deposit_done_crypto", xmr=esc(got), rate=esc(utils.inr(txn.get("credited_rate", 0))), amount=esc(utils.inr(txn["amount"])),
                   total=esc(utils.inr(bal["total"])))
    return T.t("deposit_done_upi", amount=esc(utils.inr(txn["amount"])), total=esc(utils.inr(bal["total"])))


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
