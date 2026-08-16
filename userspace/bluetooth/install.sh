#!/bin/sh
# Bluetooth for motorola-rhodep. Run as root on the phone, or in the chroot.
#
# The controller and its firmware work out of the box: hci_qca loads
# qca/crbtfw21.tlv and qca/crnv21.bin and reports "QCA setup on UART is
# completed". Two things still needed doing.
#
# The service was found masked on this device, with a drop-in restricting
# bluetoothd to --noplugin=input,avdtp,a2dp,avrcp, which reads like it was
# turned off while the audio stack was being debugged and never turned back
# on. Unmasked and verified here: bluetoothd starts, hci0 goes UP RUNNING
# PSCAN, a scan finds devices, and the audio card and its PCMs are unchanged
# afterwards.
#
# And the address was random, see rhodep-bt-address.
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
install -d /usr/local/sbin

PROTECT=/usr/local/sbin/rhodep-protect-files
if [ -x "$PROTECT" ]; then
	"$PROTECT" release /usr/local/sbin/rhodep-bt-address \
		/etc/systemd/system/rhodep-bt-address.service 2>/dev/null || true
fi

install -D -m 0755 "$here/rhodep-bt-address" /usr/local/sbin/rhodep-bt-address
install -D -m 0644 "$here/systemd/rhodep-bt-address.service" \
	/etc/systemd/system/rhodep-bt-address.service
install -D -m 0644 "$here/README.md" /usr/share/doc/rhodep-bluetooth/README.md 2>/dev/null || true

if [ -d /run/systemd/system ]; then
	systemctl unmask bluetooth >/dev/null 2>&1 || true
	systemctl daemon-reload
	systemctl enable rhodep-bt-address.service >/dev/null 2>&1
	systemctl enable bluetooth >/dev/null 2>&1 || true
	systemctl start rhodep-bt-address.service >/dev/null 2>&1 || true
	systemctl restart bluetooth >/dev/null 2>&1 || true
	echo "bluetooth: $(systemctl is-active bluetooth), address $(hciconfig hci0 2>/dev/null | sed -n 's/.*BD Address: \([0-9A-F:]*\).*/\1/p')"
else
	rm -f /etc/systemd/system/bluetooth.service   # the mask, if the image carries one
	install -d /etc/systemd/system/bluetooth.service.wants
	ln -sf /etc/systemd/system/rhodep-bt-address.service \
		/etc/systemd/system/bluetooth.service.wants/rhodep-bt-address.service
	echo "bluetooth staged (no running systemd)"
fi

if [ -x "$PROTECT" ]; then
	"$PROTECT" register bluetooth 0755 /usr/local/sbin/rhodep-bt-address 2>/dev/null || true
	"$PROTECT" register bluetooth-conf 0644 \
		/etc/systemd/system/rhodep-bt-address.service 2>/dev/null || true
fi
