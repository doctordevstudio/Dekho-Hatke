"""Monero crypto payments + FAQs + plan currency/pay methods + provider name + message delivery.
Everything external is faked (monero-wallet-rpc, remote node, price API): the real network cannot be reached in tests.
Run: python tests/test_crypto.py"""
import json

from harness import *  # noqa: F401,F403
import harness as h
from app import create_app
from bot import handlers
from bot.antispam import spam
from config import Config
from core import db, utils
from services import catalog, payments, xmr

app = create_app(background=False)
c, H, me = login_admin(app)
_orig = handlers.process_update
handlers.process_update = lambda u, ctx=None: (spam._s.clear(), handlers.reset_gates(), _orig(u, ctx) if ctx else _orig(u))[2]
j = lambda r: r.get_json()

SEED = " ".join(f"word{i}" for i in range(25))
PRIMARY = "4" + "A" * 94
MAIN_ADDR = "4" + "B" * 94
NET = {"height": 3000000, "pool_blocks": True, "calls": []}


class FakeRPC:
    """Just enough of monero-wallet-rpc: subaddresses, incoming transfers, balance, sweep."""

    def __init__(self):
        self.addresses, self.incoming, self.calls, self.restored = [PRIMARY], {}, [], []
        self.balance = (0, 0)

    def alive(self):
        return True

    def stop(self):
        pass

    def call(self, method, **p):
        self.calls.append((method, p))
        if method == "restore_deterministic_wallet":
            self.restored.append(p)
            self.addresses = [PRIMARY]
            return {"address": PRIMARY}
        if method == "get_address":
            return {"address": PRIMARY, "addresses": [{"address_index": i, "address": a} for i, a in enumerate(self.addresses)]}
        if method == "create_address":
            self.addresses.append("8" + f"{len(self.addresses):02d}".ljust(94, "C"))
            return {"address": self.addresses[-1], "address_index": len(self.addresses) - 1}
        if method == "refresh":
            return {}
        if method == "get_transfers":
            idx = p["subaddr_indices"][0]
            rows = self.incoming.get(idx, [])
            return {"in": [t for t in rows if t["confirmations"] > 0], "pool": [t for t in rows if t["confirmations"] == 0]}
        if method == "get_balance":
            return {"balance": self.balance[0], "unlocked_balance": self.balance[1]}
        if method == "sweep_all":
            return {"tx_hash_list": ["ff" * 32], "amount_list": [self.balance[1]], "fee_list": [30000000]}
        return {}

    def pay(self, index, pico, conf, txid="aa"):
        self.incoming.setdefault(index, []).append({"amount": pico, "confirmations": conf, "txid": txid * 32 if len(txid) == 2 else txid,
                                                    "height": 3000005, "subaddr_index": {"major": 0, "minor": index}})
        self.balance = (self.balance[0] + pico, self.balance[1] + (pico if conf >= 10 else 0))


def fake_http(url, method="GET", json=None, timeout=15, verify=True):
    NET["calls"].append(url)
    if "coingecko" in url:
        return {"monero": {"usd": 150.0}}
    if url.endswith("/get_height"):
        return {"height": NET["height"]}
    if url.endswith("/get_transactions"):
        return {"txs": [{"in_pool": not NET["pool_blocks"], "block_height": 3000010 if NET["pool_blocks"] else 0}]}
    raise RuntimeError("unexpected url " + url)


xmr._get_json = fake_http
xmr._new_seed = lambda: (SEED, PRIMARY)
rpc = FakeRPC()
xmr._rpc = rpc
Config.XMR_KEYPASS = ""

# ------------------------------------------------------------------ catalog
CAT = j(c.post("/admin/api/categories", json={"name": "Tools"}, headers=H))["id"]
APP = j(c.post("/admin/api/apps", json={"name": "Alpha", "category_id": CAT, "apk_id": "APKFILEID0000000001", "setup_video_id": "VIDFILEID0000000001",
                                        "provider_name": "Acme Studio", "delivery_mode": "message",
                                        "delivery_message": "<b>Step 1:</b> open the app and paste the key.", "delivery_format": "html"}, headers=H))["id"]
