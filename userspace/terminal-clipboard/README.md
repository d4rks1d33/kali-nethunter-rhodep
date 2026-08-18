# Clipboard keys for the terminal

	sudo apt-get install wl-clipboard
	sudo ./install.sh
	# open a new terminal

	Ctrl V    paste the clipboard into the command line
	Ctrl O    copy the command line to the clipboard
	cmd | wl-copy    copy a command's output

## Why this is in the shell and not in the keyboard

Because the keyboard cannot do it. Three separate walls, each confirmed with a
protocol trace rather than guessed at:

1. **Ctrl+Shift+C never arrives.** plasma-keyboard sends the modifier field of
   `zwp_input_method_context_v1.keysym` as a hardcoded `0`
   (`src/inputlisteneritem.cpp`), so no mask leaves the process.
2. **KWin would ignore it anyway.** `InputMethod::forwardKeySym` calls
   `notifyKeyboardModifiers(keyCode->modifiers, ...)` — the modifiers the
   *keymap* needs to produce that symbol, not the ones the input method sent.
   Which is also why an attempt at Ctrl+C arrived as a capital `C`: producing
   `C` needs Shift, so KWin added it.
3. **The keyboard cannot read the clipboard to paste it either.** It has no
   keyboard focus, so KWin never sends it a selection offer. The trace contains
   not a single `data_offer`, and every read came back empty.

What the keyboard *can* do is send control characters as text, and Ctrl+V is the
byte `0x16`. The shell receives that byte, and the shell — unlike the keyboard —
can reach the clipboard. So the work happens here.

`^V` was `quoted-insert` and `^O` was `accept-line-and-down-history`. Neither is
something anyone uses on a phone.

## What was rejected, and what it would have cost

Patching plasma-keyboard to use the protocol's `modifiers()` + `key()` path,
which KWin *does* honour (`xkb->updateModifiers()` then `forwardModifiers()`).
That is the general fix — it would give real Ctrl+Shift+C in every application,
not just the terminal. It was rejected on cost, not principle:

	apt-get build-dep plasma-keyboard
	35 upgraded, 73 newly installed
	Inst libkf6windowsystem6 [6.26.0-1] (6.28.0-2 kali-rolling)

The KF6 6.26 development packages are gone from the archive; only 6.28 is left.
Building means dragging part of the KDE stack up underneath the running session,
which is the partial upgrade this port avoids elsewhere. It would also need a Qt
key to evdev keycode mapping written from scratch.

Against that: `wl-clipboard` and `qmlkonsole` are one new package each and zero
upgrades.

## Selecting text on screen

Neither of these keys helps with selecting arbitrary output with a finger, and
GNOME Console (`kgx`) shows no long-press menu under Plasma Mobile. `qmlkonsole`,
Plasma Mobile's own terminal, is built for touch and is installed for that.
`tmux` copy mode is the keyboard-only route: its prefix is Ctrl+B, which is
`0x02`, which this keyboard sends.
