# extra-tools

Things that make this phone pleasant to *work on*, as opposed to the rest of the
repository, which makes the hardware work at all.

Nothing here is required to boot, place a call, or use the radio. Everything here
was written because the device was already usable and the next obstacle was no
longer the hardware — it was the software assuming a desktop, a keyboard, or a
paid API key.

	modem-at/             an AT console for the modem, over glink rather than a
	                      serial port — a third window onto the radio, working
	                      when ModemManager is down and independent of the
	                      DIAG debug fuses
	terminal-keyboard/    Esc, Tab, Ctrl, Alt and the arrows on the on-screen
	                      keyboard, in every language layout
	terminal-clipboard/   copy and paste in the terminal, from the shell side,
	                      because the keyboard cannot reach the clipboard
	claude-free/          Claude Code driven by the model providers opencode
	                      already holds keys for
	pwnagotchi/           pwnagotchi capturing on the external TP-Link (wlan1),
	                      never touching the internal wlan0
	cleanup/              weekly disk-space cleanup (caches, journal, coredumps)
	                      and a permanent cap on the systemd journal

Each directory has its own README with the reasoning, the protocol traces, and
the things that turned out to be impossible. Those are worth reading before
changing anything: the obvious approach usually does not work, and the reason is
never in the documentation.

## Installing

Each has an `install.sh` that is idempotent and safe to re-run:

	cd terminal-keyboard  && sudo ./install.sh   # then log out and back in
	cd terminal-clipboard && sudo ./install.sh   # then open a new terminal
	cd claude-free        && sudo ./install.sh
	cd pwnagotchi         && sudo ./install.sh   # needs /opt/pwnagotchi cloned
	cd cleanup            && sudo ./install.sh   # weekly timer + journal cap

`terminal-keyboard` and `terminal-clipboard` belong together — the keyboard sends
the bytes, the shell turns them into clipboard operations — so install both or
neither if you want `Ctrl+V` to paste.

## Why they are not in `userspace/`

`userspace/` holds what the port needs in order to be a working phone: the modem's
remote file system, the Bluetooth address, the audio routing, the apt holds. If any
of it is missing, something on the device is broken.

These are optional. Removing them costs comfort, not function, and mixing the two
kinds of thing in one directory made it harder to answer "what does this port
actually require".

They are protected the same way as the rest, though: registered with
`rhodep-protect-files`, so they carry the immutable bit and a snapshot, and their
packages are held.
