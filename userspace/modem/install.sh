#!/bin/sh
# Modem support for motorola-rhodep. Run as root on the phone, or in the build
# chroot (nothing here touches the hardware at install time).
#
# Four pieces, see README.md and HANDOFF-SESSION4.md session 14 for why:
#
#   1. rhodep-rfs-populate  copies the modem's remote file system out of the
#      stock modem/fsg/persist partitions at first boot. This is what actually
#      makes the radio come up: without image/modem_pr/so/*.mbn the modem sat
#      in DMS operating mode 'offline' and refused every mode transition.
#   2. a patched tqftpserv that also serves /readonly/vendor/fsg/, installed
#      alongside the distro one so the apt hold on tqftpserv stays valid.
#   3. the memshare responder (QMI service 52), which the modem asks for during
#      bring-up. Not the blocker, but the right thing to answer.
#   4. rhodep-ipa-hold.conf, which keeps ipa.ko out of the boot. TEMPORARY, and
#      the one thing between this port and working mobile data. Read the file.
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

if ! [ -e /usr/include/libqrtr.h ]; then
	echo "libqrtr-dev is needed to build this: apt install libqrtr-dev" >&2
	exit 1
fi

# --- 1. remote file system population ---------------------------------------
install -D -m 0755 "$here/rhodep-rfs-populate" /usr/local/sbin/rhodep-rfs-populate
install -D -m 0644 "$here/systemd/rhodep-rfs-populate.service" \
	/etc/systemd/system/rhodep-rfs-populate.service

# --- 2. tqftpserv that can serve the fsg partition --------------------------
# Built as a separate binary in /usr/local/bin and selected with a drop-in, so
# /usr/bin/tqftpserv keeps belonging to the held distro package.
if ! [ -e /usr/include/zstd.h ]; then
	echo "libzstd-dev is needed to build tqftpserv: apt install libzstd-dev" >&2
	exit 1
fi
cc -O2 -Wall -DHAVE_ZSTD -o /usr/local/bin/tqftpserv-rhodep \
	"$here/tqftpserv/tqftpserv.c" \
	"$here/tqftpserv/translate.c" \
	"$here/tqftpserv/zstd-decompress.c" \
	-I"$here/tqftpserv" -lqrtr -lzstd
install -d /etc/systemd/system/tqftpserv.service.d
cat > /etc/systemd/system/tqftpserv.service.d/10-rhodep.conf <<'EOF'
# Use the rhodep build of tqftpserv, which also resolves /readonly/vendor/fsg/
# (the modem asks for mcfg_sw/mbn_sw.dig there; upstream rejects the path).
# -d keeps a record of every file the modem asks for, which is how the missing
# modem_pr shared objects were found in the first place. It is a handful of
# lines per boot.
[Service]
ExecStart=
ExecStart=/usr/local/bin/tqftpserv-rhodep -d
EOF

# --- 2b. rmtfs must be read/write -------------------------------------------
# In rmtfs, -r means READ-ONLY: writes go to a per-open RAM shadow and are lost.
# The modem keeps its UIM provisioning session in NV, so with -r a SIM it has
# not seen before never gets a session and stops at 'detected'.
install -d /etc/systemd/system/rmtfs.service.d
cat > /etc/systemd/system/rmtfs.service.d/10-rhodep.conf <<'EOF'
# Overrides the packaged drop-in. -r is deliberately absent, see
# userspace/modem/README.md. Back up modemst1/modemst2/fsc before changing this.
[Service]
ExecStart=
ExecStart=/usr/bin/rmtfs -P -s
EOF

# --- 3. memshare responder ---------------------------------------------------
cc -O2 -Wall -o /usr/local/bin/rhodep-memshare "$here/memshare-daemon.c" -lqrtr
install -D -m 0644 "$here/systemd/rhodep-memshare.service" \
	/etc/systemd/system/rhodep-memshare.service

# --- 4. keep the IPA out of the boot (TEMPORARY) -----------------------------
# With ipa.ko loaded AND the modem registered the SoC watchdog-resets every few
# minutes. Until that is fixed the phone is shipped without the IPA, which
# costs mobile data and ModemManager. The file documents itself; deleting it
# and rebooting restores the full stack. See HANDOFF-SESSION4.md session 14.
install -D -m 0644 "$here/rhodep-ipa-hold.conf" /etc/modprobe.d/rhodep-ipa-hold.conf
if [ -d /lib/modules/7.2.0-rc5 ]; then
	depmod -a 7.2.0-rc5
fi

install -D -m 0644 "$here/README.md" /usr/share/doc/rhodep-modem/README.md

# systemctl is not usable in the build chroot; enabling by symlink is.
if [ -d /run/systemd/system ]; then
	systemctl daemon-reload
	systemctl enable rhodep-rfs-populate.service rhodep-memshare.service
	systemctl start rhodep-rfs-populate.service
	systemctl restart tqftpserv.service rhodep-memshare.service
	echo "modem support installed; check: journalctl -u rhodep-rfs-populate -u tqftpserv -b"
else
	install -d /etc/systemd/system/multi-user.target.wants
	ln -sf /etc/systemd/system/rhodep-rfs-populate.service \
		/etc/systemd/system/multi-user.target.wants/rhodep-rfs-populate.service
	ln -sf /etc/systemd/system/rhodep-memshare.service \
		/etc/systemd/system/multi-user.target.wants/rhodep-memshare.service
	echo "modem support staged (no running systemd; enabled by symlink)"
fi
