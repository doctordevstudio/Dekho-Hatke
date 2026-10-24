# ⚡ Dekho Hatke — admin / seller / reseller panels + Telegram license-selling bot

Python (Flask) · HTML/CSS/JS · Firebase Realtime Database · built for **Render free web service**.

* **Admin panel** (`/`) — dashboard, Purchase (categories → apps → plans), users, earnings, UPI IDs, balance & wallets, referral & withdrawals, daily check-in / spin wheel, broadcast, leaderboard, resellers, **sellers (review queue)**, logs, settings. A second admin (env vars) can do everything except delete.
* **Reseller panel** (`/reseller`) — each reseller runs their own bot (own token, UPI, prices ≥ yours), sees earnings, dues, support, force-join; the store is locked automatically when dues pass your limit.
* **Seller panel** (`/seller`) — sellers create an API, publish apps (video, logo, plans, APK) for your review, read integration docs, withdraw earnings, broadcast to their buyers (after your approval).
* **Telegram bot** — force-join → category → app → plan → pay by UPI (auto-generated QR, wallet optional) → screenshot + UTR → verify → key + APK + setup video. Refer & Earn, wallet, daily check-in, spin wheel, free trials.
* **License API** — your apps call `POST /api/v1/<app_id>/verify` (see [API_DOCS.md](API_DOCS.md))

