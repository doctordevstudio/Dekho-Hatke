import re

from flask import g, jsonify

from core import db, security, utils
from core.audit import log_action
from core.errors import ApiError
from . import ROLE_LABEL, body, bp, require_admin_role

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def _public(a):
    return {"id": a["id"], "username": a["username"], "role": a["role"], "role_label": ROLE_LABEL.get(a["role"], a["role"]), "active": a.get("active", True),
            "created_at": a.get("created_at", 0), "created_by": a.get("created_by", ""),
            "last_login": a.get("last_login", 0)}


@bp.get("/admins")
def list_admins():
    require_admin_role()
    rows = [a for a in db.get_list("/admins") if not a.get("deleted")]
    rows.sort(key=lambda a: a.get("created_at", 0))
    return jsonify(items=[_public(a) for a in rows])


@bp.post("/admins")
def create_admin():
    """Creates a VIEW-ONLY admin (they can see everything, change nothing)."""
    require_admin_role()
    d = body()
    username = utils.clean_text(d.get("username"), "Username", 32)
    if not USERNAME_RE.match(username):
        raise ApiError("Username: 3-32 characters (letters, numbers, . _ -).")
    password = str(d.get("password", ""))
    problem = security.password_problem(password)
    if problem:
        raise ApiError(problem)
    aid = utils.new_id("adm")
    if not db.set_if_absent(f"/admin_usernames/{username.lower()}", aid):
        raise ApiError("That username is already taken.")
    db.set(f"/admins/{aid}", {"id": aid, "username": username, "username_lower": username.lower(),
                              "password_hash": security.hash_password(password), "role": "viewer", "active": True,
                              "session_version": 1, "created_at": utils.now(), "created_by": g.admin["username"]})
    log_action("admin.created", f"{username} (view only)", target=aid)
    return jsonify(ok=True, id=aid), 201


def _target(aid):
    a = db.get(f"/admins/{aid}")
    if not a or a.get("deleted"):
        raise ApiError("Admin not found.", 404)
    if a["id"] == g.admin["id"]:
        raise ApiError("You cannot change your own account here.")
    if a["role"] == "admin":
        raise ApiError("The main admin cannot be modified.", 403)
    return a


@bp.patch("/admins/<aid>")
def edit_admin(aid):
    require_admin_role()
    a = _target(aid)
    d = body()
    upd = {}
    if "active" in d:
        upd["active"] = bool(d["active"])
        upd["session_version"] = int(a.get("session_version", 1)) + 1  # kicks active sessions out
    if d.get("password") and a["role"] == "admin2":
        raise ApiError("The 2nd admin's password is managed with the ADMIN2_PASSWORD environment variable.")
    if d.get("password"):
        problem = security.password_problem(str(d["password"]))
        if problem:
            raise ApiError(problem)
        upd["password_hash"] = security.hash_password(str(d["password"]))
        upd["session_version"] = int(a.get("session_version", 1)) + 1
    if not upd:
        raise ApiError("Nothing to change.")
    db.update(f"/admins/{aid}", upd)
    log_action("admin.edited", ("password reset " if "password_hash" in upd else "") +
               (("enabled" if upd["active"] else "disabled") if "active" in upd else ""), target=a["username"])
    return jsonify(ok=True)


@bp.delete("/admins/<aid>")
def delete_admin(aid):
    require_admin_role()
    a = _target(aid)
    db.update(f"/admins/{aid}", {"deleted": True, "active": False, "deleted_at": utils.now(),
                                 "deleted_by": g.admin["username"],
                                 "session_version": int(a.get("session_version", 1)) + 1})
    log_action("admin.deleted", a["username"], target=aid)
    return jsonify(ok=True)
