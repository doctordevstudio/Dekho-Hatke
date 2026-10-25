# 🌐 Dekho Hatke — Cloudflare + Render setup (dekhohatke.com)

Render's free plan allows **two custom domains**, so the project uses exactly two:

| Address | Who uses it | What it serves (and nothing else) |
|---|---|---|
| `https://drdev.dekhohatke.com` | Super Admin · 2nd Admin · view-only admins · Sellers · Resellers | admin: `/` and `/login` · sellers: `/seller` · resellers: `/reseller` (+ their `/…/api/*`, `/static`, `/media`) |
| `https://api.dekhohatke.com` | your customers' apps | the license verification API: `/api/v1/*` |
| `https://<your-service>.onrender.com` (Render's own address) | **Telegram only** | the bot webhooks `/telegram/*` and `/healthz` — everything else answers 404 |

* The Telegram bots (main bot and every reseller bot) call the Render address **directly** — no Cloudflare in between, so Cloudflare rules can never block your bots. The webhook URL contains a secret path and Telegram also sends a secret header, both checked by the app.
* **Your root domain `dekhohatke.com` / `www` is never touched.** You only *add* two DNS records, and every rule below is limited to these two host names.
* Each host serves only its own paths. `api.…/login` is a plain 404, `drdev.…/api/v1/…` is a plain 404, `xxxx.onrender.com/login` is a plain 404.
* The two custom domains can only be reached **through Cloudflare**: Cloudflare adds a secret header, the app answers 404 without it.

```
visitor ──HTTPS──▶ Cloudflare (WAF · bots · DDoS · rate limit · cache) ──HTTPS + secret header──▶ Render (Flask)
Telegram ───────────────────────────HTTPS (secret URL + secret header)───────────────────────────▶ xxxx.onrender.com/telegram/…
```

Do the steps **in this order** — it avoids downtime and lock-outs.

---

## Step 1 — Deploy on Render (without the lock yet)

1. Render → **New → Web Service** (or Blueprint with `render.yaml`) → this repo. Start command and health check are in `render.yaml`.
2. Environment variables: everything in `README.md` §3 **plus**
   * `ROOT_DOMAIN` = `dekhohatke.com`   ← turns on the two-domain mode (`drdev.dekhohatke.com` + `api.dekhohatke.com`)
   * leave `ORIGIN_SECRET` **empty for now** (Step 5)
   * don't set `BASE_URL` — the Telegram webhooks use Render's own address (`RENDER_EXTERNAL_URL`, set by Render) automatically
3. Wait for the first deploy. Note your service address, e.g. `dekho-panel.onrender.com`. Open the bot and send `/start` — it should already answer (the webhook is registered on that address).

> Want another name than `drdev`? Set `PANEL_HOST=yourname.dekhohatke.com` (and use that name everywhere below).

## Step 2 — Tell Render about the two custom domains

Render → your service → **Settings → Custom Domains → Add Custom Domain**, add:
`drdev.dekhohatke.com` and `api.dekhohatke.com`. Render shows the CNAME target (your `xxxx.onrender.com`). Don't add the root domain or `www`.

## Step 3 — Add two DNS records in Cloudflare (root domain untouched)

Cloudflare → **DNS → Records → Add record** — twice:

| Type | Name | Target | Proxy status |
|---|---|---|---|
| CNAME | `drdev` | `xxxx.onrender.com` | **DNS only** (grey cloud) for now |
| CNAME | `api` | `xxxx.onrender.com` | **DNS only** |

* Don't edit or delete the existing `@` / `www` records.
* If either name already has an **AAAA** (IPv6) record, delete it — Render doesn't support IPv6.
* Go back to Render → Custom Domains and wait until both show **Verified** and a **certificate is issued** (a few minutes). Render can only verify while the cloud is grey.
* **Now switch both records to Proxied (orange cloud).**

## Step 4 — SSL mode for these two hosts only

Do **not** change the zone-wide SSL setting (that would also affect the root domain). Use a rule instead:

Cloudflare → **Rules → Configuration Rules → Create rule**
* Name: `dekhohatke hosts: strict TLS`
* When incoming requests match → *Custom filter expression* → **Edit expression**:
  ```
  (http.host in {"drdev.dekhohatke.com" "api.dekhohatke.com"})
  ```
* Then: **SSL → Full (strict)** → Deploy.

Second Configuration Rule, `panel host: higher security`:
```
(http.host eq "drdev.dekhohatke.com")
```
→ **Security Level: High**, **Browser Integrity Check: On** → Deploy.

Optional (recommended) — force HTTPS for the two hosts: **Rules → Redirect Rules → Create rule**
`(http.host in {"drdev.dekhohatke.com" "api.dekhohatke.com"} and not ssl)` → Dynamic redirect: `concat("https://", http.host, http.request.uri.path)`, status 301. (Skip if "Always Use HTTPS" is already on for your zone.)

## Step 5 — Lock the two domains to Cloudflare

1. Make a long secret (on any computer with Python):
   ```
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. **Cloudflare first**: **Rules → Transform Rules → Modify Request Header → Create rule**
   * Name: `origin lock header`
   * Expression: `(http.host in {"drdev.dekhohatke.com" "api.dekhohatke.com"})`
   * Modify header → **Set static** → header name `X-Origin-Auth` → value = your secret → Deploy.
   (Cloudflare *overwrites* this header on every request, so a visitor can never forge it.)
3. **Then Render** → Environment → add `ORIGIN_SECRET` = the same secret → save (the service redeploys).
4. Check (Step 9). If anything is wrong you can always **delete `ORIGIN_SECRET` in Render** to open it again.

What the lock does in the app:
* `drdev.` / `api.` requests without the header → plain `404` (even for correct paths);
* any other `Host` (Render's own address, other domains): only `/telegram/*` (Telegram's webhook) and `/healthz` answer, everything else is `404`;
* with the header present, the visitor's real IP is read from `CF-Connecting-IP`, so rate limits and login lock-outs work **per visitor** instead of per Cloudflare server.

## Step 6 — WAF custom rules (Security → WAF → Custom rules)

Create these in **this order**. Use *Edit expression* and paste. They use four custom rules (Free plans have a small fixed number — check your dashboard).

**Rule 1 — Block data-centre networks and Tor on the panel host**
```
(http.host eq "drdev.dekhohatke.com") and (ip.src.country eq "T1" or ip.src.asnum in {16509 14618 15169 396982 8075 14061 63949 20473 16276 24940 31898 36351 45102 37963 132203 45090 51167 12876 9009 60781 28753 60068 54825 47583 8560 197540})
```
Action: **Block**. (AWS, Google Cloud, Azure, DigitalOcean, Linode/Akamai, Vultr, OVH, Hetzner, Oracle, IBM, Alibaba, Tencent, Contabo, Scaleway, M247, Leaseweb, Datacamp, Equinix, Hostinger, IONOS, netcup. Extend the list whenever **Security → Events** shows another hosting network. Visitors on a commercial VPN are also blocked on the panels — intended. The customer API on `api.` is *not* blocked this way, so your buyers on any network can still use their apps.)

**Rule 2 — Block scripts and fake browsers on the panel host**
```
(http.host eq "drdev.dekhohatke.com") and (len(http.user_agent) lt 20 or not http.user_agent contains "Mozilla/" or lower(http.user_agent) contains "headless" or lower(http.user_agent) contains "python" or lower(http.user_agent) contains "curl" or lower(http.user_agent) contains "wget" or lower(http.user_agent) contains "scrapy" or lower(http.user_agent) contains "httpclient" or lower(http.user_agent) contains "go-http" or lower(http.user_agent) contains "okhttp" or lower(http.user_agent) contains "node-fetch" or lower(http.user_agent) contains "axios" or lower(http.user_agent) contains "selenium" or lower(http.user_agent) contains "puppeteer" or lower(http.user_agent) contains "playwright" or lower(http.user_agent) contains "phantomjs" or lower(http.user_agent) contains "libwww")
```
Action: **Block**. (Normal browsers and phone browsers always contain `Mozilla/`. `curl` tests against the panel host will now show Cloudflare's 403 — that proves the rule works; test panels in a browser.)

**Rule 3 — Human check on panel page loads**
```
(http.host eq "drdev.dekhohatke.com") and http.request.method eq "GET" and http.request.uri.path in {"/" "/login" "/seller" "/reseller"}
```
Action: **Managed Challenge**. (Only the pages themselves are challenged — never the JSON calls — so the panels keep working; a person solves it once and Cloudflare remembers them for a while.)

**Rule 4 — The API host accepts only what it needs**
```
(http.host eq "api.dekhohatke.com" and not (http.request.method eq "POST" and starts_with(http.request.uri.path, "/api/v1/")) and not http.request.uri.path in {"/robots.txt"})
```
Action: **Block**. (Scanners poking around `api.` get nothing; apps always POST to `/api/v1/…`.)

There is deliberately **no Telegram rule**: the bots talk to Render's own address and never pass through Cloudflare.

## Step 7 — Rapid-request protection (Security → WAF → Rate limiting rules)

Free plans get **one** rate-limiting rule (counted per IP, 10-second window, 10-second block). Create:

* Name: `panel host: rapid requests`
* Expression:
  ```
  (http.host eq "drdev.dekhohatke.com" and not starts_with(http.request.uri.path, "/static/"))
  ```
* Characteristics: **IP**
* Requests: **30** per **10 seconds**
* Action: **Block**, duration **10 seconds**

This stops floods and credential stuffing on the admin / seller / reseller sign-ins at the edge. On top of it the app has its own limits (10 sign-in attempts/min per IP, account lock-out after repeated failures, 600 panel calls/min per account, 60 license checks/min per IP, signed anti-replay requests) — and since the real IP now reaches the app, those limits are accurate too. On a paid plan you can add longer windows and a rule for `api.`.

## Step 8 — DDoS, bots and caching

* **DDoS**: Cloudflare's HTTP DDoS protection is on by default for every plan. Emergency: Security → Settings → *Under Attack mode* (zone-wide, use only during an attack).
* **Bot Fight Mode — leave it OFF.** It is zone-wide (it would also change your root domain) and Cloudflare **cannot exempt** anything from it, so it could challenge your customers' apps. Rules 1-3 already cover the panels. On **Pro**, *Super Bot Fight Mode* can be skipped for `api.` with a Skip rule.
* **Caching**: nothing to do. `/static/*` (JS, CSS, logo) is sent with cache headers, so Cloudflare serves it from the nearest edge location. After a new deployment use **Caching → Purge Cache → Custom purge** if you want the new JS immediately (otherwise ≤ 5 minutes).

## Step 9 — Verify

| Check | Expected |
|---|---|
| Browser: `https://drdev.dekhohatke.com` or `/login` typed directly | **Unauthorised** (no form, no hint) |
| Telegram: super admin / 2nd admin → bot **≡ menu → Admin Login** | a button that opens the admin sign-in page (valid 30 minutes) |
| Browser: `https://drdev.dekhohatke.com/seller` and `/reseller` | seller / reseller sign-in |
| Browser: `https://drdev.dekhohatke.com/api/v1/x/verify` | "Not found" |
| `https://xxxx.onrender.com/login` (Render's address) | "Not found" |
| `https://xxxx.onrender.com/healthz` | `ok` |
| `curl -i -X POST https://api.dekhohatke.com/api/v1/app_x/verify` | JSON with `"code":"APP_NOT_FOUND"` |
| `curl -i https://api.dekhohatke.com/` | "Not found" |
| `curl -i https://drdev.dekhohatke.com/` | Cloudflare **403** (bot rule — good) |
| Telegram: send `/start` to your bot | welcome with the Dekho Hatke logo |
| `https://api.telegram.org/bot<TOKEN>/getWebhookInfo` | `url` = `https://xxxx.onrender.com/telegram/…`, no `last_error_message` |
| Cloudflare → Security → Events | blocked/challenged requests appear here |

The reseller bots' webhooks are re-registered automatically on every start; the panel's *Re-register webhook* button still exists.

## Admin entrance (signed link) and the storage channel

* **Only the admin sign-in is hidden.** `https://drdev.dekhohatke.com` and `/login` answer *Unauthorised* until the browser has opened a signed link from the bot (`/go/<token>`, valid **30 minutes**, made only for the super admin's / 2nd admin's chat). After that the normal username + password sign-in follows. `/seller` and `/reseller` stay public sign-in pages, no link needed. Nothing here touches `dekhohatke.com` itself: every rule above is scoped with `http.host eq "drdev…"` / `"api…"`, and the app only ever links to `drdev.` and `api.`.
* **Telegram account + channel (Settings → Channel & account).** Add `TG_API_ID` and `TG_API_HASH` (my.telegram.org → API development tools) to Render, make sure `SECRET_KEY` is fixed, redeploy, then link the account (phone → code → 2-step password). Create a channel with that account as owner, put its @username in the channel box and save: the account adds your main bot and every reseller bot to the channel. From then on APKs / videos that admins and sellers send to the bot are copied into the channel, and reseller bots copy them from there (no re-upload, no "forwarded from", the file name stays `game.apk`). Telegram's connection is outbound from Render, so nothing changes in Cloudflare.

## Step 10 — Keep-alive (Render free tier sleeps)

UptimeRobot (or cron-job.org): HTTP(s) monitor on `https://xxxx.onrender.com/healthz` every **5 minutes** (it needs no secret and prints just `ok`). The first Telegram message after a sleep can take ~30 s; Telegram retries, so nothing is lost.

## Step 11 — Use the logo in Telegram

The app already shows the logo in the bot's **welcome (/start) message** (Telegram's file id is cached, so it is uploaded only once per bot) and everywhere on the websites (favicon, sign-in pages, panel header, phone home-screen icon). Telegram doesn't let a server change a bot's profile picture, so do that once yourself: **@BotFather → /setuserpic → pick your bot → send `static/img/logo-welcome.jpg`**. Same for each reseller bot if you like.

## Changes you will notice
* Panel links: `https://drdev.dekhohatke.com` (admin), `/seller`, `/reseller`. Sessions from the old Render address are gone — sign in once on the new address.
* The **Create API** endpoint and the docs show `https://api.dekhohatke.com/api/v1/<app id>/verify`. Apps still using `…onrender.com/api/v1/…` get a 404 once the app is in this mode — give those developers the new endpoint (the key is unchanged).
* The *Become Seller* message in the bot links to `https://drdev.dekhohatke.com/seller`.
* APKs and videos are not uploaded through the website: sellers and admins send them to the Telegram bot and paste the file id.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Render says "domain verification failed" | the record is orange-clouded — switch to grey, wait for the certificate, then orange again |
| Cloudflare **525 / 526** | Render certificate not issued yet (keep grey cloud until it is), or SSL mode isn't Full (strict) for the host (Step 4) |
| **Too many redirects** | SSL mode is *Flexible* somewhere — use Full (strict) (Step 4) |
| `drdev.` / `api.` answer "Not found" for everything after Step 5 | `ORIGIN_SECRET` differs from the Cloudflare header value, or the Transform Rule's expression misses the host. Fix the value or delete `ORIGIN_SECRET` in Render |
| Bot silent | open `getWebhookInfo` (Step 9): the URL must be `https://xxxx.onrender.com/telegram/…`. A 404 there usually means `BASE_URL` points at `drdev.`/`api.` — remove `BASE_URL` in Render and redeploy |
| Locked yourself out of the panel by the rate limit/WAF | wait 10 s; *Security → Events* shows which rule fired; add a Skip rule for your IP if needed |
