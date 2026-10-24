# 🌐 Dekho Hatke — Cloudflare + Render sub-domain setup (dekhohatke.com)

Goal: run the whole project on **one Render free web service**, reachable **only through Cloudflare**, with one sub-domain per audience:

| Address | Who uses it | What it serves (and nothing else) |
|---|---|---|
| `https://admin.dekhohatke.com` | Super Admin, 2nd Admin, view-only admins | sign-in at `/login`, panel at `/`, `/admin/api/*` |
| `https://seller.dekhohatke.com` | Sellers | sign-in + panel at `/`, `/seller/api/*` |
| `https://reseller.dekhohatke.com` | Resellers | sign-in + panel at `/`, `/reseller/api/*` |
| `https://api.dekhohatke.com` | Your customers' apps + Telegram | `/api/v1/*` (license API), `/telegram/*` (bot webhooks), `/healthz` |

* **Your root domain `dekhohatke.com` / `www` is never touched.** You only *add* four DNS records, and every rule below is limited to these four host names.
* Each sub-domain serves **only its own panel**. `admin.…/seller` is a plain 404, `seller.…/login` is a plain 404, `api.…/` is a plain 404. Cookies are per host (`__Host-` prefix), so an admin login never works on another sub-domain.
* The Render address (`xxxx.onrender.com`) answers **404 to everything** (except `/healthz`), because the app only accepts requests that carry a secret header that only Cloudflare adds.

```
visitor ──HTTPS──▶ Cloudflare (WAF · bots · DDoS · rate limit · cache) ──HTTPS + secret header──▶ Render (Flask)
                                                                       ▲
                          anyone calling xxxx.onrender.com directly ───┘  → 404 (no secret)
```

Do the steps **in this order** — it avoids downtime and lock-outs.

---

## Step 1 — Deploy on Render (without the lock yet)

1. Render → **New → Web Service** (or Blueprint with `render.yaml`) → this repo. Start command and health check are in `render.yaml`.
2. Environment variables: everything in `README.md` §3 **plus**
   * `ROOT_DOMAIN` = `dekhohatke.com`   ← turns on the sub-domain mode
   * leave `ORIGIN_SECRET` **empty for now** (Step 5)
   * `BASE_URL` — leave empty (it becomes `https://api.dekhohatke.com` automatically; the Telegram webhooks are registered there)
3. Wait for the first deploy. Note your service address, e.g. `dekho-panel.onrender.com`.

## Step 2 — Tell Render about the four sub-domains

Render → your service → **Settings → Custom Domains → Add Custom Domain**, add each of:
`admin.dekhohatke.com`, `seller.dekhohatke.com`, `reseller.dekhohatke.com`, `api.dekhohatke.com`.
Render shows a CNAME target (your `xxxx.onrender.com`). Don't add the root domain or `www`.

## Step 3 — Add four DNS records in Cloudflare (root domain untouched)

Cloudflare → **DNS → Records → Add record** — four times:

| Type | Name | Target | Proxy status |
|---|---|---|---|
| CNAME | `admin` | `xxxx.onrender.com` | **DNS only** (grey cloud) for now |
| CNAME | `seller` | `xxxx.onrender.com` | **DNS only** |
| CNAME | `reseller` | `xxxx.onrender.com` | **DNS only** |
| CNAME | `api` | `xxxx.onrender.com` | **DNS only** |

* Don't edit or delete the existing `@` / `www` records.
* If any of the four names already has an **AAAA** (IPv6) record, delete it — Render doesn't support IPv6.
* Go back to Render → Custom Domains and wait until all four show **Verified** and a **certificate is issued** (a few minutes). Render can only verify while the cloud is grey.
* **Now switch all four records to Proxied (orange cloud).**

## Step 4 — SSL mode for these four hosts only

Do **not** change the zone-wide SSL setting (that would also affect the root domain). Use a rule instead:

