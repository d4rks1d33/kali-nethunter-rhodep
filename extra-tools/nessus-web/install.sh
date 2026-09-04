#!/bin/sh
# nessus-web: Nessus Professional as a native fullscreen QtWebEngine app.
# Run as root. Idempotent.
set -e
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "run me as root" >&2; exit 1; }

log() { echo "nessus-web: $*"; }
LIVE=0
[ -d /run/systemd/system ] && LIVE=1

if [ "$LIVE" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends \
        python3-pyqt6 python3-pyqt6.qtwebengine qt6-wayland >/dev/null 2>&1 \
        || log "could not install Qt deps (no network?)"
fi

WRAPPER=/usr/local/bin/nessus-web
APP=/usr/local/share/rhodep/nessus-web/nessus-web.py
DESKTOP=/usr/share/applications/nessus-web.desktop
ICON=/usr/share/icons/hicolor/scalable/apps/nessus-web.svg
PROTECT=/usr/local/sbin/rhodep-protect-files

[ -x "$PROTECT" ] && "$PROTECT" release "$WRAPPER" "$APP" "$DESKTOP" "$ICON" 2>/dev/null || true

install -D -m 0644 "$here/nessus-web.py"      "$APP"
install -D -m 0755 "$here/nessus-web"          "$WRAPPER"
install -D -m 0644 "$here/nessus-web.desktop"  "$DESKTOP"
install -D -m 0644 "$here/nessus-web.svg"      "$ICON"

update-desktop-database /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

if [ -x "$PROTECT" ]; then
    "$PROTECT" register nessus-web 0755 "$WRAPPER"  2>/dev/null || true
    "$PROTECT" register nessus-web 0644 "$APP" "$DESKTOP" "$ICON" 2>/dev/null || true
fi

log "installed. Tap 'Nessus' in the app drawer."
log "  Nessus must be running (docker start nessus) before opening the app."
log "  If it is not up yet, the app shows a waiting screen and retries."
