# Typing in Electron apps (VS Code)

VS Code left you with a text field, a cursor and no keyboard. The measurements,
in the order they were made, because two of them ruled out the obvious answers.

**It was on XWayland.** `code-oss` was started as plain `code-oss %F`, with no
ozone flags, so it fell back to XWayland -- and XWayland clients cannot speak
the Wayland text-input protocol at all. KWin says so directly:

	activeClientSupportsTextInput    false

**Forcing the keyboard does not help.** KWin exposes
`org.kde.kwin.VirtualKeyboard.forceActivate`, which raises the keyboard over
anything. With VS Code focused it does nothing -- `visible` stays false for
three seconds of sampling -- because KWin will not raise a keyboard over a
window that cannot receive text. So a "show keyboard" button was never going to
be the answer, which is worth knowing before writing one.

**Wayland alone is not enough either.** Adding
`--ozone-platform-hint=auto --enable-features=UseOzonePlatform` moved it onto
Wayland -- confirmed by the process holding two wayland connections and zero
X11 ones -- and the keyboard still never appeared. 60 samples over two minutes,
`activeClientSupportsTextInput` false in every one.

**Two more flags were needed, and both of them.** `--enable-wayland-ime` turns
on input-method support and `--wayland-text-input-version=3` picks the protocol
version KWin offers (it advertises `zwp_text_input_manager_v2` and `v3`).

	with both          the keyboard appears
	dropping the ime flag   no keyboard at all
	dropping the version    no keyboard at all

Chromium ignores flags it does not recognise without a word, so each of these
could only be told apart by trying it.

`applications/kali-code-oss.desktop` is the resulting user-level override; drop
it in `~/.local/share/applications/` where it takes precedence over the packaged
one. VS Code is single-instance, so it has to be fully quit before the new flags
take effect -- reopening while the old instance runs just hands the file to the
old one.

## Still open: it comes back after being minimised

With the keyboard working, minimising it with its own button hides it for a few
seconds and then it returns. The port's input method is not the cause -- it is a
thin wrapper that adds terminal keys to the stock `plasma-keyboard` and execs it.

Sampling KWin's properties every 0.3 s while typing shows what is actually
happening, and it is not what it looked like:

	01:22:47.10  active=true  visible=false
	01:22:47.46  active=true  visible=true
	01:22:48.14  active=true  visible=false
	01:22:48.50  active=true  visible=true

`active` never wavers -- KWin knows the application wants text input the whole
time -- while `visible` flips every 0.4 to 0.7 s. So the application is not
re-asking for the keyboard; the keyboard surface itself is going up and down
underneath a stable request. And after the keyboard is dismissed with its own
button, tapping the editor does not bring it back, which fits: the dismissal
never reaches Chromium, so it has no reason to ask again.

Two candidate explanations were then ruled out by measurement:

  * **The port's input method is not at fault.** It is a wrapper that adds
    terminal keys to the stock `plasma-keyboard` and execs it.
  * **The keyboard process is not crashing and being respawned.** Its pid is
    stable across the flicker, and there are no crashes in the journal.

`--wayland-text-input-version=2` was worth trying, because v2 carries explicit
show and hide panel messages and v3 leaves the decision to the compositor --
which would explain a dismissal that goes nowhere. Chromium does not implement
v2: with it, no keyboard appears at all. So v3 is the only version both sides
speak, and its missing hide path is the thing that hurts.

That puts the fault upstream, between Chromium's text-input-v3 implementation
and KWin, where no browser flag reaches. What is left is either KWin's writable
`mode` (currently 1), which applies to every application including the terminal
where this already works, or typing in something other than VS Code.
