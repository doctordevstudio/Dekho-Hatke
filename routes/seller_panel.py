"""Seller panel API (/seller/api). A seller only ever sees their own data -- and only customers' display names:
never a chat id, never a @username."""
import time

from flask import g, jsonify, request, session

from bot.tg import TelegramError, tg
from core import db, security, utils
from core.audit import log_action
from core.errors import ApiError
from services import broadcast, catalog, media, sellers
from . import body, sbp

FILE_KINDS = {"apk", "preview_video", "setup_video"}


def _me():
    return g.seller


def _log(action, detail="", target=""):
    log_action(f"seller.{action}", detail, target=target, source="seller", actor=f"seller:{_me()['name']}")


def _earn_item(r):
    """What a seller may know about one sale: what was sold, when, how much, and the buyer's NAME."""
    return {"id": r["id"], "ts": r["ts"], "app_id": r["app_id"], "app_name": r.get("app_name", ""),
            "plan_label": r.get("plan_label", ""), "amount": r["amount"], "buyer": r["user"].get("full_name", "")}


def _range_args():
    return utils.resolve_range(request.args.get("range", "today"), request.args.get("start"), request.args.get("end"))


def _endpoint(app):
    return f"{request.url_root.rstrip('/')}/api/v1/{app['id']}/verify"


def _app_row(a, detail=False):
    er = sellers.earning_rows(g.seller["id"], 0, utils.FAR_FUTURE, a["id"])
    st = sellers.review_state(a)
    maint = a.get("maintenance") or {}
    out = {"id": a["id"], "name": a["name"], "image_url": a.get("image_url", ""), "status": st,
           "status_label": sellers.STATUS_LABEL.get(st, st), "reason": a.get("review_reason", "") if st == "rejected" else "",
           "category_id": a.get("category_id", ""), "endpoint": _endpoint(a), "api_key": a.get("api_key", ""),
           "disabled": bool(a.get("disabled")), "disabled_by": a.get("disabled_by", ""),
           "maintenance": bool(maint.get("enabled")), "maintenance_message": maint.get("message", ""),
           "sales": len(er), "earned": round(sum(r["amount"] for r in er), 2), "created_at": a.get("created_at", 0),
           "has_apk": bool(a.get("apk_id")), "pending": bool(a.get("pending_submission"))}
    if a.get("status") == "live" or a.get("apk_id"):
        out["live_plans"] = [{"id": p["id"], "amount": p["amount"], "validity_value": p["validity_value"],
                              "validity_unit": p["validity_unit"], "is_best": bool(p.get("is_best")),
                              "label": utils.validity_label(p["validity_value"], p["validity_unit"])}
                             for p in catalog.live("/plans") if p["app_id"] == a["id"] and p.get("kind") != "trial"]
    if detail:
        out["ranges"] = sellers.range_summary(g.seller["id"], request.args.get("start"), request.args.get("end"), a["id"])
        out["description"], out["features"] = a.get("description", ""), a.get("features", "")
    return out


# ------------------------------------------------------------------ auth
def _csrf_ok():
    return security.csrf_valid(request.headers.get("X-CSRF-Token"))


@sbp.post("/login")
def login_api():
    if not _csrf_ok():
        raise ApiError("Session expired. Reload the page.", 403)
    ip = security.client_ip()
    ok, _ = security.limiter.hit(f"slogin:{ip}", *security.Config.LOGIN_LIMIT)
    if not ok:
        raise ApiError("Too many attempts. Wait a minute.", 429)
    d = body()
    username = str(d.get("username") or "").strip()[:64]
    password = str(d.get("password") or "")[:256]
    wait = security.login_locked_for("s:" + username, ip)
    if wait:
        raise ApiError(f"Too many failed attempts. Try again in {wait // 60 + 1} min.", 429)
    sid = db.get(f"/seller_usernames/{username.lower()}") if username else None
    s = db.get(f"/sellers/{sid}") if sid else None
    usable = bool(s) and not s.get("deleted") and s.get("active", True)
    good = security.verify_password(s["password_hash"], password) if usable else (security.burn_password_check(password) or False)
    if not good:
        security.login_failed("s:" + username, ip)
        time.sleep(0.4)
        raise ApiError("Invalid username or password.", 401)
    csrf_before = session.get("csrf")
    session.clear()
    session.permanent = True
    session["sid"], session["ssv"] = s["id"], s.get("session_version", 1)
    session["csrf"] = csrf_before or security.csrf_token()
    security.login_succeeded("s:" + username, ip)
    db.update(f"/sellers/{s['id']}", {"last_login": utils.now()})
    g.seller = s
    _log("login")
    return jsonify(ok=True)


