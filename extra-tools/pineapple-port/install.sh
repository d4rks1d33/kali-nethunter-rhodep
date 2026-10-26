#!/bin/sh
# install.sh -- installer for the WiFi Pineapple port (FASE 4) on Kali/NetHunter.
#
# Part of: extra-tools/pineapple-port/
#
# WHAT IT DOES (all contained in /opt; the OS is NOT modified beyond a handful
# of well-defined, reversible symlinks + the systemd units, and apt deps):
#   1. apt deps (nginx, php-fpm, sqlite3, python3, iw, hostapd, dnsmasq, ...),
#      detecting the php-fpm version to wire the dedicated master.
#   2. compile src/pineapd + src/pineap (make) and install the native binaries:
#        pineapd-ng -> /usr/sbin/pineapd
#        pineap-ng  -> /usr/bin/pineap
#   3. copy panel/ -> /opt/pineapple/panel, compat/ -> /opt/pineapple/compat,
#      net/ -> /opt/pineapple/net, vendor/ -> /opt/pineapple/vendor, and seed
#      etc/ (uci store, DB, pineap.conf).
#   3b. install hostapd-mana (karma + PineAPE Enterprise/WPE) for the AP radio
#       (wlan1) via vendor/hostapd/build-mana.sh: `apt install hostapd-mana` on
#       Kali/NetHunter arm64, else built from source for the target arch.
#   3c. install the PineAPE Enterprise certs scripts + eap_user under
#       /opt/pineapple/etc/pineape (symlinked to /etc/pineape).
#   4. create the symlinks the panel/daemon hardcode:
#        /pineapple      -> /opt/pineapple/panel
#        /etc/pineapple  -> /opt/pineapple/etc
#        /etc/pineape    -> /opt/pineapple/etc/pineape
#        /usr/local/sbin/hostapd_cli -> compat/hostapd_cli (Hak5->mana wrapper)
#      and the init.d shims the panel calls:
#        /etc/init.d/{pineapd,dnsmasq,firewall,cron,sshd} -> init.d-dispatcher
#   5. install the systemd units (copied to /etc/systemd/system), daemon-reload,
#      and write a php-fpm binary drop-in. UNITS ARE NOT ENABLED (on-demand).
#   6. idempotent: re-running updates files in place without duplicating state.
#
# UNINSTALL:  ./install.sh --uninstall   (reverts everything; never touches the
#             phone's OS/network beyond removing what we added).
#
# DRY-RUN:    ./install.sh --dry-run     (prints what it would do, changes nothing)
#
# SAFE DESIGN re the phone's network:
#   The Pineapple network lives on a DEDICATED bridge (br-lan) + a DEDICATED nft
#   table + a DEDICATED dnsmasq/php-fpm/nginx master. Nothing here reconfigures
#   rmnet*, the phone's default route, resolv.conf, or the system firewall. The
#   stack only runs when you `systemctl start pineapple.target`.

set -eu

# ------------------------------------------------------------------- settings
PREFIX="/opt/pineapple"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="/etc/systemd/system"
DRY_RUN=0
DO_UNINSTALL=0
SKIP_APT=0
SKIP_BUILD=0

# NOTE: stock `hostapd` is intentionally NOT listed. The port uses hostapd-mana
# (karma + PineAPE Enterprise/WPE), installed separately by
# vendor/hostapd/build-mana.sh (step 3b). On Kali the hostapd-mana package
# provides /usr/sbin/hostapd and CONFLICTS with stock hostapd, so pre-installing
# stock hostapd here would make apt remove it again (or fail the transaction).
# libssl3 is the runtime for the shipped prebuilt hostapd-mana (NEEDED libssl.so.3
# + libcrypto.so.3). When hostapd-mana is installed via apt it pulls this itself,
# but the prebuilt-fallback path needs it present, so list it explicitly.
APT_DEPS="nginx-light php-fpm php-cli php-sqlite3 sqlite3 python3 at curl iw dnsmasq openssl ttyd libssl3 libpcap0.8 libsqlite3-0 libnl-3-200 libnl-genl-3-200"
# hostapd-mana (karma + PineAPE Enterprise) is installed by
# vendor/hostapd/build-mana.sh (apt on Kali, else from source). Build deps here
# also cover a from-source hostapd-mana build (openssl + libnl + git).
BUILD_DEPS="build-essential libpcap-dev libsqlite3-dev libnl-3-dev libnl-genl-3-dev libssl-dev pkg-config git"

