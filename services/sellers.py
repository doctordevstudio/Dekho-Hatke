"""Sellers: people who build their own apps, publish them in the shop and earn from every license sold.

Lifecycle of a seller app
  create API (status "draft": endpoint + API key, nothing for sale)
  -> publish (a *submission* with plans, media and the APK)           -> status "in_verification"
  -> admin tests it (test keys), stores the Telegram file id of the APK -> approve ("live") / reject (+ reason)
  -> later edits of a live app are new submissions; the live version keeps selling until the admin approves them.
Money: each sale credits the seller's balance (minus the optional platform commission). The seller withdraws
through UPI; the admin approves/rejects and the seller is told in the panel and on Telegram.
"""
import hashlib
import logging
import math
import re
import secrets

from bot import messages as m
from bot.tg import TelegramError, tg
from core import db, security, utils
from core.cache import cache
from core.errors import ApiError
from services import catalog, licenses, media, notify, reviewchecks, tgfiles

log = logging.getLogger("sellers")
DEFAULTS = {"min_withdraw": 100, "fee_pct": 5.0, "commission_pct": 0.0, "signup_open": True, "max_apk_mb": 150}
MAX_VIDEO_MB = tgfiles.MAX_VIDEO_MB
STATUS_LABEL = {"draft": "Not published", "in_verification": "In verification", "live": "Live", "rejected": "Rejected"}


# ------------------------------------------------------------------ settings
def settings():
    s = db.get("/settings/seller") or {}
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS}}
    out["signup_open"] = bool(out["signup_open"])
    return out


def save_settings(d):
    mw = utils.parse_amount(d.get("min_withdraw"))
    try:
        fee = round(float(d.get("fee_pct", 0)), 2)
        com = round(float(d.get("commission_pct", 0)), 2)
        mb = int(d.get("max_apk_mb", DEFAULTS["max_apk_mb"]))
    except (TypeError, ValueError):
        raise ApiError("Enter valid numbers.")
    if not (0 <= fee < 100 and 0 <= com < 100):
        raise ApiError("Percentages must be between 0 and 99.")
    if not 10 <= mb <= 200:
        raise ApiError("Max APK size must be between 10 and 200 MB.")
    db.update("/settings/seller", {"min_withdraw": mw, "fee_pct": fee, "commission_pct": com,
                                   "signup_open": bool(d.get("signup_open", True)), "max_apk_mb": mb})
    cache.clear()


# ------------------------------------------------------------------ accounts
def get(sid):
    s = db.get(f"/sellers/{sid}") if sid else None
    return s if s and not s.get("deleted") else None


def get_or_404(sid):
    s = get(sid)
    if not s:
        raise ApiError("Seller not found.", 404)
    return s


def all_sellers():
    rows = [s for s in db.get_list("/sellers") if not s.get("deleted")]
    rows.sort(key=lambda s: s.get("created_at", 0), reverse=True)
    return rows


def current(sid):
    """For the panel session (cached)."""
    hit = cache.get(f"seller_sess:{sid}")
    if hit is None:
        hit = (db.get(f"/sellers/{sid}"),)
        cache.set(f"seller_sess:{sid}", hit, 15)
    s = hit[0]
    return s if s and not s.get("deleted") and s.get("active", True) else None


def avatar(s):
    if s.get("profile_image_url"):
        return s["profile_image_url"]
    return f"/media/tg/{s['profile_file_id']}" if s.get("profile_file_id") else ""


def public(s, admin=False):
    out = {"id": s["id"], "name": s.get("name", ""), "telegram_linked": bool(s.get("chat_id")),
           "active": s.get("active", True), "balance": round(float(s.get("balance", 0)), 2)}
    if admin:
        out.update(username=s.get("tg_username", ""), login_username=s.get("login_username", ""),
                   chat_id=s.get("chat_id", ""), profile_image_url=avatar(s), created_at=s.get("created_at", 0),
                   note=s.get("note", ""), total_earned=round(float(s.get("total_earned", 0)), 2),
                   sales_count=int(s.get("sales_count", 0)))
    return out


def create(d, actor="signup", admin=False):
    """Seller signup (self-service) or created by an admin."""
    if not admin and not settings()["signup_open"]:
        raise ApiError("Seller sign-ups are closed right now.", 403)
    name = utils.clean_text(d.get("name"), "Name", 60)
    login = utils.clean_text(d.get("login_username") or d.get("username_login"), "Username", 32)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", login):
        raise ApiError("Username: 3-32 letters, numbers, . _ -")
    pw = str(d.get("password") or "")
    problem = security.password_problem(pw)
    if problem:
        raise ApiError(problem)
    sid = utils.new_id("slr")
    if not db.set_if_absent(f"/seller_usernames/{login.lower()}", sid):
        raise ApiError("That username is already taken.")
    rec = {"id": sid, "name": name, "login_username": login, "login_lower": login.lower(),
           "password_hash": security.hash_password(pw), "active": True, "session_version": 1,
           "created_at": utils.now(), "created_by": actor, "balance": 0, "total_earned": 0, "sales_count": 0}
    if admin:
        if d.get("chat_id"):
            rec["chat_id"] = utils.clean_chat_id(d["chat_id"])
        rec["tg_username"] = utils.clean_username(d.get("username"))
        rec["note"] = utils.clean_text(d.get("note"), "Note", 300, False)
    db.set(f"/sellers/{sid}", rec)
    return rec


