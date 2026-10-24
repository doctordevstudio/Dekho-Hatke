# License API — how your app talks to the panel

Every app you create in the panel gets an **auto-generated endpoint** and an **API key**
(open the app page → *Endpoint* / *API key*).

```
POST  https://api.dekhohatke.com/api/v1/<app_id>/verify
POST  https://api.dekhohatke.com/api/v1/<app_id>/heartbeat      (same behaviour — use it for periodic pings)
```

## What your app must send

| Part | Value |
|---|---|
| Header `X-API-Key` | the app's API key (from the panel) |
| Header `Content-Type` | `application/json` |
| Body `license_key` | **required** — the 18-character key the user bought (letters/numbers) |
| Body `device_id` | **required** — a stable unique id of the user's device, 8–128 chars `A-Z a-z 0-9 . _ : -` |
| Body `device_name` | optional — shown in the panel (e.g. `Pixel 8`) |
| Body `app_version` | optional — e.g. `2.4.1` |

```bash
curl -X POST "https://api.dekhohatke.com/api/v1/app_0123456789ab/verify" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"license_key":"ABCDEFGHJKLMNPQRS2","device_id":"android-9f1c2d3e4b5a","device_name":"Pixel 8","app_version":"2.4.1"}'
```

### One license = one device
The **first** successful call links the key to that `device_id`. Any other device gets
`403 DEVICE_MISMATCH`. In the panel (license → *Reset device*) you can unlink it so the user can move.
Use an id that survives app restarts (e.g. Android `ANDROID_ID`, an id you generate once and store in
the keychain/secure storage). Don't use values that change on every launch.

## Success response — `200`

```json
{
  "success": true,
  "code": "OK",
  "message": "License valid.",
  "server_time": 1760500000,
  "license": {
    "key": "ABCDEFGHJKLMNPQRS2",
    "status": "active",
    "plan": "30 Days",
    "expires_at": 1763092000,
    "remaining_seconds": 2592000,
    "user": { "full_name": "Rahul K", "username": "rahul", "chat_id": "123456789" }
  }
}
```
Times are Unix seconds (UTC). Trust `server_time`/`remaining_seconds`, not the phone's clock.

## Error responses

Every error has `success: false`, a machine-readable `code`, and a human `message`.

| HTTP | `code` | Meaning / what the app should do |
|---|---|---|
| 400 | `BAD_REQUEST` / `INVALID_LICENSE_FORMAT` / `INVALID_DEVICE_ID` | Fix the request |
| 401 | `INVALID_API_KEY` | Wrong/missing `X-API-Key` (or the key was regenerated) |
| 404 | `APP_NOT_FOUND` | Wrong `<app_id>` or the app was deleted |
| 404 | `LICENSE_NOT_FOUND` | Key doesn't exist for this app → ask user to re-enter |
| 403 | `LICENSE_EXPIRED` | Show "expired — renew in the bot"; `expired_at` is included |
| 403 | `APP_DISABLED` | The app was disabled by the admin → lock the app and show the message |
| 403 | `LICENSE_REVOKED` | Key was revoked by admin → lock the app |
| 403 | `DEVICE_MISMATCH` | Key already used on another device |
| 429 | `RATE_LIMITED` | Too many requests; wait `Retry-After` seconds |
| *custom* | `MAINTENANCE` | Maintenance rule is ON (see below) |

### Maintenance mode
When you switch a **maintenance rule** on (Settings → Maintenance) for an app (or all apps), the API
answers with the **exact HTTP status code you typed** (e.g. `200` or `301`) and this body:

```json
{ "success": false, "maintenance": true, "code": "MAINTENANCE", "status_code": 301,
  "message": "We'll be back in 30 minutes", "server_time": 1760500000 }
```
Always check `maintenance` / `code == "MAINTENANCE"` **before** assuming success — a maintenance reply
can be HTTP 200. (A 3xx is returned without a `Location` header, so HTTP clients will not follow it.)

## Rate limits
* 60 requests / minute / IP
* 20 requests / minute / license key
* 10 invalid-key attempts / minute / IP → that IP is blocked for 5 minutes

Cache a successful result in the app (e.g. re-check every 6–24 h, or on launch) instead of calling on
every screen. Handle `429` by backing off.

## Recommended client flow