UNITS="pineapple-firstboot.service pineapple-net.service pineapple-dnsmasq.service pineapple-php-fpm.service pineapple-nginx.service pineapd.service pineapple-ap.service pineape-creds.service pineapple-recon-ws.service pineapple.target"
INITD_SHIMS="pineapd dnsmasq firewall cron sshd"

# ------------------------------------------------------------------- helpers
log()  { echo "[install] $*"; }
warn() { echo "[install] WARNING: $*" >&2; }
die()  { echo "[install] ERROR: $*" >&2; exit 1; }

run() {
    # Execute (or print, in dry-run) a command.
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + $*"
    else
        "$@"
    fi
}

# dry-run-aware shell snippet (for redirections/globs we can't pass to run()).
sh_run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + sh -c: $*"
    else
        sh -c "$*"
    fi
}

need_root() {
    if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
    [ "$(id -u)" = "0" ] || die "must run as root (or use --dry-run)"
}

# ------------------------------------------------------------------- args
while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) DO_UNINSTALL=1 ;;
        --dry-run)   DRY_RUN=1 ;;
        --skip-apt)  SKIP_APT=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

# ------------------------------------------------------------------- php-fpm detect
detect_php_fpm() {
    # Return the php-fpm binary (e.g. php-fpm8.3). Prefer the newest.
    for b in /usr/sbin/php-fpm8.4 /usr/sbin/php-fpm8.3 /usr/sbin/php-fpm8.2 \
             /usr/sbin/php-fpm8.1 /usr/sbin/php-fpm7.4 /usr/sbin/php-fpm; do
        [ -x "$b" ] && { echo "$b"; return 0; }
    done
    # fall back to PATH lookup
    for v in 8.4 8.3 8.2 8.1 7.4; do
        command -v "php-fpm$v" >/dev/null 2>&1 && { command -v "php-fpm$v"; return 0; }
    done
    command -v php-fpm >/dev/null 2>&1 && { command -v php-fpm; return 0; }
    return 1
}

# =====================================================================
#  UNINSTALL
# =====================================================================
do_uninstall() {
    need_root
    log "uninstalling WiFi Pineapple port"

    # stop the stack if running (best-effort).
    run systemctl stop pineapple.target 2>/dev/null || true
    for u in $UNITS; do
        run systemctl stop "$u" 2>/dev/null || true
        run systemctl disable "$u" 2>/dev/null || true
    done

    # remove units + drop-ins
    for u in $UNITS; do
        run rm -f "$UNIT_DIR/$u"
    done
    run rm -rf "$UNIT_DIR/pineapple-php-fpm.service.d"
    run systemctl daemon-reload 2>/dev/null || true

    # remove init.d shims we created (only if they point at our dispatcher)
    for s in $INITD_SHIMS; do
        if [ -L "/etc/init.d/$s" ]; then
            tgt="$(readlink "/etc/init.d/$s" 2>/dev/null || true)"
            case "$tgt" in
                "$PREFIX/compat/init.d-dispatcher") run rm -f "/etc/init.d/$s" ;;
                *) warn "/etc/init.d/$s does not point at our dispatcher; leaving it" ;;
            esac
        fi
    done

    # remove binaries we installed
    run rm -f /usr/sbin/pineapd /usr/bin/pineap

    # remove absolute-path shims we created (only if they point into our prefix)
    for pair in "/usr/sbin/resetssids:resetssids" "/sbin/wifi:wifi" "/usr/local/sbin/hostapd_cli:hostapd_cli"; do
        dest="${pair%%:*}"; shim="${pair##*:}"
        if [ -L "$dest" ]; then
            tgt="$(readlink "$dest" 2>/dev/null || true)"
            case "$tgt" in
                "$PREFIX/compat/$shim") run rm -f "$dest" ;;
                *) warn "$dest does not point at our shim; leaving it" ;;
            esac
        fi
    done

    # restore the real ttyd if we wrapped it
    if [ -L /usr/bin/ttyd ] && [ -e /usr/bin/ttyd.real ]; then
        tgt="$(readlink /usr/bin/ttyd 2>/dev/null || true)"
        case "$tgt" in
            "$PREFIX/compat/ttyd") run rm -f /usr/bin/ttyd; run mv /usr/bin/ttyd.real /usr/bin/ttyd ;;
        esac
    fi

    # remove the hostapd-mana name links we may have created (NOT the apt package)
    for b in /usr/sbin/hostapd-mana /usr/sbin/hostapd_cli-mana; do
        [ -L "$b" ] && run rm -f "$b"
    done

    # remove symlinks we created (only if symlinks into our prefix)
    for ln in /pineapple /etc/pineapple /etc/pineape; do
        if [ -L "$ln" ]; then
            run rm -f "$ln"
        elif [ -e "$ln" ]; then
            warn "$ln exists and is not a symlink; leaving it untouched"
        fi
    done

    # remove /opt/pineapple entirely
    run rm -rf "$PREFIX"

    # board_name shim we may have written
    if [ -f /tmp/sysinfo/board_name ]; then
        run rm -f /tmp/sysinfo/board_name
    fi

    log "uninstall complete. The OS, its network, firewall and DNS were untouched."
    log "apt packages were NOT removed (they may be used elsewhere); remove manually if desired:"
    log "  apt remove $APT_DEPS"
    exit 0
}

