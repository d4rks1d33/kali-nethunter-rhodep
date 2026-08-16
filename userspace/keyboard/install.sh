#!/bin/sh
# Terminal keys for the Plasma Mobile keyboard on motorola-rhodep.
#
# Adds a third page to the symbols layout with Esc, Tab, the arrows and real
# Ctrl combinations, so the terminal is usable without an external keyboard.
#
# Why not an existing on-screen keyboard: KWin advertises zwlr_layer_shell_v1
# and zwp_input_method_v1 but NOT zwp_virtual_keyboard_manager_v1, so wvkbd can
# draw itself and cannot inject a keystroke, and squeekboard speaks
# input_method_v2 which KWin does not have either - which is what its crashes
# here were always about. Extending the keyboard that is already integrated is
# both shorter and safer.
#
# The one non-obvious part: plasma-keyboard does NOT read
# /usr/share/plasma/keyboard/layouts. That directory belongs to
# qml6-module-qtquick-virtualkeyboard and plasma-keyboard prefers the layouts
# compiled into its own qrc, ":/qt/qml/org/kde/plasma/keyboard/". The only way
# in is QT_VIRTUALKEYBOARD_LAYOUT_PATH, and pointing that at a directory
# replaces the whole set - so this copies the stock tree first and changes one
# file in it.
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
DEST=/usr/local/share/rhodep-keyboard/layouts
STOCK=/usr/share/plasma/keyboard/layouts

[ -d "$STOCK/fallback" ] || { echo "no stock layouts at $STOCK; is qml6-module-qtquick-virtualkeyboard installed?" >&2; exit 1; }

PROTECT=/usr/local/sbin/rhodep-protect-files
if [ -x "$PROTECT" ]; then
	"$PROTECT" release "$DEST/en_US/symbols.qml" \
		/usr/local/bin/rhodep-plasma-keyboard \
		/usr/share/applications/org.kde.plasma.keyboard.desktop 2>/dev/null || true
fi

rm -rf "$DEST"
install -d "$(dirname "$DEST")"
cp -a "$STOCK" "$DEST"

# en_US ships .fallback markers; a real symbols.qml takes precedence over them.
install -D -m 0644 "$here/symbols.qml" "$DEST/en_US/symbols.qml"
rm -f "$DEST/en_US/symbols.fallback"

# plasma-keyboard is started by KWin from this .desktop file, so the wrapper is
# where the environment has to be set.
install -D -m 0755 /dev/stdin /usr/local/bin/rhodep-plasma-keyboard <<'WRAP'
#!/bin/sh
# Point Qt Virtual Keyboard at the layout tree that carries the terminal page.
# Without this plasma-keyboard uses the layouts baked into its own qrc.
export QT_VIRTUALKEYBOARD_LAYOUT_PATH=/usr/local/share/rhodep-keyboard/layouts
exec /usr/bin/plasma-keyboard "$@"
WRAP

# Keep KWin's own .desktop file, only change what it runs. A copy is left as
# .orig the first time, so this is reversible.
D=/usr/share/applications/org.kde.plasma.keyboard.desktop
[ -e "$D.rhodep-orig" ] || cp -a "$D" "$D.rhodep-orig"
sed -i 's|^Exec=plasma-keyboard$|Exec=/usr/local/bin/rhodep-plasma-keyboard|' "$D"
grep -q '^Exec=/usr/local/bin/rhodep-plasma-keyboard$' "$D" || {
	echo "could not point the desktop file at the wrapper; restoring" >&2
	cp -a "$D.rhodep-orig" "$D"; exit 1; }

if [ -d /run/systemd/system ]; then
	pkill -f '/usr/bin/plasma-keyboard' 2>/dev/null || true
	echo "installed; the keyboard restarts on its own."
	echo "Reach it with the &123 key, then 1/3 twice."
fi

if [ -x "$PROTECT" ]; then
	"$PROTECT" register keyboard 0755 /usr/local/bin/rhodep-plasma-keyboard 2>/dev/null || true
	"$PROTECT" register keyboard-conf 0644 \
		"$DEST/en_US/symbols.qml" \
		/usr/share/applications/org.kde.plasma.keyboard.desktop 2>/dev/null || true
fi
