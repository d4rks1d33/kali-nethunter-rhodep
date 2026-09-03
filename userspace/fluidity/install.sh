#!/bin/sh
# rhodep-fluidity: userspace tuning for a smoother UI. Run as root.
#
# The GPU is already maxed for this silicon (840 MHz fuse ceiling, 120 Hz panel
# default, ~117 fps / 0 janks measured) and the CPU governor (schedutil) is
# correct. What is left on the userspace side are three cheap, measurable wins:
#
#   1. KWin: shorten animations and drop the two most fill-rate-heavy effects on
#      a mobile Adreno (Blur, Background Contrast). Snappier UI, frees GPU time.
#   2. power-profiles-daemon: without it Plasma's power slider does nothing and
#      there is no way to ask for a performance profile. Install it and default
#      to balanced (the user can pick performance).
#   3. sysctl: with fast zram swap already active, raise swappiness and drop
#      page-cluster so the phone leans on zram instead of killing foreground
#      apps under memory pressure (which is what killed plasmashell mid-build).
#
# The big.LITTLE/EAS scheduler fix is NOT here -- that is a kernel DTS change
# (patch 0114), because the scheduler could not tell the A78 cores from the A55
# ones. See "How to continue the port" / that patch.
#
# Chroot-aware: in the debos chroot it lays files down; on a live system it also
# reloads/enables. Idempotent.
set -e

log() { echo "rhodep-fluidity: $*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

LIVE=0
[ -d /run/systemd/system ] && LIVE=1

# --------------------------------------------------------------- 1. KWin effects
# Written system-wide so it applies to every user, including a fresh image with
# no per-user config yet. Only these keys are touched.
mkdir -p /etc/xdg
KWINRC=/etc/xdg/kwinrc
KDEG=/etc/xdg/kdeglobals

set_key() {  # file group key value
	if command -v kwriteconfig6 >/dev/null 2>&1; then
		kwriteconfig6 --file "$1" --group "$2" --key "$3" "$4"
	else
		# minimal INI writer for the chroot: ensure [group] then key=value
		f=$1; g=$2; k=$3; v=$4
		[ -f "$f" ] || : > "$f"
		if ! grep -q "^\[$g\]" "$f" 2>/dev/null; then printf '\n[%s]\n' "$g" >> "$f"; fi
		if grep -q "^$k=" "$f" 2>/dev/null; then
			sed -i "s|^$k=.*|$k=$v|" "$f"
		else
			sed -i "/^\[$g\]/a $k=$v" "$f"
		fi
	fi
}

# Disable the two heavy effects. blur and background contrast are per-pixel
# passes that are cheap on a desktop dGPU and expensive on an Adreno 619.
set_key "$KWINRC" Plugins blurEnabled false
set_key "$KWINRC" Plugins contrastEnabled false

# Shorten (not disable) animations: half duration reads as "snappy" without
# losing the motion cues that tell you what happened. 0 would feel abrupt.
set_key "$KDEG" KDE AnimationDurationFactor 0.5

log "KWin: blur + background-contrast off, AnimationDurationFactor=0.5"

# --------------------------------------------------------------- 2. power-profiles
if [ "$LIVE" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
	if ! command -v powerprofilesctl >/dev/null 2>&1; then
		log "installing power-profiles-daemon"
		apt-get install -y --no-install-recommends power-profiles-daemon >/dev/null 2>&1 \
			|| log "could not install power-profiles-daemon (no network?)"
	fi
	if command -v powerprofilesctl >/dev/null 2>&1; then
		systemctl enable --now power-profiles-daemon >/dev/null 2>&1 || true
		log "power-profiles-daemon enabled (Plasma's power slider now works)"
	fi
else
	log "chroot: ensure power-profiles-daemon is in the rootfs; enabling by symlink"
	if [ -e /usr/lib/systemd/system/power-profiles-daemon.service ]; then
		mkdir -p /etc/systemd/system/multi-user.target.wants
		ln -sf /usr/lib/systemd/system/power-profiles-daemon.service \
			/etc/systemd/system/multi-user.target.wants/power-profiles-daemon.service
	fi
fi

# --------------------------------------------------------------- 3. sysctl (zram)
# zram swap is already active (~half of RAM). Lean on it: on a phone with fast
# compressed swap, a high swappiness keeps foreground apps resident instead of
# reclaiming their pages, and page-cluster 0 (no readahead) suits random zram
# access. This is what stops plasmashell being the thing that dies under memory
# pressure.
install -d /etc/sysctl.d
cat > /etc/sysctl.d/90-rhodep-fluidity.conf <<'EOF'
# rhodep: lean on zram swap instead of evicting foreground apps.
vm.swappiness = 120
vm.page-cluster = 0
# reclaim a bit more eagerly so we never hit a hard wall (the value is in
# 10KB units of free memory kept in reserve headroom terms via watermark_scale).
vm.watermark_scale_factor = 125
EOF
[ "$LIVE" -eq 1 ] && sysctl -p /etc/sysctl.d/90-rhodep-fluidity.conf >/dev/null 2>&1 || true
log "sysctl: swappiness=120, page-cluster=0, watermark_scale_factor=125"

# systemd-oomd: PSI is enabled in the kernel, so let oomd kill the actual
# memory hog under pressure instead of the kernel OOM killer picking plasmashell.
if [ "$LIVE" -eq 1 ]; then
	if systemctl list-unit-files 2>/dev/null | grep -q systemd-oomd; then
		systemctl enable --now systemd-oomd >/dev/null 2>&1 || true
		log "systemd-oomd enabled (kills the hog, not the shell)"
	fi
fi

# --------------------------------------------------------------- protect
PROTECT=/usr/local/sbin/rhodep-protect-files
if [ -x "$PROTECT" ]; then
	"$PROTECT" release "$KWINRC" "$KDEG" /etc/sysctl.d/90-rhodep-fluidity.conf 2>/dev/null || true
	"$PROTECT" register fluidity 0644 \
		/etc/sysctl.d/90-rhodep-fluidity.conf 2>/dev/null || true
	# kwinrc/kdeglobals are shared config other installers also write, so do NOT
	# make them immutable (it would block plasma-apps and the user); the sysctl
	# file is ours alone and safe to freeze.
	log "protected: sysctl drop-in"
fi

log "done. Log out/in (or reboot) for the KWin effect + animation changes."