[ "$DO_UNINSTALL" -eq 1 ] && do_uninstall

# =====================================================================
#  INSTALL
# =====================================================================
need_root

# --- 1. apt deps -----------------------------------------------------------
if [ "$SKIP_APT" -eq 0 ]; then
    if command -v apt-get >/dev/null 2>&1; then
        log "installing apt dependencies"
        run apt-get update
        # shellcheck disable=SC2086
        run apt-get install -y $APT_DEPS $BUILD_DEPS
        run systemctl enable --now atd 2>/dev/null || warn "could not enable atd"
    else
        warn "apt-get not found; skipping dependency install (assuming deps present)"
    fi
else
    log "--skip-apt: not installing dependencies"
fi

# --- 2. compile + install native binaries ----------------------------------
if [ "$SKIP_BUILD" -eq 0 ]; then
    log "compiling pineapd-ng"
    # Build pineapd with the SSID DB pointed at the port's DB path so --initdbs
    # creates the schema in /opt/pineapple/etc/pineapple.db by default.
    run make -C "$SELF_DIR/src/pineapd" clean
    run make -C "$SELF_DIR/src/pineapd" \
        EXTRA_DEFS='-DPINEAPD_SSID_DB=\"/opt/pineapple/etc/pineapple.db\" -DPINEAPD_RECON_DB=\"/opt/pineapple/etc/pineapple.db\" -DPINEAPD_LOG_DB=\"/opt/pineapple/etc/pineapple.db\"'
    log "compiling pineap-ng"
    run make -C "$SELF_DIR/src/pineap" clean
    run make -C "$SELF_DIR/src/pineap"
else
    log "--skip-build: not compiling"
fi

log "installing native binaries"
run install -D -m 0755 "$SELF_DIR/src/pineapd/pineapd-ng" /usr/sbin/pineapd
run install -D -m 0755 "$SELF_DIR/src/pineap/pineap-ng"   /usr/bin/pineap

# --- 3. copy the tree into /opt/pineapple ----------------------------------
log "installing files into $PREFIX"
run mkdir -p "$PREFIX" "$PREFIX/etc" "$PREFIX/etc/uci" "$PREFIX/run" /var/log/pineapple /run/php

# compat, net, vendor: overwrite in place (idempotent, no user content there).
run rm -rf "$PREFIX/compat" "$PREFIX/net" "$PREFIX/vendor"
run cp -a "$SELF_DIR/compat" "$PREFIX/compat"
run cp -a "$SELF_DIR/net"    "$PREFIX/net"
run cp -a "$SELF_DIR/vendor" "$PREFIX/vendor"

