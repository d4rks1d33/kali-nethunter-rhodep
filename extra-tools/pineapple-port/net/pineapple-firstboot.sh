#!/bin/sh
# pineapple-firstboot.sh -- idempotent first-boot provisioning.
#
# Part of: extra-tools/pineapple-port/net/   (FASE 4)
# Invoked by: pineapple-firstboot.service (Type=oneshot).
#
# Creates runtime dirs, seeds the UCI store + DBs + /tmp/pineap.conf, and marks
# /opt/pineapple/.provisioned. Safe to run more than once (every step guards
# against clobbering existing state).

set -eu

PREFIX="${PINEAPPLE_PREFIX:-/opt/pineapple}"
export PINEAPPLE_UCI_DIR="${PINEAPPLE_UCI_DIR:-$PREFIX/etc/uci}"
PINEAPD="${PINEAPD:-/usr/sbin/pineapd}"
GENCFG="${GENCFG:-$PREFIX/net/gen-pineap-config.sh}"

log() { echo "[firstboot] $*"; }

# --- 1. runtime dirs -------------------------------------------------------
log "creating runtime directories"
mkdir -p "$PREFIX/etc/uci" \
         "$PREFIX/etc/sessions" \
         "$PREFIX/run" \
         /var/log/pineapple \
         /run/php \
         /root/portals
chmod 0750 "$PREFIX/etc/sessions" 2>/dev/null || true
# EvilPortal stores its captive-portal library in /root/portals (the firmware's
# "internal" storage location). php-fpm runs as root so it can read/write here.
chmod 0755 /root/portals 2>/dev/null || true

# --- 2. seed the UCI store (only if a package file is missing) --------------
# The installer copies etc/uci/* into $PREFIX/etc/uci; if a fresh install left
# the dir empty, nothing to seed here -- the uci shim + defaults still work.
for pkg in pineap wireless firewall system network landingpage reporting; do
    if [ ! -e "$PINEAPPLE_UCI_DIR/$pkg" ] && [ -e "$PREFIX/etc/uci.seed/$pkg" ]; then
        log "seeding UCI package $pkg"
        cp -a "$PREFIX/etc/uci.seed/$pkg" "$PINEAPPLE_UCI_DIR/$pkg"
    fi
done

# --- 3. panel SQLite DB (auth + tables) ------------------------------------
# The panel opens /etc/pineapple/pineapple.db (symlink -> $PREFIX/etc). Seed it
# from the shipped seed if it does not exist yet.
if [ ! -e "$PREFIX/etc/pineapple.db" ]; then
    if [ -e "$PREFIX/etc/pineapple.db.seed" ]; then
        log "seeding panel DB from pineapple.db.seed"
        cp -a "$PREFIX/etc/pineapple.db.seed" "$PREFIX/etc/pineapple.db"
    else
        log "no DB seed found; panel will create its own auth table on first login"
    fi
fi

# --- 4. SQLite schema for the daemon (recon/log/ssids) ---------------------
# pineapd --initdbs creates CREATE TABLE IF NOT EXISTS for all daemon tables in
# the DB path from the config, so render the config first, then init.
if [ -x "$GENCFG" ]; then
    log "rendering /tmp/pineap.conf"
    "$GENCFG" /tmp/pineap.conf
fi
if [ -x "$PINEAPD" ]; then
    log "initialising daemon DB schema (pineapd --initdbs)"
    "$PINEAPD" /tmp/pineap.conf --initdbs || log "WARNING: pineapd --initdbs returned non-zero"
fi

# --- 5. board_name shim (panel reads /tmp/sysinfo/board_name) --------------
if [ ! -e /tmp/sysinfo/board_name ]; then
    mkdir -p /tmp/sysinfo
    echo "pineapple-g82" > /tmp/sysinfo/board_name
fi

# --- 6. mark provisioned ---------------------------------------------------
date -u +%Y-%m-%dT%H:%M:%SZ > "$PREFIX/.provisioned"
log "provisioning complete"
exit 0
