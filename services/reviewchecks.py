"""Live verification checks for a seller app that is waiting for approval.

The admin generates a TEST license key, installs the APK and uses it. Every call the app makes to the license API with
that key is observed here and ticks a check automatically:

  login        the key was accepted (code OK)
  device       the same key from a *different* device was refused (DEVICE_MISMATCH)
  maintenance  the app answered with the maintenance response while maintenance was switched on (MAINTENANCE)
  revoke       the revoked key was refused (LICENSE_REVOKED)
  expire       (optional) the expired key was refused (LICENSE_EXPIRED)

The first four must pass before the Approve button unlocks.
"""
from core import db, utils

CHECKS = [
    ("login", "Login / logic", True, "Open the app and log in with the test key."),
    ("device", "Device uniqueness", True, "Log in with the same key on a second device — it must be refused."),
    ("maintenance", "Maintenance mode", True, "Switch maintenance on below, then open the app."),
    ("revoke", "Revoked key", True, "Revoke the test key below, then open the app."),
    ("expire", "Expired key (optional)", False, "Expire the test key below, then open the app."),
]
CODE_TO_CHECK = {"OK": "login", "DEVICE_MISMATCH": "device", "MAINTENANCE": "maintenance",
                 "LICENSE_REVOKED": "revoke", "LICENSE_EXPIRED": "expire"}
CODE_LABEL = {"OK": "Key accepted", "DEVICE_MISMATCH": "Refused: other device", "MAINTENANCE": "Maintenance response",
              "LICENSE_REVOKED": "Refused: revoked", "LICENSE_EXPIRED": "Refused: expired",
              "LICENSE_NOT_FOUND": "Key not found", "INVALID_DEVICE_ID": "Bad device id"}
MAX_EVENTS = 40


def observe(app, key, code, device_id="", device_name="", app_version=""):
    """Called by the public API for calls made with a test key while the app is under review."""
    sub_id = app.get("pending_submission")
    if not sub_id:
        return
    ev_id = utils.ts_id()
    db.set(f"/submissions/{sub_id}/check_events/{ev_id}", {
        "id": ev_id, "ts": utils.now(), "code": code, "label": CODE_LABEL.get(code, code),
        "device": (device_name or device_id or "")[:40], "device_id_tail": str(device_id)[-6:], "version": app_version[:20]})
    check = CODE_TO_CHECK.get(code)
    if check and not db.get(f"/submissions/{sub_id}/checks/{check}"):
        db.set(f"/submissions/{sub_id}/checks/{check}", {"passed": True, "ts": utils.now(), "detail": CODE_LABEL.get(code, code)})
    events = db.get(f"/submissions/{sub_id}/check_events") or {}
    if len(events) > MAX_EVENTS + 10:  # keep the log short
        for old in sorted(events)[:len(events) - MAX_EVENTS]:
            db.set(f"/submissions/{sub_id}/check_events/{old}", None)


def state(sub_id):
    sub = db.get(f"/submissions/{sub_id}") or {}
    done = sub.get("checks") or {}
    rows = [{"key": k, "label": label, "required": req, "hint": hint, "passed": bool((done.get(k) or {}).get("passed")),
             "ts": (done.get(k) or {}).get("ts", 0)} for k, label, req, hint in CHECKS]
    events = sorted((sub.get("check_events") or {}).values(), key=lambda e: e["id"], reverse=True)[:15]
    return {"checks": rows, "ready": all(r["passed"] for r in rows if r["required"]), "events": events}


def ready(sub_id):
    return state(sub_id)["ready"]


def reset(sub_id):
    db.set(f"/submissions/{sub_id}/checks", None)
    db.set(f"/submissions/{sub_id}/check_events", None)
