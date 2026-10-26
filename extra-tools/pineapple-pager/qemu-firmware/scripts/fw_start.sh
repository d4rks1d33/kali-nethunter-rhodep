#!/bin/bash
# fw_start.sh - start the Pineapple Pager firmware under patched qemu-mipsel,
# fully detached, logging to fw_boot.log. Run as root.
#
# This version ALSO guarantees the WiFi back-end bridge is in place so the
# firmware's PineAP connects to our NATIVE aarch64 pineapd-pager instead of its
# own (useless-under-qemu) MIPS pineapd:
#   * setup wizard is marked complete (config pager: setup_complete + eula) so
#     the server emits "Starting PineAP" instead of "Deferring PineAP".
#   * the MIPS pineapd is neutralised in the rootfs (init.d stub + stub binary)
#     so it can't bind /tmp/pineap_sock and compete.
#   * pineapd-pager.service (native) must be running (listens on
#     /opt/pineapple-pager/run/pineap_sock).
#   * pineap-bridge.service translates the 8-byte header endianness between the
#     firmware (little-endian) and the native daemon (big-endian) and listens on
#     /tmp/pineap_sock -- which is where qemu-user actually puts the firmware's
#     AF_UNIX connect() (it does NOT prepend the -L rootfs prefix to socket paths).
# Paths default to the relocated /opt layout; override via env for a dev tree
# (e.g. PAGER_QEMU_ROOT=/home/kali/pager-qemu ... with the legacy sub-paths).
PAGER_QEMU_ROOT="${PAGER_QEMU_ROOT:-/opt/pineapple-pager/qemu}"
ROOTFS="${ROOTFS:-$PAGER_QEMU_ROOT/rootfs}"
QEMU="${QEMU:-$PAGER_QEMU_ROOT/bin/qemu-mipsel}"
LOG="${LOG:-/tmp/pager_fw_boot.log}"
FIFO="${FIFO:-/tmp/pager_input.fifo}"

# --- make sure the native WiFi back-end + bridge are up ------------------------
systemctl is-active --quiet pineapd-pager.service || systemctl start pineapd-pager.service
systemctl is-active --quiet pineap-bridge.service  || systemctl start pineap-bridge.service

# sanity: the firmware's socket path must resolve to the bridge
if [ ! -S /tmp/pineap_sock ]; then
    echo "WARNING: /tmp/pineap_sock is not a socket; the bridge may be down" >&2
fi

# --- launch the firmware -------------------------------------------------------
pkill -f "$QEMU" 2>/dev/null
sleep 1
[ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo "$FIFO"; chmod 666 "$FIFO"; }

cd "$ROOTFS" || exit 1
# keep the fifo open for writing from a holder so the firmware's reader never sees EOF
setsid bash -c "exec 9<>$FIFO; cd $ROOTFS; GOMAXPROCS=1 GODEBUG=asyncpreemptoff=1 PAGER_INPUT_FIFO=$FIFO exec $QEMU -L $ROOTFS ./pineapple/pineapple" </dev/null >"$LOG" 2>&1 &
echo "launched pid $!"
sleep 2
if pgrep -f qemu-mipsel >/dev/null; then
    echo "qemu running"
else
    echo "qemu NOT running; log:"
    cat "$LOG"
fi
