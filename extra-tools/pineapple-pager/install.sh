#!/bin/sh
# install.sh -- install the WiFi Pineapple Pager PORT into /opt/pineapple-pager.
#
# Coexists with the WiFi Pineapple NANO port (/opt/pineapple). Nothing is enabled
# at boot; start on demand with `systemctl start pager.target`. Reverts cleanly.
#
#   sudo sh install.sh              # build + install
#   sudo sh install.sh --uninstall # remove (reverts network too)
#
# Deps (Kali/NetHunter aarch64): build-essential libpcap-dev libsqlite3-dev
#   libnl-3-dev libnl-genl-3-dev python3 python3-msgpack dnsmasq
set -eu

PREFIX=/opt/pineapple-pager
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

log() { echo "[pager-install] $*"; }

if [ "${1:-}" = "--uninstall" ]; then
    log "stopping + disabling pager stack"
    systemctl stop pager.target 2>/dev/null || true
    for u in pager-server pineapd-pager pager-dnsmasq pager-net; do
        systemctl stop "$u" 2>/dev/null || true
        systemctl disable "$u" 2>/dev/null || true
        rm -f "/etc/systemd/system/$u.service"
    done
    rm -f /etc/systemd/system/pager.target
    systemctl daemon-reload 2>/dev/null || true
    # kiosk launcher: .desktop + icon
    rm -f /usr/share/applications/wifi-pineapple-pager.desktop
    rm -f /usr/share/icons/hicolor/scalable/apps/wifi-pineapple-pager.svg
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database /usr/share/applications 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && \
        gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
    # best-effort network revert then remove the tree
    [ -x "$PREFIX/net/pager-net.sh" ] && sh "$PREFIX/net/pager-net.sh" down 2>/dev/null || true
    rm -rf "$PREFIX"
    log "uninstalled. The OS, its network and firewall were untouched."
    exit 0
fi

# ---- 1. deps -----------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
    log "installing build deps (apt)"
    apt-get install -y --no-install-recommends \
        build-essential libpcap-dev libsqlite3-dev libnl-3-dev libnl-genl-3-dev \
        python3 python3-msgpack dnsmasq 2>/dev/null || \
        log "WARN: some apt deps failed; ensure gcc/libpcap/libsqlite3/libnl/python3-msgpack are present"
fi

# ---- 2. tree -----------------------------------------------------------------
log "installing into $PREFIX"
mkdir -p "$PREFIX"/bin "$PREFIX"/run "$PREFIX"/etc /var/log/pineapple-pager
# Overwrite in place (idempotent). rm first: `cp -a dir dir` would otherwise
# nest a copy INSIDE an existing dir instead of replacing it. run/ (user data)
# lives elsewhere and is NOT touched here.
rm -rf "$PREFIX/net" "$PREFIX/panel" "$PREFIX/frontend" "$PREFIX/vendor" "$PREFIX/docs" "$PREFIX/app"
cp -a "$SELF_DIR/net"      "$PREFIX/net"
cp -a "$SELF_DIR/panel"    "$PREFIX/panel"
cp -a "$SELF_DIR/frontend" "$PREFIX/frontend"
cp -a "$SELF_DIR/vendor"   "$PREFIX/vendor"
cp -a "$SELF_DIR/docs"     "$PREFIX/docs" 2>/dev/null || true
[ -f "$SELF_DIR/etc/pineapd.conf" ] && cp "$SELF_DIR/etc/pineapd.conf" "$PREFIX/etc/pineapd.conf"
chmod +x "$PREFIX"/net/*.sh 2>/dev/null || true

# ---- community content: themes, ringtones, payloads -------------------------
# All the Hak5 community themes (54), ringtones (101) and payloads (199) ship
# with the port. themes/ feed the real-display renderer; ringtones/ play on the
# phone speaker; payloads/ are runnable bash scripts (recon/alerts/user).
# These are LARGE; copy without deleting user-added content under payloads/user.
log "installing community content (themes / ringtones / payloads)"
mkdir -p "$PREFIX"/community/themes "$PREFIX"/community/ringtones \
         "$PREFIX"/community/payloads
if [ -d "$SELF_DIR/community/themes" ]; then
    rm -rf "$PREFIX/community/themes"
    cp -a "$SELF_DIR/community/themes" "$PREFIX/community/themes"
fi
if [ -d "$SELF_DIR/community/ringtones" ]; then
    rm -rf "$PREFIX/community/ringtones"
    cp -a "$SELF_DIR/community/ringtones" "$PREFIX/community/ringtones"
fi
# payloads: merge (keep any user-created ones), overwrite the shipped library.
if [ -d "$SELF_DIR/community/payloads" ]; then
    cp -a "$SELF_DIR/community/payloads/." "$PREFIX/community/payloads/"
fi
# runtime payload tree the daemon/runner reads (user loot/alerts live here too)
mkdir -p "$PREFIX"/payloads/alerts "$PREFIX"/payloads/recon "$PREFIX"/payloads/user

# ---- 3. build the daemon -----------------------------------------------------
log "building pineapd-pager (native aarch64)"
make -C "$SELF_DIR/src/pineapd" clean >/dev/null 2>&1 || true
if make -C "$SELF_DIR/src/pineapd"; then
    cp "$SELF_DIR/src/pineapd/pineapd-pager" "$PREFIX/bin/pineapd-pager"
    log "pineapd-pager installed -> $PREFIX/bin/pineapd-pager"
else
    log "ERROR: build failed. Fix deps and re-run."
    exit 1
fi

# ---- 4. systemd units (NOT enabled; on-demand) -------------------------------
log "installing systemd units (on-demand, not enabled at boot)"
for u in pager-net pager-dnsmasq pineapd-pager pager-server; do
    cp "$SELF_DIR/systemd/$u.service" "/etc/systemd/system/$u.service"
done
cp "$SELF_DIR/systemd/pager.target" "/etc/systemd/system/pager.target"
systemctl daemon-reload

# ---- 5. app nativa kiosk (.desktop + launcher + icono) -----------------------
log "installing kiosk app (launcher + .desktop + icon)"
# 5a. script launcher
mkdir -p "$PREFIX/app"
cp "$SELF_DIR/app/pager-kiosk.sh" "$PREFIX/app/pager-kiosk.sh"
cp "$SELF_DIR/app/pager-icon.svg" "$PREFIX/app/pager-icon.svg"
chmod +x "$PREFIX/app/pager-kiosk.sh"
# 5b. icono en el tema hicolor (scalable)
install -Dm644 "$SELF_DIR/app/pager-icon.svg" \
    /usr/share/icons/hicolor/scalable/apps/wifi-pineapple-pager.svg
# 5c. .desktop en el menu de apps
install -Dm644 "$SELF_DIR/app/pager.desktop" \
    /usr/share/applications/wifi-pineapple-pager.desktop
# 5d. refrescar caches de escritorio (best-effort)
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database /usr/share/applications 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true

log "install complete."
log ""
log "  Start the Pager stack:   systemctl start pager.target"
log "  Open the virtual Pager:  http://172.16.52.1:1471   (or your bind IP:1471)"
log "  Native kiosk app:        launch 'WiFi Pineapple Pager' from the app grid"
log "                           (or run /opt/pineapple-pager/app/pager-kiosk.sh)"
log "  Stop everything:         systemctl stop  pager.target"
log "  Uninstall:               sh $SELF_DIR/install.sh --uninstall"
log ""
log "  Coexists with the NANO port (/opt/pineapple). Use one or the other."