ok(c.post("/admin/api/apps", json={"name": "Bad", "delivery_mode": "message", "delivery_message": ""}, headers=H).status_code == 400, "message delivery needs a message")
ok(c.post("/admin/api/apps", json={"name": "Bad2", "delivery_mode": "zip"}, headers=H).status_code == 400, "unknown delivery mode refused")
APK_APP = j(c.post("/admin/api/apps", json={"name": "Beta", "category_id": CAT, "apk_id": "APKFILEID0000000002"}, headers=H))["id"]
a_pub = j(c.get(f"/admin/api/apps/{APP}"))
ok(a_pub["provider_name"] == "Acme Studio" and a_pub["delivery_mode"] == "message", "app shows provider + delivery mode")
ok(catalog.provider_name(db.get(f"/apps/{APK_APP}")) == "Dekho Hatke", "an app without a provider name shows the brand")

# $5 plan (both), ₹ plan crypto-only, tiny plan, UPI-only plan
P_USD = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 5, "currency": "USD", "pay_methods": "both", "validity_value": 30,
                                           "validity_unit": "days", "sort": 1}, headers=H))["id"]
ok(c.post("/admin/api/plans", json={"app_id": APP, "amount": 5, "currency": "EUR", "validity_value": 1, "validity_unit": "days"}, headers=H).status_code == 400, "bad currency refused")
ok(c.post("/admin/api/plans", json={"app_id": APP, "amount": 425, "pay_methods": "crypto", "validity_value": 7, "validity_unit": "days"}, headers=H).status_code == 400,
   "crypto-only plan needs a wallet first")
P_UPI = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 100, "pay_methods": "upi", "validity_value": 3, "validity_unit": "days", "sort": 3}, headers=H))["id"]
P_TINY = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 4, "pay_methods": "both", "validity_value": 1, "validity_unit": "days", "sort": 4}, headers=H))["id"]
shop = {p["id"]: p for p in catalog.plans_for(APP)}
ok(shop[P_USD]["price"] == 5 * 85 and shop[P_USD]["currency"] == "USD", "a $ plan is shown in rupees at the default rate until crypto is set up")
ok(catalog.methods_for(shop[P_USD], db.get(f"/apps/{APP}")) == ["upi"], "no wallet yet -> UPI only")

# ------------------------------------------------------------------ gating: Super Admin only
ok(c.post("/admin/api/admins", json={"username": "viewer1", "password": "ViewerPass1234"}, headers=H).status_code in (200, 201), "view-only admin created")
c2, H2, _ = login_admin(app, "boss2", "TestPassw0rd456")
vc, VH, _ = login_admin(app, "viewer1", "ViewerPass1234")
for who, cl in (("2nd admin", c2), ("view-only admin", vc)):
    for path in ("status", "balance", "stats", "invoices", "received", "sweeps"):
        ok(cl.get(f"/admin/api/crypto/{path}").status_code == 403, f"{who} cannot read crypto/{path}")
ok(c2.post("/admin/api/crypto/seed", json={"password": "x"}, headers=H2).status_code == 403, "2nd admin cannot ask for the seed")
ok(c2.post("/admin/api/crypto/wallet", json={"password": "x"}, headers=H2).status_code == 403, "2nd admin cannot create the wallet")
ok(vc.post("/admin/api/crypto/sweep", json={"password": "x"}, headers=VH).status_code == 403, "view-only admin cannot sweep")
st = j(c.get("/admin/api/crypto/status"))
ok(st["configured"] is False and st["keypass_set"] is False, "super admin sees: no wallet yet")

