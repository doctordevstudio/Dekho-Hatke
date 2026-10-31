"""Monero (XMR) payments: wallet that survives Render restarts, one subaddress per invoice, automatic detection.

How it works (no local daemon, no files that must survive a restart):

* The wallet seed is generated ONCE (monero-python `Seed`), encrypted with a key derived from the XMR_KEYPASS environment
  variable (scrypt + AES-256-GCM) and stored in Firebase at /crypto/wallet. The plain seed is never stored anywhere.
* On every boot the wallet is restored from that seed into a throw-away folder, by a `monero-wallet-rpc` process that talks to
  a public remote node (XMR_NODE). `restore_height` = the block height recorded when the wallet was created, so only the
  blocks since then are ever scanned. (After a full sweep to your main address the height moves forward again.)
* An invoice = a fresh subaddress + the expected XMR amount (USD price / live XMR price) + the block height at that moment +
  an expiry (60 min). The poller asks the wallet for transfers of that subaddress index from that height on. Paid = the
  expected amount (minus a tiny tolerance for exchange fees) with >= 1 confirmation. Expired = nothing is queried any more.

Everything that talks to the outside world goes through three small functions so tests can fake them:
`_get_json` (HTTP: price + remote node), `_rpc` (the wallet-rpc client) and `_new_seed` (monero-python).
"""
import base64
import hmac
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal

import requests

from config import Config
from core import db, utils
from core.errors import ApiError

log = logging.getLogger("xmr")
PICO = Decimal(10) ** 12
ADDR_RE = re.compile(r"^[48][1-9A-HJ-NP-Za-km-z]{94}$|^4[1-9A-HJ-NP-Za-km-z]{105}$")
_lock = threading.RLock()
_state = {"opened": False, "wpass": "", "price": None, "price_ts": 0, "poller": False, "poll_err": "", "poll_ts": 0}


class XmrError(ApiError):
    def __init__(self, message, status=503):
        super().__init__(message, status)


# ===================================================================== small helpers
def pico_to_xmr(p):
    """Atomic units -> '0.123456789012' (trailing zeros trimmed)."""
    s = f"{(Decimal(int(p)) / PICO):.12f}".rstrip("0").rstrip(".")
    return s or "0"


def xmr_to_pico(x):
    return int((Decimal(str(x)) * PICO).to_integral_value())


def usd_to_pico(usd, rate):
    if not rate or rate <= 0:
        raise XmrError("The XMR price is not available right now. Try again in a minute.")
    return int((Decimal(str(usd)) / Decimal(str(rate)) * PICO).to_integral_value(rounding=ROUND_UP))


def valid_address(addr):
    addr = (addr or "").strip()
    if not ADDR_RE.match(addr):
        return False
    try:  # monero-python also checks the checksum when it is installed
        from monero.address import address as _addr
        _addr(addr)
    except ImportError:
        pass
    except Exception:
        return False
    return True


# ===================================================================== settings + wallet record (Firebase)
def keypass():
    return Config.XMR_KEYPASS


def check_password(pw):
    """The password typed in the panel must equal the XMR_KEYPASS environment variable."""
    if not keypass():
        raise ApiError("Add the XMR_KEYPASS variable in Render → Environment first (it is the password your wallet seed is "
                       "encrypted with), then redeploy.", 400)
    if not isinstance(pw, str) or not hmac.compare_digest(pw.encode(), keypass().encode()):
        raise ApiError("Wrong password.", 403)


def wallet_doc():
    return db.get("/crypto/wallet") or None


def configured():
    d = wallet_doc()
    return bool(d and d.get("ct"))


def settings():
    s = db.get("/crypto/settings") or {}
    return {"enabled": bool(s.get("enabled", True)),
            "inr_per_usd": float(s.get("inr_per_usd") or Config.DEFAULT_INR_PER_USD),
            "rate_set": bool(s.get("inr_per_usd")),
            "main_address": s.get("main_address", ""), "min_usd": float(s.get("min_usd") or Config.XMR_MIN_USD),
            # wallet deposits are credited at THIS fixed rate (₹ per 1 XMR), never at the moving market price
            "xmr_inr": float(s.get("xmr_inr") or 0), "deposit_enabled": bool(s.get("deposit_enabled", True))}


