#!/bin/bash
# payload_env.sh - prepare the HOST so the qemu-user firmware can actually RUN
# WiFi Pineapple Pager payloads (DuckyScript + bash), faithfully.
#
# ============================= THE PROBLEM ====================================
# The firmware (MIPSel Go under qemu-user, NO chroot) launches a payload by:
#   1. writing /tmp/payload-XXXX.sh  (ABSOLUTE path -> lands on the HOST /tmp)
#   2. prepending a header that sources the Hak5 shell environment:
#         PATH="$PATH:/mmc/bin:/mmc/sbin:/mmc/usr/bin:/mmc/usr/sbin"
#         . /lib/hak5/commands.sh
#         . /lib/hak5/pineapple.sh
#         . /lib/pineapple/payloads.sh
#   3. exec'ing it with the HOST /bin/bash (aarch64), env={PAYLOAD_HOME,...},
#      stdin/stdout/stderr all = /dev/null.
#
# Because qemu's -L only rewrites the ELF loader path (not runtime open()),
# that host bash resolves ABSOLUTE paths against the HOST filesystem. So:
#   - /lib/pineapple/payloads.sh did not exist on the host  -> "No such file"
#   - the DuckyScript commands (LOG, ALERT, PROMPT, WAIT_FOR_BUTTON_PRESS, the
#     *_PICKER family, SPINNER, ...) were not in the host $PATH -> "command not
#     found" -> every payload exited 127 -> "experienced an error".
#
# ============================= THE MECHANISM =================================
# On the real Pager, each DuckyScript command is a small bash script in
# /usr/bin that POSTs to the firmware's LOCAL HTTP API over /tmp/api.sock via
# HAK5_API_POST (see /lib/hak5/commands.sh). The firmware serves that same
# /tmp/api.sock under qemu, so the mechanism works UNCHANGED on the host -- we
# only need the command scripts to be reachable and the sourced libs to exist.
#
# The stock rootfs already ships ~67 of these (LED, VIBRATE, UI_TAKEOVER,
# WIFI_*, WIGLE_*, ...). It is MISSING the UI/flow ones (LOG, ALERT,
# ERROR_DIALOG, PROMPT, CONFIRMATION_DIALOG, WAIT_FOR_INPUT,
# WAIT_FOR_BUTTON_PRESS, *_PICKER, SPINNER). We supply those, 1:1 against the
# firmware's /payload/interact/* endpoints.
#
# ============================= WHAT THIS DOES ================================
#   (a) /lib/pineapple -> rootfs/lib/pineapple        (so the 3rd source works)
#   (b) builds a host "ducky bin" dir that contains:
#         - symlinks to the stock rootfs DuckyScript scripts (fully faithful)
#         - the MISSING command scripts shipped alongside this port
#
#   (c) exposes that dir on the payload's ACTUAL PATH. IMPORTANT: the firmware
#       launches the payload with a CLEAN environment (only PAYLOAD_HOME /
#       _PAYLOAD_HOME; NO inherited PATH). The injected header is literally:
#           PATH="$PATH:/mmc/bin:/mmc/sbin:/mmc/usr/bin:/mmc/usr/sbin"
#       so with $PATH empty the ONLY real search dirs are /mmc/{bin,sbin,
#       usr/bin,usr/sbin}. We therefore materialize the commands under
#       /mmc/usr/bin (+ /mmc/bin) on the host -- the exact dirs bash searches.
#       (On the real device these commands live in /usr/bin; /mmc/* is the
#       SD-card overlay the header adds for user-installed payloads. Using
#       /mmc/usr/bin here is faithful to that search order.)
#
# Idempotent. Safe to re-run. Touches ONLY host-side glue, never the payloads.
set -u

PAGER_QEMU_ROOT="${PAGER_QEMU_ROOT:-/opt/pineapple-pager/qemu}"
ROOTFS="${ROOTFS:-$PAGER_QEMU_ROOT/rootfs}"
# Where the port ships the missing DuckyScript command scripts.
DUCKY_SRC="${DUCKY_SRC:-$PAGER_QEMU_ROOT/hak5-ducky-commands}"
# Host dir that ends up on the payload's PATH (built here).
DUCKY_BIN="${DUCKY_BIN:-$PAGER_QEMU_ROOT/hak5bin}"