@sbp.post("/signup")
def signup():
    if not _csrf_ok():
        raise ApiError("Session expired. Reload the page.", 403)
    ok, _ = security.limiter.hit(f"sslogup:{security.client_ip()}", 5, 3600)
    if not ok:
        raise ApiError("Too many sign-ups from this network. Try later.", 429)
    d = body()
    if str(d.get("password")) != str(d.get("confirm") or d.get("password")):
        raise ApiError("Passwords do not match.")
    s = sellers.create({"name": d.get("name"), "login_username": d.get("username"), "password": d.get("password")})
    log_action("seller.signup", s["name"], target=s["id"], source="seller", actor=f"seller:{s['name']}")
    from services import notify
    notify.to_admins(f"🆕 <b>New seller signed up</b>\n👤 {s['name']}")
    return jsonify(ok=True), 201


@sbp.post("/logout")
def logout():
    csrf = session.get("csrf")
    session.clear()
    return jsonify(ok=True)


@sbp.get("/me")
def me():
    s = _me()
    rows, unread = sellers.notifications(s["id"], 1)
    return jsonify(**sellers.public(s), csrf=security.csrf_token(), unread=unread, settings=_public_settings(),
                   bot_username=_bot_username(), categories=[{"id": c["id"], "name": c["name"]} for c in catalog.categories()],
                   appwrite=bool(__import__("services.appwrite", fromlist=["x"]).configured()))


def _public_settings():
    c = sellers.settings()
    return {k: c[k] for k in ("min_withdraw", "fee_pct", "max_apk_mb")}


def _bot_username():
    try:
        return tg.get_me().get("username", "")
    except TelegramError:
        return ""


@sbp.get("/notifications")
def notifications():
    rows, unread = sellers.notifications(_me()["id"], 40)
    return jsonify(items=rows, unread=unread)


@sbp.post("/notifications/read")
def notifications_read():
    sellers.mark_read(_me()["id"])
    return jsonify(ok=True)


@sbp.post("/telegram/link")
def telegram_link():
    tok = sellers.link_token(_me()["id"])
    bot = _bot_username()
    return jsonify(url=f"https://t.me/{bot}?start=sl_{tok}" if bot else "", bot=bot)


@sbp.post("/password")
def password():
    d = body()
    sellers.change_password(_me(), d.get("current"), d.get("new"))
    s = db.get(f"/sellers/{_me()['id']}")
    session["ssv"] = s["session_version"]  # this session stays alive, every other one is signed out
    _log("password_changed")
    return jsonify(ok=True)


# ------------------------------------------------------------------ dashboard / earnings
@sbp.get("/dashboard")
def dashboard():
    s = _me()
    apps = sellers.my_apps(s["id"])
    by = {"live": 0, "in_verification": 0, "rejected": 0, "draft": 0}
    for a in apps:
        by[sellers.review_state(a)] = by.get(sellers.review_state(a), 0) + 1
    return jsonify(ranges=sellers.range_summary(s["id"], request.args.get("start"), request.args.get("end")),
                   balance=round(float(s.get("balance", 0)), 2), total_earned=round(float(s.get("total_earned", 0)), 2),
                   sales_count=int(s.get("sales_count", 0)), apps=by, apps_total=len(apps),
                   pending_withdraw=sum(w["amount"] for w in sellers.withdrawals(s["id"], "pending")))


@sbp.get("/earnings")
def earnings():
    """Every sale of a period, paginated (drill-down for the dashboard cards)."""
    lo, hi = _range_args()
    rows = sellers.earning_rows(_me()["id"], lo, hi, request.args.get("app_id") or None)
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(amount_total=round(sum(r["amount"] for r in rows), 2), count=len(rows), items=[_earn_item(r) for r in page], **meta)


@sbp.get("/apps-summary")
def apps_summary():
    """Drill-down for the 'apps' cards: all apps (optionally one status), paginated."""
    status = request.args.get("status", "")
    rows = [a for a in sellers.my_apps(_me()["id"]) if not status or sellers.review_state(a) == status]
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[_app_row(a) for a in page], **meta)


# ------------------------------------------------------------------ apps
@sbp.get("/apps")
def list_apps():
    rows = sellers.my_apps(_me()["id"])
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[_app_row(a) for a in page], **meta)


