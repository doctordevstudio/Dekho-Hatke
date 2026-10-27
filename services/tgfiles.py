"""Files that admins and sellers send to the main bot.

The bot answers every such file with its Telegram file id and remembers WHO sent it. A file id is shown only to its
sender (in the bot chat and in their own panel list), and a seller can attach only ids that are on their OWN list, so
nobody can use or even see somebody else's file. Nothing is stored on our side: Telegram keeps the file, we keep
the id (this replaces the old external storage for big APKs; Telegram accepts files up to 2 GB).
"""
import hashlib

from core import db, utils

KEEP = 60  # newest files remembered per sender
MAX_VIDEO_MB = 49
MB = 1024 * 1024
APK_MIME = "application/vnd.android.package-archive"
LABEL = {"apk": "APK file", "video": "Video", "photo": "Photo", "document": "File", "animation": "GIF", "audio": "Audio",
         "voice": "Voice", "video_note": "Video note", "sticker": "Sticker"}


def _key(owner, file_id):
    return hashlib.sha1(f"{owner}|{file_id}".encode()).hexdigest()[:32]


def classify(msg):
    """(kind, file_id, name, size, mime) for the media in a Telegram message, or None."""
    d = msg.get("document")
    if d:
        name, mime = d.get("file_name") or "", d.get("mime_type") or ""
        kind = "apk" if name.lower().endswith(".apk") or mime == APK_MIME else "document"
        return kind, d["file_id"], name or "file", int(d.get("file_size") or 0), mime
    v = msg.get("video")
    if v:
        return "video", v["file_id"], v.get("file_name") or "video.mp4", int(v.get("file_size") or 0), v.get("mime_type") or "video/mp4"
    if msg.get("photo"):
        ph = msg["photo"][-1]
        return "photo", ph["file_id"], "photo", int(ph.get("file_size") or 0), "image/jpeg"
    for field, kind in (("animation", "animation"), ("audio", "audio"), ("voice", "voice"), ("video_note", "video_note"),
                        ("sticker", "sticker")):
        if msg.get(field):
            x = msg[field]
            return kind, x["file_id"], x.get("file_name") or kind, int(x.get("file_size") or 0), x.get("mime_type") or ""
    return None


def problem(wanted, kind, name, size, mime, max_apk_mb):
    """Why a file cannot be used as `wanted` (apk | preview_video | setup_video), or None when it is fine."""
    if wanted == "apk":
        if kind != "apk":
            return "That is not an APK. Send the file whose name ends with .apk (send it as a file / document)."
        if size and size > max_apk_mb * MB:
            return f"The APK is too large (max {max_apk_mb} MB)."
        return None
    if wanted in ("preview_video", "setup_video"):
        if kind != "video":
            return "Send it to the bot as a Video (an .mp4), not as a file."
        if mime and mime != "video/mp4":
            return "Videos must be MP4 files."
        if size and size >= MAX_VIDEO_MB * MB:
            return f"The video must be smaller than {MAX_VIDEO_MB + 1} MB."
        return None
    return "Unknown file type."


def register(owner, role, kind, file_id, name, size, mime):
    owner = str(owner)
    rec = {"id": _key(owner, file_id), "owner": owner, "role": role, "file_id": file_id, "kind": kind,
           "name": str(name)[:80], "size": int(size or 0), "mime": mime or "", "ts": utils.now()}
    db.set(f"/tg_files/{rec['id']}", rec)
    from services import tgchannel
    tgchannel.remember(file_id, kind, name, size, mime)  # by file id: delivery needs the real name and the channel copy
    rows = mine(owner, limit=10_000)
    for old in rows[KEEP:]:
        db.set(f"/tg_files/{old['id']}", None)
    return rec


def mine(owner, kinds=None, limit=30):
    """The sender's own files, newest first."""
    rows = list(db.query_equal("/tg_files", "owner", str(owner)))
    if kinds:
        rows = [r for r in rows if r.get("kind") in kinds]
    rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return rows[:limit]


def find(owner, file_id):
    """The record when `file_id` was sent by `owner`; otherwise None."""
    rec = db.get(f"/tg_files/{_key(owner, file_id)}")
    return rec if rec and rec.get("owner") == str(owner) and rec.get("file_id") == file_id else None


def public(rec):
    return {"file_id": rec["file_id"], "kind": rec["kind"], "name": rec["name"], "size": rec["size"], "ts": rec["ts"]}
