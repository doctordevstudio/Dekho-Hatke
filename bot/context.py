"""Bot context: which bot (main or a reseller's) is handling the current update."""
from bot.tg import client_for, tg
from core import db
from core.cache import cache


class Ctx:
    def __init__(self, tenant="", client=None, reseller=None):
        self.tenant = tenant or ""
        self.tg = client or tg
        self.reseller = reseller

    @property
    def is_main(self):
        return not self.tenant

    @property
    def key(self):
        return self.tenant or "main"

    @property
    def brand(self):
        return "Our Store" if self.is_main else (self.reseller.get("bot_name") or self.reseller.get("name") or "Store")

    def __repr__(self):  # pragma: no cover
        return f"<Ctx {self.key}>"


MAIN = Ctx()


def get_reseller(rid):
    hit = cache.get(("reseller", rid))
    if hit is not None:
        return hit[0]
    r = db.get(f"/resellers/{rid}")
    if r and (r.get("deleted") or not r.get("active", True)):
        r = None
    cache.set(("reseller", rid), (r,), 10)
    return r


def get_ctx(tenant=""):
    """Context for a tenant. Returns None when the reseller is missing / disabled."""
    if not tenant:
        return MAIN
    r = get_reseller(tenant)
    if not r or not r.get("bot_token"):
        return None
    return Ctx(tenant, client_for(r["bot_token"]), r)