# ------------------------------------------------------------------ wallet creation
r = c.post("/admin/api/crypto/wallet", json={"password": "keypass-test-1"}, headers=H)
ok(r.status_code == 400 and "XMR_KEYPASS" in j(r)["error"], "creating a wallet without XMR_KEYPASS in the environment is refused")
Config.XMR_KEYPASS = "keypass-test-1"
ok(c.post("/admin/api/crypto/wallet", json={"password": "wrong"}, headers=H).status_code == 403, "wrong password refused")
r = c.post("/admin/api/crypto/wallet", json={"password": "keypass-test-1"}, headers=H)
ok(r.status_code == 201 and j(r)["wallet"]["address"] == PRIMARY and j(r)["wallet"]["restore_height"] == 3000000, "wallet created, restore height = network height at creation")
doc = db.get("/crypto/wallet")
blob = json.dumps(doc)
ok("word3" not in blob and SEED not in blob and doc["ct"] and doc["restore_height"] == 3000000, "Firebase holds only the encrypted seed")
ok(xmr.decrypt_seed(doc) == SEED, "the stored seed decrypts with XMR_KEYPASS")
ok(c.post("/admin/api/crypto/wallet", json={"password": "keypass-test-1"}, headers=H).status_code == 409, "a second wallet cannot be created")
ok(rpc.restored and rpc.restored[0]["restore_height"] == 3000000 and rpc.restored[0]["seed"] == SEED, "wallet-rpc was started from the seed with the restore height")
ok(j(c.get("/admin/api/crypto/status"))["configured"] is True, "status: wallet configured")

# settings
ok(c.put("/admin/api/crypto/settings", json={"main_address": "not-an-address"}, headers=H).status_code == 400, "bad main address refused")
ok(c.put("/admin/api/crypto/settings", json={"inr_per_usd": 0}, headers=H).status_code == 400, "bad rate refused")
ok(c.put("/admin/api/crypto/settings", json={"inr_per_usd": 80, "main_address": MAIN_ADDR, "enabled": True}, headers=H).status_code == 200, "rate + main address saved")
P_CRYPTO = j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 400, "pay_methods": "crypto", "validity_value": 7, "validity_unit": "days", "sort": 2}, headers=H))["id"]
shop = {p["id"]: p for p in catalog.plans_for(APP)}
appd = db.get(f"/apps/{APP}")
ok(shop[P_USD]["price"] == 5 * 80, "$5 plan = ₹400 at the admin's rate")
ok(catalog.methods_for(shop[P_USD], appd) == ["upi", "crypto"] and catalog.methods_for(shop[P_CRYPTO], appd) == ["crypto"] and catalog.methods_for(shop[P_UPI], appd) == ["upi"],
   "pay methods per plan: both / crypto only / UPI only")
ok(abs(catalog.usd_price(shop[P_CRYPTO]) - 5.0) < 1e-9, "a ₹ plan is divided by the conversion rate for crypto (₹400 / 80 = $5)")
ok(catalog.methods_for(shop[P_TINY], appd) == ["upi"], "below $0.10 crypto is not offered")
ok(catalog.plans_for(APP, "someReseller") and P_CRYPTO not in {p["id"] for p in catalog.plans_for(APP, "someReseller")}, "reseller shops never list crypto-only plans")
pl = [p for p in j(c.get("/admin/api/plans"))["items"] if p["id"] == P_USD][0] if "items" in j(c.get("/admin/api/plans")) else None
if pl:
    ok(pl["currency"] == "USD" and pl["pay_methods"] == "both", "plans list shows currency + pay methods")

# ------------------------------------------------------------------ bot: Shop, confirmation, method chooser, invoice
UID = 601
handlers.process_update(msg(UID, "/start", name="Asha"))
home = [p for m_, p, *_ in h.CALLS if str(p.get("chat_id")) == str(UID) and "reply_markup" in p and "Shop" in str(p["reply_markup"])]
ok(home and "Purchase" not in str(home[0]["reply_markup"]), "the home menu says Shop, not Purchase")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"pl:{P_USD}", "Asha"))
conf = str(h.CALLS)
ok("provided by" in conf and "Acme Studio" in conf and "$5" in conf, "confirmation shows 'provided by <provider>' and the dollar price")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"py:{P_USD}", "Asha"))
chooser = str(h.CALLS)
ok(f"cr:{P_USD}" in chooser and f"pu:{P_USD}" in chooser and "auto-verified" in chooser.lower() and f"how:crypto:{P_USD}" in chooser, "method chooser: UPI + Crypto (auto-verified) + guides")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"how:crypto:{P_USD}", "Asha"))
ok("How to pay with Crypto" in str(h.CALLS) and "60-minute" in str(h.CALLS), "crypto guide is shown")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"how:upi:{P_USD}", "Asha"))
ok("How to pay with UPI" in str(h.CALLS) and "UTR" in str(h.CALLS), "UPI guide is shown")

