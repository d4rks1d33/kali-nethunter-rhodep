# Terminal keys for the Plasma Mobile keyboard

	sudo ./install.sh
	# then log out and back in, or reboot

Adds a third page to the symbols layout: **Esc, Tab, the arrows, Home/End,
PgUp/PgDn**, the characters a shell needs constantly (`~ / \ | - _ * >`), and
**real Ctrl combinations** — `^C ^D ^Z ^L ^A ^E ^R ^W ^U ^K`, plus `⇧^C` and
`⇧^V` for terminal copy and paste.

Reach it with the **&123** key, then **1/3** twice.

## Why not an existing keyboard

Checked before writing anything. Nothing available works on this device, and
the reason is one line of protocol support:

	KWin advertises   zwlr_layer_shell_v1, zwp_input_method_v1,
	                  zwp_text_input_manager_v2/v3
	KWin does NOT     zwp_virtual_keyboard_manager_v1

- **wvkbd** is packaged, small, and has exactly the terminal layer wanted — and
  it injects keys through `virtual_keyboard_v1`. It would draw itself and type
  nothing.
- **squeekboard** speaks `input_method_v2`, from wlroots. KWin only has v1.
  That, and not some subtle bug, is what its crashes here were always about;
  README.md's note about it can be read that way now.
- **maliit-keyboard** is Qt5, drags in half of the old stack, and has no
  terminal keys either.
- No terminal in the archive ships its own key bar.

So the shortest and safest path is to extend the keyboard that is already
integrated with KWin.

## The part that is not obvious

`plasma-keyboard` **does not read `/usr/share/plasma/keyboard/layouts`.** That
directory belongs to `qml6-module-qtquick-virtualkeyboard`; plasma-keyboard
prefers the layouts compiled into its own qrc:

	strings /usr/bin/plasma-keyboard
	  QT_VIRTUALKEYBOARD_LAYOUT_PATH
	  prefer :/qt/qml/org/kde/plasma/keyboard/

Dropping a `symbols.qml` into that directory changes nothing at all, which is
exactly what happened on the first attempt. The only way in is
`QT_VIRTUALKEYBOARD_LAYOUT_PATH`, and setting it replaces the *whole* layout
set — so `install.sh` copies the stock tree to
`/usr/local/share/rhodep-keyboard/layouts` and changes one file in it.

The variable has to reach the process KWin starts, and that is its own small
trap: editing `Exec=` in Plasma's `.desktop` file is not enough, KWin goes on
launching the binary it already knows. It takes a `.desktop` file of our own
that KWin is pointed at with `kwinrc [Wayland] InputMethod` — and **KWin reads
that setting only at startup**, so the change needs a logout or a reboot.
Plasma's own file is left untouched, so an upgrade cannot fight us over it.

The layout was checked with `qmllint` from `qt6-declarative-dev-tools` before
asking for that reboot. It reports only *unqualified access* style warnings —
the same ones Qt's own stock `symbols.qml` produces.

And a third trap, which is why an `en_US`-only layout still showed nothing with
everything else correct: **Qt Virtual Keyboard picks the layout directory from
the input locale**, and this session runs `LANG=C.UTF-8`. So the layout goes
into `fallback/`, which is where 30 of the 44 locales — `C.UTF-8` and `en_GB`
among them — resolve through their `symbols.fallback` marker. The 13 that ship
a symbols page of their own (Arabic, Hebrew, CJK, `es_ES`, `es_MX`) are left
alone because their symbols are language specific; `install.sh` says so if the
session is using one of them.

Quick way to tell whether the layout is live at all: tap **&123** and read the
page indicator. Stock says **1/2**, ours says **1/3**.

## The Ctrl keys are real key events

They do not fake control characters. Each calls

	InputContext.inputEngine.virtualKeyClick(Qt.Key_C, "", Qt.ControlModifier)

which is the documented API and produces a key event with the modifier set.
That is why `⇧^C` arrives at the terminal as copy rather than as a stray
character.

## If something goes wrong

The keyboard is how you type, so there is a way back that needs no keyboard —
over SSH:

	kwriteconfig6 --file kwinrc --group Wayland --key InputMethod \
	    /usr/share/applications/org.kde.plasma.keyboard.desktop

and log in again. The stock `.desktop` file is also kept as
`org.kde.plasma.keyboard.desktop.rhodep-orig`.
