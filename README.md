# ⚡ GZ Panel — admin panel + Telegram license-selling bot

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
   * Start: `gunicorn wsgi:app --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:$PORT`
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
| `APPWRITE_ENDPOINT` / `APPWRITE_PROJECT_ID` | already in `render.yaml` (Appwrite is used for seller APKs larger than 45 MB) |
| `APPWRITE_API_KEY` | **set the real key only in the Render dashboard** (`your-api-key` is just a placeholder; needs storage file/bucket scopes) |
| `TIMEZONE` | default `Asia/Kolkata` (used for today / yesterday) |

4. Open `https://YOUR-SERVICE.onrender.com` → sign in → **Settings → Telegram bot**: set Admin Chat ID, support username, welcome message → Save → **Send test**.
   The webhook registers itself on every start (button *Re-register webhook* if ever needed).
5. **Home → New app**, **Plans → Add plan**, **UPI ID → Add UPI** (upload the QR), **Settings → Force join** (add channels). Open your bot and try a purchase.

### ⚠️ Render free tier sleeps
Free services sleep after ~15 min without traffic; the first request then takes ~30–60 s. Telegram retries
webhooks, so the bot recovers, but to keep it snappy ping `https://YOUR-SERVICE.onrender.com/healthz` every
5 minutes with a free monitor (UptimeRobot / cron-job.org). Payment expiry (10 min) is also swept while the
service is awake, and re-checked whenever the panel or a user touches a transaction.

## Daily operations cheat-sheet
* **Sell**: Purchase → add category → add app (video id, setup video id, APK id, category) → add plans (paid / free trial, hours · days · years). Telegram file ids: just send any video / APK / photo to your bot as admin — it replies with the file id.
* **UPI**: add UPI IDs (round-robin). The QR is generated from `upi://pay?pa=…&am=…` per transaction; your uploaded QR image is only a fallback. UPI owners press /start and get **My Payment Collection** (today / yesterday / all; pending / failed / success; name, amount, counts — never @username).
* **Referral**: Referral page → default % (per-user override on the user's detail), withdrawals approve/reject with reason + photo. Commission is lifetime and every referrer gets a notification (name, plan, app, commission, new balance — no chat id / username).
* **Resellers**: Resellers → add (bot token, name, UPI, commission %, due limit, login). They log in at `/reseller`. When earnings since the last settlement reach the limit their bot is put in maintenance until you press **Settle**.
* **Sellers**: Sellers section → settings (platform commission %, min withdraw, withdraw fee %). Submissions queue → open → *Generate test key* → run the four live checks from your own test app (login, second-device, maintenance, revoke) → **Approve** (only enabled when all four pass). For APKs > 45 MB, download from Appwrite, send it to your bot, paste the returned APK id, then approve.
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
HTTPS-only secure cookies (`__Host-` prefix) · HttpOnly + SameSite · CSRF token on every change · strict CSP (no inline JS/handlers) · clickjacking/MIME/referrer headers · login rate limit + persistent lockout (5 fails → 15 min) for admin, reseller and seller logins · separate session scopes (a reseller/seller session can never call the admin API) · server-side role enforcement · input validation + escaping · image uploads decoded + re-encoded · per-bot webhook secret path **and** Telegram secret header · public API: API key + per-IP / per-license rate limits + guess lockout · Firebase locked by rules (`database.rules.json`, with indexes).

## Known limitations / decisions
* Reseller bots re-upload the APK/video (Telegram file ids are bot-specific) → files > 20 MB can't be delivered by a reseller bot via the Bot API download limit.
* Seller APKs > 45 MB go to Appwrite (free-plan file-size limits apply to you); the admin copies them to Telegram and pastes the APK id.
* Seller platform commission defaults to **0 %** (set it in Sellers → settings).
* Super admin can force-approve a submission without passing all checks (logged); the 2nd admin can't.
* The Render free tier sleeps — ping `/healthz` every 5 minutes (UptimeRobot) and keep a single worker (in-memory rate limits & broadcast threads assume one process).

## Local development
```bash
pip install -r requirements.txt
APP_ENV=development USE_MEMORY_DB=1 ADMIN_PASSWORD=ChangeMe12345 python app.py   # http://127.0.0.1:5000
python tests/test_flows.py     # catalog, QR, seller publish, admin review checks (no network)
python tests/test_flows2.py    # referral, seller wallet, reseller, bot admin commands
python tests/ui_check.py       # headless-browser check of all three panels (needs playwright + chromium)
```
`USE_MEMORY_DB=1` is only for tests — data vanishes on restart. Production uses Firebase.

## Project layout
```
app.py  wsgi.py  config.py        app factory, entrypoint, env config
core/        db (Firebase + memory), security, audit, utils, cache
services/    licenses, payments, stats, referral, rewards, resellers, sellers, appwrite, reviewchecks, broadcast …
routes/      admin API, reseller API, seller API, public license API, webhook, media proxy, pages
bot/         tg client, multi-tenant context, handlers (flow), messages, antispam
templates/   login / app shells      static/ css + ES-module JS (pages/ per panel)
tests/       test_flows.py, test_flows2.py, ui_check.py, harness.py
```
