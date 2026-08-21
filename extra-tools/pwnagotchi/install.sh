#!/bin/sh
# pwnagotchi on rhodep, capturing on the external TP-Link (wlan1) only.
# Run as root on the phone.
#
# pwnagotchi targets a Raspberry Pi with a monitor-capable adapter as its only
# WiFi. This phone is the other way round: the internal wlan0 (ath10k/WCN3990)
# is the only real WiFi and CANNOT do monitor mode, so it must stay a managed
# client, and all capture happens on the external RTL8188EUS (wlan1). The whole
# point of this glue is to make pwnagotchi use wlan1 and never touch wlan0.
#
# It expects the pwnagotchi source already cloned at /opt/pwnagotchi (the
# jayofelony fork) and bettercap installed (Kali has it).
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PWN=/opt/pwnagotchi

[ -d "$PWN/pwnagotchi" ] || {
	echo "pwnagotchi source not found at $PWN"
	echo "  git clone https://github.com/jayofelony/pwnagotchi $PWN"
	exit 1
}
command -v bettercap >/dev/null || { echo "bettercap not installed"; exit 1; }

# ---------------------------------------------------------------- python deps
# Kali is EXTERNALLY-MANAGED, so pwnagotchi runs from a venv built with
# --system-site-packages: scapy/flask/dbus/prctl/etc. come from apt, and only
# what apt does not provide (Crypto) is added into the venv. Nothing is written
# to the system site-packages.
echo "pwnagotchi: apt deps"
apt-get install -y --no-install-recommends \
	python3-prctl python3-file-read-backwards python3-flask-cors \
	python3-flask-wtf python3-dbus python3-scapy >/dev/null 2>&1 || true

if [ ! -x "$PWN/.venv/bin/python" ]; then
	echo "pwnagotchi: creating venv (--system-site-packages)"
	python3 -m venv --system-site-packages "$PWN/.venv"
fi
# pycryptodome provides the Crypto module pwnagotchi/identity.py imports; the
# Debian package installs Cryptodome (different namespace), so it comes from pip
# into the venv.
"$PWN/.venv/bin/pip" install --quiet --disable-pip-version-check pycryptodome >/dev/null 2>&1 || \
	"$PWN/.venv/bin/pip" install --quiet pycryptodome

# ---------------------------------------------------------------- pwngrid
if ! command -v pwngrid >/dev/null 2>&1; then
	echo "pwngrid: not found. Install the aarch64 build from jayofelony/pwngrid:"
	echo "  https://github.com/jayofelony/pwngrid/releases/latest"
	echo "  then: install -m0755 pwngrid /usr/bin/pwngrid"
	MISSING_PWNGRID=1
fi

# ---------------------------------------------------------------- our glue
install -d /usr/local/sbin /etc/pwnagotchi/log /etc/pwnagotchi/handshakes
for s in rhodep-pwn-monstart rhodep-pwn-monstop \
         rhodep-pwn-bettercap-launcher rhodep-pwn-launcher; do
	install -m 0755 "$here/bin/$s" "/usr/local/sbin/$s"
done

# config.toml is the user override merged over default.toml. main.name is pinned
# to the current hostname on purpose: pwnagotchi reboots the machine if it does
# not match (see the CAUTION in the file).
if [ ! -f /etc/pwnagotchi/config.toml ]; then
	install -m 0644 "$here/config/config.toml" /etc/pwnagotchi/config.toml
	sed -i "s/^main.name = .*/main.name = \"$(hostname)\"/" /etc/pwnagotchi/config.toml
	echo "pwnagotchi: wrote /etc/pwnagotchi/config.toml (name = $(hostname))"
	echo "  >>> change ui.web.username / ui.web.password in it before exposing the UI"
else
	echo "pwnagotchi: /etc/pwnagotchi/config.toml exists, left as is"
fi

# pwngrid identity keys (once)
[ -f /etc/pwnagotchi/id_rsa ] || \
	{ command -v pwngrid >/dev/null 2>&1 && pwngrid -generate -keys /etc/pwnagotchi >/dev/null 2>&1; }

for u in rhodep-pwn-bettercap rhodep-pwngrid-peer rhodep-pwnagotchi; do
	install -m 0644 "$here/systemd/$u.service" "/etc/systemd/system/$u.service"
done

if [ -d /run/systemd/system ]; then
	systemctl daemon-reload
	# On-demand, not enabled at boot: pwnagotchi transmits (deauth/assoc) in auto
	# mode, so starting it should be a deliberate act, not something that happens
	# every boot with an adapter that may not be plugged in.
	echo
	echo "pwnagotchi: installed. Plug in the TP-Link and:"
	echo "    otg on"
	echo "    systemctl start rhodep-pwnagotchi   # pulls in bettercap + pwngrid"
	echo "  web UI: http://<phone>:8080  (or 172.16.42.1:8080 over usb)"
	echo "  default mode is MANUAL (listens, never transmits). For the full loop,"
	echo "  set ExecStart in rhodep-pwnagotchi.service to '... launcher auto'."
else
	echo "pwnagotchi: no running systemd, files installed only"
fi

[ -n "$MISSING_PWNGRID" ] && echo "pwnagotchi: remember to install pwngrid (above)."
exit 0