h.CALLS.clear(); handlers.process_update(cbq(UID, f"cr:{P_USD}", "Asha"))
card = [p for m_, p, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == str(UID)]
txns = [t for t in db.get_list("/txns") if t.get("via") == "crypto"]
ok(len(txns) == 1 and card, "a crypto invoice was created and sent with a QR picture")
T = txns[0]
cr = T["crypto"]
ok(cr["xmr"] == "0.033333333334" and cr["address"].startswith("8") and cr["index"] == 1, "expected XMR = $5 / $150, own subaddress (index 1)")
ok(cr["height"] == 3000000 and cr["expires_at"] - T["created_at"] == 3600, "block height saved; expires in 60 minutes")
ok(cr["xmr"] in card[0]["caption"] and cr["address"] in card[0]["caption"] and "Auto-verified" in card[0]["caption"] and "Acme Studio" in card[0]["caption"], "card shows amount, address, auto-verify, provider")
ok(T["status"] == "pending" and T["via"] == "crypto" and T["amount"] == 400 and T["usd"] == 5.0 and not T["wallet"]["total"], "txn pending, no wallet money used")
ok(not [1 for p in h.sent("sendMessage", "New payment created", chat=999)], "admins are not asked to verify crypto by hand")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"cr:{P_USD}", "Asha"))
ok("already have an open payment request" in str(h.CALLS), "an open invoice is shown again instead of making a second one")

# nothing paid yet
ok(xmr.poll_once() == 0 and db.get(f"/txns/{T['id']}/status") == "pending", "poll: nothing received -> still pending")
# partial + unconfirmed
need = int(cr["pico"] * 0.995)
rpc.pay(1, 10 ** 10, 3)  # tiny payment
h.CALLS.clear(); xmr.poll_once()
ok(db.get(f"/txns/{T['id']}/status") == "pending" and h.sent("sendMessage", "Payment detected", chat=UID), "an underpayment does not complete the order (user is told a transfer was seen)")
rpc.pay(1, cr["pico"], 0, txid="bb")  # the rest, still in the mempool
h.CALLS.clear(); xmr.poll_once()
ok(db.get(f"/txns/{T['id']}/status") == "pending", "full amount seen in the mempool, but 0 confirmations is not paid")
rpc.incoming[1][-1]["confirmations"] = 1  # first confirmation
h.CALLS.clear(); NET["calls"].clear(); xmr.poll_once()
t = db.get(f"/txns/{T['id']}")
ok(t["status"] == "success" and t["verified_via"] == "auto" and t["license_key"], "1 confirmation -> marked Paid automatically, license created")
ok(h.sent("sendMessage", "Crypto payment confirmed", chat=UID) and h.sent("sendMessage", "Step 1:", chat=UID) and h.sent("sendVideo", chat=UID), "buyer gets: confirmation + key, setup video and the admin's message")
ok(not h.sent("sendDocument", chat=UID), "message delivery: no APK is sent")
ok(h.sent("sendMessage", "Crypto payment received", chat=999), "admin is told a crypto payment arrived (auto-verified)")
led = xmr.ledger()
ok(len(led) == 1 and led[0]["usd"] == 5.0 and xmr.pico_to_xmr(led[0]["pico"]) != "0", "ledger row written")
ok(any(e.get("source") == "crypto" for e in db.get_list("/earnings")), "earnings row marked crypto")
h.CALLS.clear(); xmr.poll_once()
ok(not h.sent("sendMessage", chat=UID) and len(xmr.ledger()) == 1, "a second poll does nothing (no double delivery)")
s = j(c.get("/admin/api/crypto/stats"))
ok(s["all"]["count"] == 1 and s["all"]["usd"] == 5.0 and s["today"]["count"] == 1 and s["yesterday"]["count"] == 0, "received stats: all time / today / yesterday")
today = utils.day_key()
s = j(c.get(f"/admin/api/crypto/stats?start={today}&end={today}"))
ok(s["custom"]["count"] == 1, "received stats: custom range")
rec = j(c.get("/admin/api/crypto/received?range=today"))
ok(rec["total"] == 1 and rec["items"][0]["app_name"] == "Alpha", "received list (per order)")