def update(sid, d, avatar_bytes=None, actor=""):
    """Admin: edit everything about a seller."""
    s = get_or_404(sid)
    upd = {}
    if "name" in d:
        upd["name"] = utils.clean_text(d.get("name"), "Name", 60)
    if "username" in d:
        upd["tg_username"] = utils.clean_username(d.get("username"))
    if "chat_id" in d:
        upd["chat_id"] = utils.clean_chat_id(d["chat_id"]) if str(d.get("chat_id") or "").strip() else ""
    if "note" in d:
        upd["note"] = utils.clean_text(d.get("note"), "Note", 300, False)
    if "active" in d:
        upd["active"] = bool(d.get("active"))
        upd["session_version"] = int(s.get("session_version", 1)) + 1
    login = str(d.get("login_username") or "").strip()
    if login and login.lower() != s.get("login_lower"):
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", login):
            raise ApiError("Username: 3-32 letters, numbers, . _ -")
        if not db.set_if_absent(f"/seller_usernames/{login.lower()}", sid):
            raise ApiError("That username is already taken.")
        if s.get("login_lower"):
            db.set(f"/seller_usernames/{s['login_lower']}", None)
        upd.update(login_username=login, login_lower=login.lower())
    pw = str(d.get("password") or "")
    if pw:
        problem = security.password_problem(pw)
        if problem:
            raise ApiError(problem)
        upd["password_hash"] = security.hash_password(pw)
        upd["session_version"] = int(s.get("session_version", 1)) + 1
    if avatar_bytes:
        upd["profile_image_url"] = f"/media/img/{media.save_image(avatar_bytes, 'seller_avatar', actor)}"
    if upd:
        db.update(f"/sellers/{sid}", upd)
    cache.clear()
    return get(sid)


def soft_delete(sid, actor):
    s = get_or_404(sid)
    db.update(f"/sellers/{sid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor, "active": False,
                                  "session_version": int(s.get("session_version", 1)) + 1})
    if s.get("login_lower"):
        db.set(f"/seller_usernames/{s['login_lower']}", None)
    for a in my_apps(sid):  # their apps stop selling; licenses already sold keep working
        db.update(f"/apps/{a['id']}", {"disabled": True, "disabled_by": "admin"})
    cache.clear()


def change_password(s, old, new):
    if not security.verify_password(s["password_hash"], old or ""):
        raise ApiError("Current password is wrong.", 403)
    problem = security.password_problem(new or "")
    if problem:
        raise ApiError(problem)
    db.update(f"/sellers/{s['id']}", {"password_hash": security.hash_password(new),
                                      "session_version": int(s.get("session_version", 1)) + 1})
    cache.clear()


# ------------------------------------------------------------------ telegram link
def link_token(sid):
    tok = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
    db.set(f"/seller_links/{tok}", {"seller_id": sid, "ts": utils.now()})
    return tok


def link_telegram(token, frm, profile_file_id=""):
    """The bot got /start sl_<token>. Returns the seller or None."""
    rec = db.get(f"/seller_links/{token}")
    if not rec or utils.now() - int(rec.get("ts", 0)) > 86400:
        return None
    s = get(rec["seller_id"])
    if not s:
        return None
    upd = {"chat_id": str(frm["id"]), "tg_username": frm.get("username") or "", "linked_at": utils.now()}
    if profile_file_id:
        upd["profile_file_id"] = profile_file_id
    db.update(f"/sellers/{s['id']}", upd)
    db.set(f"/seller_links/{token}", None)
    cache.clear()
    return get(s["id"])


# ------------------------------------------------------------------ notifications (panel inbox + Telegram)
def tell(s, title, text, button_text=None):
    """Panel notification + Telegram message to the seller."""
    nid = utils.ts_id()
    db.set(f"/seller_notifs/{s['id']}/{nid}", {"id": nid, "ts": utils.now(), "title": title, "text": text, "read": False})
    if s.get("chat_id"):
        notify.to_chat(s["chat_id"], f"<b>{m.esc(title)}</b>\n━━━━━━━━━━━━━━━━━━\n\n{m.esc(text)}")


def notifications(sid, limit=30):
    rows = db.get_list(f"/seller_notifs/{sid}")
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit], sum(1 for r in rows if not r.get("read"))


def mark_read(sid):
    for r in db.get_list(f"/seller_notifs/{sid}"):
        if not r.get("read"):
            db.update(f"/seller_notifs/{sid}/{r['id']}", {"read": True})


# ------------------------------------------------------------------ apps
def my_apps(sid):
    rows = [a for a in catalog.live("/apps") if a.get("seller_id") == sid]
    rows.sort(key=lambda a: a.get("created_at", 0), reverse=True)
    return rows


def review_state(app):
    """What the seller sees: in_verification while a submission is waiting, otherwise the app's own status."""
    return "in_verification" if app.get("pending_submission") else app.get("status", "live")


def resolve_category(category_id, new_name, seller_name=""):
    if new_name and str(new_name).strip():
        name = utils.clean_text(new_name, "Category name", 30)
        for c in catalog.categories():
            if c["name"].lower() == name.lower():
                return c["id"]
        cid = utils.new_id("cat")
        db.set(f"/categories/{cid}", {"id": cid, "name": name, "icon": "", "order": 999, "created_at": utils.now(),
                                      "created_by": f"seller:{seller_name}", "by_seller": True})
        return cid
    cid = str(category_id or "")
    c = db.get(f"/categories/{cid}") if cid else None
    if not c or c.get("deleted"):
        raise ApiError("Choose a category or type a new one.")
    return cid


PACKAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")


def clean_description(v):
    d = utils.clean_text(v, "Short description", 400)
    if len(d) < 10:
        raise ApiError("Short description: tell buyers what the app does (at least 10 characters).")
    return d


def clean_features(v):
    f = utils.clean_text(v, "Features", 1500)
    if len([x for x in f.splitlines() if x.strip()]) < 1 or len(f) < 5:
        raise ApiError("Features: list at least one feature (one per line).")
    return f


