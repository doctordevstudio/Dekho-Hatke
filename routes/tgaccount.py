"""Settings -> Channel & account: link a personal Telegram account and choose the storage channel.

The panel only ever learns "linked / not linked" (never the phone number, the 2-step password or the session). Linking and
choosing the channel: Super Admin and 2nd Admin. Unlinking: Super Admin only.
"""
from flask import g, jsonify

from core import security
from core.audit import log_action
from core.errors import ApiError
from services import tgchannel, userbot
from . import body, bp, require_admin_role


def tg_state():
    return {**userbot.status(), "channel": tgchannel.public()}


def _guard(fn):
    """Turn the account service's readable errors into normal API errors."""
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except userbot.UserbotError as e:
            raise ApiError(e.message)
    run.__name__ = fn.__name__
    return run


def _actor():
    return g.admin["username"]


@bp.get("/tg-account")
def tg_account():
    return jsonify(**tg_state())


@bp.post("/tg-account/start")
@_guard
def tg_start():
    ok, wait = security.limiter.hit("tgacc:start", 5, 600)
    if not ok:
        raise ApiError(f"Too many tries. Wait {wait // 60 + 1} min.", 429)
    userbot.start(body().get("phone"))
    log_action("tgaccount.code_requested", "")  # never the number
    return jsonify(ok=True, **tg_state())


@bp.post("/tg-account/code")
@_guard
def tg_code():
    ok, wait = security.limiter.hit("tgacc:code", 10, 600)
    if not ok:
        raise ApiError(f"Too many tries. Wait {wait // 60 + 1} min.", 429)
    step = userbot.submit_code(body().get("code"), _actor())
    if step == "done":
        log_action("tgaccount.linked", "")
    return jsonify(ok=True, **{**tg_state(), "result": step})


@bp.post("/tg-account/password")
@_guard
def tg_password():
    ok, wait = security.limiter.hit("tgacc:pw", 10, 600)
    if not ok:
        raise ApiError(f"Too many tries. Wait {wait // 60 + 1} min.", 429)
    userbot.submit_password(body().get("password"), _actor())
    log_action("tgaccount.linked", "2-step verification")
    return jsonify(ok=True, **tg_state())


@bp.post("/tg-account/cancel")
@_guard
def tg_cancel():
    userbot.cancel()
    return jsonify(ok=True, **tg_state())


@bp.post("/tg-account/unlink")
@_guard
def tg_unlink():
    require_admin_role()  # Super Admin only
    userbot.unlink()
    log_action("tgaccount.unlinked", "")
    return jsonify(ok=True, **tg_state())


@bp.put("/tg-channel")
@_guard
def tg_channel_save():
    info, bots = tgchannel.save_channel(body().get("chat"), _actor())
    log_action("tgchannel.saved", (info or {}).get("title", "(cleared)"))
    return jsonify(ok=True, bots=bots, admin=bool((info or {}).get("admin")), **tg_state())


@bp.post("/tg-channel/sync")
@_guard
def tg_channel_sync():
    if not tgchannel.enabled():
        raise ApiError("Choose the storage channel first.")
    bots = tgchannel.grant_all()
    log_action("tgchannel.bots_synced", f"{sum(1 for b in bots if b['ok'])}/{len(bots)} bots")
    return jsonify(ok=True, bots=bots, **tg_state())
