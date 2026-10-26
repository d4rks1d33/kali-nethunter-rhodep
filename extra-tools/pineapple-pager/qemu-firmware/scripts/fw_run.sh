#!/bin/bash
# fw_run.sh - FOREGROUND launcher of the Pineapple Pager firmware under the
# patched qemu-mipsel, for use as a systemd Type=simple ExecStart.
#
# Unlike fw_start.sh (which detaches into the background and is meant for
# interactive/manual use), this script execs qemu in the FOREGROUND so systemd
# owns the process directly: clean Restart=on-failure, logs go to the journal,
# and `systemctl stop` kills it reliably.
#
# Paths default to the relocated /opt layout but can be overridden via env:
#   PAGER_QEMU_ROOT   base dir (default /opt/pineapple-pager/qemu)
#   ROOTFS, QEMU, FIFO
#
# The native WiFi back-end (pineapd-pager) + the endianness bridge
# (pineap-bridge) are expected to be up already (the unit orders After/Requires
# them); we still do a best-effort check so a manual run also works.
set -u

PAGER_QEMU_ROOT="${PAGER_QEMU_ROOT:-/opt/pineapple-pager/qemu}"
ROOTFS="${ROOTFS:-$PAGER_QEMU_ROOT/rootfs}"
QEMU="${QEMU:-$PAGER_QEMU_ROOT/bin/qemu-mipsel}"
FIFO="${FIFO:-/tmp/pager_input.fifo}"
# Host dir holding the DuckyScript command scripts (LOG, LED, ALERT, ...).
# Built by payload_env.sh; prepended to the firmware's PATH so payloads can
# resolve DuckyScript commands (they POST to /tmp/api.sock, same as on device).
DUCKY_BIN="${DUCKY_BIN:-$PAGER_QEMU_ROOT/hak5bin}"

log() { echo "[fw_run] $*" >&2; }

# --- UCI write bridge: /etc/config -> firmware rootfs config -------------------
# The firmware (qemu-user, NO chroot) persists its wireless/AP state with UCI,
# which writes the ABSOLUTE host path /etc/config/<pkg> (qemu's -L only affects
# the ELF loader, not runtime file opens). Without this, the Open AP toggle's
# write to /etc/config/wireless fails with ENOENT and the enable never persists,
# so the native openap watcher never sees it. Point /etc/config at the rootfs
# config dir so those writes land where both the firmware AND pineapd-pager's
# openap watcher read them. Idempotent; refuses to clobber a real dir.
if [ ! -e /etc/config ]; then
    ln -sfn "$ROOTFS/etc/config" /etc/config 2>/dev/null || \
        log "WARNING: could not create /etc/config symlink (UCI AP toggles may not persist)"
elif [ -L /etc/config ]; then
    # keep it pointing at our rootfs (in case ROOTFS changed)
    ln -sfn "$ROOTFS/etc/config" /etc/config 2>/dev/null || true
else
    log "WARNING: /etc/config exists and is not a symlink; leaving it untouched"
fi

# --- DuckyScript payload environment (host-side bridge) -----------------------
# Make /lib/pineapple resolvable and (re)build the host DuckyScript command dir
# so launched payloads find LOG/ALERT/PROMPT/WAIT_FOR_*/etc. Idempotent.
if [ -x "$PAGER_QEMU_ROOT/scripts/payload_env.sh" ]; then
    PAGER_QEMU_ROOT="$PAGER_QEMU_ROOT" ROOTFS="$ROOTFS" DUCKY_BIN="$DUCKY_BIN" \
        "$PAGER_QEMU_ROOT/scripts/payload_env.sh" || \
        log "WARNING: payload_env.sh failed; DuckyScript commands may be unavailable"
fi

# --- best-effort: make sure native back-end + bridge are up -------------------
if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet pineapd-pager.service 2>/dev/null || \
        systemctl start pineapd-pager.service 2>/dev/null || true
    systemctl is-active --quiet pineap-bridge.service 2>/dev/null || \
        systemctl start pineap-bridge.service 2>/dev/null || true
fi

if [ ! -S /tmp/pineap_sock ]; then
    log "WARNING: /tmp/pineap_sock is not a socket yet; the bridge may still be starting"
fi

# --- don't leave a stale qemu competing for :1471 -----------------------------
pkill -f "$QEMU" 2>/dev/null || true
sleep 1

# --- input fifo (kept open by a holder so the firmware reader never sees EOF) --
[ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo "$FIFO"; chmod 666 "$FIFO"; }
# background holder: opens the fifo rw and sleeps, so there is always a writer.
setsid bash -c "exec 9<>$FIFO; exec sleep 2147483647" </dev/null >/dev/null 2>&1 &

if [ ! -x "$QEMU" ]; then
    log "ERROR: qemu binary not found/executable at $QEMU"; exit 1
fi
if [ ! -e "$ROOTFS/pineapple/pineapple" ]; then
    log "ERROR: firmware not found at $ROOTFS/pineapple/pineapple"; exit 1
fi

cd "$ROOTFS" || exit 1
log "launching firmware: $QEMU -L $ROOTFS ./pineapple/pineapple"
# The firmware builds the payload's PATH as "$PATH:/mmc/...", inheriting the
# PATH we hand to qemu here. Prepend DUCKY_BIN so launched payloads resolve the
# DuckyScript command scripts (LOG, LED, ALERT, ...). We also keep the usual
# host sbin dirs so network/system binaries (ip, iw, curl, nmap, ...) that
# payloads call run NATIVELY and reach the real hardware/network.
PAYLOAD_PATH="$DUCKY_BIN:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# exec so systemd tracks the qemu process as the service MainPID.
exec env GOMAXPROCS=1 GODEBUG=asyncpreemptoff=1 PAGER_INPUT_FIFO="$FIFO" \
    PATH="$PAYLOAD_PATH" "$QEMU" -L "$ROOTFS" ./pineapple/pineapple
