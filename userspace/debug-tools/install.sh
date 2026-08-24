#!/bin/sh
# Diagnostic tools for the display and modem investigations, plus the one
# thing here that is not a tool: rhodep-pstore-keep, which copies pstore
# records aside on every boot so a crash log is not overwritten by the next
# boot before anyone reads it. Three captures of the same bug were lost that
# way before it existed.
#
# None of these run on their own except rhodep-pstore-keep, and that one only
# copies files. Nothing here is needed to boot or to place a call.
set -e

SBIN=/usr/local/sbin
SHARE=/usr/local/share

install -m755 rhodep-glitch-capture   "$SBIN/rhodep-glitch-capture"
install -m755 rhodep-repaint-bench    "$SBIN/rhodep-repaint-bench"
install -m755 rhodep-repaint-bench.py "$SHARE/rhodep-repaint-bench.py"
install -m755 rhodep-idle-power-ab    "$SBIN/rhodep-idle-power-ab"
install -m755 rhodep-fwdump           "$SBIN/rhodep-fwdump"
install -m755 rhodep-wdt-arm          "$SBIN/rhodep-wdt-arm"
install -m755 rhodep-pstore-keep      "$SBIN/rhodep-pstore-keep"
install -m644 rhodep-pstore-keep.service /etc/systemd/system/rhodep-pstore-keep.service

systemctl daemon-reload
systemctl enable rhodep-pstore-keep.service

# Same two layers everything else in this port gets: immutable on the live
# file, and a record so an apt purge does not quietly take them away.
if [ -x /usr/local/sbin/rhodep-protect-files ]; then
	/usr/local/sbin/rhodep-protect-files register debug-tools protected \
		"$SBIN/rhodep-glitch-capture" \
		"$SBIN/rhodep-repaint-bench" \
		"$SHARE/rhodep-repaint-bench.py" \
		"$SBIN/rhodep-idle-power-ab" \
		"$SBIN/rhodep-fwdump" \
		"$SBIN/rhodep-wdt-arm"
	/usr/local/sbin/rhodep-protect-files register pstore-keep protected \
		"$SBIN/rhodep-pstore-keep" \
		/etc/systemd/system/rhodep-pstore-keep.service
	/usr/local/sbin/rhodep-protect-files register-units pstore-keep \
		rhodep-pstore-keep.service
	/usr/local/sbin/rhodep-protect-files enforce
fi

echo "debug-tools installed and registered"
