#!/usr/bin/env bash
# Downloads the official monero-wallet-rpc into ./bin (used for the Monero payment gateway: it signs transfers and scans
# subaddresses). It talks to a PUBLIC REMOTE NODE -- no local blockchain, no daemon. Safe to run on every build:
# a failure only prints a warning, so the rest of the site still deploys (crypto then stays "engine not installed").
set -u
cd "$(dirname "$0")/.."
BIN="bin/monero-wallet-rpc"
if [ -x "$BIN" ]; then echo "monero-wallet-rpc already installed"; exit 0; fi
warn() { echo "WARNING: $* -- crypto payments will be unavailable until monero-wallet-rpc is installed"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
URL="${XMR_CLI_URL:-https://downloads.getmonero.org/cli/linux64}"
curl -fsSL --retry 3 -o "$TMP/monero.tar.bz2" "$URL" || warn "download failed"
SUM="$(sha256sum "$TMP/monero.tar.bz2" | cut -d' ' -f1)"
# the official hash list is published separately from the download host: the file must match it
curl -fsSL --retry 3 -o "$TMP/hashes.txt" https://www.getmonero.org/downloads/hashes.txt || warn "could not fetch the official hash list"
grep -qi "$SUM" "$TMP/hashes.txt" || warn "SHA-256 of the download is not in the official hash list ($SUM)"
tar -xjf "$TMP/monero.tar.bz2" -C "$TMP" --wildcards '*/monero-wallet-rpc' || warn "could not unpack"
mkdir -p bin
cp "$TMP"/monero-*/monero-wallet-rpc "$BIN" && chmod +x "$BIN" || warn "could not install"
echo "installed $("$BIN" --version 2>/dev/null | head -1)"
