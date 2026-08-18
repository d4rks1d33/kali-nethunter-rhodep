# Clipboard keys for the terminal on motorola-rhodep.
#
# Why this lives in the shell and not in the keyboard: the on-screen keyboard
# cannot do it. Ctrl+Shift+C/V never reach the terminal, because plasma-keyboard
# sends the protocol's modifier field as a hardcoded 0 and KWin ignores it
# anyway, deriving modifiers from the keymap instead. And the keyboard cannot
# read the clipboard to paste it either: it has no keyboard focus, so KWin never
# sends it a selection offer - a protocol trace shows not one data_offer.
#
# What the keyboard *can* do is send control characters as text, and Ctrl+V is
# the byte 0x16. So zsh picks it up here, where the clipboard is reachable.
#
# ^V was quoted-insert and ^O was accept-line-and-down-history. Neither is
# something you use on a phone.

if (( $+commands[wl-paste] )); then
	rhodep-paste-clipboard() {
		local text
		text=$(wl-paste --no-newline 2>/dev/null) || return
		[[ -n $text ]] || return
		LBUFFER+=$text
	}
	zle -N rhodep-paste-clipboard
	bindkey '^V' rhodep-paste-clipboard
fi

if (( $+commands[wl-copy] )); then
	rhodep-copy-line() {
		print -rn -- $BUFFER | wl-copy
	}
	zle -N rhodep-copy-line
	bindkey '^O' rhodep-copy-line
fi