def create_api_app(seller, name, logo_bytes, category_id="", new_category="", description="", package_name="", contact=""):
    """Create API: everything is asked (only the support contact is optional)."""
    name = utils.clean_text(name, "App name", 60)
    cid = resolve_category(category_id, new_category, seller["name"])
    if not logo_bytes:
        raise ApiError("Upload the app logo.")
    desc = clean_description(description)
    pkg = utils.clean_text(package_name, "Package name", 120)
    if not PACKAGE_RE.fullmatch(pkg):
        raise ApiError("Package name looks wrong. Use the Android format, e.g. com.company.app")
    contact = utils.clean_text(contact, "Support contact", 80, False)
    img = f"/media/img/{media.save_image(logo_bytes, 'app_logo', seller['id'])}"
    aid = utils.new_id("app")
    app = {"id": aid, "name": name, "image_url": img, "category_id": cid, "api_key": utils.gen_api_key(),
           "description": desc, "package_name": pkg, "support_contact": contact,
           "created_at": utils.now(), "created_by": f"seller:{seller['name']}", "seller_id": seller["id"],
           "status": "draft", "sales_count": 0}
    db.set(f"/apps/{aid}", app)
    cache.clear()
    return app


def own_app(seller, app_id):
    a = catalog.get_app(app_id)
    if not a or a.get("seller_id") != seller["id"]:
        raise ApiError("App not found.", 404)
    return a


def regenerate_key(app):
    key = utils.gen_api_key()
    db.update(f"/apps/{app['id']}", {"api_key": key, "key_rotated_at": utils.now()})
    return key


def set_disabled(app, disabled, by):
    """Seller (or admin) switches an app off/on. A seller cannot undo an admin's block."""
    if not disabled and app.get("disabled") and app.get("disabled_by") == "admin" and by == "seller":
        raise ApiError("This app was disabled by the admin. Contact support.", 403)
    db.update(f"/apps/{app['id']}", {"disabled": bool(disabled), "disabled_by": by if disabled else ""})
    cache.clear()


def set_maintenance(seller, app_id, enabled, message=""):
    """app_id 'all' = every app of the seller."""
    msg = utils.clean_text(message, "Message", 300, False) or "This app is under maintenance. Please try again later."
    targets = my_apps(seller["id"]) if app_id == "all" else [own_app(seller, app_id)]
    if not targets:
        raise ApiError("You have no apps yet.")
    for a in targets:
        db.update(f"/apps/{a['id']}", {"maintenance": {"enabled": bool(enabled), "message": msg, "status_code": 503,
                                                         "by": "seller", "ts": utils.now()}})
    cache.clear()
    return len(targets)


# ------------------------------------------------------------------ files: the seller sends them to the bot, then pastes the id
FILE_KINDS = ("apk", "preview_video", "setup_video")