@sbp.post("/apps")
def create_app():
    """Create API: name, logo, category (existing or typed) -> endpoint + API key."""
    f = request.files.get("logo")
    raw = None
    if f and f.filename:
        raw = f.read(2 * 1024 * 1024 + 1)
        ok, _ = media.verify_real_image(raw, 2 * 1024 * 1024, 32)
        if not ok:
            raise ApiError("The logo must be a real JPG / PNG image (max 2 MB).")
    form = request.form
    if len(sellers.my_apps(_me()["id"])) >= 30:
        raise ApiError("You reached the limit of 30 apps.")
    app = sellers.create_api_app(_me(), form.get("name"), raw, form.get("category_id", ""), form.get("new_category", ""))
    _log("api_created", app["name"], app["id"])
    return jsonify(_app_row(app)), 201


@sbp.get("/apps/<app_id>")
def app_detail(app_id):
    a = sellers.own_app(_me(), app_id)
    lo, hi = _range_args()
    rows = sellers.earning_rows(_me()["id"], lo, hi, app_id)
    page, meta = utils.paginate(rows, request.args.get("page"))
    cur = {}
    sub = db.get(f"/submissions/{a['pending_submission']}") if a.get("pending_submission") else None
    if sub:
        cur = sub["data"]
    return jsonify(app=_app_row(a, detail=True), period_total=round(sum(r["amount"] for r in rows), 2),
                   period_count=len(rows), items=[_earn_item(r) for r in page], pending=cur, **meta)


@sbp.post("/apps/<app_id>/regenerate-key")
def regen(app_id):
    a = sellers.own_app(_me(), app_id)
    key = sellers.regenerate_key(a)
    _log("api_key_revoked", a["name"], app_id)
    return jsonify(api_key=key)


@sbp.post("/apps/<app_id>/disable")
def disable(app_id):
    a = sellers.own_app(_me(), app_id)
    dis = bool(body().get("disabled", True))
    sellers.set_disabled(a, dis, "seller")
    _log("app_disabled" if dis else "app_enabled", a["name"], app_id)
    return jsonify(ok=True)


@sbp.post("/apps/<app_id>/maintenance")
def maint_one(app_id):
    d = body()
    sellers.set_maintenance(_me(), app_id, bool(d.get("enabled")), d.get("message", ""))
    _log("maintenance_on" if d.get("enabled") else "maintenance_off", "", app_id)
    return jsonify(ok=True)


@sbp.post("/maintenance")
def maint_all():
    d = body()
    app_id = d.get("app_id") or "all"
    n = sellers.set_maintenance(_me(), app_id, bool(d.get("enabled")), d.get("message", ""))
    _log("maintenance_on" if d.get("enabled") else "maintenance_off", f"{n} app(s)", app_id)
    return jsonify(ok=True, apps=n)


# ------------------------------------------------------------------ uploads + publish
@sbp.post("/uploads/<kind>")
def upload(kind):
    f = request.files.get("file")
    if not f or not f.filename:
        raise ApiError("Choose a file.")
    if kind == "logo":
        raw = f.read(2 * 1024 * 1024 + 1)
        ok, _ = media.verify_real_image(raw, 2 * 1024 * 1024, 32)
        if not ok:
            raise ApiError("The logo must be a real JPG / PNG image (max 2 MB).")
        mid = media.save_image(raw, "app_logo", _me()["id"])
        return jsonify(media=mid, url=f"/media/img/{mid}")
    if kind not in FILE_KINDS:
        raise ApiError("Unknown upload type.", 404)
    rec = sellers.store_upload(_me(), kind, f)
    _log("upload", f"{kind} {rec['filename']} ({rec['size'] // 1024} KB)")
    return jsonify(id=rec["id"], filename=rec["filename"], size=rec["size"], big=rec["size"] > sellers.TG_APK_LIMIT_MB * 1024 * 1024)


@sbp.post("/apps/<app_id>/publish")
def publish(app_id):
    a = sellers.own_app(_me(), app_id)
    sub = sellers.submit(_me(), a, body())
    _log("submitted", f"{sub['app_name']} ({sub['kind']})", app_id)
    return jsonify(ok=True, message="Your app is under review. You will be notified on Telegram once it is approved.",
                   submission=sub["id"]), 201


@sbp.get("/publish-check")
def publish_check():
    """The Publish page needs an API app first."""
    mine = [a for a in sellers.my_apps(_me()["id"])]
    return jsonify(has_api=bool(mine), message="" if mine else "Please create an app first (Create API).",
                   apps=[{"id": a["id"], "name": a["name"], "status": sellers.review_state(a),
                          "pending": bool(a.get("pending_submission"))} for a in mine])


