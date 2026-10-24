"""Sub-domain routing + Cloudflare origin lock, as a WSGI layer that runs before Flask.

Sub-domain mode (ROOT_DOMAIN set):
  admin.<domain>     admin / 2nd admin / view-only panel          (/, /login, /admin/api/*)
  seller.<domain>    seller panel, lives at "/"                   (/, /seller/api/*)
  reseller.<domain>  reseller panel, lives at "/"                 (/, /reseller/api/*)
  api.<domain>       license API + Telegram webhooks              (/api/v1/*, /telegram/*)
Each host serves ONLY its own paths; everything else -- and any unknown Host such as the raw onrender.com address --
gets an identical plain 404, so a panel never leaks onto another sub-domain.

Origin lock (ORIGIN_SECRET set): Cloudflare adds `X-Origin-Auth: <secret>` to every request it forwards. A request
without it (someone hitting the Render address directly) gets 404. Only then is CF-Connecting-IP trusted as the real
client IP, so rate limits and login lockouts work per visitor instead of per Cloudflare edge server.
"""
import hmac
import ipaddress

from config import Config

OPEN_PATHS = ("/healthz",)  # keep-alive pings and Render's health check never need the secret or a known host
STATIC = ("/static/", "/media/")
RULES = {
    "admin": {"exact": {"/", "/login", "/favicon.ico", "/robots.txt"}, "prefix": ("/admin/api/",) + STATIC},
    "seller": {"exact": {"/", "/favicon.ico", "/robots.txt"}, "prefix": ("/seller/api/",) + STATIC},
    "reseller": {"exact": {"/", "/logout", "/favicon.ico", "/robots.txt"}, "prefix": ("/reseller/api/",) + STATIC},
    "api": {"exact": {"/robots.txt"}, "prefix": ("/api/v1/", "/telegram/")},
}
REWRITE = {"seller": {"/": "/seller"}, "reseller": {"/": "/reseller", "/logout": "/reseller/logout"}}
LEGACY = {"seller": "/seller", "reseller": "/reseller"}  # old shared-host paths -> bounce to "/" on the sub-domain


def _plain(start_response, status, body=b"Not found", headers=()):
    start_response(status, [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body))),
                            ("Cache-Control", "no-store")] + list(headers))
    return [body]


class HostRouter:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/") or "/"
        if path in OPEN_PATHS:
            return self.app(environ, start_response)

        # 1) origin lock ------------------------------------------------------------------------------------------
        if Config.ORIGIN_SECRET:
            got = environ.get("HTTP_" + Config.ORIGIN_HEADER.upper().replace("-", "_"), "")
            if not hmac.compare_digest(got.encode("utf-8", "ignore"), Config.ORIGIN_SECRET.encode()):
                return _plain(start_response, "404 Not Found")
            cf_ip = (environ.get("HTTP_CF_CONNECTING_IP") or "").strip()
            if cf_ip:
                try:
                    environ["REMOTE_ADDR"] = str(ipaddress.ip_address(cf_ip))
                except ValueError:
                    pass

        # 2) host routing -----------------------------------------------------------------------------------------
        if not Config.HOST_MODE:
            return self.app(environ, start_response)
        host = (environ.get("HTTP_HOST") or "").split(":")[0].strip().lower().rstrip(".")
        kind = Config.HOSTS.get(host)
        if not kind:
            return _plain(start_response, "404 Not Found")
        rule = RULES[kind]
        if path == LEGACY.get(kind):
            loc = "/" + ("?" + environ["QUERY_STRING"] if environ.get("QUERY_STRING") else "")
            return _plain(start_response, "308 Permanent Redirect" if environ.get("REQUEST_METHOD") != "GET" else "302 Found",
                          b"", [("Location", loc)])
        if path not in rule["exact"] and not path.startswith(rule["prefix"]):
            return _plain(start_response, "404 Not Found")
        environ["PATH_INFO"] = REWRITE.get(kind, {}).get(path, path)
        environ["gz.host_kind"] = kind
        return self.app(environ, start_response)
