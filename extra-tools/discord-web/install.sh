#!/bin/sh
# discord-web: Discord's web client as a native fullscreen QtWebEngine app.
# Run as root. Chroot-aware, idempotent. Optional -- not needed to boot or place
# a call.
#
# QtWebEngine IS Chromium, so discord.com/app works exactly as in Chrome (no
# box64, no Electron). Hardware GL via freedreno (the wrapper sets --use-gl=egl)
# so it is not the CPU-bound SwiftShader slideshow; an Android UA + device scale
# factor give the phone (single-panel) layout.
#
# The Discord session (cookies/localStorage) is created by the app at runtime in
# ~/.local/share/discord-web/ -- this installer never touches it, and it must
# never be committed to the repo.
set -e
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "run me as root" >&2; exit 1; }

log() { echo "discord-web: $*"; }
LIVE=0
[ -d /run/systemd/system ] && LIVE=1

# --- deps (live only; fail open with no network) -----------------------------
# QtWebEngine (Chromium) + PyQt6 binding + Qt Wayland platform.
if [ "$LIVE" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
	apt-get install -y --no-install-recommends \
		python3-pyqt6 python3-pyqt6.qtwebengine qt6-wayland >/dev/null 2>&1 \
		|| log "could not install Qt deps (no network?)"
fi

WRAPPER=/usr/local/bin/discord-web
APP=/usr/local/share/rhodep/discord-web/discord-web.py
DESKTOP=/usr/share/applications/discord-web.desktop
ICON=/usr/share/icons/hicolor/scalable/apps/discord.svg
PROTECT=/usr/local/sbin/rhodep-protect-files

# release before overwrite so a re-run works against immutable files
[ -x "$PROTECT" ] && "$PROTECT" release "$WRAPPER" "$APP" "$DESKTOP" "$ICON" 2>/dev/null || true

install -D -m 0644 "$here/discord-web.py"      "$APP"
install -D -m 0755 "$here/discord-web"         "$WRAPPER"
install -D -m 0644 "$here/discord-web.desktop" "$DESKTOP"
install -D -m 0644 "$here/discord.svg"         "$ICON"

# refresh caches so it shows in the app grid
update-desktop-database /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

# protect our files (fails open if apply-holds.sh has not run yet)
if [ -x "$PROTECT" ]; then
	"$PROTECT" register discord-web 0755 "$WRAPPER" 2>/dev/null || true
	"$PROTECT" register discord-web 0644 "$APP" "$DESKTOP" "$ICON" 2>/dev/null || true
fi

log "installed. Look for 'Discord' in the app drawer."
log "  first run: log in (email/password -- the QR panel is hidden on a narrow"
log "  screen). The session persists in ~/.local/share/discord-web/."
