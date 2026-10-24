from flask import jsonify, request

from core import db, utils
from core.audit import SECTIONS
from . import bp

SCAN_LIMIT = 3000  # newest N log rows are searchable in the UI; all rows stay in the database


@bp.get("/logs")
def list_logs():
    section = (request.args.get("section") or "").strip()
    q = (request.args.get("q") or "").strip().lower()
    who = (request.args.get("admin") or "").strip().lower()
    if section and section in dict(SECTIONS):
        rows = db.query_last(f"/logs_sec/{section}", "ts", SCAN_LIMIT)
    else:
        rows = db.query_last("/logs", "ts", SCAN_LIMIT)
    rows.sort(key=lambda r: (r.get("ts", 0), r["id"]), reverse=True)
    if who:
        rows = [r for r in rows if who in (r.get("admin") or "").lower()]
    if q:
        rows = [r for r in rows if q in (r.get("action") or "").lower() or q in (r.get("details") or "").lower()
                or q in (r.get("target") or "").lower() or q in (r.get("admin") or "").lower()]
    page, meta = utils.paginate(rows, request.args.get("page"), 30)
    # NOTE: the "ip" field is intentionally NOT returned.
    items = [{"id": r["id"], "ts": r["ts"], "admin": r.get("admin", ""), "source": r.get("source", ""),
              "action": r.get("action", ""), "target": r.get("target", ""), "details": r.get("details", "")}
             for r in page]
    return jsonify(items=items, sections=[{"key": k, "label": v} for k, v in SECTIONS], **meta)