Cloudflare → **Rules → Configuration Rules → Create rule**
* Name: `dekhohatke sub-domains: strict TLS`
* When incoming requests match → *Custom filter expression* → **Edit expression**:
  ```
  (http.host in {"admin.dekhohatke.com" "seller.dekhohatke.com" "reseller.dekhohatke.com" "api.dekhohatke.com"})
  ```
* Then: **SSL → Full (strict)**
* Deploy.

Create a second Configuration Rule, `panel hosts: higher security`:
```
(http.host in {"admin.dekhohatke.com" "seller.dekhohatke.com" "reseller.dekhohatke.com"})
```
→ **Security Level: High**, **Browser Integrity Check: On** → Deploy.

Optional (recommended) — force HTTPS for the four hosts: **Rules → Redirect Rules → Create rule**
`(http.host in {…the four hosts…} and not ssl)` → Dynamic redirect: `concat("https://", http.host, http.request.uri.path)`, status 301. (Skip if "Always Use HTTPS" is already on for your zone.)

## Step 5 — Lock the origin to Cloudflare (the "don't expose Render" step)

1. Make a long secret (on any computer with Python):
   ```
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. **Cloudflare first**: **Rules → Transform Rules → Modify Request Header → Create rule**
   * Name: `origin lock header`
   * Expression: the same four-host expression as in Step 4
   * Modify header → **Set static** → header name `X-Origin-Auth` → value = your secret → Deploy.
   (Cloudflare *overwrites* this header on every request, so a visitor can never forge it.)
3. **Then Render** → Environment → add `ORIGIN_SECRET` = the same secret → save (the service redeploys).
4. Check (see Step 9). If anything is wrong you can always **delete `ORIGIN_SECRET` in Render** to open the origin again.

What the lock does in the app:
* no / wrong header → plain `404 Not found` (even for the correct URL path);
* unknown `Host` (the raw `onrender.com` address, or any other domain) → `404`;
* `/healthz` is the only exception (Render's health check and your keep-alive ping need it; it just prints `ok`);
* with the secret present, the visitor's real IP is read from `CF-Connecting-IP`, so rate limits and login lock-outs work **per visitor** instead of per Cloudflare server.

## Step 6 — WAF custom rules (Security → WAF → Custom rules)

Create these in **this order** (order = priority). Use *Edit expression* and paste. Free plans have a small number of custom rules (5 at the time of writing — check your dashboard), these use exactly five.

**Rule 1 — Telegram webhook is always allowed**
Expression:
```
(http.host eq "api.dekhohatke.com" and starts_with(http.request.uri.path, "/telegram/") and ip.src in {149.154.160.0/20 91.108.4.0/22})
```
Action: **Skip** → tick *All remaining custom rules*, *All rate limiting rules*, *All managed rules*. (These are Telegram's published webhook ranges; the app additionally checks a secret path + secret header.)

**Rule 2 — Block data-centre networks and Tor on the three panel hosts**
```
(http.host in {"admin.dekhohatke.com" "seller.dekhohatke.com" "reseller.dekhohatke.com"}) and (ip.src.country eq "T1" or ip.src.asnum in {16509 14618 15169 396982 8075 14061 63949 20473 16276 24940 31898 36351 45102 37963 132203 45090 51167 12876 9009 60781 28753 60068 54825 47583 8560 197540})
```
Action: **Block**. (AWS, Google Cloud, Azure, DigitalOcean, Linode/Akamai, Vultr, OVH, Hetzner, Oracle, IBM, Alibaba, Tencent, Contabo, Scaleway, M247, Leaseweb, Datacamp, Equinix, Hostinger, IONOS, netcup. Extend the list whenever **Security → Events** shows another hosting network. Visitors on a commercial VPN are also blocked on the panels — that is intended. The customer API on `api.` is *not* blocked this way, so your buyers on any network can still use their apps.)

**Rule 3 — Block scripts and fake browsers on the panel hosts**
```
(http.host in {"admin.dekhohatke.com" "seller.dekhohatke.com" "reseller.dekhohatke.com"}) and (len(http.user_agent) lt 20 or not http.user_agent contains "Mozilla/" or lower(http.user_agent) contains "headless" or lower(http.user_agent) contains "python" or lower(http.user_agent) contains "curl" or lower(http.user_agent) contains "wget" or lower(http.user_agent) contains "scrapy" or lower(http.user_agent) contains "httpclient" or lower(http.user_agent) contains "go-http" or lower(http.user_agent) contains "okhttp" or lower(http.user_agent) contains "node-fetch" or lower(http.user_agent) contains "axios" or lower(http.user_agent) contains "selenium" or lower(http.user_agent) contains "puppeteer" or lower(http.user_agent) contains "playwright" or lower(http.user_agent) contains "phantomjs" or lower(http.user_agent) contains "libwww")
```
Action: **Block**. (Normal browsers and phone browsers always contain `Mozilla/`. Note: `curl` tests against a panel host will now show Cloudflare's 403 — that proves the rule works; test panels in a browser.)

**Rule 4 — Human check on panel page loads**
```
(http.host in {"admin.dekhohatke.com" "seller.dekhohatke.com" "reseller.dekhohatke.com"}) and http.request.method eq "GET" and http.request.uri.path in {"/" "/login"}
```
Action: **Managed Challenge**. (Only the page itself is challenged — never the JSON calls — so the panel keeps working; a person solves it once and Cloudflare remembers them for a while.)

**Rule 5 — The API host accepts only what it needs**
```
(http.host eq "api.dekhohatke.com" and not (http.request.method eq "POST" and (starts_with(http.request.uri.path, "/api/v1/") or starts_with(http.request.uri.path, "/telegram/"))) and not http.request.uri.path in {"/healthz" "/robots.txt"})
```
Action: **Block**. (Scanners poking around `api.` get nothing; apps and Telegram always POST.)

## Step 7 — Rapid-request protection (Security → WAF → Rate limiting rules)

Free plans get **one** rate-limiting rule (counted per IP, 10-second window, 10-second block). Create:

* Name: `panel hosts: rapid requests`
* Expression:
  ```
  (http.host in {"admin.dekhohatke.com" "seller.dekhohatke.com" "reseller.dekhohatke.com"} and not starts_with(http.request.uri.path, "/static/"))
  ```
* Characteristics: **IP**
* Requests: **30** per **10 seconds**
* Action: **Block**, duration **10 seconds**

This stops floods and credential stuffing at the edge. On top of it the app has its own limits (10 sign-in attempts/min per IP, account lock-out after repeated failures, 600 panel calls/min per account, signed anti-replay requests) — and since the real IP now reaches the app, those limits are accurate too. If you are on a paid Cloudflare plan you can add longer windows (e.g. 5 sign-ins per minute on `POST /` and `/login`).

## Step 8 — DDoS, bots and caching

* **DDoS**: Cloudflare's HTTP DDoS protection is on by default for every plan; nothing to switch on. Emergency: Security → Settings → *Under Attack mode* (this one is zone-wide, use only during an attack).
* **Bot Fight Mode — leave it OFF.** It is zone-wide (it would also change your root domain) and Cloudflare **cannot exempt** anything from it, so it could challenge Telegram's servers and your customers' apps. Rules 2-4 above already cover the panels. If you later upgrade to **Pro**, use *Super Bot Fight Mode* instead — it can be skipped by Rule 1.
* **Caching**: nothing to do. `/static/*` (JS, CSS, logo) is sent with cache headers, so Cloudflare serves it from the nearest edge location — your sleeping/slow Render server isn't touched for it. After you deploy a new version, use **Caching → Purge Cache → Custom purge** if you want the new JS immediately (otherwise ≤ 5 minutes).

## Step 9 — Verify

| Check | Expected |
|---|---|
| Browser: `https://admin.dekhohatke.com` | Dekho Hatke sign-in (maybe a one-time Cloudflare check) |
| Browser: `https://seller.dekhohatke.com` | Seller sign-in; `/login`, `/admin/api/me` → "Not found" |
| Browser: `https://reseller.dekhohatke.com` | Reseller sign-in |
| Browser: `https://admin.dekhohatke.com/seller` | "Not found" |
| `https://xxxx.onrender.com/login` (your Render address) | "Not found" |
| `https://xxxx.onrender.com/healthz` | `ok` |
| `curl -i -X POST https://api.dekhohatke.com/api/v1/app_x/verify` | JSON with `"code":"APP_NOT_FOUND"` |
| `curl -i https://api.dekhohatke.com/` | "Not found" |
| `curl -i https://admin.dekhohatke.com/` | Cloudflare **403** (bot rule — good) |
| Telegram: send `/start` to your bot | welcome with the Dekho Hatke logo |
| `https://api.telegram.org/bot<TOKEN>/getWebhookInfo` | `url` = `https://api.dekhohatke.com/telegram/…`, no `last_error_message` |
| Cloudflare → Security → Events | blocked/challenged requests appear here |

The reseller bots' webhooks are re-registered automatically on every start; the panel's *Re-register webhook* button still exists.

## Step 10 — Keep-alive (Render free tier sleeps)

UptimeRobot (or cron-job.org): HTTP(s) monitor on `https://api.dekhohatke.com/healthz` every **5 minutes**. (It goes through Cloudflare, so the `api.` rules apply — `/healthz` is allowed by Rule 5.)

## Step 11 — Use the logo in Telegram

The app already shows the logo in the bot's **welcome (/start) message** (users get it as a photo; Telegram's file id is cached, so it is uploaded only once per bot) and everywhere on the websites (favicon, sign-in pages, panel header, phone home-screen icon). Telegram doesn't let a server change a bot's profile picture, so do that once yourself: **@BotFather → /setuserpic → pick your bot → send `static/img/logo-welcome.jpg`**. Do the same for each reseller bot if you like.

## Changes you will notice
* Panel links are now the sub-domains. Sessions from the old Render address are gone — sign in once on the new address.
* The **Create API** endpoint and the docs show `https://api.dekhohatke.com/api/v1/<app id>/verify`. Apps already using the old `…onrender.com/api/v1/…` endpoint will get a 404 once the lock is on — give those developers the new endpoint (the key is unchanged).
* The *Become Seller* message in the bot links to `https://seller.dekhohatke.com`.
* APKs and videos are not uploaded through the website at all any more: sellers send them to the Telegram bot and paste the file id, so nothing big passes through Cloudflare (which limits one request to 100 MB on Free/Pro).

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Render says "domain verification failed" | the record is orange-clouded — switch to grey, wait for the certificate, then orange again |
| Cloudflare **525 / 526** | Render certificate not issued yet (keep grey cloud until it is), or SSL mode isn't Full (strict) for the host (Step 4) |
| **Too many redirects** | SSL mode is *Flexible* somewhere — use Full (strict) (Step 4) |
| Every page "Not found" after Step 5 | `ORIGIN_SECRET` differs from the Cloudflare header value, or the Transform Rule's expression misses the host. Fix the value or delete `ORIGIN_SECRET` in Render |
| "Not found" on one sub-domain only | that host is missing from `ROOT_DOMAIN`/custom hosts or mistyped in Render Custom Domains |
| Bot silent after the move | open `getWebhookInfo` (Step 9): a 403/404 there usually means Rule 1/Rule 5 or the secret; the webhook must be `https://api.dekhohatke.com/telegram/…` |
| Locked yourself out of the panel by the rate limit/WAF | wait 10 s; add your IP to a Skip rule if you need to; *Security → Events* shows which rule fired |