def attach_file(seller, kind, file_id):
    """The seller pasted a Telegram file id. Accept it only when it is one of the files THEY sent to the bot and it
    fits the slot (APK / MP4 video). Returns the upload record the draft and the submission refer to."""
    if kind not in FILE_KINDS:
        raise ApiError("Unknown file type.")
    fid = (file_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{10,400}", fid):
        raise ApiError("That does not look like a Telegram file id. Send the file to the bot and copy the id it replies with.")
    chat = str(seller.get("chat_id") or "")
    if not chat:
        raise ApiError("Connect your Telegram first (Settings → Connect Telegram).")
    rec = tgfiles.find(chat, fid)
    if not rec:
        raise ApiError("This id is not one of the files YOU sent to the bot. Send the file to the bot from the Telegram "
                       "account linked to your seller panel, then paste the id it replies with.")
    why = tgfiles.problem(kind, rec["kind"], rec["name"], rec["size"], rec["mime"], settings()["max_apk_mb"])
    if why:
        raise ApiError(why)
    uid = "upl_" + hashlib.sha1(f"{seller['id']}|{kind}|{fid}".encode()).hexdigest()[:14]
    up = {"id": uid, "seller_id": seller["id"], "kind": kind, "filename": rec["name"], "size": rec["size"],
          "mime": rec["mime"], "ts": utils.now(), "tg_file_id": fid}
    db.set(f"/seller_uploads/{uid}", up)
    return up


def my_files(seller, wanted=None):
    """Files this seller sent to the bot (newest first); `wanted` filters to what fits that slot."""
    kinds = {"apk": ("apk",), "preview_video": ("video",), "setup_video": ("video",)}.get(wanted)
    return [tgfiles.public(r) for r in tgfiles.mine(seller.get("chat_id") or "-", kinds)]


def get_upload(seller, upload_id, kind):
    if not upload_id:
        return None
    u = db.get(f"/seller_uploads/{upload_id}")
    if not u or u.get("seller_id") != seller["id"] or u.get("kind") != kind:
        raise ApiError("Upload not found. Please upload the file again.")
    return u


# ------------------------------------------------------------------ publishing
def clean_plans(raw, app_id, existing_ids):
    if not isinstance(raw, list) or not raw:
        raise ApiError("Add at least one plan.")
    if len(raw) > 8:
        raise ApiError("A maximum of 8 plans is allowed.")
    out, seen, best = [], set(), 0
    for p in raw:
        val, unit = utils.parse_validity(p.get("validity_value"), p.get("validity_unit"))
        amount = utils.parse_amount(p.get("amount"))
        if (val, unit) in seen:
            raise ApiError("Two plans have the same validity.")
        seen.add((val, unit))
        pid = str(p.get("id") or "")
        if pid and pid not in existing_ids:
            pid = ""
        is_best = bool(p.get("is_best"))
        best += is_best
        out.append({"id": pid, "amount": amount, "validity_value": val, "validity_unit": unit, "is_best": is_best})
    if best > 1:
        raise ApiError("Mark only one plan as best value.")
    return out


MAX_TRIAL_HOURS = 30 * 24
_HOURS = {"hours": 1, "days": 24, "years": 8760}


def clean_trial(raw):
    """None = untouched. {'enabled': False} removes the trial; {'enabled': True, validity...} sets it (max 30 days)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ApiError("Invalid free-trial settings.")
    if not raw.get("enabled"):
        return {"enabled": False}
    val, unit = utils.parse_validity(raw.get("validity_value"), raw.get("validity_unit"))
    if val * _HOURS[unit] > MAX_TRIAL_HOURS:
        raise ApiError("A free trial can last at most 30 days.")
    return {"enabled": True, "validity_value": val, "validity_unit": unit}


def submit(seller, app, d):
    """Create a review submission. `d` is the JSON from the publish form."""
    if not seller.get("chat_id"):
        raise ApiError("Connect your Telegram first (Settings → Connect Telegram) so we can notify you.")
    if app.get("pending_submission"):
        raise ApiError("This app is already under review. Wait for the result before sending another update.")
    first = app.get("status") in ("draft", "rejected") and not app.get("apk_id")
    name = utils.clean_text(d.get("name"), "App name", 60)
    cid = resolve_category(d.get("category_id"), d.get("new_category"), seller["name"])
    desc = clean_description(d.get("description"))
    feats = clean_features(d.get("features"))
    existing = {p["id"] for p in catalog.live("/plans") if p["app_id"] == app["id"]}
    plans = clean_plans(d.get("plans"), app["id"], existing)
    files = {}
    for kind in ("apk", "preview_video", "setup_video"):
        up = get_upload(seller, d.get(f"{kind}_upload"), kind)
        if up:
            files[kind] = {k: up.get(k, "") for k in ("id", "filename", "size", "tg_file_id")}
    if first and "apk" not in files:
        raise ApiError("Add your APK: send it to the bot and paste the file id.")
    if first and "preview_video" not in files:
        raise ApiError("Add the preview video: send the .mp4 to the bot as a video and paste the file id.")
    if "apk" in files and not reviewchecks.ready(f"draft:{app['id']}"):
        miss = ", ".join(c["label"] for c in reviewchecks.state(f"draft:{app['id']}")["checks"] if c["required"] and not c["passed"])
        raise ApiError("Finish the live APK tests before submitting. Still missing: " + miss + ".")
    trial = clean_trial(d.get("trial"))
    logo = d.get("logo_media")  # media id returned by the logo upload endpoint
    if logo:
        rec = db.get(f"/media/{logo}")
        if not rec or rec.get("by") != seller["id"]:
            raise ApiError("Upload the logo again.")
        files["logo"] = f"/media/img/{logo}"
    if first and "logo" not in files and not app.get("image_url"):
        raise ApiError("Upload your app logo.")
    sid_ = utils.new_id("sub")
    sub = {"id": sid_, "app_id": app["id"], "app_name": name, "seller_id": seller["id"], "seller_name": seller["name"],
           "kind": "new" if first else "update", "status": "pending", "created_at": utils.now(),
           "data": {"name": name, "category_id": cid, "description": desc, "features": feats, "plans": plans},
           "files": files, "note": "", "reason": ""}
    if trial is not None:
        sub["data"]["trial"] = trial
    sub["seller_checks"] = [{"key": c["key"], "label": c["label"], "passed": c["passed"]}
                            for c in reviewchecks.state(f"draft:{app['id']}")["checks"]]
    db.set(f"/submissions/{sid_}", sub)
    db.update(f"/apps/{app['id']}", {"pending_submission": sid_, "status": app.get("status") if app.get("status") == "live" else "in_verification"})
    draft_close(app)
    cache.clear()
    notify.to_admins("🆕 <b>App submitted for review</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                     f"📱 <b>{m.esc(name)}</b> ({'new' if first else 'update'})\n👤 Seller: {m.esc(seller['name'])}\n\n"
                     "Open the panel → <b>Seller → Submissions</b> to test and approve.")
    return sub


def submissions(status=None, seller_id=None):
    rows = db.query_equal("/submissions", "status", status) if status else db.get_list("/submissions")
    if seller_id:
        rows = [r for r in rows if r.get("seller_id") == seller_id]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def get_submission(sub_id):
    sub = db.get(f"/submissions/{sub_id}")
    if not sub:
        raise ApiError("Submission not found.", 404)
    return sub


def save_review(sub_id, d, actor):
    """Admin edits while reviewing: data, Telegram file ids, note."""
    sub = get_submission(sub_id)
    if sub["status"] != "pending":
        raise ApiError("This submission was already decided.")
    upd = {}
    if "note" in d:
        upd["note"] = utils.clean_text(d.get("note"), "Note", 600, False)
    data = dict(sub["data"])
    for k, label, mx in (("name", "App name", 60), ("description", "Description", 400), ("features", "Features", 1500)):
        if k in d:
            data[k] = utils.clean_text(d[k], label, mx, k != "description" and k != "features")
    if "category_id" in d:
        data["category_id"] = resolve_category(d["category_id"], "", "")
    if "plans" in d:
        existing = {p["id"] for p in catalog.live("/plans") if p["app_id"] == sub["app_id"]}
        data["plans"] = clean_plans(d["plans"], sub["app_id"], existing)
    if "trial" in d:
        t = clean_trial(d.get("trial"))
        if t is None:
            data.pop("trial", None)
        else:
            data["trial"] = t
    upd["data"] = data
    files = dict(sub.get("files") or {})
    for kind, field in (("apk", "apk_id"), ("preview_video", "video_id"), ("setup_video", "setup_video_id")):
        if field in d:
            files.setdefault(kind, {})
            files[kind] = {**files[kind], "tg_file_id": utils.clean_text(d[field], "Telegram id", 400, False)}
    upd["files"] = files
    upd["edited_by"] = actor
    db.update(f"/submissions/{sub_id}", upd)
    return get_submission(sub_id)


def approve(sub_id, actor, force=False):
    sub = get_submission(sub_id)
    if sub["status"] != "pending":
        raise ApiError("This submission was already decided.")
    st = reviewchecks.state(sub_id)
    if not st["ready"] and not force:
        missing = ", ".join(c["label"] for c in st["checks"] if c["required"] and not c["passed"])
        raise ApiError(f"Finish the live checks before approving. Still missing: {missing}.")
    app = catalog.get_app(sub["app_id"])
    if not app:
        raise ApiError("The app no longer exists.", 404)
    files, data = sub.get("files") or {}, sub["data"]
    apk_id = (files.get("apk") or {}).get("tg_file_id") or ("" if "apk" in files else app.get("apk_id", ""))
    if not apk_id:
        raise ApiError("Enter the APK's Telegram id first: send the APK to the bot, copy the id it replies with and "
                       "paste it in the APK id field.")
    upd = {"name": data["name"], "category_id": data["category_id"], "description": data.get("description", ""),
           "features": data.get("features", ""), "apk_id": apk_id, "status": "live", "pending_submission": "",
           "approved_at": utils.now(), "review_reason": ""}
    if files.get("logo"):
        upd["image_url"] = files["logo"]
    for kind, field in (("preview_video", "video_id"), ("setup_video", "setup_video_id")):
        if kind in files:
            upd[field] = files[kind].get("tg_file_id") or app.get(field, "")
    for kind, field in (("apk", "apk_storage"), ("preview_video", "preview_storage"), ("setup_video", "setup_storage")):
        if kind in files:
            upd[field] = {k: files[kind].get(k, "") for k in ("filename", "size")}
    upd["review_maintenance"] = None
    db.update(f"/apps/{app['id']}", upd)
    _sync_plans(app["id"], data["plans"], actor)
    _sync_trial(app["id"], data.get("trial"), actor)
    if force and not st["ready"]:
        db.update(f"/submissions/{sub_id}", {"forced_without_checks": True})
    db.update(f"/submissions/{sub_id}", {"status": "approved", "decided_at": utils.now(), "decided_by": actor})
    cache.clear()
    s = get(sub["seller_id"])
    if s:
        tell(s, "✅ App approved", f"“{data['name']}” is now live in the shop. Customers can buy it right away!")
    return get_submission(sub_id)


def reject(sub_id, reason, actor):
    sub = get_submission(sub_id)
    if sub["status"] != "pending":
        raise ApiError("This submission was already decided.")
    reason = utils.clean_text(reason, "Reason", 400)
    app = catalog.get_app(sub["app_id"]) or {}
    upd = {"pending_submission": "", "review_reason": reason, "review_maintenance": None}
    if app.get("status") != "live":
        upd["status"] = "rejected"
    if app:
        db.update(f"/apps/{app['id']}", upd)
    db.update(f"/submissions/{sub_id}", {"status": "rejected", "reason": reason, "decided_at": utils.now(),
                                         "decided_by": actor})
    cache.clear()
    s = get(sub["seller_id"])
    if s:
        tell(s, "❌ App rejected", f"“{sub['app_name']}” was not approved.\nReason: {reason}\n\n"
                                  "Fix the issue and publish it again from the panel.")
    return get_submission(sub_id)


def _sync_plans(app_id, plans, actor):
    """Make the app's plans equal to the approved submission (keeps plan ids so reseller prices survive)."""
    current = {p["id"]: p for p in catalog.live("/plans") if p["app_id"] == app_id}
    keep = set()
    for n, p in enumerate(plans, start=1):  # the seller's list order is the initial sort number (the admin can change it)
        if p["id"] and p["id"] in current:
            upd = {"amount": p["amount"], "validity_value": p["validity_value"],
                   "validity_unit": p["validity_unit"], "is_best": p["is_best"], "kind": "paid"}
            if not current[p["id"]].get("sort"):
                upd["sort"] = n
            db.update(f"/plans/{p['id']}", upd)
            keep.add(p["id"])
        else:
            pid = utils.new_id("plan")
            db.set(f"/plans/{pid}", {"id": pid, "app_id": app_id, "kind": "paid", "is_best": p["is_best"],
                                     "amount": p["amount"], "validity_value": p["validity_value"],
                                     "validity_unit": p["validity_unit"], "created_at": utils.now(), "sort": n, "upi_id": "",
                                     "created_by": f"approved by {actor}"})
            keep.add(pid)
    for pid, p in current.items():
        if pid not in keep and p.get("kind") != "trial":
            db.update(f"/plans/{pid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor})


def _sync_trial(app_id, trial, actor):
    """The seller's free-trial plan (one per app). None = the seller did not touch it."""
    if trial is None:
        return
    cur = next((p for p in catalog.live("/plans") if p["app_id"] == app_id and p.get("kind") == "trial"), None)
    if trial.get("enabled"):
        upd = {"kind": "trial", "amount": 0, "is_best": False, "validity_value": trial["validity_value"],
               "validity_unit": trial["validity_unit"]}
        if cur:
            db.update(f"/plans/{cur['id']}", upd)
        else:
            pid = utils.new_id("plan")
            db.set(f"/plans/{pid}", {"id": pid, "app_id": app_id, "created_at": utils.now(), "created_by": f"approved by {actor}", **upd})
    elif cur:
        db.update(f"/plans/{cur['id']}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor})


# ------------------------------------------------------------------ money in: sales
def credit_sale(app, txn, lic):
    """A license for a seller's app was sold: credit the seller (list price minus platform commission)."""
    sid = app.get("seller_id")
    seller = get(sid)
    if not seller:
        return None
    plan = catalog.get_plan(txn.get("plan_id", "")) or {}
    gross = float(plan.get("amount", txn.get("plan_price", 0)) or 0)
    if gross <= 0:
        return None
    pct = float(settings()["commission_pct"])
    net = round(gross * (100 - pct) / 100, 2)
    eid = utils.ts_id()
    u = txn.get("user") or {}
    db.set(f"/seller_earnings/{eid}", {
        "id": eid, "ts": utils.now(), "seller_id": sid, "app_id": app["id"], "app_name": app.get("name", ""),
        "plan_id": txn.get("plan_id", ""), "plan_label": txn.get("plan_label", ""), "gross": gross,
        "commission_pct": pct, "amount": net, "license_key": lic["key"], "txn_id": txn["id"],
        "reseller_id": txn.get("reseller_id", ""),
        "user": {"chat_id": u.get("chat_id", ""), "full_name": u.get("full_name", ""),
                 "username": u.get("username", ""), "profile_image_url": u.get("profile_image_url", "")}})
    db.transaction(f"/sellers/{sid}/balance", lambda c: round(float(c or 0) + net, 2))
    db.transaction(f"/sellers/{sid}/total_earned", lambda c: round(float(c or 0) + net, 2))
    db.transaction(f"/sellers/{sid}/sales_count", lambda c: int(c or 0) + 1)
    if seller.get("chat_id"):
        notify.to_chat(seller["chat_id"],
                       "💰 <b>New sale!</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                       f"📱 {m.esc(app.get('name'))} — {m.esc(txn.get('plan_label'))}\n"
                       f"🙋 {m.esc(u.get('full_name'))}\n💵 You earned <b>{m.esc(utils.inr(net))}</b>",
                       effect="5046509860389126442")
    return net


def earning_rows(sid=None, lo=0, hi=utils.FAR_FUTURE, app_id=None):
    rows = db.query_equal("/seller_earnings", "seller_id", sid) if sid else db.get_list("/seller_earnings")
    rows = [r for r in rows if lo <= r.get("ts", 0) <= hi]
    if app_id:
        rows = [r for r in rows if r.get("app_id") == app_id]
    rows.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
    return rows


def sum_rows(rows):
    return {"earned": round(sum(r["amount"] for r in rows), 2), "sales": len(rows)}


def range_summary(sid=None, start=None, end=None, app_id=None):
    out = {}
    names = ["today", "yesterday", "all"] + (["custom"] if start and end else [])
    for n in names:
        lo, hi = utils.resolve_range(n, start, end)
        out[n] = sum_rows(earning_rows(sid, lo, hi, app_id))
    return out


# ------------------------------------------------------------------ money out: withdrawals
def quote_withdraw(amount):
    cfg = settings()
    fee = round(amount * cfg["fee_pct"] / 100, 2)
    receive = int(math.floor(amount - fee))
    return {"fee_pct": cfg["fee_pct"], "fee": fee, "receive": receive}


def request_withdraw(seller, name, upi_id, amount):
    cfg = settings()
    name = utils.clean_text(name, "Name", 60)
    upi_id = utils.clean_text(upi_id, "UPI ID", 60)
    if not re.fullmatch(r"[A-Za-z0-9._\-]{2,64}@[A-Za-z0-9.\-]{2,32}", upi_id):
        raise ApiError("Enter a valid UPI ID (like name@bank).")
    try:
        amt = int(float(amount))
    except (TypeError, ValueError):
        raise ApiError("Enter a whole-number amount.")
    if amt < cfg["min_withdraw"]:
        raise ApiError(f"Minimum withdrawal is {utils.inr(cfg['min_withdraw'])}.")
    wid = utils.new_id("swd")
    state = {"ok": False}

    def fn(cur):
        cur = float(cur or 0)
        state["ok"] = cur + 1e-9 >= amt
        return round(cur - amt, 2) if state["ok"] else round(cur, 2)

    db.transaction(f"/sellers/{seller['id']}/balance", fn)
    if not state["ok"]:
        raise ApiError("You don't have enough balance.")
    q = quote_withdraw(amt)
    rec = {"id": wid, "seller_id": seller["id"], "seller_name": seller["name"], "name": name, "upi_id": upi_id,
           "amount": amt, "fee_pct": q["fee_pct"], "fee": q["fee"], "receive": q["receive"], "status": "pending",
           "created_at": utils.now()}
    db.set(f"/seller_withdrawals/{wid}", rec)
    notify.to_admins("💸 <b>Seller withdrawal request</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                     f"👤 {m.esc(seller['name'])}\n💰 Amount: <b>{m.esc(utils.inr(amt))}</b>  → pay "
                     f"<b>{m.esc(utils.inr(q['receive']))}</b>\n🏦 UPI: <code>{m.esc(upi_id)}</code>\n"
                     f"🧾 <code>{wid}</code>\n\nOpen <b>Seller → Withdrawals</b> in the panel.")
    return rec


def withdrawals(sid=None, status=""):
    rows = db.query_equal("/seller_withdrawals", "seller_id", sid) if sid else db.get_list("/seller_withdrawals")
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def resolve_withdraw(wid, approve, reason, photo_bytes, actor):
    rec = db.get(f"/seller_withdrawals/{wid}")
    if not rec:
        raise ApiError("Withdrawal not found.", 404)
    reason = utils.clean_text(reason, "Reason", 300, required=not approve)
    new = "approved" if approve else "rejected"
    state = {"ok": False}

    def fn(cur):
        state["ok"] = cur == "pending"
        return new if state["ok"] else cur

    db.transaction(f"/seller_withdrawals/{wid}/status", fn)
    if not state["ok"]:
        raise ApiError("This withdrawal was already decided.", 409)
    upd = {"resolved_at": utils.now(), "resolved_by": actor, "reason": reason}
    if photo_bytes:
        upd["proof_media_id"] = media.save_image(photo_bytes, "seller_withdraw_proof", actor)
    db.update(f"/seller_withdrawals/{wid}", upd)
    s = get(rec["seller_id"])
    if not approve:
        db.transaction(f"/sellers/{rec['seller_id']}/balance", lambda c: round(float(c or 0) + rec["amount"], 2))
    if s:
        if approve:
            tell(s, "✅ Withdrawal approved", f"{utils.inr(rec['receive'])} is on its way to {rec['upi_id']}."
                 + (f"\nNote: {reason}" if reason else ""))
        else:
            tell(s, "❌ Withdrawal rejected", f"Reason: {reason}\n{utils.inr(rec['amount'])} was returned to your balance.")
        if photo_bytes and s.get("chat_id"):
            try:
                tg.send_media("photo", s["chat_id"], photo_bytes, "Payment proof", filename="proof.png")
            except TelegramError:
                pass
    return db.get(f"/seller_withdrawals/{wid}")


# ------------------------------------------------------------------ admin overview
def overview(lo, hi):
    rows = earning_rows(None, lo, hi)
    signups = [s for s in all_sellers() if lo <= s.get("created_at", 0) <= hi]
    return {"earned": round(sum(r["amount"] for r in rows), 2), "sales": len(rows), "signups": len(signups),
            "sellers_earning": len({r["seller_id"] for r in rows})}


def seller_row(s):
    apps = my_apps(s["id"])
    return {**public(s, admin=True), "apps_published": sum(1 for a in apps if a.get("status") == "live"),
            "apps_total": len(apps)}


# ------------------------------------------------------------------ review test tools (admin)
def test_license_for(sub_id, key):
    sub = get_submission(sub_id)
    lic = licenses.get_license(key)
    if not lic.get("test") or lic.get("app_id") != sub["app_id"]:
        raise ApiError("That is not a test key of this app.", 404)
    return sub, lic


def create_test_key(sub_id, hours, actor):
    sub = get_submission(sub_id)
    if sub["status"] != "pending":
        raise ApiError("This submission was already decided.")
    app = catalog.get_app(sub["app_id"])
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        raise ApiError("Hours must be a number.")
    if not 1 <= hours <= 720:
        raise ApiError("Test key validity must be 1-720 hours.")
    lic = licenses.create_test_license(app, hours, actor, f"review {sub_id}")
    db.update(f"/submissions/{sub_id}", {"test_keys": {**(sub.get("test_keys") or {}), lic["key"]: utils.now()}})
    return lic


def test_keys(sub_id):
    sub = get_submission(sub_id)
    out = []
    for key in (sub.get("test_keys") or {}):
        lic = db.get(f"/licenses/{key}")
        if lic and not lic.get("deleted"):
            out.append({"key": key, "status": licenses.status_of(lic), "expires_at": lic["expires_at"],
                        "device": lic.get("device_name") or ("linked" if lic.get("device_id") else ""),
                        "bound": bool(lic.get("device_id")), "created_at": lic["created_at"]})
    out.sort(key=lambda x: -x["created_at"])
    return out


def set_review_maintenance(sub_id, enabled):
    sub = get_submission(sub_id)
    db.update(f"/apps/{sub['app_id']}", {"review_maintenance": {"enabled": bool(enabled), "ts": utils.now()} if enabled else None})
    cache.clear()


# ================================================================== drafts: the publish wizard saves as you go
DRAFT_STEPS = 6  # 1 details · 2 media · 3 plans · 4 APK · 5 live tests · 6 review & submit
MAX_TEST_KEYS = 6


def draft_path(app_id):
    return f"/app_drafts/{app_id}"


def get_draft(app_id):
    return db.get(draft_path(app_id))


def prefill(app):
    """Values of the live app (or empty ones) to start a new draft from."""
    plans = [{"id": p["id"], "amount": p["amount"], "validity_value": p["validity_value"],
              "validity_unit": p["validity_unit"], "is_best": bool(p.get("is_best"))}
             for p in catalog.live("/plans") if p["app_id"] == app["id"] and p.get("kind") != "trial"]
    trial = next((p for p in catalog.live("/plans") if p["app_id"] == app["id"] and p.get("kind") == "trial"), None)
    return {"name": app.get("name", ""), "category_id": app.get("category_id", ""), "new_category": "",
            "description": app.get("description", ""), "features": app.get("features", ""),
            "plans": plans or [{"amount": 99, "validity_value": 7, "validity_unit": "days", "is_best": False},
                               {"amount": 299, "validity_value": 30, "validity_unit": "days", "is_best": True}],
            "trial": ({"enabled": True, "validity_value": trial["validity_value"], "validity_unit": trial["validity_unit"]}
                      if trial else {"enabled": False, "validity_value": 1, "validity_unit": "days"})}


def _soft_plans(raw):
    out = []
    for p in (raw if isinstance(raw, list) else [])[:8]:
        if isinstance(p, dict):
            out.append({"id": str(p.get("id") or "")[:60], "amount": str(p.get("amount", ""))[:12],
                        "validity_value": str(p.get("validity_value", ""))[:6],
                        "validity_unit": str(p.get("validity_unit", "days"))[:6], "is_best": bool(p.get("is_best"))})
    return out


def draft_save(seller, app, d):
    """Autosave (partial, unvalidated): the seller can leave at any moment and continue later."""
    cur = get_draft(app["id"]) or {}
    upd = {"app_id": app["id"], "seller_id": seller["id"], "updated_at": utils.now()}
    if not cur:
        upd["created_at"] = utils.now()
    if "step" in d:
        try:
            upd["step"] = max(1, min(int(d["step"]), DRAFT_STEPS))
        except (TypeError, ValueError):
            pass
    if isinstance(d.get("data"), dict):
        src, data = d["data"], dict(cur.get("data") or {})
        for k, mx in (("name", 60), ("description", 400), ("features", 1500), ("new_category", 30), ("category_id", 60)):
            if k in src:
                data[k] = str(src[k] or "")[:mx]
        if "plans" in src:
            data["plans"] = _soft_plans(src["plans"])
        if isinstance(src.get("trial"), dict):
            t = src["trial"]
            data["trial"] = {"enabled": bool(t.get("enabled")), "validity_value": str(t.get("validity_value", 1))[:4],
                             "validity_unit": str(t.get("validity_unit", "days"))[:6]}
        upd["data"] = data
    if isinstance(d.get("uploads"), dict):
        ups = dict(cur.get("uploads") or {})
        for kind in ("apk", "preview_video", "setup_video"):
            if kind in d["uploads"]:
                uid = d["uploads"][kind]
                if not uid:
                    ups.pop(kind, None)
                else:
                    u = get_upload(seller, uid, kind)
                    ups[kind] = {"id": u["id"], "filename": u["filename"], "size": u["size"], "ts": u["ts"], "file_id": u.get("tg_file_id", "")}
        if "logo" in d["uploads"]:
            mid = d["uploads"]["logo"]
            rec = db.get(f"/media/{mid}") if mid else None
            if mid and (not rec or rec.get("by") != seller["id"]):
                raise ApiError("Upload the logo again.")
            if mid:
                ups["logo"] = {"media": mid, "url": f"/media/img/{mid}", "filename": str(d.get("logo_name") or "logo")[:60],
                               "size": int(d.get("logo_size") or 0)}
            else:
                ups.pop("logo", None)
        upd["uploads"] = ups
    db.update(draft_path(app["id"]), upd)
    return get_draft(app["id"])


def draft_close(app):
    """Submitted or discarded: remove the draft, end the test mode and retire the test keys."""
    d = get_draft(app["id"]) or {}
    for key in (d.get("test_keys") or {}):
        try:
            licenses.revoke(key, "test finished", "system")
        except ApiError:
            pass
    db.set(draft_path(app["id"]), None)
    db.update(f"/apps/{app['id']}", {"draft_test": False, "review_maintenance": None})
    cache.clear()


# ------------------------------------------------------------------ seller live testing (before submitting)
def draft_ctx(app):
    return f"draft:{app['id']}"


def draft_test_state(app):
    d = get_draft(app["id"]) or {}
    st = reviewchecks.state(draft_ctx(app))
    keys = []
    for key in (d.get("test_keys") or {}):
        lic = db.get(f"/licenses/{key}")
        if lic and not lic.get("deleted"):
            keys.append({"key": key, "status": licenses.status_of(lic), "expires_at": lic["expires_at"],
                         "device": lic.get("device_name") or ("linked" if lic.get("device_id") else ""),
                         "bound": bool(lic.get("device_id")), "created_at": lic["created_at"]})
    keys.sort(key=lambda x: -x["created_at"])
    return {**st, "test_keys": keys, "maintenance": bool((app.get("review_maintenance") or {}).get("enabled"))}


def draft_test_key(seller, app, hours):
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        raise ApiError("Hours must be a number.")
    if not 1 <= hours <= 72:
        raise ApiError("Test key validity must be 1-72 hours.")
    d = get_draft(app["id"]) or draft_save(seller, app, {"step": 5})
    if len(d.get("test_keys") or {}) >= MAX_TEST_KEYS:
        raise ApiError(f"You can create up to {MAX_TEST_KEYS} test keys. Use ↩️ Restore on an existing one.")
    lic = licenses.create_test_license(app, hours, f"seller:{seller['name']}", "seller draft test")
    db.update(f"/licenses/{lic['key']}", {"review_ctx": draft_ctx(app)})
    db.update(draft_path(app["id"]), {f"test_keys/{lic['key']}": utils.now()})
    db.update(f"/apps/{app['id']}", {"draft_test": True})
    cache.clear()
    return lic


def draft_key_action(seller, app, key, action):
    d = get_draft(app["id"]) or {}
    if key not in (d.get("test_keys") or {}):
        raise ApiError("That is not one of your test keys.", 404)
    who = f"seller:{seller['name']}"
    if action == "revoke":
        licenses.revoke(key, "seller test", who)
    elif action == "expire":
        licenses.expire_now(key, who)
    elif action == "reset-device":
        licenses.reset_device(key, who)
    elif action == "restore":
        lic = licenses.get_license(key)
        db.update(f"/licenses/{key}", {"status": "active", "revoked_reason": "",
                                        "expires_at": max(int(lic["expires_at"]), utils.now()) + 6 * 3600})
    else:
        raise ApiError("Unknown action.", 404)


def draft_set_maintenance(app, enabled):
    db.update(f"/apps/{app['id']}", {"review_maintenance": {"enabled": True, "ts": utils.now()} if enabled else None,
                                      "draft_test": True})
    cache.clear()


# ================================================================== "Become Seller" inside the Telegram bot
_PW_ALPHA = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_password(n=20):
    """Random, unique, easy to type (no look-alike characters). Always has lower, upper and digit."""
    while True:
        pw = "".join(secrets.choice(_PW_ALPHA) for _ in range(n))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def seller_for_chat(chat_id):
    sid = db.get(f"/seller_usernames/{str(chat_id).lower()}")
    return get(sid) if sid else None


def seller_by_chat(chat_id):
    """The seller whose Telegram is linked to this chat (bot-created sellers AND panel-created ones that tapped Connect Telegram)."""
    found = seller_for_chat(chat_id)
    if found:
        return found
    cid = str(chat_id)
    return next((x for x in all_sellers() if str(x.get("chat_id") or "") == cid and x.get("active", True)), None)


def create_from_bot(chat_id, full_name, tg_username="", profile_file_id=""):
    """The user tapped Become Seller. Username = their Telegram chat id, password = random.
    Returns (seller, password, created). An existing seller gets a fresh password (the old one is invalidated)."""
    existing = seller_for_chat(chat_id)
    pw = gen_password()
    if existing:
        db.update(f"/sellers/{existing['id']}", {"password_hash": security.hash_password(pw), "chat_id": str(chat_id),
                                                 "session_version": int(existing.get("session_version", 1)) + 1,
                                                 "active": True})
        cache.clear()
        return get(existing["id"]), pw, False
    if not settings()["signup_open"]:
        raise ApiError("Seller sign-ups are closed right now.", 403)
    name = re.sub(r"[^\w .\-]", "", full_name or "", flags=re.UNICODE).strip()[:60] or f"Seller {str(chat_id)[-4:]}"
    rec = create({"name": name, "login_username": str(chat_id), "password": pw, "chat_id": str(chat_id),
                  "username": tg_username or ""}, actor="bot", admin=True)
    if profile_file_id:
        db.update(f"/sellers/{rec['id']}", {"profile_file_id": profile_file_id})
    notify.to_admins("🆕 <b>New seller joined from the bot</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                     f"👤 {m.esc(name)}\n🆔 <code>{chat_id}</code>")
    return get(rec["id"]), pw, True
