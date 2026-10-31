# Shortener (step-gate) — setup

The Shortener page (admin panel → ☰ → **🔗 Shortener**) makes links on the blog pages of your root domain. Every page makes
the visitor wait at the **top**, then at the **bottom**; the last button is **Generate**:

| Type | After the last Generate click |
|------|------------------------------|
| 🔑 **License key** | the Telegram bot sends the license key (+ APK + setup video). Used by **Free plans** (Plans → *Free via short link*). The bot sends "Page 1 completed — 2 pages remaining" after every page. |
| ↪️ **Redirect** | the visitor is sent to your redirect URL. **Nothing is sent in Telegram.** You get one shareable link; every visitor gets their own run. |

## 1. Environment variables (Render)

| Name | Value |
|------|-------|
| `PROXY_SECRET` | any long random string — the **same** value your Cloudflare worker sends as header `X-DH-Proxy`. Without it the gate API stays closed (404). |
| `SITE_URL` | `https://dekhohatke.com` (only used as the default; you can change it in *Shortener → ⚙️ Pages & website*). The app only **builds** links to this domain, it never serves or redirects it. |
| `TURNSTILE_SITEKEY` / `TURNSTILE_SECRET` | optional Cloudflare Turnstile on Verify / Generate |

Optional tuning: `GATE_GRACE` (0.35 s), `GATE_MIN_SCROLL_GAP` (1.2 s), `GATE_IP_LIMIT` (90 gate calls / visitor IP / minute).

## 2a. Direct mode (default, simplest)

The website's `assets/config.js` has `api_base:"https://<name>.onrender.com"`. The visitor's browser then calls this server
**directly** (CORS), so no Cloudflare rule on `dekhohatke.com` can block it and no worker / `PROXY_SECRET` is needed.
* The server answers only requests whose `Origin` is `SITE_URL` (or its `www.`), plus a few extra sites in `GATE_ORIGINS`.
* `GATE_DIRECT=0` turns direct mode off (only the worker may call).
* Test: open `https://<name>.onrender.com/api/ping` in a browser → `{"ok":true,"proxy":"direct",…}`.
* Trade-off: the Render address is visible in the page's network tab (it is already your Telegram webhook host).

## 2. Your Cloudflare worker (optional alternative, `api_base:""`)

* The website's `_worker.js` (Cloudflare Pages) proxies `https://dekhohatke.com/api/*`. Set its **BACKEND_URL** to this service's
  **Render address** (`https://<name>.onrender.com`, the same address Telegram uses). Do **not** use `api.` or `drdev.` there:
  those two hosts are locked to Cloudflare and only serve the license API / the panels.
* It must send `X-DH-Proxy: <PROXY_SECRET>` and `X-DH-IP: <real visitor IP>` (exactly what your current worker does).
* The app answers `/api/init`, `/api/event`, `/api/verify`, `/api/ping`, `/api/health` there. Test: `GET /api/ping` with the header → `{"ok":true,"proxy":"ok"}`.
* UptimeRobot: ping `https://<name>.onrender.com/api/health` (or `/healthz`) every 5 minutes.

## 3. gate.js — one small addition (redirect type)

Everything the page already reads (`redirect`, `next`, `done`, `bot`, `phase`, `remaining`, `n`, `turnstile`) is unchanged. For the
**redirect** type the last `verify` answer is `{ "ok": true, "done": false, "final": true, "next": "<url>", "redirect": "<url>" }`.
The `gate.js` shipped in the updated website already follows `next` (any `/path` or `http(s)://` address) — nothing to add.

For the **license** type nothing changes: `{ "ok": true, "done": true, "bot": "https://t.me/<bot>" }`.

## 4. Pages

*Shortener → ⚙️ Pages & website*: the website address and the pool of blog pages (one path per line). Each step uses a different
page of the pool; **every page must load `/assets/gate.js`**. A shortener can also have its own page list.

## 5. How it stays fast

* Every running link lives in memory; the gate API never waits for Firebase or Telegram. A background thread saves links and
  counters to Firebase every ~2 s and a link that is not in memory (after a redeploy) is loaded on first use, so nothing is lost.
* Telegram messages and license delivery run on a worker pool and **retry** (a user is never left without the key; after 8 failed
  tries the admin is told). Delivery is idempotent — a retry never creates a second key.
* Keep **one** gunicorn worker (`--workers 1 --threads 40`, as in `render.yaml`): the in-memory state is per process.

## 6. What the history shows

Per shortener: successful completions **today / yesterday / all time / custom range**; started, opened page 1, success rate,
people still in progress; the funnel **"Left after completing page X"** for the same ranges; and every single link (user, status,
pages with completion times, time spent, bot flags, license key and delivery state). "Left" is counted when a link expires
unfinished, on the day the user was last active. IP addresses are stored but never shown.
