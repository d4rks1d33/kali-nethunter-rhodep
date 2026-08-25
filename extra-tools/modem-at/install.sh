#!/bin/sh
# Install the modem AT console. Run on the phone, as root.
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

install -D -m 0755 "$here/rhodep-modem-at" /usr/local/sbin/rhodep-modem-at

# sudo's secure_path here is /usr/sbin:/usr/bin:/sbin:/bin, with no
# /usr/local/sbin, so "sudo rhodep-modem-at" would not find it. Link it into a
# directory sudo does search, rather than making everyone type the full path.
ln -sf /usr/local/sbin/rhodep-modem-at /usr/sbin/rhodep-modem-at

# rpmsg_ctrl is what exposes /dev/rpmsg_ctrl2. It is normally already loaded as
# a dependency of rpmsg_char, but make it explicit so the tool works on a boot
# where nothing else pulled it in.
modprobe rpmsg_ctrl 2>/dev/null || true

echo "installed: /usr/local/sbin/rhodep-modem-at"
echo
if [ -e /dev/rpmsg_ctrl2 ]; then
	echo "try:  sudo rhodep-modem-at -l"
	echo "      sudo rhodep-modem-at ATI"
else
	echo "warning: /dev/rpmsg_ctrl2 is not there yet."
	echo "It appears once the modem remoteproc is up; check with"
	echo "  cat /sys/class/remoteproc/remoteproc0/state"
fi
