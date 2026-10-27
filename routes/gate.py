"""Public step-gate API used by /assets/gate.js on the blog pages (through your Cloudflare worker).

POST /api/init    page opened            -> state (or `redirect` to the page the visitor must be on)
POST /api/event   top_click / bottom_click
POST /api/verify  'Verify' on pages 1..N-1, 'Generate' on the last one
GET  /api/ping    proves the proxy secret is right
GET  /api/health  keep-alive (UptimeRobot) -- open

Only the worker may call it: the request must carry  X-DH-Proxy: <PROXY_SECRET>  and  X-DH-IP: <visitor ip>.
All answers are tiny JSON documents with Cache-Control: no-store; unknown / expired links all get the same `{"ok": false}`.
"""
import hmac

from flask import Blueprint, Response, jsonify, request

from config import Config
from services import shortener

gate_bp = Blueprint("gate_api", __name__, url_prefix="/api")
GATE_PATHS = ("/api/init", "/api/event", "/api/verify", "/api/ping", "/api/health")


def _json(data, status=200):
    resp = jsonify(data)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def client_ip():
    if Config.PROXY_SECRET:  # the header is trusted only because the proxy secret matched (see guard)
        ip = request.headers.get("X-DH-IP", "")
        if ip:
            return ip.split(",")[0].strip()[:64]
    return (request.remote_addr or "")[:64]


@gate_bp.before_request
def guard():
    if request.endpoint == "gate_api.health":
        return None
    if not Config.PROXY_SECRET or not hmac.compare_digest(request.headers.get("X-DH-Proxy", "").encode(), Config.PROXY_SECRET.encode()):
        return Response("Not found", 404)
    if request.endpoint != "gate_api.ping" and shortener.rate_limited(client_ip()):
        return _json({"ok": False, "err": "slow_down"}, 429)
    return None


@gate_bp.route("/health", methods=["GET", "HEAD"])
def health():
    return _json({"status": "ok"})


@gate_bp.get("/ping")
def ping():
    return _json({"ok": True, "proxy": "ok", "site": shortener.get_config()["site_url"]})


def _data():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


@gate_bp.post("/init")
def init():
    d = _data()
    return _json(shortener.api_init(str(d.get("t", "")), str(d.get("p", "")), client_ip()))


@gate_bp.post("/event")
def event():
    d = _data()
    return _json(shortener.api_event(str(d.get("t", "")), str(d.get("ev", "")), d.get("n", ""), d.get("sig")))


@gate_bp.post("/verify")
def verify():
    d = _data()
    return _json(shortener.api_verify(str(d.get("t", "")), d.get("n", ""), d.get("sig"), str(d.get("cf") or ""), client_ip()))
