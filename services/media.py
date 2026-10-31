"""Image storage for UPI scanners (stored in Firebase as base64) and QR generation."""
import base64
import hashlib
import io
from urllib.parse import quote

from PIL import Image

from core import db, utils
from core.errors import ApiError

Image.MAX_IMAGE_PIXELS = 25_000_000
MAX_UPLOAD = 2 * 1024 * 1024


def save_scanner(raw, uploaded_by=""):
    return save_image(raw, "upi_scanner", uploaded_by)


def save_image(raw, kind="image", uploaded_by=""):
    """Validate + re-encode an uploaded image (strips metadata, blocks non-images). Returns media id."""
    if not raw or len(raw) > MAX_UPLOAD:
        raise ApiError("The image is missing or larger than 2 MB.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise ApiError("That file is not a valid image.")
    img.thumbnail((1000, 1000))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    mid = utils.new_id("med")
    db.set(f"/media/{mid}", {
        "id": mid, "kind": kind, "mime": "image/png", "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "data": base64.b64encode(data).decode(),
        "created_at": utils.now(), "by": uploaded_by,
    })
    return mid


def get_media(media_id):
    rec = db.get(f"/media/{media_id}")
    if not rec or "data" not in rec:
        return None, None
    return base64.b64decode(rec["data"]), rec.get("mime", "image/png")


def upi_uri(upi_id, name, amount, note):
    """upi://pay?pa=<UPI_ID>&pn=<PAYEE_NAME>&am=<AMOUNT>&cu=INR&tn=<NOTE>  (the standard every UPI app understands)"""
    a = float(amount)
    am = f"{a:.2f}"
    return (f"upi://pay?pa={quote(upi_id, safe='@._-')}&pn={quote((name or 'Merchant').strip(), safe='')}"
            f"&am={am}&cu=INR&tn={quote(str(note), safe='')}")


def make_upi_qr(upi_id, name, amount, txn_id):
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(upi_uri(upi_id, name, amount, txn_id))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_qr(data):
    """Any text -> PNG bytes (used for the Monero payment QR)."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ strict "is this really an image?" check
_MAGIC = (("jpeg", b"\xff\xd8\xff"), ("png", b"\x89PNG\r\n\x1a\n"), ("webp", b"RIFF"))
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def verify_real_image(raw, max_bytes=8 * 1024 * 1024, min_side=200):
    """True only for a genuine, fully decodable JPEG / PNG / WEBP. A renamed .txt/.exe/.pdf/.gif/.svg, a truncated
    file or a polyglot (image header + payload) is rejected: we check the magic bytes, let Pillow decode every pixel
    and make sure the format Pillow found is on the allow-list. Returns (ok, reason)."""
    if not raw or len(raw) < 1024:
        return False, "empty or too small"
    if len(raw) > max_bytes:
        return False, "too large"
    head = raw[:12]
    kind = next((k for k, sig in _MAGIC if head.startswith(sig)), None)
    if kind is None or (kind == "webp" and head[8:12] != b"WEBP"):
        return False, "not a JPEG/PNG/WEBP file"
    try:
        probe = Image.open(io.BytesIO(raw))
        fmt = probe.format
        if fmt not in ALLOWED_FORMATS or getattr(probe, "is_animated", False):
            return False, "unsupported image type"
        probe.verify()
        img = Image.open(io.BytesIO(raw))
        img.load()  # decodes all pixel data: truncated / corrupted files raise here
        if min(img.size) < min_side or img.size[0] * img.size[1] > Image.MAX_IMAGE_PIXELS:
            return False, "image dimensions look wrong"
    except Exception:
        return False, "corrupted or not an image"
    return True, ""