# panel: DO NOT wipe it wholesale -- community modules the user installs from the
# panel's Module Manager (EvilPortal, Terminal, ...) and their data (/root/portals
# etc. is separate, but modules/<X> live under panel/modules/) must survive a
# reinstall. Overlay the repo's panel on top instead, preserving any extra
# module directories that aren't part of this port.
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + overlay $SELF_DIR/panel -> $PREFIX/panel (preserving extra modules)"
elif [ -d "$PREFIX/panel" ]; then
    # Update the core files from the repo but keep user-installed modules.
    if command -v rsync >/dev/null 2>&1; then
        rsync -a "$SELF_DIR/panel/" "$PREFIX/panel/"
    else
        cp -a "$SELF_DIR/panel/." "$PREFIX/panel/"
    fi
else
    cp -a "$SELF_DIR/panel" "$PREFIX/panel"
fi
run chmod -R +x "$PREFIX/compat" 2>/dev/null || true
sh_run "chmod +x '$PREFIX'/net/*.sh '$PREFIX'/net/pineape-creds-import '$PREFIX'/vendor/hostapd/*.sh 2>/dev/null || true"

# Repair PRESERVED community modules (EvilPortal, PortalAuth, ...) so they work
# on this port: rewrite the old LAN IP (172.16.42.x -> 172.16.84.x), fix EvilPortal
# portal descriptors and the Live Preview. Idempotent; never touches captured data.
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + $PREFIX/net/fix-community-modules.sh (repair preserved community modules)"
else
    PINEAPPLE_PREFIX="$PREFIX" sh "$PREFIX/net/fix-community-modules.sh" \
        || warn "community-module fixups reported an issue (non-fatal)"
fi

# --- 3b. patched hostapd (hostapd-mana: karma + PineAPE Enterprise/WPE) -----
# Install hostapd-mana for the AP radio (wlan1). On Kali/NetHunter arm64 this is
# `apt install hostapd-mana`; elsewhere it builds from source for the target.
# The Hak5-vocabulary wrapper (compat/hostapd_cli) talks to this mana hostapd.
if [ "$SKIP_BUILD" -eq 0 ]; then
    log "installing hostapd-mana (karma + PineAPE Enterprise)"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  + $PREFIX/vendor/hostapd/build-mana.sh install"
    else
        "$PREFIX/vendor/hostapd/build-mana.sh" install \
            || warn "hostapd-mana install failed; install it manually (apt install hostapd-mana) then re-run"
    fi
else
    log "--skip-build: not installing hostapd-mana (expecting /usr/sbin/hostapd-mana present)"
fi

# --- 3c. PineAPE Enterprise: certs scripts + eap_user ----------------------
# /etc/pineape/{certs,hostapd-pineape.eap_user}. The panel's generate/clear-cert
# actions call /etc/pineape/certs/{configure,bootstrap,clean}.sh; the Enterprise
# VAP's hostapd config points at /etc/pineape/certs/{ca.pem,server.crt,server.key}
# and /etc/pineape/hostapd-pineape.eap_user. We install into $PREFIX and symlink
# /etc/pineape -> $PREFIX/etc/pineape (certs are generated at runtime, so don't
# clobber a live certs dir on re-install).
log "installing PineAPE Enterprise certs + eap_user"
run mkdir -p "$PREFIX/etc/pineape/certs"
# eap_user + cert config templates (safe to overwrite; configs, not live certs)
run install -m 0644 "$SELF_DIR/etc/pineape/hostapd-pineape.eap_user" \
    "$PREFIX/etc/pineape/hostapd-pineape.eap_user"
sh_run "cp -a '$SELF_DIR'/etc/pineape/certs/*.cnf '$SELF_DIR'/etc/pineape/certs/*.master '$SELF_DIR'/etc/pineape/certs/xpextensions '$SELF_DIR'/etc/pineape/certs/dh '$PREFIX'/etc/pineape/certs/ 2>/dev/null || true"
sh_run "install -m 0755 '$SELF_DIR'/etc/pineape/certs/configure.sh '$SELF_DIR'/etc/pineape/certs/bootstrap.sh '$SELF_DIR'/etc/pineape/certs/clean.sh '$SELF_DIR'/etc/pineape/certs/reset.sh '$PREFIX'/etc/pineape/certs/ 2>/dev/null || true"

