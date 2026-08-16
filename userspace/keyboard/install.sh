#!/bin/sh
# Terminal keys for the Plasma Mobile keyboard on motorola-rhodep.
#
# Adds a third page to the symbols layout with Esc, Tab, the arrows and real
# Ctrl combinations, so the terminal is usable without an external keyboard.
# Reach it with the &123 key, then 1/3 twice.
#
# Two things about this are not obvious, and both cost an attempt each:
#
#  1. plasma-keyboard does NOT read /usr/share/plasma/keyboard/layouts. That
#     directory belongs to qml6-module-qtquick-virtualkeyboard, while
#     plasma-keyboard prefers the layouts compiled into its own qrc - see
#     "prefer :/qt/qml/org/kde/plasma/keyboard/" in the binary. A symbols.qml
#     dropped there does nothing at all. The way in is
#     QT_VIRTUALKEYBOARD_LAYOUT_PATH, and that replaces the whole layout set,
#     so the stock tree is copied first and one file in it is changed.
#
#  2. The variable has to reach the process KWin starts, and editing
#     Exec= in Plasma's own .desktop file is not enough - KWin keeps launching
#     the binary it already knows. It needs a .desktop file of our own that
#     kwinrc points at, and KWin reads that setting ONLY at startup, so the
#     change takes effect on the next login.
#
# Usage:  sudo ./install.sh          then log out and back in
#         sudo ./install.sh --revert
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DEST=/usr/local/share/rhodep-keyboard/layouts
STOCK_LAYOUTS=/usr/share/plasma/keyboard/layouts
WRAPPER=/usr/local/bin/rhodep-plasma-keyboard
STOCK_DESKTOP=/usr/share/applications/org.kde.plasma.keyboard.desktop
OUR_DESKTOP=/usr/share/applications/rhodep-terminal-keyboard.desktop
PROTECT=/usr/local/sbin/rhodep-protect-files

[ "$(id -u)" = 0 ] || { echo "run me as root" >&2; exit 1; }

# The config belongs to the desktop user, not to root.
user=${SUDO_USER:-kali}
id "$user" >/dev/null 2>&1 || { echo "no such user: $user" >&2; exit 1; }

# HOME is set explicitly: under sudo it would otherwise be root's, and the
# setting would land in a kwinrc that KWin never reads.
home=$(getent passwd "$user" | cut -d: -f6)
kwin_set() {
	su "$user" -c "HOME='$home' kwriteconfig6 --file kwinrc --group Wayland --key InputMethod '$1'" 2>/dev/null
	got=$(su "$user" -c "HOME='$home' kreadconfig6 --file kwinrc --group Wayland --key InputMethod" 2>/dev/null)
	[ "$got" = "$1" ] || { echo "could not set kwinrc InputMethod (got '$got')" >&2; return 1; }
}

if [ "$1" = "--revert" ]; then
	[ -x "$PROTECT" ] && "$PROTECT" release "$WRAPPER" "$OUR_DESKTOP" \
		"$DEST/en_US/symbols.qml" 2>/dev/null || true
	[ -d "$DEST" ] && chattr -R -i "$DEST" 2>/dev/null || true
	kwin_set "$STOCK_DESKTOP"
	rm -f "$OUR_DESKTOP" "$WRAPPER"
	rm -rf /usr/local/share/rhodep-keyboard
	# The first attempt wrote here; make sure nothing is left behind.
	rm -f "$STOCK_LAYOUTS/en_US/symbols.qml"
	[ -e "$STOCK_LAYOUTS/en_US/symbols.fallback.orig" ] && \
		mv -f "$STOCK_LAYOUTS/en_US/symbols.fallback.orig" "$STOCK_LAYOUTS/en_US/symbols.fallback"
	[ -e "$STOCK_DESKTOP.rhodep-orig" ] && mv -f "$STOCK_DESKTOP.rhodep-orig" "$STOCK_DESKTOP"
	echo "reverted; log out and back in to get the stock keyboard."
	exit 0
fi

[ -d "$STOCK_LAYOUTS/fallback" ] || {
	echo "no stock layouts in $STOCK_LAYOUTS" >&2
	echo "install qml6-module-qtquick-virtualkeyboard first." >&2; exit 1; }
