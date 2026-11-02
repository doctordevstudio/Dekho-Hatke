"""Audit log, stored in separate sections so each area can be tracked on its own.

Every row is written to the master list (/logs) and to the section(s) it belongs to (/logs_sec/<section>).
The IP address is stored but never returned by the API / shown in the UI.
"""
import logging

from flask import g, has_request_context

from core import db, utils
from core.security import client_ip

log = logging.getLogger("audit")

SECTIONS = [  # (key, label) in the order the UI shows them
    ("super_admin", "Super Admin"), ("admin2", "2nd Admin"), ("reseller", "Reseller"), ("referral", "Referral"),
    ("seller", "Seller"), ("checkin", "Daily Check-in"), ("spin", "Spin Wheel"), ("bot", "Bot / Users"),
    ("system", "System"),
]
SECTION_KEYS = {k for k, _ in SECTIONS}
FEATURE_PREFIX = {"referral": "referral", "withdraw": "referral", "checkin": "checkin", "spin": "spin",
                  "reseller": "reseller", "seller": "seller", "submission": "seller", "swithdraw": "seller"}
ROLE_SECTION = {"admin": "super_admin", "admin2": "admin2", "viewer": "super_admin"}


def sections_for(action, actor_section):
    """Feature section (if the action belongs to one) + the human actor's section."""
    out = []
    feat = FEATURE_PREFIX.get(str(action).split(".")[0])
    if feat:
        out.append(feat)
    if actor_section in ("super_admin", "admin2", "reseller", "seller") and actor_section not in out:
        out.append(actor_section)
    if not out:
        out.append(actor_section if actor_section in SECTION_KEYS else "system")
    return out


def log_action(action, details="", target="", source=None, actor=None, role=None):
    """Record who did what.

    Inside a panel request the actor comes from `g` (admin or reseller). For events that happen elsewhere
    (bot, background jobs) pass `actor` and, when a human admin acted through Telegram, `role`
    ('admin' / 'admin2').
    """
    who_id, who, src, actor_section, ip = "", actor or "system", source or "system", "system", ""
    if has_request_context():
        ip = client_ip()
        admin, reseller = getattr(g, "admin", None), getattr(g, "reseller", None)
        seller = getattr(g, "seller", None)
        if seller:
            who_id, who, src, actor_section = seller["id"], seller.get("name") or seller.get("login_username"), "seller_panel", "seller"
        elif reseller:
            who_id, who, src, actor_section = reseller["id"], reseller.get("login_username") or reseller["name"], "reseller_panel", "reseller"
        elif admin:
            who_id, who, src = admin["id"], admin["username"], source or "panel"
            actor_section = ROLE_SECTION.get(admin.get("role"), "super_admin")
    if actor_section == "system":
        if role:
            actor_section = ROLE_SECTION.get(role, "bot")
        elif src in ("telegram", "bot"):
            actor_section = "bot"
    lid = utils.ts_id()
    secs = sections_for(action, actor_section)
    row = {"id": lid, "ts": utils.now(), "admin_id": who_id, "admin": who, "source": src, "sections": secs,
           "action": str(action)[:80], "target": str(target)[:120], "details": str(details)[:500], "ip": ip}
    try:
        upd = {f"logs/{lid}": row}
        for s in secs:
            upd[f"logs_sec/{s}/{lid}"] = row
        db.update("/", upd)
    except Exception:  # never break a request because logging failed
        log.exception("audit log failed")
