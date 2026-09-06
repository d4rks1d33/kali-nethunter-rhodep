#!/bin/sh
# Install rhodep-gnss from this directory, without building the .deb.
# Run as root. See ../../packages/rhodep-gnss/ for the packaged version.
set -e

PKG="$(dirname "$0")/../../packages/rhodep-gnss"

if [ ! -d "$PKG" ]; then
	echo "cannot find $PKG" >&2
	exit 1
fi

# libqmi's typelib is what the qmi-cellid and cell sources use. The wifi source
# does not need it, the cell source can fall back to the qmicli binary, and
# --source=auto degrades rather than failing, so this is a nudge and not a hard
# requirement.
if ! python3 -c 'import gi; gi.require_version("Qmi", "1.0")' 2>/dev/null; then
	echo "gir1.2-qmi-1.0 is missing; installing"
	apt-get install -y gir1.2-qmi-1.0 || \
		echo "carrying on: --source=wifi does not need it" >&2
fi

install -m 0755 "$(dirname "$0")/rhodep-gnss-daemon" \
	/usr/local/sbin/rhodep-gnss-daemon
install -m 0755 "$(dirname "$0")/rhodep-cell-db" \
	/usr/local/sbin/rhodep-cell-db
# The XTRA fetch/inject path: the job stock Android gives to xtra-daemon.
# Injection only -- it cannot start the measurement engine. See the file.
install -m 0755 "$(dirname "$0")/rhodep-xtra" \
	/usr/local/sbin/rhodep-xtra

# Prove the generators and parsers before anything gets a chance to run them
# for real.
/usr/local/sbin/rhodep-gnss-daemon --self-test
/usr/local/sbin/rhodep-cell-db self-test
# Offline: proves the 1024-byte chunker and the ALLOWED_MESSAGES guard that
# keeps QMI_LOC_START out of this tool. Opens no device.
/usr/local/sbin/rhodep-xtra self-test

install -D -m 0644 "$PKG/usr/lib/systemd/system/rhodep-gnss.service" \
	/usr/lib/systemd/system/rhodep-gnss.service
install -D -m 0644 "$PKG/usr/lib/systemd/system/rhodep-qmi-proxy.service" \
	/usr/lib/systemd/system/rhodep-qmi-proxy.service
# Stops anything enabling GNSS through ModemManager; see the file itself.
install -D -m 0644 "$PKG/usr/share/dbus-1/system.d/zz-rhodep-no-modem-gnss.conf" \
	/usr/share/dbus-1/system.d/zz-rhodep-no-modem-gnss.conf
systemctl reload dbus.service 2>/dev/null || true
install -D -m 0644 "$PKG/usr/lib/systemd/system/gpsd.service.d/10-rhodep-gnss.conf" \
	/usr/lib/systemd/system/gpsd.service.d/10-rhodep-gnss.conf
install -D -m 0644 "$PKG/usr/lib/systemd/system/rhodep-cell-db.service" \
	/usr/lib/systemd/system/rhodep-cell-db.service
install -D -m 0644 "$PKG/usr/lib/systemd/system/rhodep-cell-db.timer" \
	/usr/lib/systemd/system/rhodep-cell-db.timer
install -D -m 0644 "$PKG/usr/lib/systemd/system/rhodep-xtra.service" \
	/usr/lib/systemd/system/rhodep-xtra.service
install -D -m 0644 "$PKG/usr/lib/systemd/system/rhodep-xtra.timer" \
	/usr/lib/systemd/system/rhodep-xtra.timer
# Not overwritten if it is already there: it is where the MCC list lives.
[ -f /etc/default/rhodep-cell-db ] || \
	install -D -m 0644 "$PKG/etc/default/rhodep-cell-db" \
		/etc/default/rhodep-cell-db
mkdir -p /var/lib/rhodep-gnss
install -D -m 0644 "$PKG/etc/geoclue/conf.d/20-rhodep-wifi.conf" \
	/etc/geoclue/conf.d/20-rhodep-wifi.conf
# Superseded by 20-rhodep-wifi.conf, which says everything it said.
rm -f /etc/geoclue/conf.d/10-rhodep-gnss.conf
install -D -m 0644 "$PKG/usr/share/doc/rhodep-gnss/README" \
	/usr/share/doc/rhodep-gnss/README

# Same conservative rule the postinst uses: only touch gpsd's own conffile
# while it is still at the Debian default, and keep a copy.
GPSD_DEFAULT=/etc/default/gpsd
if [ -f "$GPSD_DEFAULT" ] && grep -q '^DEVICES=""$' "$GPSD_DEFAULT" \
   && ! grep -q '127.0.0.1:2948' "$GPSD_DEFAULT"; then
	cp -a "$GPSD_DEFAULT" "$GPSD_DEFAULT.rhodep-gnss.bak"
	sed -i \
		-e 's|^DEVICES=""$|DEVICES="tcp://127.0.0.1:2948"|' \
		-e 's|^GPSD_OPTIONS=""$|GPSD_OPTIONS="-n"|' \
		"$GPSD_DEFAULT"
	echo "pointed $GPSD_DEFAULT at tcp://127.0.0.1:2948"
fi

systemctl daemon-reload
# Own the shared qmi-proxy from its own unit, so restarting the daemon does not
# kill ModemManager's transport. `start`, not `restart`: bouncing a proxy MM is
# already using would re-enumerate the modem.
if [ -x /usr/libexec/qmi-proxy ]; then
	systemctl enable rhodep-qmi-proxy.service
	systemctl start rhodep-qmi-proxy.service || true
fi
systemctl enable --now rhodep-gnss.service
# Safe to enable: this only ever injects assistance data, and injection is
# what A-GPS does *before* a fix. It sends no QMI_LOC_START, so unlike
# anything that touches the measurement engine it cannot trip the reset.
systemctl enable --now rhodep-xtra.timer
# rhodep-cell-db.timer is deliberately left disabled: a refresh streams over a
# gigabyte. See /etc/default/rhodep-cell-db.
if [ -x /usr/sbin/gpsd ]; then
	systemctl enable gpsd.socket gpsd.service
	systemctl restart gpsd.service
fi
systemctl stop geoclue.service 2>/dev/null || true

sleep 3
systemctl --no-pager --lines=20 status rhodep-gnss.service || true
echo
echo "  rhodep-xtra status                             # read-only LOC state"
echo "  rhodep-xtra inject                             # refresh the almanac now"
echo "  rhodep-gnss-daemon --locate-once               # one wifi lookup"
echo "  rhodep-gnss-daemon --cells-once                # one cell-tower lookup"
echo "  socat -u UNIX-CONNECT:/run/gnss-share.sock -   # the raw feed"
echo "  gpspipe -w                                     # through gpsd"
echo
echo "  rhodep-cell-db build --mcc 722 --source ocid,mls   # optional, ~2.7 GB"
