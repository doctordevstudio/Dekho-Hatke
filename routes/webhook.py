"""Telegram webhooks: one secret URL per bot (main bot, and each reseller's bot)."""
import hmac
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, abort, request

from config import Config

log = logging.getLogger("webhook")
webhook_bp = Blueprint("webhook", __name__)
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot")
_seen = deque(maxlen=2000)


def _run(update, tenant):
    from bot.context import get_ctx
    from bot.handlers import process_update

    try:
        ctx = get_ctx(tenant)
        if ctx:
            process_update(update, ctx)
    except Exception:
        log.exception("update handler crashed")


def _accept(tenant):
    if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), Config.header_secret(tenant)):
        abort(403)
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        abort(400)
    key = (tenant, update.get("update_id"))
    if key in _seen:  # Telegram retries -> de-duplicate
        return "ok"
    _seen.append(key)
    _pool.submit(_run, update, tenant)  # answer Telegram immediately, work in the background
    return "ok"


@webhook_bp.post("/telegram/<secret>")
def telegram_webhook(secret):
    if not hmac.compare_digest(secret, Config.path_secret("")):
        abort(404)
    return _accept("")


@webhook_bp.post("/telegram/<secret>/<rid>")
def reseller_webhook(secret, rid):
    if not hmac.compare_digest(secret, Config.path_secret(rid)):
        abort(404)
    return _accept(rid)
