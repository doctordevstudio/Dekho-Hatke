"""Host routing + Cloudflare origin lock, as a WSGI layer that runs before Flask.

Sub-domain mode (ROOT_DOMAIN set) -- Render's free plan has two custom domains, so:
  drdev.<domain>   every panel:  admin (/ and /login, /admin/api/*), seller (/seller, /seller/api/*),
                   reseller (/reseller, /reseller/api/*), plus /static and /media
  api.<domain>     the license verification API only (/api/v1/*)
  anything else    (the raw xxxx.onrender.com address): ONLY Telegram's webhooks (/telegram/*) and /healthz
Every other path on a host is an identical plain 404, so a panel never shows up on the API host and vice versa.

Origin lock (ORIGIN_SECRET set): Cloudflare adds `X-Origin-Auth: <secret>` to every request it forwards for the two
custom domains. A request for one of those hosts without it (someone going around Cloudflare) gets 404. Only then is
CF-Connecting-IP trusted as the real client IP, so rate limits and login lockouts work per visitor.
Telegram cannot send the header; its webhook calls the Render address directly and is protected by the secret URL
and the secret header token that the webhook handler checks.
"""
import hmac
import ipaddress

from config import Config

OPEN_PATHS = ("/healthz",)  # keep-alive pings and Render's health check never need the secret or a known host
STATIC = ("/static/", "/media/")
RULES = {
    "panel": {"exact": {"/", "/login", "/seller", "/reseller", "/reseller/logout", "/favicon.ico", "/robots.txt"},
              "prefix": ("/admin/api/", "/seller/api/", "/reseller/api/") + STATIC},
    "api": {"exact": {"/robots.txt"}, "prefix": ("/api/v1/",)},
}


def _plain(start_response, status="404 Not Found", body=b"Not found"):
    start_response(status, [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body))),
                            ("Cache-Control", "no-store")])
    return [body]


class HostRouter:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/") or "/"
        if path in OPEN_PATHS:
            return self.app(environ, start_response)
        host = (environ.get("HTTP_HOST") or "").split(":")[0].strip().lower().rstrip(".")
        kind = Config.HOSTS.get(host) if Config.HOST_MODE else None

        # Telegram webhooks: only on the Render address (a host that is not one of our two custom domains)
        if path.startswith("/telegram/"):
            if Config.HOST_MODE and kind:
                return _plain(start_response)
            return self.app(environ, start_response)

        # 1) origin lock (the two custom domains are reached through Cloudflare) --------------------------------------
        if Config.ORIGIN_SECRET and (kind or not Config.HOST_MODE):
            got = environ.get("HTTP_" + Config.ORIGIN_HEADER.upper().replace("-", "_"), "")
            if not hmac.compare_digest(got.encode("utf-8", "ignore"), Config.ORIGIN_SECRET.encode()):
                return _plain(start_response)
            cf_ip = (environ.get("HTTP_CF_CONNECTING_IP") or "").strip()
            if cf_ip:
                try:
                    environ["REMOTE_ADDR"] = str(ipaddress.ip_address(cf_ip))
                except ValueError:
                    pass
        elif Config.ORIGIN_SECRET and Config.HOST_MODE:
            return _plain(start_response)  # unknown host (the Render address): nothing but Telegram + /healthz

        # 2) host routing -----------------------------------------------------------------------------------------
        if not Config.HOST_MODE:
            return self.app(environ, start_response)
        if not kind:
            return _plain(start_response)
        rule = RULES[kind]
        if path not in rule["exact"] and not path.startswith(rule["prefix"]):
            return _plain(start_response)
        environ["gz.host_kind"] = kind
        return self.app(environ, start_response)
