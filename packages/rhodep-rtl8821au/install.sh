#!/bin/sh
# rhodep-rtl8821au: driver for the TP-Link Archer T2U Nano (AC600), the
# RTL8811AU/8821AU dual-band USB adapter, with monitor mode + injection on the
# Kali NetHunter Pro rhodep port (Linux 7.2, aarch64). Run as root on the phone.
#
# Unlike the RTL8188EUS (single-band, uses the aircrack-ng rtl8188eus driver),
# this chip is served by the mac80211 driver rtw88. Kali's out-of-tree
# realtek-rtl88xxau-dkms does NOT build on 7.2 (dead kernel API: from_timer /
# del_timer_sync removed, and a broken include path), and aircrack-ng itself now
# marks that driver deprecated and points at rtw88. lwfinger/rtw88 compiles
# clean on 7.2 with no patches and, being mac80211, gives standard
# airmon-ng/monitor/inject with no flip-back quirks.
#
# The chip is USB 2357:011e, matched by the rtw_8821au module (rtw8821au.c).
set -e

VER=0.6
SRC=/usr/src/rtw88-$VER
REPO=https://github.com/lwfinger/rtw88

log() { echo "rhodep-rtl8821au: $*"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

# --------------------------------------------------------------- kernel headers
# DKMS needs the headers for the running kernel. The pmbootstrap apk ships none,
# so this repo provides linux-headers-<KVER>.deb (see ../../kernel/ notes).
KVER=$(uname -r)
[ -e "/lib/modules/$KVER/build" ] || {
	log "ERROR: no kernel headers at /lib/modules/$KVER/build"
	log "       install linux-headers-$KVER first (see kernel/ build notes)"
	exit 1
}

# --------------------------------------------------------------- get the source
# Prefer a checkout vendored next to this script (offline installs); otherwise
# clone it. Either way it lands in /usr/src for DKMS.
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ ! -d "$SRC" ]; then
	if [ -d "$here/rtw88" ]; then
		log "using vendored source $here/rtw88"
		cp -a "$here/rtw88" "$SRC"
	else
		command -v git >/dev/null || { log "git not installed and no vendored source"; exit 1; }
		log "cloning $REPO"
		git clone --depth 1 "$REPO" "$SRC"
	fi
	rm -rf "$SRC/.git"
fi

# --------------------------------------------------------------- remove the
# broken Kali package if it is present, so its half-added DKMS entry cannot
# shadow this one.
if dpkg -l realtek-rtl88xxau-dkms 2>/dev/null | grep -q '^ii'; then
	log "removing the non-building realtek-rtl88xxau-dkms"
	apt-get remove -y realtek-rtl88xxau-dkms >/dev/null 2>&1 || true
	dkms remove realtek-rtl88xxau --all 2>/dev/null || true
fi

# --------------------------------------------------------------- dkms
if dkms status "rtw88/$VER" 2>/dev/null | grep -q installed; then
	log "rtw88/$VER already installed via DKMS"
else
	log "building rtw88/$VER (DKMS)"
	dkms add "rtw88/$VER" 2>/dev/null || true
	dkms build "rtw88/$VER" -k "$KVER"
	dkms install "rtw88/$VER" -k "$KVER" --force
fi
depmod -a "$KVER"

# --------------------------------------------------------------- load + report
modprobe rtw_8821au 2>/dev/null || true
if [ -e /sys/class/net/wlan1 ] || iw dev 2>/dev/null | grep -q wlan1; then
	log "OK: wlan1 present"
	iw dev 2>/dev/null | awk '/Interface wlan1/{print "    "$0}'
else
	log "wlan1 not present yet -- plug the adapter in and run 'otg on'"
	log "  (the module autoloads on the USB modalias when it enumerates)"
fi

log "done. Put it in monitor with rhodep-pwn-monstart (or airmon-ng start wlan1)."
