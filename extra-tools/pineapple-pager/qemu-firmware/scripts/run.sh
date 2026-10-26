#!/bin/bash
# run.sh - launch the WiFi Pineapple Pager Go firmware under the patched
# qemu-user-mipsel (with fake nl80211 genl + fake /proc/mounts overlay).
#
# Usage:
#   sudo ./run.sh                 # run firmware (foreground, Ctrl-C to stop)
#   sudo ./run.sh --strace        # run with qemu syscall trace to strace.txt
#   sudo CAPTURE=8 ./run.sh       # run, after 8s dump framebuffer and exit
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOTFS="${ROOTFS:-/home/kali/pager-qemu/rootfs}"
QEMU="${QEMU:-$HERE/qemu-8.2.2/build/qemu-mipsel}"

if [ "$(id -u)" != "0" ]; then
    echo "must run as root (firmware checks getuid()==0). Re-run with sudo." >&2
    exit 1
fi

# populate fake nodes
ROOTFS="$ROOTFS" bash "$HERE/prepare.sh"

cd "$ROOTFS"
ARGS=(-L "$ROOTFS")
[ "$1" = "--strace" ] && ARGS+=(-strace)

if [ -n "$CAPTURE" ]; then
    # zero fb, run in bg, dump after CAPTURE seconds
    dd if=/dev/zero of="$ROOTFS/dev/fb0" bs=426240 count=1 2>/dev/null
    "$QEMU" "${ARGS[@]}" ./pineapple/pineapple &
    QPID=$!
    sleep "$CAPTURE"
    cp "$ROOTFS/dev/fb0" "$HERE/fb_capture.dump"
    kill -TERM "$QPID" 2>/dev/null || true
    wait "$QPID" 2>/dev/null || true
    echo "[run] framebuffer captured to $HERE/fb_capture.dump"
    python3 "$HERE/fbrender.py" "$HERE/fb_capture.dump" "$HERE/fb_capture.png"
    echo "[run] rendered to $HERE/fb_capture.png"
else
    exec "$QEMU" "${ARGS[@]}" ./pineapple/pineapple
fi