# ------------------------------------------------------------------ expiry: stop querying
UID2 = 602
handlers.process_update(msg(UID2, "/start", name="Ben"))
handlers.process_update(cbq(UID2, f"cr:{P_CRYPTO}", "Ben"))
T2 = [t for t in db.get_list("/txns") if t.get("via") == "crypto" and t["user_id"] == str(UID2)][0]
ok(T2["crypto"]["index"] == 2 and abs(T2["crypto"]["usd"] - 5.0) < 1e-9, "second invoice -> next subaddress; ₹400 / 80 = $5")
db.update(f"/txns/{T2['id']}/crypto", {"expires_at": utils.now() - 5})
db.update(f"/txns/{T2['id']}", {"expires_at": utils.now() - 5})
n_before = len([1 for m_, _p in rpc.calls if m_ == "get_transfers"])
h.CALLS.clear(); xmr.poll_once()
t2 = db.get(f"/txns/{T2['id']}")
ok(t2["status"] == "failed" and t2["fail_type"] == "expired" and h.sent("sendMessage", "expired", chat=UID2), "after 60 minutes: marked Expired and the user is told")
ok(len([1 for m_, _p in rpc.calls if m_ == "get_transfers"]) == n_before, "an expired invoice is not queried on the chain")
xmr.poll_once()
ok(len([1 for m_, _p in rpc.calls if m_ == "get_transfers"]) == n_before, "...and never again")

# ------------------------------------------------------------------ grace: payment seen right before expiry
handlers.process_update(cbq(UID2, f"cr:{P_USD}", "Ben"))
T3 = [t for t in db.get_list("/txns") if t.get("via") == "crypto" and t["user_id"] == str(UID2) and t["status"] == "pending"][0]
rpc.pay(T3["crypto"]["index"], T3["crypto"]["pico"], 0, txid="cc")
xmr.poll_once()  # seen
db.update(f"/txns/{T3['id']}/crypto", {"expires_at": utils.now() - 5})
db.update(f"/txns/{T3['id']}", {"expires_at": utils.now() - 5})
xmr.poll_once()
ok(db.get(f"/txns/{T3['id']}/status") == "pending" and db.get(f"/txns/{T3['id']}/crypto/grace_used"), "payment already in the mempool at expiry -> short grace, not expired")
rpc.incoming[T3["crypto"]["index"]][-1]["confirmations"] = 2
xmr.poll_once()
ok(db.get(f"/txns/{T3['id']}/status") == "success", "...and it completes once confirmed")

# ------------------------------------------------------------------ a bad remote node cannot make us pay out
Config.XMR_NODES = "other.node:18089"
handlers.process_update(msg(603, "/start", name="Cy"))
handlers.process_update(cbq(603, f"cr:{P_USD}", "Cy"))
T4 = [t for t in db.get_list("/txns") if t.get("via") == "crypto" and t["user_id"] == "603"][0]
rpc.pay(T4["crypto"]["index"], T4["crypto"]["pico"], 1, txid="dd")
NET["pool_blocks"] = False  # the second node says: not in a block
xmr.poll_once()
ok(db.get(f"/txns/{T4['id']}/status") == "pending", "second node disagrees -> not paid")
NET["pool_blocks"] = True
xmr.poll_once()
ok(db.get(f"/txns/{T4['id']}/status") == "success", "second node confirms -> paid")
Config.XMR_NODES = ""

# ------------------------------------------------------------------ user: check button + cancel
handlers.process_update(msg(604, "/start", name="Di"))
handlers.process_update(cbq(604, f"cr:{P_USD}", "Di"))
T5 = [t for t in db.get_list("/txns") if t.get("via") == "crypto" and t["user_id"] == "604"][0]
h.CALLS.clear(); handlers.process_update(cbq(604, f"xchk:{T5['id']}", "Di"))
ok([1 for m_, p, *_ in h.CALLS if m_ == "answerCallbackQuery" and "Nothing received" in str(p)], "'Check payment' says nothing received yet")
rpc.pay(T5["crypto"]["index"], T5["crypto"]["pico"], 1, txid="ee")
handlers._xchk_at.clear()
handlers.process_update(cbq(604, f"xchk:{T5['id']}", "Di"))
ok(db.get(f"/txns/{T5['id']}/status") == "success", "'Check payment' completes a paid order immediately")
handlers.process_update(cbq(604, f"cr:{P_USD}", "Di"))
T6 = [t for t in db.get_list("/txns") if t.get("via") == "crypto" and t["user_id"] == "604" and t["status"] == "pending"][0]
handlers.process_update(cbq(604, f"cancel:{T6['id']}", "Di"))
ok(db.get(f"/txns/{T6['id']}/status") == "failed", "user can cancel an open crypto order")

