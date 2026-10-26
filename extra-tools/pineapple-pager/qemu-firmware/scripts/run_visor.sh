#!/bin/bash
# run_visor.sh - one-shot launcher: start the firmware under the patched qemu
# (with the input FIFO exposed) in the background, then launch the live viewer.
#
#   sudo ./run_visor.sh           # firmware (bg) + viewer (fg, on Wayland)
#   sudo ./run_visor.sh --nofw    # viewer only (firmware already running)
#   sudo FW_ONLY=1 ./run_visor.sh # firmware only, no viewer (headless test)
#
# The viewer must reach the user's Wayland session; we export the vars.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export ROOTFS="${ROOTFS:-/home/kali/pager-qemu/rootfs}"
export QEMU="${QEMU:-/home/kali/pager-qemu/qemu-8.2.2/build/qemu-mipsel}"
export PAGER_INPUT_FIFO="${PAGER_INPUT_FIFO:-/tmp/pager_input.fifo}"
export PAGER_FB="${PAGER_FB:-$ROOTFS/dev/fb0}"

if [ "$(id -u)" != "0" ]; then
    echo "run as root (firmware requires getuid()==0)." >&2; exit 1
fi

LOG="${LOG:-/home/kali/pager-qemu/visor_fw.log}"

start_fw() {
    ROOTFS="$ROOTFS" bash "$HERE/prepare.sh"
    echo "[run_visor] starting firmware -> $LOG"
    ( cd "$ROOTFS" && PAGER_INPUT_FIFO="$PAGER_INPUT_FIFO" \
        "$QEMU" -L "$ROOTFS" ./pineapple/pineapple ) >"$LOG" 2>&1 &
    echo $! > /tmp/pager_fw.pid
    echo "[run_visor] firmware pid $(cat /tmp/pager_fw.pid)"
}

if [ "$1" != "--nofw" ]; then
    start_fw
    sleep 6
fi

if [ -n "$FW_ONLY" ]; then
    echo "[run_visor] FW_ONLY set, not launching viewer. Log: $LOG"
    exit 0
fi

# viewer needs the invoking user's Wayland session
WUSER="${SUDO_USER:-kali}"
WUID="$(id -u "$WUSER")"
export XDG_RUNTIME_DIR="/run/user/$WUID"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
echo "[run_visor] launching viewer as $WUSER (WAYLAND_DISPLAY=$WAYLAND_DISPLAY)"
exec sudo -u "$WUSER" \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    PAGER_FB="$PAGER_FB" \
    PAGER_INPUT_FIFO="$PAGER_INPUT_FIFO" \
    python3 "$HERE/pager_viewer.py"
