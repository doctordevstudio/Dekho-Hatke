"""Public step-gate API used by /assets/gate.js on the blog pages (through your Cloudflare worker).

POST /api/init    page opened            -> state (or `redirect` to the page the visitor must be on)
POST /api/event   top_click / bottom_click
POST /api/verify  'Verify' on pages 1..N-1, 'Generate' on the last one
GET  /api/ping    proves the proxy secret is right
GET  /api/health  keep-alive (UptimeRobot) -- open

Two ways in: (1) the Cloudflare worker, with  X-DH-Proxy: <PROXY_SECRET>  and  X-DH-IP: <visitor ip>;
(2) DIRECT mode (GATE_DIRECT=1, default): the visitor's browser on your website calls this server with CORS; the request's
Origin must be SITE_URL (or its www.). Anything else gets 404.
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


def _origins():
    """Websites allowed to call the gate straight from the browser."""
    out = set()
    for base in (shortener.get_config()["site_url"], Config.SITE_URL, *Config.GATE_ORIGINS.split(",")):
        base = (base or "").strip().rstrip("/").lower()
        if not base.startswith(("http://", "https://")):
            continue
        out.add(base)
        scheme, host = base.split("://", 1)
        out.add(f"{scheme}://{host[4:] if host.startswith('www.') else 'www.' + host}")
    return out


def _origin():
    o = request.headers.get("Origin", "").strip().rstrip("/").lower()
    return o if o and o in _origins() else ""


def _via_proxy():
    return bool(Config.PROXY_SECRET) and hmac.compare_digest(request.headers.get("X-DH-Proxy", "").encode(), Config.PROXY_SECRET.encode())


def client_ip():
    if _via_proxy():  # the header is trusted only because the proxy secret matched (see guard)
        ip = request.headers.get("X-DH-IP", "")
        if ip:
            return ip.split(",")[0].strip()[:64]
    else:  # direct mode: Render's edge puts the visitor's address in these headers
        ip = request.headers.get("CF-Connecting-IP", "") or request.headers.get("X-Forwarded-For", "")
        if ip:
            return ip.split(",")[0].strip()[:64]
    return (request.remote_addr or "")[:64]


@gate_bp.before_request
def guard():
    if request.method == "OPTIONS":  # CORS preflight from our own website
        if Config.GATE_DIRECT and _origin():
            return Response("", 204)
        return Response("Not found", 404)
    if request.endpoint == "gate_api.health":
        return None
    if not _via_proxy():
        direct = Config.GATE_DIRECT and (_origin() or request.endpoint == "gate_api.ping")  # ping may be opened in a browser tab
        if not direct:
            return Response("Not found", 404)
    if request.endpoint != "gate_api.ping" and shortener.rate_limited(client_ip()):
        return _json({"ok": False, "err": "slow_down"}, 429)
    return None


@gate_bp.after_request
def cors(resp):
    o = _origin() if Config.GATE_DIRECT else ""
    if o:
        resp.headers["Access-Control-Allow-Origin"] = o
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "content-type"
        resp.headers["Access-Control-Max-Age"] = "7200"
    return resp


@gate_bp.route("/health", methods=["GET", "HEAD"])
def health():
    return _json({"status": "ok"})


@gate_bp.get("/ping")
def ping():
    return _json({"ok": True, "proxy": "ok" if _via_proxy() else "direct", "site": shortener.get_config()["site_url"]})


def _data():
    d = request.get_json(force=True, silent=True)  # force: the browser sends text/plain to skip the CORS preflight
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