# seeds: keep a pristine uci.seed for firstboot, and seed the live store only
# if empty (don't clobber an existing configured store on re-install).
run rm -rf "$PREFIX/etc/uci.seed"
run cp -a "$SELF_DIR/etc/uci" "$PREFIX/etc/uci.seed"
sh_run "for f in '$SELF_DIR'/etc/uci/*; do b=\$(basename \"\$f\"); [ -e '$PREFIX'/etc/uci/\"\$b\" ] || cp -a \"\$f\" '$PREFIX'/etc/uci/\"\$b\"; done"

# DB + pineap.conf seeds (don't overwrite a live DB)
run install -m 0644 "$SELF_DIR/etc/pineapple.db.seed" "$PREFIX/etc/pineapple.db.seed"
sh_run "[ -e '$PREFIX'/etc/pineapple.db ] || cp -a '$SELF_DIR'/etc/pineapple.db.seed '$PREFIX'/etc/pineapple.db"
run install -m 0644 "$SELF_DIR/etc/pineap.conf.seed" "$PREFIX/etc/pineap.conf.seed"

# --- 3d. runtime dirs. php-fpm/nginx run as ROOT (like the real Pineapple), so
#         the panel can create /root/portals, run the ttyd terminal, write DBs,
#         and do everything the firmware expects without permission adaptations.
#         (php-fpm is launched with -R to allow the root pool.)
run mkdir -p "$PREFIX/etc/sessions" "$PREFIX/etc/uci"
# Everything stays root-owned; no chown needed.

# --- 4. symlinks the panel/daemon hardcode ---------------------------------
log "creating symlinks (/pineapple, /etc/pineapple) + init.d shims"
# /pineapple -> panel (doc-root)
if [ -L /pineapple ] || [ ! -e /pineapple ]; then
    run ln -sfn "$PREFIX/panel" /pineapple
else
    warn "/pineapple exists and is not a symlink; NOT overwriting"
fi
# /etc/pineapple -> etc (DBs, flags). The panel opens /etc/pineapple/pineapple.db
# AND /etc/pineapple/pineape.db (the Enterprise creds DB). Both live under
# $PREFIX/etc.
if [ -L /etc/pineapple ] || [ ! -e /etc/pineapple ]; then
    run ln -sfn "$PREFIX/etc" /etc/pineapple
else
    warn "/etc/pineapple exists and is not a symlink; NOT overwriting"
fi
# /etc/pineape -> etc/pineape (certs + eap_user). The panel's cert scripts and
# the Enterprise hostapd config both hardcode /etc/pineape/*.
if [ -L /etc/pineape ] || [ ! -e /etc/pineape ]; then
    run ln -sfn "$PREFIX/etc/pineape" /etc/pineape
else
    warn "/etc/pineape exists and is not a symlink; NOT overwriting"
fi

# Seed an empty pineape.db with the chalresp/basic schema (don't clobber a live
# one). pineape-creds-import also creates it on first run, but seeding here means
# the panel's Enterprise view works immediately (empty) after install.
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + seed $PREFIX/etc/pineape.db (chalresp/basic schema)"
else
    [ -e "$PREFIX/etc/pineape.db" ] || \
        "$PREFIX/net/pineape-creds-import" --db "$PREFIX/etc/pineape.db" --log /dev/null --once >/dev/null 2>&1 \
        || warn "could not seed pineape.db (python3 missing?)"
fi

# init.d shims -> dispatcher
run mkdir -p /etc/init.d
for s in $INITD_SHIMS; do
    run ln -sfn "$PREFIX/compat/init.d-dispatcher" "/etc/init.d/$s"
done

