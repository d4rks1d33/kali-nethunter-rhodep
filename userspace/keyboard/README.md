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

The likely explanation is that Chromium re-requests the input panel whenever the
editor updates, and KWin honours each request, while the terminal does not
because nothing re-asks after the user hides it. If so, no browser flag will
settle it and the fix belongs on the KWin side -- `org.kde.kwin.VirtualKeyboard`
has a writable `mode`, currently 1 -- which is worth being careful with, because
it applies to every application including the terminal where this already works.