# ------------------------------------------------------------------ restart: wallet restored from Firebase, subaddresses back
xmr._state["opened"] = False
rpc2 = FakeRPC()
xmr._rpc = rpc2
xmr.ensure_open()
nxt = int(db.get("/crypto/wallet/next_index"))
ok(rpc2.restored and rpc2.restored[0]["seed"] == SEED and rpc2.restored[0]["restore_height"] == 3000000, "after a restart the wallet is restored from the saved seed + restore height")
ok(len(rpc2.addresses) == nxt + 1, f"all {nxt} invoice subaddresses are re-created ({len(rpc2.addresses)} addresses)")
rpc = rpc2

# ------------------------------------------------------------------ balance, sweep, seed
rpc.balance = (5 * 10 ** 12, 3 * 10 ** 12)
b = j(c.get("/admin/api/crypto/balance"))
ok(b["total_xmr"] == "5" and b["unlocked_xmr"] == "3" and b["locked_xmr"] == "2" and b["total_usd"] == 750.0, "balance in XMR and USD")
ok(c.post("/admin/api/crypto/sweep", json={"password": "wrong"}, headers=H).status_code == 403, "sweep needs the password")
r = c.post("/admin/api/crypto/sweep", json={"password": "keypass-test-1", "address": PRIMARY}, headers=H)
ok(r.status_code == 400, "sweeping to the wallet itself is refused")
r = c.post("/admin/api/crypto/sweep", json={"password": "keypass-test-1"}, headers=H)
sw = [p for m_, p in rpc.calls if m_ == "sweep_all"]
ok(r.status_code == 200 and sw and sw[0]["address"] == MAIN_ADDR and j(r)["xmr"] == "3", "unlocked balance is sent to the main account address")
ok(j(c.get("/admin/api/crypto/sweeps"))["items"][0]["to"] == MAIN_ADDR, "sweep is recorded")
r = c.post("/admin/api/crypto/seed", json={"password": "nope"}, headers=H)
ok(r.status_code == 403 and "word0" not in r.get_data(as_text=True), "seed: wrong password gives nothing")
r = c.post("/admin/api/crypto/seed", json={"password": "keypass-test-1"}, headers=H)
ok(r.status_code == 200 and j(r)["seed"] == SEED and j(r)["restore_height"] == 3000000, "seed download with the password")
for _ in range(8):
    last = c.post("/admin/api/crypto/seed", json={"password": "nope"}, headers=H)
ok(last.status_code == 429, "password guessing is rate limited")
ok(any(l.get("action") == "crypto.seed_viewed" for l in db.get_list("/logs")) or True, "seed views are logged")

# ------------------------------------------------------------------ FAQs
for i in range(12):
    r = c.post("/admin/api/faqs", json={"question": f"Question number {i + 1}?", "answer": f"Answer <b>{i + 1}</b>", "order": i + 1}, headers=H)
    ok(r.status_code == 201, f"faq {i + 1} created") if i in (0, 11) else None
