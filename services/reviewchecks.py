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


def _base(ctx_id):
    """Where the checks live: an admin review (submission id) or a seller's own pre-submission test ("draft:<app id>")."""
    ctx_id = str(ctx_id)
    return f"/app_drafts/{ctx_id[6:]}" if ctx_id.startswith("draft:") else f"/submissions/{ctx_id}"

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


def observe(ctx_id, code, device_id="", device_name="", app_version=""):
    """Called by the public API for calls made with a test key while an app is being tested / reviewed."""
    if not ctx_id:
        return
    base = _base(ctx_id)
    if not db.get(base):
        return
    ev_id = utils.ts_id()
    db.set(f"{base}/check_events/{ev_id}", {
        "id": ev_id, "ts": utils.now(), "code": code, "label": CODE_LABEL.get(code, code),
        "device": (device_name or device_id or "")[:40], "device_id_tail": str(device_id)[-6:], "version": app_version[:20]})
    check = CODE_TO_CHECK.get(code)
    if check and not db.get(f"{base}/checks/{check}"):
        db.set(f"{base}/checks/{check}", {"passed": True, "ts": utils.now(), "detail": CODE_LABEL.get(code, code)})
    events = db.get(f"{base}/check_events") or {}
    if len(events) > MAX_EVENTS + 10:  # keep the log short
        for old in sorted(events)[:len(events) - MAX_EVENTS]:
            db.set(f"{base}/check_events/{old}", None)


def state(ctx_id):
    sub = db.get(_base(ctx_id)) or {}
    done = sub.get("checks") or {}
    rows = [{"key": k, "label": label, "required": req, "hint": hint, "passed": bool((done.get(k) or {}).get("passed")),
             "ts": (done.get(k) or {}).get("ts", 0)} for k, label, req, hint in CHECKS]
    events = sorted((sub.get("check_events") or {}).values(), key=lambda e: e["id"], reverse=True)[:15]
    return {"checks": rows, "ready": all(r["passed"] for r in rows if r["required"]), "events": events}


def ready(ctx_id):
    return state(ctx_id)["ready"]


def reset(ctx_id):
    base = _base(ctx_id)
    db.set(f"{base}/checks", None)
    db.set(f"{base}/check_events", None)