@sbp.get("/apps/<app_id>/publish-form")
def publish_form(app_id):
    """Current values to prefill the form (so editing a live app starts from what is live)."""
    a = sellers.own_app(_me(), app_id)
    plans = [{"id": p["id"], "amount": p["amount"], "validity_value": p["validity_value"],
              "validity_unit": p["validity_unit"], "is_best": bool(p.get("is_best"))}
             for p in catalog.live("/plans") if p["app_id"] == app_id and p.get("kind") != "trial"]
    return jsonify(app={"id": a["id"], "name": a["name"], "category_id": a.get("category_id", ""),
                        "description": a.get("description", ""), "features": a.get("features", ""),
                        "image_url": a.get("image_url", ""), "has_apk": bool(a.get("apk_id")),
                        "status": sellers.review_state(a)}, plans=plans)


# ------------------------------------------------------------------ wallet
@sbp.get("/wallet")
def wallet():
    s = _me()
    cfg = sellers.settings()
    return jsonify(balance=round(float(s.get("balance", 0)), 2), total_earned=round(float(s.get("total_earned", 0)), 2),
                   min_withdraw=cfg["min_withdraw"], fee_pct=cfg["fee_pct"],
                   pending=sum(w["amount"] for w in sellers.withdrawals(s["id"], "pending")))


@sbp.get("/wallet/quote")
def quote():
    try:
        amt = int(float(request.args.get("amount", 0)))
    except ValueError:
        raise ApiError("Enter a whole-number amount.")
    return jsonify(amount=amt, **sellers.quote_withdraw(max(0, amt)))


@sbp.post("/wallet/withdraw")
def withdraw():
    d = body()
    rec = sellers.request_withdraw(_me(), d.get("name"), d.get("upi_id"), d.get("amount"))
    _log("withdraw_requested", utils.inr(rec["amount"]), rec["id"])
    return jsonify(_wd(rec)), 201


def _wd(w):
    return {k: w.get(k) for k in ("id", "name", "upi_id", "amount", "fee_pct", "fee", "receive", "status", "created_at",
                                  "resolved_at", "reason")} | {"proof_url": f"/media/img/{w['proof_media_id']}" if w.get("proof_media_id") else ""}


@sbp.get("/wallet/withdrawals")
def withdrawals():
    page, meta = utils.paginate(sellers.withdrawals(_me()["id"]), request.args.get("page"))
    return jsonify(items=[_wd(w) for w in page], **meta)


@sbp.get("/wallet/history")
def earning_history():
    page, meta = utils.paginate(sellers.earning_rows(_me()["id"]), request.args.get("page"))
    return jsonify(items=[_earn_item(r) for r in page], **meta)


# ------------------------------------------------------------------ broadcast (admin approves before anything is sent)
@sbp.get("/broadcasts")
def my_broadcasts():
    rows = broadcast.listing(_me()["id"])
    page, meta = utils.paginate(rows, request.args.get("page"))
    return jsonify(items=[{k: v for k, v in broadcast.public(b).items() if k not in ("owner",)} for b in page], **meta)


@sbp.get("/broadcasts/audience")
def audience():
    ids = [i for i in request.args.get("apps", "").split(",") if i]
    mine = {a["id"] for a in sellers.my_apps(_me()["id"])}
    ids = [i for i in ids if i in mine]
    return jsonify(count=broadcast.audience_count(_me()["id"], {"type": "apps", "app_ids": ids}))


@sbp.post("/broadcasts")
def create_broadcast():
    d = body()
    mine = {a["id"] for a in sellers.my_apps(_me()["id"])}
    ids = [i for i in (d.get("app_ids") or []) if i in mine]  # empty list = all of the seller's apps
    msg = broadcast.clean_message(d.get("message"))
    if msg["media"] and not str(msg["media"]["ref"]).startswith("http"):
        raise ApiError("Use an https image link for the picture.")
    aud = {"type": "apps", "app_ids": ids}
    n = broadcast.audience_count(_me()["id"], aud)
    if not n:
        raise ApiError("None of your customers would receive this yet.")
    b = broadcast.create(_me()["id"], _me()["name"], msg, aud, needs_approval=True, actor=f"seller:{_me()['name']}")
    from services import notify
    notify.to_admins(f"📣 <b>Seller broadcast needs approval</b>\n👤 {_me()['name']} → {n} customers\n"
                     "Open <b>Broadcast</b> in the panel to approve or reject.")
    _log("broadcast_requested", f"{n} customers", b["id"])
    return jsonify(ok=True, id=b["id"], audience=n, message="Sent to the admin for approval."), 201