# absolute-path shims: a few panel call sites invoke OpenWrt tools by FULL path
# (so the php-fpm PATH does not help). Symlink those exact paths to our shims:
#   modules/PineAP/api/module.php:424  exec('/usr/sbin/resetssids', ...)
#   api/Setup.php:133/160              exec('/sbin/wifi ...')
#
# hostapd_cli: pineap-ng runs `hostapd_cli -i wlan1 karma_*` (AP radio) and the
# panel runs `hostapd_cli -i wlan1_0 pineape_*` (Enterprise VAP). Our Hak5->mana
# translating wrapper MUST win over the stock hostapd_cli the hostapd-mana apt
# package installs at /usr/sbin/hostapd_cli -- otherwise karma_enable reaches
# mana untranslated and it replies "Unknown command 'karma_enable'".
#   - pineap-ng now calls the wrapper by ABSOLUTE path (PINEAP_HOSTAPD_CLI,
#     default /usr/local/sbin/hostapd_cli), so it is PATH-independent.
#   - The panel resolves bare `hostapd_cli` via the php-fpm pool PATH, which puts
#     /opt/pineapple/compat first.
#   - Interactive shells vary, so we ALSO claim BOTH /usr/local/sbin/hostapd_cli
#     AND /usr/sbin/hostapd_cli for the wrapper (build-mana.sh moved the stock
#     binary aside to hostapd_cli.stock-mana first). The real mana cli stays as
#     hostapd_cli-mana, which the wrapper calls.
# Format: "<shim-in-compat>:<absolute-path-the-panel-uses>"
run mkdir -p /usr/sbin /sbin /usr/local/sbin
for pair in "resetssids:/usr/sbin/resetssids" "wifi:/sbin/wifi" \
            "hostapd_cli:/usr/local/sbin/hostapd_cli" "hostapd_cli:/usr/sbin/hostapd_cli"; do
    shim="${pair%%:*}"; dest="${pair##*:}"
    if [ -L "$dest" ] || [ ! -e "$dest" ]; then
        run ln -sfn "$PREFIX/compat/$shim" "$dest"
    else
        warn "$dest exists and is not a symlink; NOT overwriting (run build-mana.sh to retire a stock hostapd_cli)"
    fi
done

# --- 4b. ttyd wrapper: the Terminal module runs `ttyd ... /bin/login`, but Kali's
#         /bin/login needs a getty TTY and dies inside ttyd ("Press Enter to
#         reconnect"). Move the apt ttyd to ttyd.real and drop in our wrapper
#         (compat/ttyd) that swaps /bin/login for an interactive root shell.
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + install ttyd wrapper (/usr/bin/ttyd -> compat/ttyd, real -> ttyd.real)"
elif [ -e /usr/bin/ttyd ] && [ ! -L /usr/bin/ttyd ]; then
    # /usr/bin/ttyd is the real apt binary -> stash it once.
    [ -e /usr/bin/ttyd.real ] || run mv /usr/bin/ttyd /usr/bin/ttyd.real
    run ln -sfn "$PREFIX/compat/ttyd" /usr/bin/ttyd
elif [ -L /usr/bin/ttyd ]; then
    # already wrapped (symlink to our compat/ttyd) -> ensure it points at us.
    run ln -sfn "$PREFIX/compat/ttyd" /usr/bin/ttyd
fi

# --- 5. systemd units ------------------------------------------------------
log "installing systemd units (NOT enabled; on-demand)"
for u in $UNITS; do
    run install -D -m 0644 "$SELF_DIR/systemd/$u" "$UNIT_DIR/$u"
done

# php-fpm binary drop-in for the dedicated master
PHP_FPM_BIN="$(detect_php_fpm || true)"
if [ -n "$PHP_FPM_BIN" ]; then
    log "detected php-fpm binary: $PHP_FPM_BIN"
    run mkdir -p "$UNIT_DIR/pineapple-php-fpm.service.d"
    sh_run "cat > '$UNIT_DIR/pineapple-php-fpm.service.d/10-binary.conf' <<EOF
[Service]
ExecStart=
ExecStart=$PHP_FPM_BIN -R --nodaemonize --fpm-config /opt/pineapple/net/php-fpm-pineapple-main.conf
EOF"
else
    warn "no php-fpm binary found; pineapple-php-fpm.service default (php-fpm8.3) may not exist."
    warn "install php-fpm and re-run, or edit the unit's ExecStart."
fi

run systemctl daemon-reload

log "install complete."
log ""
log "  Start the whole stack on demand:   systemctl start pineapple.target"
log "  Access the panel:                  http://172.16.84.1:1471"
log "  Stop everything (reverts net):     systemctl stop  pineapple.target"
log "  Uninstall:                         $SELF_DIR/install.sh --uninstall"
log ""
log "Units are NOT enabled at boot, so the phone boots normally."
