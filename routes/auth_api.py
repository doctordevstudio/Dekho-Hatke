from flask import g, jsonify, session

from core import db, security, utils
from core.audit import log_action
from core.errors import ApiError
from . import body, bp


@bp.get("/me")
def me():
    return jsonify(**security.public_admin(g.admin), csrf=security.csrf_token())


@bp.post("/logout")
def logout():
    log_action("auth.logout")
    session.clear()
    return jsonify(ok=True)


@bp.post("/me/password")
def change_password():
    d = body()
    cur, new = str(d.get("current", "")), str(d.get("new", ""))
    if not security.verify_password(g.admin["password_hash"], cur):
        raise ApiError("Current password is wrong.", 403)
    problem = security.password_problem(new)
    if problem:
        raise ApiError(problem)
    sv = int(g.admin.get("session_version", 1)) + 1
    db.update(f"/admins/{g.admin['id']}", {"password_hash": security.hash_password(new), "session_version": sv,
                                           "password_changed_at": utils.now()})
    session["sv"] = sv  # keep this session alive, kill all others
    log_action("auth.password_changed")
    return jsonify(ok=True)
