// Documentation: how to call the license API from Java, C++, Python, JavaScript and Node.js + payload, responses, best practices.
import { $, api, copyText, esc, tabsBar } from '../core.js';

export default async function (root) {
  // the license API lives on its own sub-domain (api.<domain>); panels run on seller./admin./reseller.
  const base = location.origin.replace(/\/\/[^./]+\./, '//api.');
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
    cpp: () => `// C++17 · libcurl + nlohmann/json (header-only).  Build: g++ -std=c++17 license.cpp -lcurl -o license
// Android NDK: ship libcurl (or call the Java helper through JNI) and run it on a background thread, never the UI thread.
#include <curl/curl.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <cctype>
#include <iostream>
#include <optional>
#include <string>

static const std::string ENDPOINT = "${EP()}";
static const std::string API_KEY  = "${KEY()}";   // keep it obfuscated / split in your build

static size_t onData(char* data, size_t size, size_t count, void* out) {
    static_cast<std::string*>(out)->append(data, size * count);
    return size * count;
}

// Returns std::nullopt when the license is valid, otherwise the error code
// (LICENSE_EXPIRED, DEVICE_MISMATCH, MAINTENANCE ...). NETWORK_ERROR = retry, never unlock.
std::optional<std::string> checkLicense(std::string licenseKey, const std::string& deviceId) {
    licenseKey.erase(std::remove_if(licenseKey.begin(), licenseKey.end(), [](unsigned char c) { return std::isspace(c); }), licenseKey.end());
    std::transform(licenseKey.begin(), licenseKey.end(), licenseKey.begin(), [](unsigned char c) { return std::toupper(c); });

    nlohmann::json body = {{"license_key", licenseKey}, {"device_id", deviceId},
                           {"device_name", "C++ client"}, {"app_version", "1.0.0"}};
    const std::string payload = body.dump();
    std::string response;

    CURL* curl = curl_easy_init();
    if (!curl) return std::string("NETWORK_ERROR");
    curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    headers = curl_slist_append(headers, ("X-API-Key: " + API_KEY).c_str());

    curl_easy_setopt(curl, CURLOPT_URL, ENDPOINT.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, payload.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, static_cast<long>(payload.size()));
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, onData);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 1L);   // HTTPS only, verify the certificate
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 2L);

    const CURLcode rc = curl_easy_perform(curl);
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    if (rc != CURLE_OK) return std::string("NETWORK_ERROR");

    try {
        const auto res = nlohmann::json::parse(response);
        if (res.value("success", false)) return std::nullopt;
        return res.value("code", std::string("ERROR"));
    } catch (...) {
        return std::string("NETWORK_ERROR");
    }
}

int main() {
    curl_global_init(CURL_GLOBAL_DEFAULT);
    const auto bad = checkLicense("YOURLICENSEKEY12345", "9f3c2a7d51b8e4a0");   // device id must be STABLE
    std::cout << (bad ? *bad : std::string("valid")) << std::endl;
    curl_global_cleanup();
    return bad ? 1 : 0;
}`,
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
  const tb = tabsBar([['java', 'Java'], ['cpp', 'C++'], ['python', 'Python'], ['js', 'JavaScript'], ['node', 'Node.js']], lang, (k) => { lang = k; drawCode(); });
  $('#tb', root).appendChild(tb.el);
  $('#app', root).onchange = (e) => { app = apps.find((a) => a.id === e.target.value); drawCode(); };
  root.addEventListener('click', (e) => { const b = e.target.closest('[data-c]'); if (b) copyText(b.parentElement.querySelector('pre').textContent); });
  drawCode();
}