1. On first launch ask for the license key; generate + store a `device_id`.
2. `POST /verify`. If `success` → unlock, remember `expires_at`.
3. On every launch (or every few hours) call `/heartbeat`. Active-user statistics in the panel are
   built from these successful calls (one count per license per day).
4. On `LICENSE_EXPIRED` / `LICENSE_REVOKED` / `DEVICE_MISMATCH` → lock features and show the message.
5. On network errors → allow a short offline grace period you choose; never loop faster than 1 request / 5 s.

## Code samples

**Python**
```python
import requests, uuid
API = "https://api.dekhohatke.com/api/v1/app_0123456789ab/verify"
r = requests.post(API, headers={"X-API-Key": "YOUR_API_KEY"},
                  json={"license_key": "ABCDEFGHJKLMNPQRS2", "device_id": "pc-" + hex(uuid.getnode())},
                  timeout=15)
data = r.json()
if data.get("maintenance"):
    print("Maintenance:", data["message"])
elif data["success"]:
    print("OK until", data["license"]["expires_at"])
else:
    print("Blocked:", data["code"], data["message"])
```

**C++ (libcurl + nlohmann/json)**
```cpp
// g++ -std=c++17 license.cpp -lcurl -o license
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <string>

static size_t onData(char* d, size_t s, size_t n, void* out) { static_cast<std::string*>(out)->append(d, s * n); return s * n; }

// returns "" when the license is valid, otherwise the error code (NETWORK_ERROR = retry, never unlock)
std::string checkLicense(const std::string& api, const std::string& apiKey, const std::string& key, const std::string& deviceId) {
    nlohmann::json body = {{"license_key", key}, {"device_id", deviceId}, {"app_version", "1.0.0"}};
    std::string payload = body.dump(), response;
    CURL* c = curl_easy_init();
    if (!c) return "NETWORK_ERROR";
    curl_slist* h = curl_slist_append(nullptr, "Content-Type: application/json");
    h = curl_slist_append(h, ("X-API-Key: " + apiKey).c_str());
    curl_easy_setopt(c, CURLOPT_URL, api.c_str());
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload.c_str());
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, onData);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(c, CURLOPT_TIMEOUT, 10L);
    CURLcode rc = curl_easy_perform(c);
    curl_slist_free_all(h); curl_easy_cleanup(c);
    if (rc != CURLE_OK) return "NETWORK_ERROR";
    try {
        auto r = nlohmann::json::parse(response);
        return r.value("success", false) ? "" : r.value("code", std::string("ERROR"));
    } catch (...) { return "NETWORK_ERROR"; }
}
```
The seller panel's **Documentation** page has the full version with every header and error code.

**JavaScript / Node / browser extension**
```js
const res = await fetch(API, {
  method: "POST",
  headers: { "X-API-Key": API_KEY, "Content-Type": "application/json" },
  body: JSON.stringify({ license_key: key, device_id: deviceId }),
});
const data = await res.json();
if (data.maintenance) { /* show data.message */ }
else if (data.success) { /* unlock */ }
else { /* data.code / data.message */ }
```

**Kotlin (OkHttp)**
```kotlin
val body = JSONObject().put("license_key", key).put("device_id", deviceId).toString()
    .toRequestBody("application/json".toMediaType())
val req = Request.Builder().url(API).header("X-API-Key", API_KEY).post(body).build()
client.newCall(req).execute().use { resp ->
    val json = JSONObject(resp.body!!.string())
    if (json.optBoolean("maintenance")) { /* show json.getString("message") */ }
    else if (json.getBoolean("success")) { /* unlock */ }
    else { /* json.getString("code") */ }
}
```

## Security notes
* The API key identifies *your app*, not the user. Anything shipped inside an app can be extracted, so
  the real protection is the **license key + device lock + rate limits**. If a key leaks, press
  *Regenerate* on the app page and ship the new key.
* Always use HTTPS (Render provides it). Never log license keys.

## Integration test keys (Seller panel → app under review)
When you submit an app, the admin generates a **test key** and runs four live checks against the *same* endpoint:
login (valid key → `200`), device uniqueness (same key from another `device_id` → `DEVICE_MISMATCH`),
maintenance (`MAINTENANCE` reply), and revoke (`LICENSE_REVOKED`). Your app must handle all four
codes (`LICENSE_EXPIRED`, `LICENSE_REVOKED`, `DEVICE_MISMATCH`, `MAINTENANCE`) correctly or the review will not pass.
