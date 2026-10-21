// Documentation: how to call the license API from Java, smali, Python, JavaScript and Node.js + payload, responses, best practices.
import { $, api, copyText, esc, tabsBar } from '../core.js';

export default async function (root) {
  const base = location.origin;
  const apps = (await api('/apps')).items;
  let app = apps[0], lang = 'java';
  const EP = () => (app ? app.endpoint : `${base}/api/v1/<APP_ID>/verify`);
  const KEY = () => (app ? app.api_key : '<API_KEY>');

  const CODE = {
    java: () => `// Android (Java). Run it on a background thread — never on the UI thread.
import java.io.*;
import java.net.*;
import org.json.JSONObject;
import android.provider.Settings;

public final class License {
    private static final String ENDPOINT = "${EP()}";
    private static final String API_KEY  = "${KEY()}";   // keep it obfuscated / split in your build

    /** @return null when the license is valid, otherwise the error code (LICENSE_EXPIRED, DEVICE_MISMATCH, MAINTENANCE …). */
    public static String check(android.content.Context ctx, String licenseKey) {
        try {
            String deviceId = Settings.Secure.getString(ctx.getContentResolver(), Settings.Secure.ANDROID_ID);
            JSONObject body = new JSONObject()
                    .put("license_key", licenseKey.trim().toUpperCase())
                    .put("device_id", deviceId)
                    .put("device_name", android.os.Build.MODEL)
                    .put("app_version", "1.0.0");

            HttpURLConnection c = (HttpURLConnection) new URL(ENDPOINT).openConnection();
            c.setRequestMethod("POST");
            c.setConnectTimeout(10000);
            c.setReadTimeout(10000);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("X-API-Key", API_KEY);
            c.setDoOutput(true);
            try (OutputStream os = c.getOutputStream()) { os.write(body.toString().getBytes("UTF-8")); }

            InputStream in = c.getResponseCode() < 400 ? c.getInputStream() : c.getErrorStream();
            String json = new java.util.Scanner(in, "UTF-8").useDelimiter("\\\\A").next();
            JSONObject res = new JSONObject(json);
            return res.optBoolean("success") ? null : res.optString("code", "ERROR");
        } catch (Exception e) {
            return "NETWORK_ERROR";   // let the user retry; do NOT unlock on errors
        }
    }
}`,
    smali: () => `# smali — calls the static Java helper above from an Activity's onCreate().
# 1) Add the License class (compile the Java above to smali with your toolchain) as Lcom/yourapp/License;
# 2) In your launcher Activity smali, after .locals/.prologue, run the check off the main thread and close the app on failure.

.method private runLicenseCheck(Ljava/lang/String;)V
    .locals 3

    new-instance v0, Ljava/lang/Thread;
    new-instance v1, Lcom/yourapp/LicenseTask;
    invoke-direct {v1, p0, p1}, Lcom/yourapp/LicenseTask;-><init>(Landroid/content/Context;Ljava/lang/String;)V
    invoke-direct {v0, v1}, Ljava/lang/Thread;-><init>(Ljava/lang/Runnable;)V
    invoke-virtual {v0}, Ljava/lang/Thread;->start()V
    return-void
.end method

# LicenseTask.run():  String code = License.check(ctx, key);
#   if (code != null) -> runOnUiThread { show message for code; finishAffinity(); }
.method public run()V
    .locals 3
    iget-object v0, p0, Lcom/yourapp/LicenseTask;->ctx:Landroid/content/Context;
    iget-object v1, p0, Lcom/yourapp/LicenseTask;->key:Ljava/lang/String;
    invoke-static {v0, v1}, Lcom/yourapp/License;->check(Landroid/content/Context;Ljava/lang/String;)Ljava/lang/String;
    move-result-object v2
    if-eqz v2, :ok
    # invalid -> close the app (replace with your own UI)
    check-cast v0, Landroid/app/Activity;
    invoke-virtual {v0}, Landroid/app/Activity;->finishAffinity()V
    :ok
    return-void
.end method`,
    python: () => `import requests, uuid

ENDPOINT = "${EP()}"
API_KEY  = "${KEY()}"

def check_license(license_key: str, device_id: str | None = None) -> str | None:
    """Returns None when valid, otherwise the error code."""
    device_id = device_id or hex(uuid.getnode())[2:].rjust(8, "0")   # any STABLE 8-128 char id
    try:
        r = requests.post(
            ENDPOINT,
            json={"license_key": license_key.strip().upper(), "device_id": device_id, "device_name": "Python client", "app_version": "1.0.0"},
            headers={"X-API-Key": API_KEY},
            timeout=10,
        )
        data = r.json()
    except Exception:
        return "NETWORK_ERROR"
    return None if data.get("success") else data.get("code", "ERROR")

if __name__ == "__main__":
    print(check_license("YOURLICENSEKEY12345") or "valid ✅")`,
    js: () => `// Browser / web view JavaScript
const ENDPOINT = "${EP()}";
const API_KEY  = "${KEY()}";   // NOTE: anything shipped to a browser can be read — prefer a server (Node.js tab) for real protection.

async function checkLicense(licenseKey, deviceId) {
  try {
    const res = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ license_key: licenseKey.trim().toUpperCase(), device_id: deviceId, device_name: navigator.userAgent.slice(0, 50), app_version: "1.0.0" }),
    });
    const data = await res.json();
    return data.success ? null : data.code;   // null = valid
  } catch (e) {
    return "NETWORK_ERROR";
  }
}`,
    node: () => `// Node.js 18+ (built-in fetch)
const ENDPOINT = "${EP()}";
const API_KEY  = process.env.LICENSE_API_KEY || "${KEY()}";

async function checkLicense(licenseKey, deviceId) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const res = await fetch(ENDPOINT, {
      method: "POST",
      signal: ctl.signal,
      headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
      body: JSON.stringify({ license_key: licenseKey.trim().toUpperCase(), device_id: deviceId, app_version: "1.0.0" }),
    });
    const data = await res.json();
    return data.success ? null : data.code;
  } catch {
    return "NETWORK_ERROR";
  } finally {
    clearTimeout(t);
  }
}

// Express example: protect a route
// app.post("/api/run", async (req, res) => { const bad = await checkLicense(req.body.key, req.body.device); if (bad) return res.status(403).json({ error: bad }); … });`,
  };
  const block = (code) => `<div class="copyable"><button class="btn sm" data-c>Copy</button><pre class="codebox">${esc(code)}</pre></div>`;
  const RESP = [
    ['✅ Key valid', 200, `{
  "success": true,
  "code": "OK",
  "message": "License valid.",
  "server_time": 1760000000,
  "license": { "key": "ABCDEFGH1234567890", "status": "active", "plan": "30 days", "expires_at": 1762592000, "remaining_seconds": 2592000,
               "user": { "full_name": "Rahul" } }
}`],
    ['⌛ Key Expire', 403, `{ "success": false, "code": "LICENSE_EXPIRED", "message": "This license has expired.", "expired_at": 1762592000 }`],
    ['🔒 Already Bound (another device)', 403, `{ "success": false, "code": "DEVICE_MISMATCH", "message": "This license is already linked to another device." }`],
    ['🛠 Under Maintenance', 'your status code (default 503)', `{ "success": false, "maintenance": true, "code": "MAINTENANCE", "status_code": 503, "message": "We are upgrading — please try again soon." }`],
    ['⛔ Revoked', 403, `{ "success": false, "code": "LICENSE_REVOKED", "message": "This license has been revoked." }`],
    ['❓ Key not found', 404, `{ "success": false, "code": "LICENSE_NOT_FOUND", "message": "License key not found for this app." }`],
    ['🔑 Wrong API key', 401, `{ "success": false, "code": "INVALID_API_KEY", "message": "Missing or invalid X-API-Key header." }`],
    ['🚫 App disabled', 403, `{ "success": false, "code": "APP_DISABLED", "message": "This app is currently disabled." }`],
    ['⏱ Rate limited', 429, `{ "success": false, "code": "RATE_LIMITED", "retry_after": 30 }`],
  ];
  root.innerHTML = `<div class="page-head"><div><h1>📚 Documentation</h1><p>Protect your app with one HTTPS call. Each license key works on <b>one device</b>.</p></div>
      <label class="field" style="min-width:240px"><span>Fill in my app</span><select id="app"><option value="">— placeholders —</option>${apps.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select></label></div>
    <div class="docs">
    <div class="card"><h3>1 · Request</h3><dl class="kv"><dt>Method</dt><dd><code>POST</code></dd><dt>URL</dt><dd class="mono" id="ep"></dd><dt>Headers</dt><dd><code>Content-Type: application/json</code> · <code>X-API-Key: &lt;your API key&gt;</code></dd></dl>
      <h3>2 · Payload</h3>${block(`{
  "license_key": "ABCDEFGH1234567890",   // 18 letters / numbers the customer got in Telegram
  "device_id":   "9f3c2a7d51b8e4a0",     // 8-128 chars: letters numbers . _ : -   (must be STABLE for this device)
  "device_name": "Pixel 7",              // optional, shown to the admin
  "app_version": "1.0.0"                 // optional
}`)}</div>
    <div class="card gap-top"><h3>3 · Code</h3><div id="tb"></div><div id="code"></div></div>
    <div class="card gap-top"><h3>4 · Responses</h3>${RESP.map(([t, s, j]) => `<h4>${t} <span class="badge">HTTP ${s}</span></h4>${block(j)}`).join('')}</div>
    <div class="card gap-top"><h3>5 · Best practices</h3><ul>
      <li><b>Run the check on every app start</b> and again every few hours (the same call works as a heartbeat) — expired or revoked keys stop working at the next check.</li>
      <li><b>Use a stable device id</b> (Android: <code>ANDROID_ID</code>). If it changes, the key looks like a new device and returns <code>DEVICE_MISMATCH</code>.</li>
      <li><b>Never unlock on network errors</b> — but allow a retry and show a friendly message. Fail closed on <code>success:false</code>.</li>
      <li><b>Handle every code</b>: LICENSE_EXPIRED → show a renew prompt, DEVICE_MISMATCH → ask the user to contact support to reset the device, MAINTENANCE → show the message and retry later.</li>
      <li><b>Protect the API key</b>: obfuscate / split it, never log it, rotate it from <b>My Apps → Revoke key</b> if it leaks (update the app after rotating).</li>
      <li><b>Check the HTTP status AND the JSON</b> (<code>success</code>, <code>code</code>); do not rely on the text of <code>message</code>.</li>
      <li><b>Verify on a server</b> for anything valuable; a client-only check can be patched out.</li>
      <li><b>Rate limits:</b> keep to a few calls per minute per key; repeated invalid keys temporarily block the network.</li>
      <li><b>Use HTTPS only</b> and set sensible timeouts (10 s).</li></ul></div></div>`;
  const drawCode = () => { $('#code', root).innerHTML = block(CODE[lang]()); $('#ep', root).textContent = EP(); };
  const tb = tabsBar([['java', 'Java'], ['smali', 'smali'], ['python', 'Python'], ['js', 'JavaScript'], ['node', 'Node.js']], lang, (k) => { lang = k; drawCode(); });
  $('#tb', root).appendChild(tb.el);
  $('#app', root).onchange = (e) => { app = apps.find((a) => a.id === e.target.value); drawCode(); };
  root.addEventListener('click', (e) => { const b = e.target.closest('[data-c]'); if (b) copyText(b.parentElement.querySelector('pre').textContent); });
  drawCode();
}
