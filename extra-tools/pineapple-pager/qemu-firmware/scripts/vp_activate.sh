#!/bin/bash
# vp_activate.sh - one-shot: ensure the firmware's native Virtual Pager TCP API
# is enabled on :1471, then run a visor capture + an input-navigation proof.
#
# Assumes the firmware is already running under qemu (see fw_start.sh) and that
# rootfs/etc/shadow has a known root password (see --password below).
#
#   sudo ./vp_activate.sh [PASSWORD]
#
# Honest notes:
#  - The Virtual Pager screen/input WS are served by default in this build; no
#    $_VIRTUAL_PAGER_ENABLED toggle or /tmp/.cc_unlock_ui is required here.
#  - enable_tcp is done via the trusted local unix socket /tmp/api.sock.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="/home/kali/pager-qemu"
PW="${1:-pineapplepager}"
HOST="127.0.0.1:1471"
SOCK="/tmp/api.sock"

echo "[1/4] enable TCP API via unix socket ($SOCK)"
curl -s --max-time 5 -X POST --unix-socket "$SOCK" \
    http://localhost/api/system/enable_tcp -w " (http %{http_code})\n" || true
sleep 1

echo "[2/4] confirm :1471 listener"
ss -lntp 2>/dev/null | grep 1471 || { echo "  !! :1471 not listening"; exit 1; }

echo "[3/4] visor: capture one native Virtual Pager frame"
python3 "$HERE/vp_screencap.py" --host "$HOST" --password "$PW" \
    --out "$OUT/vp_frame.png"

echo "[4/4] input: before/after ArrowDown navigation proof"
python3 "$HERE/vp_input_test.py" --host "$HOST" --password "$PW" \
    --key ArrowDown --before "$OUT/vp_before.png" --after "$OUT/vp_after.png"

echo "done. PNGs in $OUT/: vp_frame.png vp_before.png vp_after.png"