## 1. Firebase (5 min)
1. [console.firebase.google.com](https://console.firebase.google.com) → create project → **Build → Realtime Database → Create database** (any region).
2. **Rules** tab → paste the content of `database.rules.json` → Publish. (Clients are locked out; only your server, via the service account, can read/write. The indexes make queries fast.)
3. **Project settings → Service accounts → Generate new private key** → a JSON file downloads.
4. Copy the database URL (looks like `https://xxxx-default-rtdb.firebaseio.com`).

## 2. Telegram bot
1. [@BotFather](https://t.me/BotFather) → `/newbot` → copy the **token**.
2. Add the bot as **administrator** in each channel you want to force-join (needed to check membership).
3. Get your own chat id from [@userinfobot](https://t.me/userinfobot) — this is your **Admin Chat ID** (also message your bot once with /start so it can write to you). Each UPI owner must also press /start on the bot once.

## 3. Deploy on Render
1. Push this folder to a GitHub repo (the `.gitignore` keeps secrets out).
2. Render → **New → Web Service** → pick the repo (or use *Blueprint* with `render.yaml`).
   * Build: `pip install -r requirements.txt`
   * Start: `gunicorn wsgi:app --workers 1 --worker-class gthread --threads 40 --timeout 300 --keep-alive 30 --bind 0.0.0.0:$PORT`
   * Health check path: `/healthz`
3. **Environment variables**

| Variable | Value |
|---|---|
| `SECRET_KEY` | long random string (Render can generate it) |
| `FIREBASE_DB_URL` | your database URL |
| `FIREBASE_CREDENTIALS_JSON` | the whole service-account JSON pasted on one line |
| `BOT_TOKEN` | token from BotFather |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | first admin (password: 10+ chars, letters + numbers). If you omit the password, a random one is printed once in the Render logs |
| `ADMIN_CHAT_ID` | optional; can be set in Settings → Telegram bot |
| `ADMIN2_USERNAME` / `ADMIN2_PASSWORD` / `ADMIN2_CHAT_ID` | optional second admin (everything except deleting); synced from env on every start |
| `TIMEZONE` | default `Asia/Kolkata` (used for today / yesterday) |
| `ROOT_DOMAIN` | `dekhohatke.com` → `admin.` / `seller.` / `reseller.` / `api.` sub-domains, each serving only its own panel (see **CLOUDFLARE_SETUP.md**) |
| `ORIGIN_SECRET` | secret that Cloudflare adds as header `X-Origin-Auth`; without it the app answers 404 (the Render address is unusable directly) |

4. Open `https://admin.dekhohatke.com` (or `https://YOUR-SERVICE.onrender.com` while ROOT_DOMAIN is not set) → sign in → **Settings → Telegram bot**: set Admin Chat ID, support username, welcome message → Save → **Send test**.
   The webhook registers itself on every start (button *Re-register webhook* if ever needed).
5. **Home → New app**, **Plans → Add plan**, **UPI ID → Add UPI** (upload the QR), **Settings → Force join** (add channels). Open your bot and try a purchase.

### ⚠️ Render free tier sleeps
Free services sleep after ~15 min without traffic; the first request then takes ~30–60 s. Telegram retries
webhooks, so the bot recovers, but to keep it snappy ping `https://api.dekhohatke.com/healthz` every
5 minutes with a free monitor (UptimeRobot / cron-job.org). Payment expiry (10 min) is also swept while the
service is awake, and re-checked whenever the panel or a user touches a transaction.

## 🌐 Domain, Cloudflare and speed
Full step-by-step guide (DNS records, Render custom domains, WAF / bot / data-centre / rate-limit rules, origin lock, verification): **[CLOUDFLARE_SETUP.md](CLOUDFLARE_SETUP.md)**.

Speed work in this code base:
* **Bot replies** — the webhook answers Telegram instantly and processes the update on a 24-thread pool; a per-user lock + 0.9 s click gate drops accidental double taps; the catalog tree is cached for 5 s; Telegram file ids are cached (a photo/video is uploaded once per bot, then sent by id — the logo too); long lists are paged 5 per page.
* **Database** — read-through cache for the hot Firebase paths with a version counter that is bumped on every write (reads stay correct, repeated reads are in-memory); admin / reseller / seller sessions are cached for 15 s.
* **Panels** — gzip on every JSON / JS / CSS / HTML response, `gunicorn` gthread with 40 threads and 30 s keep-alive, paged tables, debounced search, no framework (ES modules), lazy-loaded page scripts.
* **Edge (new)** — behind Cloudflare, `/static/*` (JS, CSS, logo, icons) is cached at the nearest Cloudflare location with `Cache-Control`, TLS and HTTP/2/3 are terminated at the edge, and Cloudflare keeps connections to Render open — the Render free instance only handles real work (JSON / bot).

## What's new in this version
* **No external file storage (Appwrite removed)**: sellers and admins **send the APK / videos to the bot**; the bot replies with the file id and remembers who sent it. The Publish wizard has *APK file id* / *Preview video id* / *Setup video id* boxes (plus tap-to-use chips of your own files); the server accepts only ids from files **that seller sent**. Admins get the same chips in the app form, and the review page can *send the seller's file to your Telegram* by id. File ids are visible only to the sender (and to admins reviewing a submission).
* **Dekho Hatke branding + sub-domains**: your logo is the favicon, home-screen icon, sign-in image and panel header, and the picture on the bot's /start welcome (unless a custom welcome image is set). `admin.` / `seller.` / `reseller.` / `api.` serve only their own panel; the Render address is locked to Cloudflare; the real visitor IP is used for limits. API docs: **smali removed, C++ (libcurl) added**.
* **Seller Publish App** is a step-by-step dialog (details → logo & videos → plans + **free trial** → APK → **live APK testing** → review). Progress is saved as a **draft** automatically; leave any time and press *Continue draft*. Live testing = test key, login, 2nd-device refusal, maintenance, revoked key — all required before submit. Every detail is required except setup video, free trial and support contact; file type / name / size are checked in the browser and on the server (`.apk` only for APKs).
* **Create API / admin-created seller or reseller / manual license** ask for complete data (fields marked *, hints inside every box).
* **Become Seller** is done from the bot (the web sign-up is gone): *Become Seller* → message → button → account with **username = Telegram chat id** and a long random password (re-tap to reset).
* **Plans**: every plan has a **sort number** (1 = shown first) and a **UPI ID** (one fixed UPI, or random round-robin). The Plans page lists apps first; click an app to see its plans, with sales split **admin bot vs reseller bots**.
* **Enable / disable**: whole **category**, one **app** (🛒 *Selling* switch — existing customers keep working) or one **plan** can be hidden from the bot's Purchase flow. Resellers can additionally hide categories, apps, plans and the free trial in their own bot, and raise prices up to **+100 %** of the base.
* **Promotion** page: pick an app → send its plans as buttons to *all users*, *chosen users* or *a number of random users*. **Rank** categories and apps by drag & drop and **highlight** (🔥) apps.
* **Feature switches** (Settings → Features): Refer & Earn, Reseller sign-ups, Seller sign-ups.
* **Bot**: single-tap rule (rapid taps on different buttons run only the first), Refer & Earn asks to confirm before showing the link, *Download APK* button under every live key (sends APK + setup video), 5-per-page lists for keys / referrals / history, check-in animation, 2-decimal check-in / spin amounts, `/block <chat_id>` (asks a reason; blocked users get **no reply at all**; also from the panel), the ≡ menu shows only `/start`, and **new-user alerts go only to the super admin and 2nd admin**.
* **Security**: every panel write carries a signed timestamp + one-time nonce (`X-Req-Ts`, `X-Req-Nonce`, `X-Req-Sig = HMAC-SHA256(csrf, METHOD|path|ts|nonce)`), so a captured request cannot be replayed (window 120 s). File ids (APK / video) are shown only to the seller, admin and super admin.

## Daily operations cheat-sheet
* **Sell**: Purchase → add category → add app (video id, setup video id, APK id, category) → add plans (paid / free trial, hours · days · years). Telegram file ids: just send any video / APK / photo to your bot as admin — it replies with the file id.
* **UPI**: add UPI IDs (round-robin). The QR is generated from `upi://pay?pa=…&am=…` per transaction; your uploaded QR image is only a fallback. UPI owners press /start and get **My Payment Collection** (today / yesterday / all; pending / failed / success; name, amount, counts — never @username).
* **Referral**: Referral page → default % (per-user override on the user's detail), withdrawals approve/reject with reason + photo. Commission is lifetime and every referrer gets a notification (name, plan, app, commission, new balance — no chat id / username).
* **Resellers**: Resellers → add (bot token, name, UPI, commission %, due limit, login). They log in at `/reseller`. When earnings since the last settlement reach the limit their bot is put in maintenance until you press **Settle**.
* **Sellers**: Sellers section → settings (platform commission %, min withdraw, withdraw fee %). Submissions queue → open → *Generate test key* → run the four live checks from your own test app (login, second-device, maintenance, revoke) → **Approve** (only enabled when all four pass). The seller's APK / videos arrive as Telegram file ids (the seller sent them to the bot); use **📲 Send to my Telegram** to install and try the APK, then approve.
* **Bot admin commands** (super admin & 2nd admin): `/help`, `/send <chat_id> <message>`, `/broadcast` (preview → Confirm / Cancel, shows recipient count), `/pending` (pending payments grouped by UPI id).
* **Broadcast**: text/HTML, image/video/file, URL button; history with edit / re-broadcast / delete (reflected in user chats). Seller broadcasts wait for your approval and reach only that seller's buyers.

## How the important rules work
* **Payments**: 10-minute window · max **5 requests / user / day** · a user with a txn *in verification* can't start another · failed txns can be retried · one screenshot / UTR can't be reused.
* **Screenshots** must be real JPG/PNG images (decoded and re-encoded); anything else is refused.
* **Privacy**: sellers and referrers only ever see names — never chat ids or usernames. IP addresses are logged but never shown.
* **Soft delete**: nothing is ever removed from the database — "delete" sets `deleted: true`.
* **Referral**: default % + per-user override; wallets: Deposit and Refer money with min deposit / withdraw; the user chooses whether to use the wallet before confirming a purchase.
* **Daily check-in / spin**: random between min and max, expiry, reminders, resets at 12 AM; admin stats for valid / expired / claimed / used.
* **Logs** are split into sections (payments, users, referral, resellers, sellers, settings, security …).
* **Admins**: first admin full control; 2nd admin (env) can do everything except delete; *view-only* admins can read everything.

## Security checklist (what's built in)
signed one-time requests (anti-replay) on every panel write · HTTPS-only secure cookies (`__Host-` prefix) · HttpOnly + SameSite · CSRF token on every change · strict CSP (no inline JS/handlers) · clickjacking/MIME/referrer headers · login rate limit + persistent lockout (5 fails → 15 min) for admin, reseller and seller logins · separate session scopes (a reseller/seller session can never call the admin API) · server-side role enforcement · input validation + escaping · image uploads decoded + re-encoded · per-bot webhook secret path **and** Telegram secret header · public API: API key + per-IP / per-license rate limits + guess lockout · Firebase locked by rules (`database.rules.json`, with indexes).

## Known limitations / decisions
* Sub-domain mode assumes **one** Render instance (limits and nonces live in memory). The Cloudflare rules are documented, not code: apply them from `CLOUDFLARE_SETUP.md`.
* Reseller bots re-upload the APK/video (Telegram file ids are bot-specific) → files > 20 MB can't be delivered by a reseller bot via the Bot API download limit.
* APKs and videos are never stored by the shop: sellers/admins send them to the bot, Telegram keeps them and the shop keeps only the file id (Telegram accepts files up to 2 GB). Ids are recorded per sender, shown only to that sender, and a seller can only attach ids from their own list.
* A blocked user is silenced in every bot, but their licenses keep working. Reseller free trials are OFF until the reseller enables them. The bot ignores a second button tap within 0.9 s of the previous accepted one.
* Seller platform commission defaults to **0 %** (set it in Sellers → settings).
* Super admin can force-approve a submission without passing all checks (logged); the 2nd admin can't.
* The Render free tier sleeps — ping `/healthz` every 5 minutes (UptimeRobot) and keep a single worker (in-memory rate limits & broadcast threads assume one process).

## Local development
```bash
pip install -r requirements.txt
APP_ENV=development USE_MEMORY_DB=1 ADMIN_PASSWORD=ChangeMe12345 python app.py   # http://127.0.0.1:5000
python tests/test_flows.py     # catalog, QR, seller publish, admin review checks (no network)
python tests/test_flows2.py    # referral, seller wallet, reseller, bot admin commands
python tests/test_flows3.py    # replay guard, drafts + live tests, ban, click gate, plan sort/UPI, enable/disable, promotion, reseller caps, file-id registry (bot → seller panel)
python tests/test_hosts.py     # sub-domain routing, Cloudflare origin lock, real-IP, logo assets, C++ docs
python tests/ui_check.py       # headless-browser check of all three panels (needs playwright + chromium)
```
`USE_MEMORY_DB=1` is only for tests — data vanishes on restart. Production uses Firebase.

## Project layout
```
app.py  wsgi.py  config.py        app factory, entrypoint, env config
core/        db (Firebase + memory), security, audit, utils, cache
services/    licenses, payments, stats, referral, rewards, resellers, sellers, tgfiles, reviewchecks, broadcast …
routes/      admin API, reseller API, seller API, public license API, webhook, media proxy, pages
bot/         tg client, multi-tenant context, handlers (flow), messages, antispam
templates/   login / app shells      static/ css + ES-module JS (pages/ per panel)
tests/       test_flows.py, test_flows2.py, test_flows3.py, test_hosts.py, ui_check.py, harness.py
```
