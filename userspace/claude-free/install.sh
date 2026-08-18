#!/bin/sh
# Claude Code on opencode Zen's free models.
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "run me as root" >&2; exit 1; }

user=${SUDO_USER:-kali}
home=$(getent passwd "$user" | cut -d: -f6) || { echo "no such user: $user" >&2; exit 1; }

command -v node >/dev/null 2>&1 || { echo "node is needed for the proxy" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is needed to read the model cache" >&2; exit 1; }

PROXY=/usr/local/libexec/rhodep/zen-anthropic-proxy.mjs
WRAPPER=/usr/local/bin/claude-free
PROTECT=/usr/local/sbin/rhodep-protect-files

[ -x "$PROTECT" ] && "$PROTECT" release "$PROXY" "$WRAPPER" 2>/dev/null || true

install -D -m 0644 "$here/zen-anthropic-proxy.mjs" "$PROXY"
install -D -m 0755 "$here/claude-free" "$WRAPPER"
install -D -m 0644 "$here/README.md" /usr/local/share/rhodep/claude-free/README.md

# A config file the user owns, so model choices survive a reinstall.
conf=$home/.config/rhodep/claude-free.conf
if [ ! -e "$conf" ]; then
	install -d -m 0755 -o "$user" -g "$user" "$home/.config/rhodep"
	cat > "$conf" <<'CONF'
# Models for claude-free. See `claude-free --models` for what is available.
#
# Every free model on Zen supports tool calling, which is what Claude Code needs;
# the difference between them is context, speed and quality.
CLAUDE_FREE_MODEL=nemotron-3-ultra-free
CLAUDE_FREE_SMALL_MODEL=ling-3.0-tiny-free
CLAUDE_FREE_PORT=8787
CONF
	chown "$user":"$user" "$conf"
	echo "wrote $conf"
else
	echo "$conf exists, left alone"
fi

if [ -x "$PROTECT" ]; then
	"$PROTECT" register claude-free 0755 "$WRAPPER" 2>/dev/null || true
	"$PROTECT" register claude-free-conf 0644 "$PROXY" 2>/dev/null || true
fi

cat <<'MSG'
installed.

  claude-free                 Claude Code on a free model
  claude-free --models        what is available
  claude-free --status        is the proxy up
  claude-free --stop          stop it

Models are set in ~/.config/rhodep/claude-free.conf
MSG