log() { echo "[payload_env] $*" >&2; }

# -- (a) /lib/hak5 and /lib/pineapple must be resolvable on the HOST ----------
# /lib/hak5 is usually already linked by an earlier step; assert both.
for libdir in hak5 pineapple; do
    src="$ROOTFS/lib/$libdir"
    [ -e "$src" ] || { log "WARNING: $src missing in rootfs"; continue; }
    if [ ! -e "/lib/$libdir" ]; then
        ln -sfn "$src" "/lib/$libdir" 2>/dev/null || log "WARNING: could not link /lib/$libdir"
    elif [ -L "/lib/$libdir" ]; then
        ln -sfn "$src" "/lib/$libdir" 2>/dev/null || true
    else
        log "NOTE: /lib/$libdir exists and is not a symlink; leaving untouched"
    fi
done

# jshn.sh is sourced by commands.sh; it lives in the rootfs. Make it resolvable.
if [ -e "$ROOTFS/usr/share/libubox/jshn.sh" ] && [ ! -e /usr/share/libubox/jshn.sh ]; then
    mkdir -p /usr/share/libubox 2>/dev/null || true
    ln -sfn "$ROOTFS/usr/share/libubox/jshn.sh" /usr/share/libubox/jshn.sh 2>/dev/null || true
fi

# -- (b) build the host DuckyScript command dir ------------------------------
mkdir -p "$DUCKY_BIN"

# 1) Symlink EVERY stock DuckyScript command script from the rootfs (faithful:
#    these are Hak5's own scripts, unchanged). We only take UPPERCASE names
#    (the DuckyScript convention) that are shell scripts, from the usual bins.
for d in usr/bin bin usr/sbin sbin; do
    [ -d "$ROOTFS/$d" ] || continue
    for f in "$ROOTFS/$d"/*; do
        [ -f "$f" ] || continue
        name="$(basename "$f")"
        # UPPERCASE DuckyScript-style names only (A-Z, 0-9, _)
        case "$name" in
            *[!A-Z0-9_]*) continue ;;
        esac
        # must be a shell script (#!), not a MIPS ELF
        if head -c2 "$f" 2>/dev/null | grep -q '#!'; then
            # don't clobber a port-provided override (see step 2)
            [ -e "$DUCKY_SRC/$name" ] && continue
            ln -sfn "$f" "$DUCKY_BIN/$name" 2>/dev/null || true
        fi
    done
done

# 2) Install the port's MISSING command scripts (LOG, ALERT, ...). These take
#    precedence (copied as real executables so permissions are guaranteed).
if [ -d "$DUCKY_SRC" ]; then
    for f in "$DUCKY_SRC"/*; do
        [ -f "$f" ] || continue
        name="$(basename "$f")"
        install -m 0755 "$f" "$DUCKY_BIN/$name" 2>/dev/null || {
            cp -f "$f" "$DUCKY_BIN/$name" && chmod 0755 "$DUCKY_BIN/$name"
        }
    done
fi

# -- (c) expose the commands on the payload's ACTUAL search path -------------
# The firmware runs payloads with an EMPTY inherited PATH; the injected header
# expands to ":/mmc/bin:/mmc/sbin:/mmc/usr/bin:/mmc/usr/sbin". So the real
# search dirs are under /mmc. Mirror DUCKY_BIN into /mmc/usr/bin (primary) and
# /mmc/bin via a single symlink each per command. qemu-user resolves these
# absolute host paths directly.
for mmcbin in /mmc/usr/bin /mmc/bin; do
    mkdir -p "$mmcbin" 2>/dev/null || true
    for f in "$DUCKY_BIN"/*; do
        [ -e "$f" ] || continue
        ln -sfn "$f" "$mmcbin/$(basename "$f")" 2>/dev/null || true
    done
done

log "ready: DUCKY_BIN=$DUCKY_BIN ($(ls "$DUCKY_BIN" 2>/dev/null | wc -l) commands) -> /mmc/usr/bin, /lib/pineapple -> $(readlink -f /lib/pineapple 2>/dev/null)"
