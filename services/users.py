from core import db, utils


def full_name_of(tg_user):
    name = " ".join(x for x in (tg_user.get("first_name"), tg_user.get("last_name")) if x).strip()
    return name or tg_user.get("username") or str(tg_user.get("id"))


def user_path(chat_id, tenant=""):
    return f"/r_users/{tenant}/{chat_id}" if tenant else f"/users/{chat_id}"


def scoped(chat_id, tenant=""):
    """Id used for wallet / rewards / referral data. Main-bot users keep their plain chat id; a reseller's
    customers are namespaced so the same person has a separate wallet in every store."""
    return f"{tenant}~{chat_id}" if tenant else str(chat_id)


def chat_of(sid):
    return str(sid).split("~", 1)[-1]


def get_user(chat_id, tenant=""):
    return db.get(user_path(chat_id, tenant))


def upsert_user(chat_id, full_name=None, username=None, profile_image_url=None,
                profile_file_id=None, source="bot", tenant=""):
    """Create or refresh a user (main bot: /users, reseller bot: /r_users/<rid>). Only non-empty values overwrite."""
    chat_id = str(chat_id)
    path = user_path(chat_id, tenant)
    u = db.get(path)
    t = utils.now()
    if not u:
        u = {"chat_id": chat_id, "id": chat_id, "full_name": full_name or chat_id, "username": username or "",
             "profile_image_url": profile_image_url or "", "profile_file_id": profile_file_id or "",
             "joined_at": t, "last_seen": t, "source": source}
        db.set(path, u)
        return u, True
    upd = {"last_seen": t}
    if full_name and full_name != u.get("full_name"):
        upd["full_name"] = full_name
    if username is not None and username != u.get("username") and (username or source == "bot"):
        upd["username"] = username
    if profile_image_url and not u.get("profile_image_url"):
        upd["profile_image_url"] = profile_image_url
    if profile_file_id:
        upd["profile_file_id"] = profile_file_id
        upd["profile_image_url"] = profile_image_url or u.get("profile_image_url", "")
    db.update(path, upd)
    u.update(upd)
    return u, False
