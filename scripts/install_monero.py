#!/usr/bin/env python3
"""Downloads the official monero-wallet-rpc into ./bin (used by the Monero payment gateway: it signs transfers and scans subaddresses).
It only talks to a PUBLIC REMOTE NODE -- no blockchain download, no daemon.

Used two ways:
  * at build time:   python3 scripts/install_monero.py   (never fails the build: it prints a warning)
  * on demand:       services.xmr.install_engine()        (the Crypto page installs it itself if the build step was skipped)
Pure Python (urllib + tarfile + hashlib): needs no curl, bzip2 or other system tools.
"""
import hashlib
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "monero-wallet-rpc")
URL = os.getenv("XMR_CLI_URL", "https://downloads.getmonero.org/cli/linux64")
HASHES = os.getenv("XMR_HASHES_URL", "https://www.getmonero.org/downloads/hashes.txt")


def _get(url, dest=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "dekho-hatke-installer/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URLs)
        if dest is None:
            return r.read()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r, f)


def install(force=False):
    """Returns the path of the installed binary. Raises RuntimeError(reason) when it cannot be installed."""
    if os.path.isfile(BIN) and os.access(BIN, os.X_OK) and not force:
        return BIN
    tmp = tempfile.mkdtemp(prefix="xmrinst_")
    try:
        arc = os.path.join(tmp, "monero.tar.bz2")
        try:
            _get(URL, arc, timeout=300)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"download from {URL.split('/')[2]} failed: {e}")
        h = hashlib.sha256()
        with open(arc, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        if not os.getenv("XMR_SKIP_HASH"):  # the file must match the hash list published by getmonero.org
            try:
                listing = _get(HASHES, timeout=60).decode("utf-8", "replace").lower()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"could not fetch the official hash list: {e}")
            if digest not in listing and not re.search(digest, listing):
                raise RuntimeError(f"SHA-256 of the download ({digest[:16]}…) is not in the official hash list")
        try:
            with tarfile.open(arc, "r:bz2") as tf:
                member = next((m for m in tf.getmembers() if m.isfile() and m.name.endswith("/monero-wallet-rpc")), None)
                if not member:
                    raise RuntimeError("monero-wallet-rpc is not inside the archive")
                src = tf.extractfile(member)
                os.makedirs(os.path.dirname(BIN), exist_ok=True)
                part = BIN + ".part"
                with open(part, "wb") as out:
                    shutil.copyfileobj(src, out)
        except tarfile.TarError as e:
            raise RuntimeError(f"could not unpack the download: {e}")
        os.chmod(part, 0o755)
        os.replace(part, BIN)
        return BIN
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        print("monero-wallet-rpc ready:", install())
    except RuntimeError as e:
        print(f"WARNING: {e} -- crypto stays unavailable until the engine is installed (Crypto page -> Install engine)")
    sys.exit(0)
