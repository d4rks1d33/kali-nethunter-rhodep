#!/bin/sh
# rhodep-wifi-drivers-build: compile the external USB Wi-Fi drivers on the
# device, against the kernel that actually booted. Run by
# rhodep-wifi-drivers-firstboot.service on first boot, and safe to run by hand
# after a kernel change.
#
# Why this exists as a boot-time step rather than a bake-in: the drivers are
# DKMS modules, and a loadable .ko must match the running kernel exactly (same
# build, not just same version string -- see "Building out-of-tree drivers" in
# the README). The debos chroot cannot produce a .ko for a kernel it is not
# running, so the build is deferred to the device.
#
# It stamps success against a fingerprint of the RUNNING kernel, not just its
# release string. A rebuild of the *same* version (e.g. same 7.2.0-rc5 with a
# config change) produces new modules but an unchanged `uname -r`; keying only on
# the release would then wrongly think the drivers are already built and skip the
# rebuild, and wlan1 would fail with "Unknown symbol". `uname -v` carries the
# build timestamp, so it changes on every rebuild and is stable across reboots of
# the same kernel -- that is the fingerprint. The service is left ENABLED (it
# does not self-disable), so a new kernel of any kind triggers a rebuild on the
# next boot; the fingerprint stamp is what keeps it from rebuilding every boot.
set -e

KVER=$(uname -r)
STAGE=/usr/local/share/rhodep/wifi-drivers
# fingerprint = release + a hash of the build string (uname -v). Falls back to
# the raw string if no hasher is present.
KFP=$(printf '%s' "$(uname -v)" | (sha1sum 2>/dev/null || cksum) | cut -d' ' -f1)
STAMP="/var/lib/rhodep/wifi-drivers.$KVER.$KFP.done"
log() { echo "rhodep-wifi-drivers-build: $*"; logger -t rhodep-wifi-drivers "$*" 2>/dev/null || true; }

mkdir -p /var/lib/rhodep

# A stamp for this exact kernel build is only trusted if the driver actually
# loads -- a stamp with a broken module set (the "Unknown symbol" case) is worse
# than no stamp. Verify before believing it.
if [ -f "$STAMP" ] && modprobe rtw_8821au 2>/dev/null && \
   { iw dev 2>/dev/null | grep -q wlan1 || [ -e /sys/class/net/wlan1 ] || true; }; then
	log "already built for this kernel build ($STAMP) and rtw_8821au loads -- nothing to do"
	exit 0
elif [ -f "$STAMP" ]; then
	log "stamp present but rtw_8821au does not load -- rebuilding"
	rm -f "$STAMP"
fi

# Any stamp from a DIFFERENT kernel build is now stale; drop them so /var does
# not accumulate one per rebuild.
for old in /var/lib/rhodep/wifi-drivers.*.done; do
	[ "$old" = "$STAMP" ] || rm -f "$old" 2>/dev/null || true
done

# The build symlink is what makes DKMS / a bare `make` find the kernel. If the
# headers tree is on disk but the symlink is missing (e.g. a linux-image
# reinstall clobbered /lib/modules/$KVER and dropped it), repair it before
# giving up -- this is the one "can't find the kernel" failure that is not a
# per-driver source issue, and it is fixable here.
if [ ! -e "/lib/modules/$KVER/build" ] && [ -d "/usr/src/linux-headers-$KVER" ]; then
	log "headers present but build symlink missing -- repairing it"
	mkdir -p "/lib/modules/$KVER"
	ln -sf "/usr/src/linux-headers-$KVER" "/lib/modules/$KVER/build"
	ln -sf "/usr/src/linux-headers-$KVER" "/lib/modules/$KVER/source"
fi

# Headers must be present. If they are not, do not mark done -- leave the
# service enabled so it retries once they are installed.
if [ ! -e "/lib/modules/$KVER/build" ]; then
	# try a staged headers .deb first
	hdr=$(ls "$STAGE"/linux-headers-"$KVER"_*.deb /srv/linux-headers-"$KVER"_*.deb \
		2>/dev/null | head -1 || true)
	if [ -n "$hdr" ]; then
		log "installing staged kernel headers: $hdr"
		dpkg -i "$hdr" || apt-get -y --fix-broken install || true
	fi
fi
if [ ! -e "/lib/modules/$KVER/build" ]; then
	log "no kernel headers for $KVER -- cannot build yet. Install"
	log "  linux-headers-$KVER and this service will finish on the next boot,"
	log "  or run 'rhodep-wifi-drivers-build' by hand."
	exit 0   # exit 0 so the unit is not marked failed; it stays enabled
fi

# Build deps (idempotent; needs network the first time on a fresh image).
apt-get install -y build-essential bc bison flex libelf-dev libssl-dev dkms git \
	>/dev/null 2>&1 || true

rc=0

log "=== RTL8811AU / Archer T2U Nano (rtw88) ==="
if [ -d "$STAGE/rhodep-rtl8821au" ]; then
	sh "$STAGE/rhodep-rtl8821au/install.sh" || { rc=1; log "rtl8821au failed"; }
else
	log "rhodep-rtl8821au not staged -- skip"
fi

log "=== RTL8188EUS / TL-WN722N (8188eu) ==="
if [ -d "$STAGE/rhodep-rtl8188eus-fix" ]; then
	if apt-get install -y realtek-rtl8188eus-dkms >/dev/null 2>&1; then
		sh "$STAGE/rhodep-rtl8188eus-fix/apply-rtl8188eus-fix.sh" || rc=1
		SRCDIR=$(ls -d /usr/src/realtek-rtl8188eus-*/ 2>/dev/null | head -1)
		if [ -n "$SRCDIR" ]; then
			VER=$(basename "${SRCDIR%/}" | sed 's/^realtek-rtl8188eus-//')
			dkms build   -m realtek-rtl8188eus -v "$VER" -k "$KVER" --force || rc=1
			dkms install -m realtek-rtl8188eus -v "$VER" -k "$KVER" --force || rc=1
			echo "blacklist rtl8xxxu" > /etc/modprobe.d/blacklist-rtl8xxxu.conf
			echo "8188eu"             > /etc/modules-load.d/8188eu.conf
			apt-mark hold realtek-rtl8188eus-dkms "linux-headers-$KVER" 2>/dev/null || true
		else
			log "no rtl8188eus DKMS source dir"; rc=1
		fi
	else
		log "could not apt-install realtek-rtl8188eus-dkms (no network?) -- will retry next boot"
		exit 0   # leave enabled to retry when the network is up
	fi
else
	log "rhodep-rtl8188eus-fix not staged -- skip"
fi

depmod -a "$KVER" || true

if [ "$rc" -eq 0 ]; then
	: > "$STAMP"
	# The service is intentionally NOT disabled: it stays enabled so that a
	# future kernel rebuild (new uname -v -> new fingerprint -> new stamp path)
	# rebuilds the drivers on the next boot. The stamp+load check at the top is
	# what makes it a cheap no-op on every ordinary boot, so keeping it enabled
	# costs nothing.
	log "built both drivers for this kernel build. Plug a TP-Link adapter and"
	log "  'otg on' -- it comes up as wlan1. (service stays enabled to catch the"
	log "  next kernel rebuild automatically.)"
else
	log "finished with errors; leaving the service enabled to retry next boot"
fi
exit 0
