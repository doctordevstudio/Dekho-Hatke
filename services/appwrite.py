"""Appwrite Storage (REST) -- used for seller uploads that are too big for the Telegram Bot API (> 45 MB) and so
the admin can download an APK from the panel to test it before approving.

Env: APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY (+ optional APPWRITE_BUCKET_ID).
The API key stays on the server; the browser only ever talks to this panel.
"""
import logging
import threading
import uuid

import requests

from config import Config
from core.errors import ApiError

log = logging.getLogger("appwrite")
CHUNK = 5 * 1024 * 1024
_bucket_ok = False
_lock = threading.Lock()


def configured():
    return bool(Config.APPWRITE_ENDPOINT and Config.APPWRITE_PROJECT_ID and Config.APPWRITE_API_KEY
                and Config.APPWRITE_API_KEY != "your-api-key")


def _headers(extra=None):
    h = {"X-Appwrite-Project": Config.APPWRITE_PROJECT_ID, "X-Appwrite-Key": Config.APPWRITE_API_KEY}
    h.update(extra or {})
    return h


def _url(path):
    return f"{Config.APPWRITE_ENDPOINT}{path}"


def _err(r, what):
    try:
        msg = r.json().get("message", "")
    except ValueError:
        msg = r.text[:200]
    log.warning("appwrite %s failed (%s): %s", what, r.status_code, msg)
    raise ApiError(f"Storage error: {msg or r.status_code}", 502)


def ensure_bucket():
    """Create the bucket on first use (idempotent)."""
    global _bucket_ok
    if _bucket_ok:
        return
    with _lock:
        if _bucket_ok:
            return
        b = Config.APPWRITE_BUCKET_ID
        try:
            r = requests.get(_url(f"/storage/buckets/{b}"), headers=_headers(), timeout=20)
            if r.status_code == 404:
                r = requests.post(_url("/storage/buckets"), headers=_headers(), timeout=30,
                                  json={"bucketId": b, "name": "Seller uploads", "fileSecurity": False,
                                        "enabled": True, "compression": "none", "antivirus": False})
            if r.status_code >= 400:
                _err(r, "bucket")
        except requests.RequestException as e:
            raise ApiError(f"Storage is not reachable: {e}", 502)
        _bucket_ok = True


def upload(fileobj, size, filename, mime="application/octet-stream"):
    """Chunked upload of an open binary file. Returns the Appwrite file id."""
    if not configured():
        raise ApiError("Large file storage is not configured on this server.", 503)
    ensure_bucket()
    fid = uuid.uuid4().hex
    url = _url(f"/storage/buckets/{Config.APPWRITE_BUCKET_ID}/files")
    start, size = 0, int(size)
    fileobj.seek(0)
    try:
        while start < size or size == 0:
            chunk = fileobj.read(CHUNK)
            if not chunk and size:
                break
            end = start + len(chunk) - 1
            hdr = {"Content-Range": f"bytes {start}-{end}/{size}"}
            if start:
                hdr["x-appwrite-id"] = fid
            r = requests.post(url, headers=_headers(hdr), data={"fileId": fid},
                              files={"file": (filename, chunk, mime)}, timeout=180)
            if r.status_code >= 400:
                _err(r, "upload")
            start += len(chunk)
            if size == 0:
                break
    except requests.RequestException as e:
        raise ApiError(f"Storage upload failed: {e}", 502)
    return fid


def download_response(file_id):
    """A streaming `requests` response for the file (caller closes it)."""
    if not configured():
        raise ApiError("Storage is not configured.", 503)
    try:
        r = requests.get(_url(f"/storage/buckets/{Config.APPWRITE_BUCKET_ID}/files/{file_id}/download"),
                         headers=_headers(), stream=True, timeout=60)
    except requests.RequestException as e:
        raise ApiError(f"Storage is not reachable: {e}", 502)
    if r.status_code == 404:
        raise ApiError("File not found in storage.", 404)
    if r.status_code >= 400:
        _err(r, "download")
    return r


def delete(file_id):
    if not (configured() and file_id):
        return
    try:
        requests.delete(_url(f"/storage/buckets/{Config.APPWRITE_BUCKET_ID}/files/{file_id}"),
                        headers=_headers(), timeout=20)
    except requests.RequestException:
        log.info("could not delete appwrite file %s", file_id)