def save_settings(d):
    cur = settings()
    upd = {}
    if "inr_per_usd" in d:
        try:
            r = float(d["inr_per_usd"])
        except (TypeError, ValueError):
            raise ApiError("Enter the conversion rate as a number, e.g. 85.")
        if not 1 <= r <= 100000:
            raise ApiError("Conversion rate must be between 1 and 100000.")
        upd["inr_per_usd"] = round(r, 4)
    if "main_address" in d:
        a = str(d.get("main_address") or "").strip()
        if a and not valid_address(a):
            raise ApiError("That is not a valid Monero address.")
        upd["main_address"] = a
    if "enabled" in d:
        upd["enabled"] = bool(d["enabled"])
    if "min_usd" in d:
        try:
            mu = float(d["min_usd"])
        except (TypeError, ValueError):
            raise ApiError("Minimum order must be a number.")
        if mu < 0.01:
            raise ApiError("Minimum order must be at least $0.01.")
        upd["min_usd"] = round(mu, 2)
    if "xmr_inr" in d:
        raw = d["xmr_inr"]
        if raw in (None, "", 0, "0"):
            upd["xmr_inr"] = 0
        else:
            try:
                r = float(raw)
            except (TypeError, ValueError):
                raise ApiError("Enter the fixed deposit rate as a number: how many ₹ one XMR is worth, e.g. 15000.")
            if not 1 <= r <= 100000000:
                raise ApiError("Fixed deposit rate must be between ₹1 and ₹100000000 per XMR.")
            upd["xmr_inr"] = round(r, 2)
    if "deposit_enabled" in d:
        upd["deposit_enabled"] = bool(d["deposit_enabled"])
    if upd:
        db.update("/crypto/settings", upd)
    return {**cur, **upd}


def bot_enabled():
    """Crypto may be offered in the bot: a wallet exists, it is switched on and the key password is set."""
    return bool(configured() and settings()["enabled"] and keypass())


def inr_per_usd():
    return settings()["inr_per_usd"]


def deposit_ready():
    """Crypto may be offered for wallet deposits: gateway on AND a fixed ₹-per-XMR rate has been set by the admin."""
    st = settings()
    return bool(bot_enabled() and st["deposit_enabled"] and st["xmr_inr"] > 0)


def fixed_rate():
    return settings()["xmr_inr"]


def inr_to_pico(inr, rate):
    """₹ -> atomic units at the FIXED rate (rounded up, so the user never under-pays)."""
    if not rate or rate <= 0:
        raise XmrError("The fixed deposit rate is not set.")
    return int((Decimal(str(inr)) / Decimal(str(rate)) * PICO).to_integral_value(rounding=ROUND_UP))


