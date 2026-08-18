#!/bin/sh
# Clipboard keys for the terminal, from the shell side.
#
# Ctrl+V pastes the system clipboard into the command line and Ctrl+O copies the
# command line out. Both work because the keyboard can already send control
# characters, and the shell - unlike the keyboard - can reach the clipboard.
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "run me as root" >&2; exit 1; }

user=${SUDO_USER:-kali}
home=$(getent passwd "$user" | cut -d: -f6) || { echo "no such user: $user" >&2; exit 1; }

command -v wl-paste >/dev/null 2>&1 || {
	echo "wl-clipboard is missing. apt-get install wl-clipboard" >&2; exit 1; }

PROTECT=/usr/local/sbin/rhodep-protect-files
SNIPPET=/usr/local/share/rhodep/rhodep-clipboard.zsh
[ -x "$PROTECT" ] && "$PROTECT" release "$SNIPPET" 2>/dev/null || true

install -D -m 0644 "$here/rhodep-clipboard.zsh" "$SNIPPET"

# apt cannot touch this file, but a stray hand can. The .zshrc line that sources
# it is restored by rhodep-keyboard-enforce instead, since .zshrc belongs to the
# user and must stay editable.
[ -x "$PROTECT" ] && "$PROTECT" register clipboard 0644 "$SNIPPET" 2>/dev/null || true

# One guarded block, so running this twice does not stack up.
rc=$home/.zshrc
marker="# >>> rhodep clipboard keys >>>"
if ! grep -qF "$marker" "$rc" 2>/dev/null; then
	{
		echo ""
		echo "$marker"
		echo "[ -r /usr/local/share/rhodep/rhodep-clipboard.zsh ] && . /usr/local/share/rhodep/rhodep-clipboard.zsh"
		echo "# <<< rhodep clipboard keys <<<"
	} >> "$rc"
	chown "$user":"$user" "$rc"
	echo "hooked into $rc"
else
	echo "$rc already hooked, left alone"
fi

echo
echo "Open a new terminal, then:"
echo "  Ctrl V   paste the clipboard into the command line"
echo "  Ctrl O   copy the command line to the clipboard"
echo "To copy command output:  some-command | wl-copy"