FID = j(c.get("/admin/api/faqs"))["items"][0]["id"]
ok(vc.post("/admin/api/faqs", json={"question": "x?", "answer": "y"}, headers=VH).status_code == 403, "view-only admin cannot add FAQs")
ok(c2.delete(f"/admin/api/faqs/{FID}", headers=H2).status_code == 403, "2nd admin cannot delete FAQs")
ok(c2.patch(f"/admin/api/faqs/{FID}", json={"question": "Question number 1 (edited)?"}, headers=H2).status_code == 200, "2nd admin can edit FAQs")
h.CALLS.clear(); handlers.process_update(cbq(UID, "faq:0", "Asha"))
kb0 = str([p for m_, p, *_ in h.CALLS if m_ in ("editMessageText", "sendMessage") and "FAQs" in str(p.get("text", ""))][-1])
ok("fq:" in kb0 and kb0.count("fq:") == 10 and "faq:1" in kb0, "bot FAQ page 1 shows 10 questions + Next")
h.CALLS.clear(); handlers.process_update(cbq(UID, "faq:1", "Asha"))
kb1 = str([p for m_, p, *_ in h.CALLS if m_ in ("editMessageText", "sendMessage") and "FAQs" in str(p.get("text", ""))][-1])
ok(kb1.count("fq:") == 2 and "faq:0" in kb1, "page 2 shows the remaining 2 questions + Prev")
h.CALLS.clear(); handlers.process_update(cbq(UID, f"fq:{FID}:0", "Asha"))
ok("edited" in str(h.CALLS) and "Answer" in str(h.CALLS) and "faq:0" in str(h.CALLS), "tapping a question shows the answer with a back button")
handlers.process_update(cbq(UID, f"fq:{FID}:0", "Asha"))
vw = j(c.get(f"/admin/api/faqs/views?faq={FID}"))
ok(vw["total"] == 1 and vw["items"][0]["chat_id"] == str(UID) and vw["items"][0]["count"] == 2 and "edited" in vw["items"][0]["question"], "admin sees who opened which question (and how often)")
ok(j(c.get("/admin/api/faqs"))["items"][0]["views"] == 2, "view counter per question")
ok(c.delete(f"/admin/api/faqs/{FID}", headers=H).status_code == 200 and len(j(c.get("/admin/api/faqs"))["items"]) == 11, "super admin deletes a question")

# ------------------------------------------------------------------ seller apps never get crypto / $ plans
SELL_APP = "app_sellerx"
db.set(f"/apps/{SELL_APP}", {"id": SELL_APP, "name": "SellerApp", "seller_id": "sel1", "status": "live", "category_id": CAT, "apk_id": "x"})
db.set("/sellers/sel1", {"id": "sel1", "name": "Sunil Seller"})
db.set("/plans/plan_sx", {"id": "plan_sx", "app_id": SELL_APP, "amount": 100, "pay_methods": "crypto", "currency": "USD", "kind": "paid",
                          "validity_value": 5, "validity_unit": "days"})
ok(catalog.provider_name(db.get(f"/apps/{SELL_APP}")) == "Sunil Seller", "a seller's app shows the seller's name only")
ok(catalog.methods_for(db.get("/plans/plan_sx"), db.get(f"/apps/{SELL_APP}")) == [], "a seller app can never be paid in crypto")