def pico_to_inr(pico, rate):
    """Atomic units -> ₹ at the FIXED rate, rounded DOWN to paise."""
    return float((Decimal(int(pico)) / PICO * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


# ===================================================================== seed encryption (scrypt + AES-256-GCM)
def _key(salt):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(keypass().encode())


def encrypt_seed(seed):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    ct = AESGCM(_key(salt)).encrypt(nonce, seed.encode(), b"dh-xmr-seed-v1")
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return {"v": 1, "kdf": "scrypt-n15r8p1", "salt": b(salt), "nonce": b(nonce), "ct": b(ct)}


def decrypt_seed(doc):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not keypass():
        raise XmrError("XMR_KEYPASS is not set in the environment, so the wallet seed cannot be decrypted.", 500)
    try:
        d = lambda k: base64.b64decode(doc[k])  # noqa: E731
        return AESGCM(_key(d("salt"))).decrypt(d("nonce"), d("ct"), b"dh-xmr-seed-v1").decode()
    except Exception:
        raise XmrError("The wallet seed could not be decrypted. XMR_KEYPASS is different from the one used when the wallet "
                       "was created.", 500)


# ===================================================================== outside world (fakeable in tests)
def _get_json(url, method="GET", json=None, timeout=15, verify=True):
    r = requests.request(method, url, json=json, timeout=timeout, verify=verify, headers={"User-Agent": "dh-bot/1"})
    r.raise_for_status()
    return r.json()


def node_list():
    nodes = [Config.XMR_NODE] + [n.strip() for n in Config.XMR_NODES.split(",") if n.strip()]
    out = []
    for n in nodes:
        if n and n not in out:
            out.append(n)
    return out


def _node_urls(node):
    """'host:port' -> candidate base urls (https first: public nodes on :18089 are TLS)."""
    node = node.strip().rstrip("/")
    if "://" in node:
        return [node]
    return [f"https://{node}", f"http://{node}"]


def _node_json(node, path, payload=None):
    last = None
    for base in _node_urls(node):
        try:
            return _get_json(base + path, "POST" if payload is not None else "GET", payload, verify=not Config.XMR_NODE_ANY_CERT)
        except Exception as e:  # noqa: BLE001
            last = e
    raise XmrError(f"Remote node {node} is not reachable ({type(last).__name__}).")


def node_height():
    """Current network block height from the first remote node that answers."""
    err = None
    for n in node_list():
        try:
            h = int(_node_json(n, "/get_height")["height"])
            if h > 0:
                return h
        except Exception as e:  # noqa: BLE001
            err = e
    raise XmrError(f"No Monero remote node answered ({err}). Check XMR_NODE.")


def xmr_usd():
    """Live XMR price in USD (CoinGecko, then Kraken, then Coinbase), cached for XMR_PRICE_TTL seconds."""
    now = time.time()
    if _state["price"] and now - _state["price_ts"] < Config.XMR_PRICE_TTL:
        return _state["price"]
    sources = (
        ("https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=usd", lambda j: j["monero"]["usd"]),
        ("https://api.kraken.com/0/public/Ticker?pair=XMRUSD", lambda j: next(iter(j["result"].values()))["c"][0]),
        ("https://api.coinbase.com/v2/prices/XMR-USD/spot", lambda j: j["data"]["amount"]),
    )
    for url, pick in sources:
        try:
            p = float(pick(_get_json(url)))
            if p > 0:
                _state["price"], _state["price_ts"] = p, now
                return p
        except Exception as e:  # noqa: BLE001
            log.info("price source failed %s: %s", url.split("/")[2], e)
    if _state["price"] and now - _state["price_ts"] < 1800:  # a recent price is better than failing a sale
        return _state["price"]
    raise XmrError("The live XMR price could not be fetched. Try again in a minute.")


# ===================================================================== monero-wallet-rpc process + client
def _binary():
    cands = [Config.XMR_WALLET_RPC_BIN, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "monero-wallet-rpc"),
             shutil.which("monero-wallet-rpc") or ""]
    return next((c for c in cands if c and os.path.isfile(c) and os.access(c, os.X_OK)), "")


class WalletRPC:
    """Starts monero-wallet-rpc (bound to 127.0.0.1) and speaks its JSON-RPC. The wallet files live in a temp folder: they are
    rebuilt from the seed on every boot, nothing here is precious."""

    def __init__(self):
        self.proc, self.port = None, Config.XMR_RPC_PORT
        self.user, self.pw = "dh", secrets.token_hex(16)
        self.dir = ""

    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def ensure_started(self):
        if self.alive():
            return
        exe = _binary()
        if not exe:
            raise XmrError("monero-wallet-rpc is not installed on this server. Redeploy with the build command "
                           "`pip install -r requirements.txt && bash scripts/install_monero.sh` (see README, Monero section).", 500)
        self.dir = self.dir or tempfile.mkdtemp(prefix="xmrw_")
        node = node_list()[0]
        host = re.sub(r"^https?://", "", node)
        cmd = [exe, "--daemon-address", host, "--rpc-bind-ip", "127.0.0.1", "--rpc-bind-port", str(self.port),
               "--rpc-login", f"{self.user}:{self.pw}", "--wallet-dir", self.dir, "--non-interactive",
               "--log-file", os.path.join(self.dir, "rpc.log"), "--max-log-files", "1", "--max-concurrency", "2",
               "--log-level", "0"]
        if node.startswith("https://") or host.endswith(":18089"):
            cmd += ["--daemon-ssl", "enabled"]
        if Config.XMR_NODE_ANY_CERT:
            cmd += ["--daemon-ssl-allow-any-cert"]
        log.info("starting monero-wallet-rpc against %s", host)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(90):
            if not self.alive():
                raise XmrError("monero-wallet-rpc stopped right after starting. Check the node address (XMR_NODE).", 500)
            try:
                self._post("get_version", {}, timeout=3)
                return
            except XmrError:
                time.sleep(1)
        raise XmrError("monero-wallet-rpc did not start in time.", 504)

    def _post(self, method, params, timeout=120):
        try:
            r = requests.post(f"http://127.0.0.1:{self.port}/json_rpc", json={"jsonrpc": "2.0", "id": "0", "method": method,
                                                                              "params": params},
                              auth=requests.auth.HTTPDigestAuth(self.user, self.pw), timeout=timeout)
            data = r.json()
        except Exception as e:  # noqa: BLE001
            raise XmrError(f"wallet rpc {method} failed ({type(e).__name__})")
        if "error" in data:
            raise XmrError(f"{method}: {data['error'].get('message', 'error')}")
        return data.get("result") or {}

    def call(self, method, **params):
        self.ensure_started()
        return self._post(method, params)

    def stop(self):
        if self.alive():
            try:
                self.proc.terminate()
                self.proc.wait(10)
            except Exception:  # noqa: BLE001
                self.proc.kill()
        self.proc = None


_rpc = WalletRPC()  # tests replace this object with a fake that has .call(method, **params)


def _new_seed():
    """('25 words', primary address). monero-python generates the seed; without it the wallet-rpc does."""
    try:
        from monero.seed import Seed
        s = Seed()
        return s.phrase, str(s.public_address())
    except ImportError:
        log.warning("monero-python not installed: asking monero-wallet-rpc to make the seed instead")
    name = "new_" + secrets.token_hex(4)
    _rpc.call("create_wallet", filename=name, password=secrets.token_hex(8), language="English")
    seed = _rpc.call("query_key", key_type="mnemonic")["key"]
    addr = _rpc.call("get_address", account_index=0)["address"]
    _state["opened"] = False  # a different wallet is open now: reopen the real one on next use
    return seed, addr


# ===================================================================== wallet lifecycle
def create_wallet(admin_name, password):
    """First-time setup (Super Admin): seed -> encrypted -> Firebase. Returns the public wallet info (never the seed)."""
    check_password(password)
    if configured():
        raise ApiError("A wallet already exists.", 409)
    height = node_height()  # recorded BEFORE anything is paid: it is the restore height for every later scan
    seed, address = _new_seed()
    doc = {**encrypt_seed(seed), "address": address, "restore_height": height, "created_height": height,
           "next_index": 0, "created_at": utils.now(), "created_by": admin_name}
    if not db.set_if_absent("/crypto/wallet", doc):
        raise ApiError("A wallet already exists.", 409)
    log.info("Monero wallet created (restore height %s)", height)
    try:
        ensure_open(force=True)
    except ApiError as e:  # the wallet exists and is safely stored; the engine can be started later
        log.warning("wallet created but engine not started: %s", e.message)
    return public_wallet()


def public_wallet():
    d = wallet_doc()
    if not d:
        return None
    return {"address": d.get("address", ""), "restore_height": d.get("restore_height", 0), "created_at": d.get("created_at", 0),
            "created_by": d.get("created_by", ""), "invoices_made": int(d.get("next_index", 0))}


def _wallet_name():
    return "dh_wallet"


def ensure_open(force=False):
    """Restore the wallet from the encrypted seed in Firebase (once per boot) and re-create the invoice subaddresses."""
    with _lock:
        if _state["opened"] and not force:
            alive = getattr(_rpc, "alive", lambda: True)()
            if alive:
                return
            _state["opened"] = False
        doc = wallet_doc()
        if not doc:
            raise XmrError("No wallet yet. Create it in Settings → Crypto.", 409)
        seed = decrypt_seed(doc)
        _state["wpass"] = _state["wpass"] or secrets.token_hex(16)
        name = _wallet_name()
        try:
            _rpc.call("restore_deterministic_wallet", filename=name, password=_state["wpass"], seed=seed,
                      restore_height=int(doc.get("restore_height", 0)), language="English", autosave_current=False)
        except XmrError as e:
            if "exist" not in str(e).lower():
                raise
            _rpc.call("open_wallet", filename=name, password=_state["wpass"])
        have = len(_rpc.call("get_address", account_index=0).get("addresses") or [])
        need = int(doc.get("next_index", 0)) + 1
        while have < need:  # subaddresses handed out before the restart exist again (same indices, same addresses)
            _rpc.call("create_address", account_index=0, label="restored")
            have += 1
        _state["opened"] = True
        log.info("Monero wallet restored from Firebase seed (restore height %s, %s subaddresses)", doc.get("restore_height"), have)


def stop():
    with _lock:
        _state["opened"] = False
        if hasattr(_rpc, "stop"):
            _rpc.stop()


# ===================================================================== invoices
def _address_invoice(txn_id, pico, usd, rate, extra=None):
    height = node_height()
    with _lock:
        ensure_open()
        r = _rpc.call("create_address", account_index=0, label=txn_id)
        index = int(r["address_index"])
        db.transaction("/crypto/wallet/next_index", lambda c: max(int(c or 0), index))
    now = utils.now()
    return {"address": r["address"], "index": index, "xmr": pico_to_xmr(pico), "pico": pico, "usd": round(float(usd), 4),
            "rate": rate, "height": height, "created_at": now, "expires_at": now + Config.XMR_WINDOW_MINUTES * 60,
            "confirmations": Config.XMR_CONFIRMATIONS, "received_pico": 0, "seen_pico": 0, "txids": [], **(extra or {})}


def new_invoice(txn_id, usd):
    """A fresh subaddress for one order (priced at the live XMR/USD rate). Returns the dict stored on the txn as `crypto`."""
    if not bot_enabled():
        raise XmrError("Crypto payments are not available right now.")
    st = settings()
    if usd + 1e-9 < st["min_usd"]:
        raise ApiError(f"The minimum crypto payment is ${st['min_usd']:.2f}.")
    rate = xmr_usd()
    return _address_invoice(txn_id, usd_to_pico(usd, rate), usd, rate)


def new_deposit_invoice(txn_id, inr):
    """Wallet deposit: the amount is converted with the admin's FIXED ₹-per-XMR rate (no market price involved)."""
    if not deposit_ready():
        raise XmrError("Crypto deposits are not available right now.")
    st = settings()
    usd_est = float(inr) / st["inr_per_usd"]
    if usd_est + 1e-9 < st["min_usd"]:
        raise ApiError(f"The minimum crypto payment is about {utils.inr(round(st['min_usd'] * st['inr_per_usd'], 2))}.")
    return _address_invoice(txn_id, inr_to_pico(inr, st["xmr_inr"]), usd_est, None, {"fixed_inr_rate": st["xmr_inr"], "inr": float(inr)})


def _need_pico(c):
    return int(int(c["pico"]) * (1 - max(Config.XMR_TOLERANCE_PCT, 0) / 100))


def transfers_for(c):
    """What the wallet has seen on this invoice's subaddress since the invoice's block height."""
    ensure_open()
    try:
        _rpc.call("refresh")
    except XmrError as e:
        log.info("refresh: %s", e)
    r = _rpc.call("get_transfers", **{"in": True, "pending": True, "pool": True, "account_index": 0,
                                      "subaddr_indices": [int(c["index"])], "filter_by_height": True,
                                      "min_height": max(int(c.get("height", 0)) - 1, 0)})
    rows = []
    for kind in ("in", "pending", "pool"):
        for t in r.get(kind) or []:
            if int((t.get("subaddr_index") or {}).get("minor", c["index"])) != int(c["index"]):
                continue
            rows.append({"txid": t.get("txid", ""), "pico": int(t.get("amount", 0)), "conf": int(t.get("confirmations", 0) or 0),
                         "height": int(t.get("height", 0) or 0)})
    return rows


def _cross_check(txids):
    """Ask a *second* remote node whether these transactions are really in a block (a single bad node must not be able to
    make us hand out a license). Skipped when only one node is configured or the others are unreachable."""
    others = node_list()[1:]
    if not others:
        return True
    reached = False
    for n in others:
        try:
            ok = True
            for h in txids:
                j = _node_json(n, "/get_transactions", {"txs_hashes": [h], "decode_as_json": False})
                txs = j.get("txs") or []
                ok = ok and bool(txs) and not txs[0].get("in_pool") and int(txs[0].get("block_height") or 0) > 0
            reached = True
            if ok:
                return True
        except Exception as e:  # noqa: BLE001
            log.info("cross-check on %s failed: %s", n, e)
    return not reached  # nobody reachable -> do not block the sale; somebody reachable and not agreeing -> wait


def check_invoice(c):
    """-> {paid, seen, received_pico, seen_pico, txids, conf}"""
    rows = transfers_for(c)
    need = _need_pico(c)
    confirmed = [t for t in rows if t["conf"] >= int(c.get("confirmations", 1))]
    got = sum(t["pico"] for t in confirmed)
    seen = sum(t["pico"] for t in rows)
    paid = got >= need
    if paid and not _cross_check([t["txid"] for t in confirmed]):
        paid = False
    return {"paid": paid, "received_pico": got, "seen_pico": seen, "seen": seen >= need, "txids": [t["txid"] for t in rows],
            "conf": max([t["conf"] for t in rows] or [0])}


# ===================================================================== poller
def pending_invoices():
    return [t for t in db.query_equal("/txns", "status", "pending") if t.get("via") == "crypto" and t.get("crypto")]


def poll_once():
    """Check every open crypto invoice. Expired invoices are failed WITHOUT querying the chain again."""
    from services import payments
    now = utils.now()
    done = 0
    for t in pending_invoices():
        c = t["crypto"]
        txid = t["id"]
        try:
            if now >= int(c["expires_at"]):
                if c.get("grace_used") or not c.get("seen_pico"):
                    payments.expire_crypto(txid)
                    continue
                # a payment was already seen in the mempool before time ran out: give it a few more minutes to confirm
                info = check_invoice(c)
                if info["paid"]:
                    payments.complete_crypto(txid, info)
                    done += 1
                elif info["seen"]:
                    db.update(f"/txns/{txid}/crypto", {"expires_at": now + Config.XMR_GRACE_MINUTES * 60, "grace_used": True})
                    db.update(f"/txns/{txid}", {"expires_at": now + Config.XMR_GRACE_MINUTES * 60})
                else:
                    payments.expire_crypto(txid)
                continue
            info = check_invoice(c)
            if info["paid"]:
                payments.complete_crypto(txid, info)
                done += 1
            elif info["seen_pico"] != int(c.get("seen_pico", 0)) or info["conf"] != int(c.get("conf", 0)):
                db.update(f"/txns/{txid}/crypto", {"seen_pico": info["seen_pico"], "conf": info["conf"], "txids": info["txids"]})
                payments.crypto_seen(txid, info)
        except ApiError as e:
            log.warning("crypto invoice %s: %s", txid, e.message)
        except Exception:
            log.exception("crypto invoice %s failed", txid)
    return done


def _loop():
    time.sleep(15)
    while True:
        try:
            if configured() and pending_invoices():
                poll_once()
                _state["poll_err"] = ""
            _state["poll_ts"] = utils.now()
        except Exception as e:  # noqa: BLE001
            _state["poll_err"] = str(e)[:200]
            log.exception("crypto poller")
        time.sleep(max(5, Config.XMR_POLL_SECONDS))


def start():
    if _state["poller"]:
        return
    _state["poller"] = True
    threading.Thread(target=_loop, daemon=True, name="xmr-poller").start()


# ===================================================================== money in / out for the admin
def record_received(txn, info):
    """Ledger row for a paid invoice (what 'received' totals are built from)."""
    c = txn["crypto"]
    row = {"id": utils.ts_id(), "ts": utils.now(), "txn_id": txn["id"], "pico": int(info["received_pico"]),
           "usd": round(float(c["usd"]), 4), "inr": round(float(txn.get("amount", 0)), 2), "kind": txn.get("kind", "license"),
           "address": c["address"],
           "index": c["index"], "txids": info["txids"], "app_id": txn.get("app_id", ""), "app_name": txn.get("app_name", ""),
           "user": {"chat_id": txn["user_id"], "full_name": (txn.get("user") or {}).get("full_name", ""),
                    "username": (txn.get("user") or {}).get("username", "")}}
    db.set(f"/crypto_ledger/{row['id']}", row)
    return row


def ledger():
    return [r for r in db.get_list("/crypto_ledger")]


def received_stats(name, start=None, end=None):
    lo, hi = utils.resolve_range(name, start, end)
    rows = [r for r in ledger() if lo <= r["ts"] <= hi]
    return {"count": len(rows), "xmr": pico_to_xmr(sum(int(r["pico"]) for r in rows)),
            "usd": round(sum(float(r["usd"]) for r in rows), 2), "inr": round(sum(float(r["inr"]) for r in rows), 2)}


def balance():
    """Wallet balance (XMR) with a USD estimate. Needs the engine, so it is only asked for on the Crypto page."""
    ensure_open()
    b = _rpc.call("get_balance", account_index=0)
    total, unlocked = int(b.get("balance", 0)), int(b.get("unlocked_balance", 0))
    try:
        rate = xmr_usd()
    except ApiError:
        rate = None
    usd = lambda p: round(float(Decimal(p) / PICO) * rate, 2) if rate else None  # noqa: E731
    fx = settings()["xmr_inr"]
    inr = (lambda p: pico_to_inr(p, fx)) if fx > 0 else (lambda p: None)  # noqa: E731
    return {"total_xmr": pico_to_xmr(total), "unlocked_xmr": pico_to_xmr(unlocked), "locked_xmr": pico_to_xmr(total - unlocked),
            "total_usd": usd(total), "unlocked_usd": usd(unlocked), "rate": rate, "fixed_inr_rate": fx or None,
            "total_inr_fixed": inr(total), "unlocked_inr_fixed": inr(unlocked)}


def sweep(admin_name, password, address=None):
    """Move everything unlocked to the main account address (needs the monero-wallet-rpc: signing cannot be done without it)."""
    check_password(password)
    dest = (address or settings()["main_address"] or "").strip()
    if not valid_address(dest):
        raise ApiError("Save your main Monero address in Settings → Crypto first.")
    if dest == (wallet_doc() or {}).get("address"):
        raise ApiError("The destination is this same wallet.")
    ensure_open()
    b = _rpc.call("get_balance", account_index=0)
    if int(b.get("unlocked_balance", 0)) <= 0:
        raise ApiError("Nothing to transfer yet: no unlocked balance (new payments unlock after ~10 confirmations).")
    r = _rpc.call("sweep_all", address=dest, account_index=0, priority=1, get_tx_keys=False)
    hashes = r.get("tx_hash_list") or []
    amount = sum(int(a) for a in r.get("amount_list") or [])
    fee = sum(int(a) for a in r.get("fee_list") or [])
    sid = utils.ts_id()
    db.set(f"/crypto_sweeps/{sid}", {"id": sid, "ts": utils.now(), "by": admin_name, "to": dest, "txids": hashes,
                                     "pico": amount, "fee_pico": fee})
    _advance_restore_height()
    return {"txids": hashes, "xmr": pico_to_xmr(amount), "fee_xmr": pico_to_xmr(fee), "to": dest}


def sweeps():
    rows = db.get_list("/crypto_sweeps")
    rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return [{**r, "xmr": pico_to_xmr(r.get("pico", 0)), "fee_xmr": pico_to_xmr(r.get("fee_pico", 0))} for r in rows]


def _advance_restore_height():
    """After a sweep the wallet holds nothing old any more: later restores can start from (about) now, so scanning stays short.
    Open invoices keep their own height as the lower bound."""
    try:
        h = node_height() - 30
        low = min([int(t["crypto"].get("height", h)) for t in pending_invoices()] + [h])
        cur = int((wallet_doc() or {}).get("restore_height", 0))
        if low > cur:
            db.update("/crypto/wallet", {"restore_height": low})
    except Exception:  # noqa: BLE001
        log.info("could not advance restore height", exc_info=True)


def reveal_seed(admin_name, password):
    check_password(password)
    doc = wallet_doc()
    if not doc:
        raise ApiError("No wallet yet.", 404)
    return {"seed": decrypt_seed(doc), "address": doc.get("address", ""), "restore_height": doc.get("restore_height", 0),
            "created_height": doc.get("created_height", doc.get("restore_height", 0))}


def status():
    d = wallet_doc()
    st = settings()
    return {"configured": bool(d), "keypass_set": bool(keypass()), "binary": bool(_binary()), "node": Config.XMR_NODE,
            "enabled": st["enabled"], "inr_per_usd": st["inr_per_usd"], "rate_set": st["rate_set"],
            "main_address": st["main_address"], "min_usd": st["min_usd"], "xmr_inr": st["xmr_inr"], "deposit_enabled": st["deposit_enabled"],
            "deposit_ready": deposit_ready(), "window_minutes": Config.XMR_WINDOW_MINUTES,
            "confirmations": Config.XMR_CONFIRMATIONS, "wallet": public_wallet(), "running": bool(_state["opened"]),
            "poll_error": _state["poll_err"], "open_invoices": len(pending_invoices()) if d else 0}
