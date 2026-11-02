"""FAQs shown in the main bot (10 per page). Managed from the admin panel; every open of an answer is recorded
(who opened which question) so the admin can see what people are curious about."""
from core import db, utils
from core.cache import cache
from core.errors import ApiError


def _rows():
    return [f for f in db.get_list("/faqs") if not f.get("deleted")]


def active():
    rows = [f for f in _rows() if f.get("active", True)]
    rows.sort(key=lambda f: (int(f.get("order") or 0) or 10 ** 6, f.get("created_at", 0)))
    return rows


def listing():
    rows = _rows()
    rows.sort(key=lambda f: (int(f.get("order") or 0) or 10 ** 6, f.get("created_at", 0)))
    return rows


def get(faq_id):
    f = db.get(f"/faqs/{faq_id}")
    return f if f and not f.get("deleted") and f.get("active", True) else None


def _clean(d, cur=None):
    cur = cur or {}
    q = utils.clean_text(d.get("question", cur.get("question")), "Question", 200)
    a = utils.clean_text(d.get("answer", cur.get("answer")), "Answer", 3000)
    fmt = d.get("format", cur.get("format", "html"))
    if fmt not in ("html", "text"):
        raise ApiError("Answer format must be html or text.")
    try:
        order = int(d.get("order", cur.get("order", 0)) or 0)
    except (TypeError, ValueError):
        raise ApiError("Order must be a number.")
    return {"question": q, "answer": a, "format": fmt, "order": max(order, 0), "active": bool(d.get("active", cur.get("active", True)))}


def create(d, admin):
    fid = utils.new_id("faq")
    rec = {"id": fid, "created_at": utils.now(), "created_by": admin, "views": 0, **_clean(d)}
    db.set(f"/faqs/{fid}", rec)
    cache.clear()
    return rec


def update(fid, d):
    cur = db.get(f"/faqs/{fid}")
    if not cur or cur.get("deleted"):
        raise ApiError("Question not found.", 404)
    upd = {**_clean(d, cur), "updated_at": utils.now()}
    db.update(f"/faqs/{fid}", upd)
    cache.clear()
    return {**cur, **upd}


def delete(fid, admin):
    cur = db.get(f"/faqs/{fid}")
    if not cur or cur.get("deleted"):
        raise ApiError("Question not found.", 404)
    db.update(f"/faqs/{fid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": admin})
    cache.clear()
    return cur


def record_view(f, user):
    uid = str(user["chat_id"])
    now = utils.now()
    db.transaction(f"/faqs/{f['id']}/views", lambda c: int(c or 0) + 1)
    path = f"/faq_views/{f['id']}/{uid}"
    cur = db.get(path) or {}
    db.set(path, {"chat_id": uid, "full_name": user.get("full_name", ""), "username": user.get("username", ""),
                  "count": int(cur.get("count", 0)) + 1, "first_ts": cur.get("first_ts", now), "last_ts": now})


def viewers(faq_id=None):
    """[{faq_id, question, chat_id, full_name, username, count, last_ts}] newest first."""
    qs = {f["id"]: f for f in _rows()}
    out = []
    ids = [faq_id] if faq_id else list(qs)
    for fid in ids:
        if fid not in qs:
            continue
        for v in (db.get(f"/faq_views/{fid}") or {}).values():
            out.append({"faq_id": fid, "question": qs[fid]["question"], **v})
    out.sort(key=lambda v: v.get("last_ts", 0), reverse=True)
    return out