[ -x /usr/bin/plasma-keyboard ] || { echo "/usr/bin/plasma-keyboard is missing" >&2; exit 1; }

# A previous run registers these with rhodep-protect-files, which sets the
# immutable bit; without lifting it first the tree cannot be replaced.
[ -x "$PROTECT" ] && "$PROTECT" release "$WRAPPER" "$OUR_DESKTOP" \
	"$DEST/en_US/symbols.qml" "$STOCK_DESKTOP" 2>/dev/null || true
[ -d "$DEST" ] && chattr -R -i "$DEST" 2>/dev/null || true

# An earlier version of this script edited Plasma's own .desktop file instead of
# shipping one. Undo that if it is still there, or the stock launcher keeps
# pointing at our wrapper for good - and if it was registered for protection it
# is immutable, which is why the release above lists it too.
if [ -e "$STOCK_DESKTOP.rhodep-orig" ]; then
	cp -a "$STOCK_DESKTOP.rhodep-orig" "$STOCK_DESKTOP" && rm -f "$STOCK_DESKTOP.rhodep-orig"
	L=/usr/local/share/rhodep/protect.d/keyboard-conf.list
	if [ -e "$L" ]; then
		grep -v "[[:space:]]$STOCK_DESKTOP\$" "$L" > "$L.tmp" 2>/dev/null && mv "$L.tmp" "$L"
	fi
	rm -f "/usr/local/share/rhodep/files/$(printf '%s' "$STOCK_DESKTOP" | tr '/' '%')"
fi
# Same for the first attempt's stray layout file, which plasma-keyboard never
# read anyway.
rm -f "$STOCK_LAYOUTS/en_US/symbols.qml"
[ -e "$STOCK_LAYOUTS/en_US/symbols.fallback.orig" ] && \
	mv -f "$STOCK_LAYOUTS/en_US/symbols.fallback.orig" "$STOCK_LAYOUTS/en_US/symbols.fallback"
: 

# Whole tree, because QT_VIRTUALKEYBOARD_LAYOUT_PATH replaces the set entirely
# and every other locale still has to resolve through fallback/.
rm -rf "$DEST"
install -d "$(dirname "$DEST")"
cp -a "$STOCK_LAYOUTS" "$DEST"

# en_US ships a symbols.fallback marker; a real file wins over it.
install -D -m 0644 "$here/symbols.qml" "$DEST/en_US/symbols.qml"
rm -f "$DEST/en_US/symbols.fallback"

install -D -m 0755 /dev/stdin "$WRAPPER" <<'WRAP'
#!/bin/sh
# Point Qt Virtual Keyboard at the layout tree that carries the terminal page.
# Without this plasma-keyboard uses the layouts baked into its own qrc and the
# extra page simply does not exist.
export QT_VIRTUALKEYBOARD_LAYOUT_PATH=/usr/local/share/rhodep-keyboard/layouts
exec /usr/bin/plasma-keyboard "$@"
WRAP

# Our own launcher, so Plasma's file stays untouched and upgrades cannot fight
# us over it.
sed -e "s|^Exec=.*|Exec=$WRAPPER|" \
    -e "s|^Name=.*|Name=Plasma Keyboard (terminal keys)|" \
    "$STOCK_DESKTOP" > "$OUR_DESKTOP"
chmod 0644 "$OUR_DESKTOP"
grep -q "^Exec=$WRAPPER\$" "$OUR_DESKTOP" || { echo "failed to write $OUR_DESKTOP" >&2; exit 1; }

su "$user" -c "XDG_RUNTIME_DIR=/run/user/\$(id -u) kbuildsycoca6 --noincremental" >/dev/null 2>&1 || true
kwin_set "$OUR_DESKTOP"

if [ -x "$PROTECT" ]; then
	"$PROTECT" register keyboard 0755 "$WRAPPER" 2>/dev/null || true
	"$PROTECT" register keyboard-conf 0644 "$OUR_DESKTOP" "$DEST/en_US/symbols.qml" 2>/dev/null || true
fi

cat <<'MSG'
installed.

Log out and back in - KWin reads the input method setting only at startup.
Then: &123, and 1/3 twice.

If the keyboard does not come back, over SSH:
  sudo ./install.sh --revert
MSG
