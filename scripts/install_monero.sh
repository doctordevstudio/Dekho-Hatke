#!/usr/bin/env bash
# Build-time installer for monero-wallet-rpc (never fails the build). The real work is in install_monero.py, which the
# Crypto page can also run by itself if this step was skipped.
cd "$(dirname "$0")/.." || exit 0
python3 scripts/install_monero.py || true
exit 0
