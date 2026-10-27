from core import db, utils
from core.errors import ApiError
from services import earnings, users


def status_of(lic):
    if lic.get("status") == "revoked":
        return "revoked"
    if int(lic.get("expires_at", 0)) <= utils.now():
        return "expired"
    return "active"


def public(lic, app_names=None):
    """Shape a license for the UI (never exposes internals we don't need)."""
    app_names = app_names or {}
    return {
        "key": lic["key"], "app_id": lic["app_id"], "app_name": app_names.get(lic["app_id"], lic.get("app_name", "")),
        "chat_id": lic.get("chat_id", ""), "full_name": lic.get("full_name", ""), "username": lic.get("username", ""),
        "profile_image_url": lic.get("profile_image_url", ""), "amount": lic.get("amount", 0),
        "validity_label": lic.get("validity_label", ""), "created_at": lic.get("created_at", 0),
        "expires_at": lic.get("expires_at", 0), "status": status_of(lic), "source": lic.get("source", ""),
        "reseller_id": lic.get("reseller_id", ""), "device_bound": bool(lic.get("device_id")), "device_name": lic.get("device_name", ""),
        "last_seen": lic.get("last_seen", 0), "revoked_reason": lic.get("revoked_reason", ""),
    }


def get_license(key):
    key = str(key or "").strip().upper()
    lic = db.get(f"/licenses/{key}") if key.isalnum() else None
    if not lic or lic.get("deleted"):
        raise ApiError("License not found.", 404)
    lic.setdefault("key", key)
    return lic


def _unique_key():
    for _ in range(10):
        k = utils.gen_license_key()
        if db.get(f"/licenses/{k}") is None:
            return k
    raise ApiError("Could not generate a unique license key, try again.", 500)


def create_license(*, app, chat_id, full_name, username, profile_image_url, amount, validity_value,
                   validity_unit, source, txn_id="", plan_id="", upi_id="", profile_file_id="",
                   reseller_id="", record_earning=True):
    key = _unique_key()
    t = utils.now()
    exp = t + utils.validity_seconds(validity_value, validity_unit)
    lic = {
        "key": key, "id": key, "app_id": app["id"], "app_name": app.get("name", ""),
        "chat_id": str(chat_id), "full_name": full_name, "username": username,
        "profile_image_url": profile_image_url, "amount": amount, "validity_value": validity_value,
        "validity_unit": validity_unit, "validity_label": utils.validity_label(validity_value, validity_unit),
        "created_at": t, "expires_at": exp, "status": "active", "source": source,
        "txn_id": txn_id, "plan_id": plan_id, "reseller_id": reseller_id or "",
    }
    db.set(f"/licenses/{key}", lic)
    users.upsert_user(chat_id, full_name, username, profile_image_url, profile_file_id,
                      source="manual" if source == "manual" else "bot", tenant=reseller_id or "")
    if record_earning and amount and amount > 0:
        earnings.record(app=app, amount=amount, source=source, user=lic, license_key=key, txn_id=txn_id,
                        upi_id=upi_id, plan_id=plan_id, label=lic["validity_label"])
    return lic


def renew(key, value, unit, actor):
    lic = get_license(key)
    value, unit = utils.parse_validity(value, unit)
    base = max(utils.now(), int(lic.get("expires_at", 0)))
    new_exp = base + utils.validity_seconds(value, unit)
    hid = utils.ts_id()
    db.update(f"/licenses/{lic['key']}", {
        "expires_at": new_exp, "status": "active", "revoked_reason": "",
        f"history/{hid}": {"ts": utils.now(), "action": "renew", "by": actor,
                           "detail": f"+{utils.validity_label(value, unit)}"},
    })
    return new_exp, utils.validity_label(value, unit)


def revoke(key, reason, actor):
    lic = get_license(key)
    hid = utils.ts_id()
    db.update(f"/licenses/{lic['key']}", {
        "status": "revoked", "revoked_at": utils.now(), "revoked_reason": reason or "",
        f"history/{hid}": {"ts": utils.now(), "action": "revoke", "by": actor, "detail": reason or ""},
    })


def expire_now(key, actor):
    lic = get_license(key)
    hid = utils.ts_id()
    db.update(f"/licenses/{lic['key']}", {
        "expires_at": utils.now() - 1, "status": "active",
        f"history/{hid}": {"ts": utils.now(), "action": "expire_now", "by": actor, "detail": "expired manually"},
    })


def create_test_license(app, hours, actor, note=""):
    """A key for the admin to test an app before approving it. Not a sale: no user, no earnings."""
    key = _unique_key()
    t = utils.now()
    lic = {"key": key, "id": key, "app_id": app["id"], "app_name": app.get("name", ""), "chat_id": "test",
           "full_name": "Test key", "username": "", "profile_image_url": "", "amount": 0,
           "validity_value": int(hours), "validity_unit": "hours", "validity_label": utils.validity_label(hours, "hours"),
           "created_at": t, "expires_at": t + int(hours) * 3600, "status": "active", "source": "test", "test": True,
           "created_by": actor, "note": note[:120], "reseller_id": ""}
    db.set(f"/licenses/{key}", lic)
    return lic


def reset_device(key, actor):
    lic = get_license(key)
    hid = utils.ts_id()
    upd = {f"history/{hid}": {"ts": utils.now(), "action": "device_reset", "by": actor,
                              "detail": f"old device: {lic.get('device_id', '')}"[:120]}}
    db.update(f"/licenses/{lic['key']}", upd)
    db.set(f"/licenses/{lic['key']}/device_id", None)  # clear binding; history keeps the old id


def soft_delete(key, actor):
    lic = get_license(key)
    db.update(f"/licenses/{lic['key']}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": actor})


def all_licenses():
    return [l for l in db.get_list("/licenses") if not l.get("deleted")]