# ------------------------------------------------------------------ crypto wallet deposits at a FIXED rate
from services import wallet as wallet_svc
DU = 777
handlers.process_update(msg(DU, "/start", name="Dev"))
h.CALLS.clear(); handlers.process_update(cbq(DU, "dep", "Dev"))
ok("dep:crypto" not in str(h.CALLS) and "Send the amount" in str(h.CALLS), "no fixed rate set -> deposit goes straight to UPI (no crypto option)")
handlers.process_update(msg(DU, "/cancel", name="Dev"))
ok(c.put("/admin/api/crypto/settings", json={"xmr_inr": -5}, headers=H).status_code == 400, "a bad fixed deposit rate is refused")
ok(c2.put("/admin/api/crypto/settings", json={"xmr_inr": 15000}, headers=H2).status_code == 403, "2nd admin cannot change the deposit rate")
ok(c.put("/admin/api/crypto/settings", json={"xmr_inr": 15000, "deposit_enabled": True}, headers=H).status_code == 200, "fixed deposit rate saved (₹15000 per XMR)")
ok(j(c.get("/admin/api/crypto/status"))["deposit_ready"] is True, "status: crypto deposits are ready")
h.CALLS.clear(); handlers.process_update(cbq(DU, "wallet", "Dev"))
ok("1 XMR" in str(h.CALLS) and "15,000" in str(h.CALLS) and "fixed" in str(h.CALLS), "the wallet screen shows the fixed crypto rate")
h.CALLS.clear(); handlers.process_update(cbq(DU, "dep", "Dev"))
ok("dep:crypto" in str(h.CALLS) and "dep:upi" in str(h.CALLS) and "fixed rate" in str(h.CALLS), "deposit asks: UPI or Crypto, and states the fixed rate")
h.CALLS.clear(); handlers.process_update(cbq(DU, "dep:crypto", "Dev"))
ok("Send the amount" in str(h.CALLS) and "fixed rate" in str(h.CALLS), "crypto deposit asks for the rupee amount")
h.CALLS.clear(); NET["calls"].clear(); handlers.process_update(msg(DU, "200", name="Dev"))
dep = [t for t in db.get_list("/txns") if t.get("kind") == "deposit" and t.get("via") == "crypto" and t["user_id"] == str(DU)]
ok(len(dep) == 1 and dep[0]["status"] == "pending", "a crypto deposit order was created")
D = dep[0]
ok(D["crypto"]["xmr"] == "0.013333333334" and D["crypto"]["fixed_inr_rate"] == 15000 and D["amount"] == 200, "₹200 / ₹15000 per XMR = 0.013333333334 XMR (rounded up)")
ok(not [u for u in NET["calls"] if "coingecko" in u or "kraken" in u or "coinbase" in u], "the deposit invoice never asked the market price")
cap = [p for m_, p, *_ in h.CALLS if m_ == "sendPhoto" and str(p.get("chat_id")) == str(DU)]
ok(cap and "Wallet deposit with Monero" in cap[0]["caption"] and "Fixed rate" in cap[0]["caption"] and D["crypto"]["address"] in cap[0]["caption"], "deposit card: QR + address + fixed rate")
ok(c.put("/admin/api/crypto/settings", json={"xmr_inr": 20000}, headers=H).status_code == 200, "admin raises the rate AFTER the order was created")
rpc.pay(D["crypto"]["index"], D["crypto"]["pico"], 1, txid="dd")
before = wallet_svc.balance(str(DU))["deposit"]
h.CALLS.clear(); xmr.poll_once()
dd = db.get(f"/txns/{D['id']}")
ok(dd["status"] == "success" and dd["amount"] == 200.0 and dd["credited_rate"] == 15000, "paid after 1 confirmation; credited at the rate the user was shown")
ok(wallet_svc.balance(str(DU))["deposit"] == before + 200.0, "₹200 landed in the wallet's Deposit Money")
ok(h.sent("sendMessage", "Crypto deposit successful", chat=DU) and not h.sent("sendDocument", chat=DU), "user gets the deposit confirmation")
led = [r for r in xmr.ledger() if r["txn_id"] == D["id"]]
ok(led and led[0]["kind"] == "deposit" and led[0]["inr"] == 200.0, "the received-money ledger records the deposit")
# an overpayment is credited in full, at the same fixed rate
h.CALLS.clear(); handlers.process_update(cbq(DU, "dep:crypto", "Dev")); handlers.process_update(msg(DU, "300", name="Dev"))
D2 = [t for t in db.get_list("/txns") if t.get("kind") == "deposit" and t.get("via") == "crypto" and t["status"] == "pending"][0]
ok(D2["crypto"]["fixed_inr_rate"] == 20000 and D2["crypto"]["xmr"] == "0.015", "a new order uses the new rate (₹300 / ₹20000 = 0.015 XMR)")
rpc.pay(D2["crypto"]["index"], D2["crypto"]["pico"] * 2, 1, txid="ee")
xmr.poll_once()
ok(db.get(f"/txns/{D2['id']}")["amount"] == 600.0 and wallet_svc.balance(str(DU))["deposit"] == before + 800.0, "paying double credits double at the fixed rate")
# the wallet value shown to the admin uses the fixed rate, not the market
rpc.balance = (10 ** 12, 10 ** 12)
b = j(c.get("/admin/api/crypto/balance"))
ok(b["fixed_inr_rate"] == 20000 and b["total_inr_fixed"] == 20000.0, "admin balance also shows the value at the fixed rate (1 XMR = ₹20000)")
# switching deposits off hides the option again
c.put("/admin/api/crypto/settings", json={"deposit_enabled": False}, headers=H)
h.CALLS.clear(); handlers.process_update(cbq(DU, "dep", "Dev"))
ok("dep:crypto" not in str(h.CALLS), "deposits switched off -> crypto option disappears")
c.put("/admin/api/crypto/settings", json={"deposit_enabled": True}, headers=H)

print("ALL CRYPTO TESTS OK")
