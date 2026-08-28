#!/bin/sh
# Power behaviour for motorola-rhodep. Run as root on the phone, or in the chroot.
#
# Two things, and neither is a preference:
#
#   rhodep-suspend-mode  selects s2idle at boot, because `deep` does not work on
#                        this SoC. Asked to sleep for twenty seconds it came back
#                        after two, every time, with nothing in the log and
#                        nothing in /sys/power/pm_wakeup_irq -- not a wakeup, the
#                        suspend itself returning. With the screen off the phone
#                        was therefore entering and leaving suspend every couple
#                        of seconds, re-enumerating USB each time, which is how
#                        this was found: a WiFi scan on a USB dongle did not
#                        survive locking the screen in the street.
#
#   rhodep-keep-awake    holds the phone up while there is work to do, so that a
#                        capture you start and leave running is still running an
#                        hour later. It does not match program names: a process
#                        burning CPU is working, a shell at a prompt is not, and
#                        anything in a tty/pts login session counts too.
#
# This file was missing for a long time, so a fresh image had neither. If you are
# reading it because something power-related is wrong on a new install, that is
# probably why.
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
install -d /usr/local/sbin /etc/rhodep

PROTECT=/usr/local/sbin/rhodep-protect-files
if [ -x "$PROTECT" ]; then
	"$PROTECT" release \
		/usr/local/sbin/rhodep-keep-awake \
		/usr/local/sbin/rhodep-awake \
		/usr/local/sbin/rhodep-suspend-mode \
		/etc/rhodep/keep-awake.conf \
		/etc/systemd/system/rhodep-keep-awake.service \
		/etc/systemd/system/rhodep-suspend-mode.service \
		/etc/NetworkManager/conf.d/rhodep-wowlan.conf 2>/dev/null || true
fi

install -D -m 0755 "$here/rhodep-keep-awake"   /usr/local/sbin/rhodep-keep-awake
install -D -m 0755 "$here/rhodep-awake"        /usr/local/sbin/rhodep-awake
install -D -m 0755 "$here/rhodep-suspend-mode" /usr/local/sbin/rhodep-suspend-mode
install -D -m 0644 "$here/systemd/rhodep-keep-awake.service" \
	/etc/systemd/system/rhodep-keep-awake.service
install -D -m 0644 "$here/systemd/rhodep-suspend-mode.service" \
	/etc/systemd/system/rhodep-suspend-mode.service

# The config is yours once it exists: it carries thresholds and an ignore list
# that are meant to be tuned, so re-running this must not throw that away. A
# new option added upstream will not appear in an old file -- that is the price,
# and it is cheaper than silently reverting someone's tuning.
if [ -e /etc/rhodep/keep-awake.conf ]; then
	echo "keeping the existing /etc/rhodep/keep-awake.conf (compare against $here/etc/keep-awake.conf for new options)"
else
	install -D -m 0644 "$here/etc/keep-awake.conf" /etc/rhodep/keep-awake.conf
fi

# Ask the radio to stay able to wake the host rather than tearing the link down.
# NetworkManager programmes this when the connection is *activated*, so a link
# that has been up since before the file was installed still reads "disabled" in
# `iw phy0 wowlan show`; reconnect before concluding anything from that.
install -D -m 0644 "$here/etc/NetworkManager/rhodep-wowlan.conf" \
	/etc/NetworkManager/conf.d/rhodep-wowlan.conf

install -D -m 0644 "$here/README.md" /usr/share/doc/rhodep-power/README.md 2>/dev/null || true

if [ -d /run/systemd/system ]; then
	systemctl daemon-reload
	systemctl enable rhodep-suspend-mode.service >/dev/null 2>&1
	systemctl enable rhodep-keep-awake.service   >/dev/null 2>&1
	systemctl start  rhodep-suspend-mode.service >/dev/null 2>&1 || true
	systemctl restart rhodep-keep-awake.service  >/dev/null 2>&1 || true
	systemctl reload NetworkManager >/dev/null 2>&1 || true
	echo "suspend mode: $(cat /sys/power/mem_sleep 2>/dev/null)"
	echo "keep-awake:   $(systemctl is-active rhodep-keep-awake)"
else
	install -d /etc/systemd/system/multi-user.target.wants
	ln -sf /etc/systemd/system/rhodep-suspend-mode.service \
		/etc/systemd/system/multi-user.target.wants/rhodep-suspend-mode.service
	ln -sf /etc/systemd/system/rhodep-keep-awake.service \
		/etc/systemd/system/multi-user.target.wants/rhodep-keep-awake.service
	echo "power staged (no running systemd)"
fi

if [ -x "$PROTECT" ]; then
	"$PROTECT" register power 0755 \
		/usr/local/sbin/rhodep-keep-awake \
		/usr/local/sbin/rhodep-awake \
		/usr/local/sbin/rhodep-suspend-mode 2>/dev/null || true
	"$PROTECT" register power-conf 0644 \
		/etc/systemd/system/rhodep-keep-awake.service \
		/etc/systemd/system/rhodep-suspend-mode.service \
		/etc/NetworkManager/conf.d/rhodep-wowlan.conf 2>/dev/null || true
	# keep-awake.conf is deliberately NOT registered: the store restores content,
	# and this file is meant to be edited.
	"$PROTECT" register-units power \
		rhodep-keep-awake.service rhodep-suspend-mode.service 2>/dev/null || true
fi
