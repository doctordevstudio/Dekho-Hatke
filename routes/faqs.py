"""FAQs: questions shown in the bot + who opened which one. (Navigation drawer → FAQs)"""
from flask import g, jsonify, request

from core import utils
from core.audit import log_action
from services import faqs
from . import body, bp


def _public(f):
    return {"id": f["id"], "question": f["question"], "answer": f["answer"], "format": f.get("format", "html"),
            "order": int(f.get("order") or 0), "active": bool(f.get("active", True)), "views": int(f.get("views", 0)),
            "created_at": f.get("created_at", 0)}


@bp.get("/faqs")
def list_faqs():
    return jsonify(items=[_public(f) for f in faqs.listing()])


@bp.post("/faqs")
def add_faq():
    f = faqs.create(body(), g.admin["username"])
    log_action("faq.created", f["question"][:80], target=f["id"])
    return jsonify(ok=True, item=_public(f)), 201


@bp.patch("/faqs/<fid>")
def edit_faq(fid):
    f = faqs.update(fid, body())
    log_action("faq.edited", f["question"][:80], target=fid)
    return jsonify(ok=True, item=_public(f))


@bp.delete("/faqs/<fid>")
def remove_faq(fid):
    f = faqs.delete(fid, g.admin["username"])
    log_action("faq.deleted", f["question"][:80], target=fid)
    return jsonify(ok=True)


@bp.get("/faqs/views")
def faq_views():
    """Every (user, question) pair with how often it was opened. ?faq=<id> narrows to one question."""
    page, meta = utils.paginate(faqs.viewers(request.args.get("faq") or None), request.args.get("page"))
    return jsonify(items=page, **meta)
