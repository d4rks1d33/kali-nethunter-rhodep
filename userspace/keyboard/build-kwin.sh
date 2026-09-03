#!/bin/sh
# rhodep: rebuild KWin with the forced-activation patch so the on-screen
# keyboard can be raised over XWayland / Java windows (Burp Suite, and any other
# X11 app) that cannot speak the Wayland text-input protocol.
#
# This is a real KWin rebuild, not a configuration change: nothing reachable
# from outside KWin can raise the keyboard over a surface it thinks cannot
# receive text (measured -- see README.md and the top-level nice-to-have #7).
# The patch adds one bit of state, m_forcedActive, that makes forceActivate()
# stick.
#
# Build ON THE PHONE (or any aarch64 machine with the SAME KWin version -- the
# resulting kwin_wayland links against the exact Plasma ABI, so a mismatch will
# refuse to load).
#
#   ./build-kwin.sh            build only, into $WORK, do not touch the system
#   ./build-kwin.sh --install  build, then install over the packaged KWin
#
# STEP 0, do this first: confirm plasma-keyboard delivers KEYCODES, not
# committed strings. This patch only helps the keycode path (keycodes go to the
# seat's keyboard focus, XWayland included). Run the shell keyboard under a
# Wayland trace and watch which interface carries the keystrokes:
#
#   WAYLAND_DEBUG=1 plasma-keyboard 2>&1 | grep -Ei 'virtual_keyboard|text_input'
#
# If you see zwp_virtual_keyboard_v1.key -> keycodes, this patch is the right
# fix. If you see zwp_input_method/text_input .commit_string -> it commits
# strings, and a different (larger) patch is needed; do not flash this and
# expect Burp to type. The README spells out both branches.
#
# Build deps (Debian/Kali):
#   apt build-dep kwin        # pulls the full KWin build environment
# or, if build-dep is unavailable, at least:
#   apt install build-essential cmake extra-cmake-modules ninja-build \
#       qt6-base-dev qt6-wayland-dev libkf6* libwayland-dev
set -e

here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
WORK=${WORK:-/tmp/rhodep-kwin}
PATCH="$here/kwin-patches/0001-inputmethod-forced-activation-for-xwayland.patch"

INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

# Match the installed KWin version so the rebuilt binary loads against the same
# Plasma. dpkg is the source of truth on the device.
KWINVER=$(dpkg-query -W -f='${Version}' kwin-wayland 2>/dev/null || true)
echo "installed kwin-wayland: ${KWINVER:-unknown}"

mkdir -p "$WORK"
cd "$WORK"

# Prefer the distribution's own source (exact version + Debian packaging) over a
# random upstream tag, so the rebuild matches what is on the phone.
if [ ! -d kwin-src ]; then
	echo "fetching KWin source via apt (matches the installed version)..."
	if apt-get source kwin >/dev/null 2>&1; then
		srcdir=$(find . -maxdepth 1 -type d -name 'kwin-*' | head -1)
		mv "$srcdir" kwin-src
	else
		echo "apt-get source kwin failed. Enable deb-src in your apt sources," >&2
		echo "or clone the matching Plasma/6.x branch from" >&2
		echo "https://invent.kde.org/plasma/kwin into $WORK/kwin-src by hand." >&2
		exit 1
	fi
fi

cd kwin-src

# Idempotent: skip if the marker member is already present.
if grep -q 'm_forcedActive' src/inputmethod.h 2>/dev/null; then
	echo "patch already applied"
else
	# Try -p1 (git-style a/ b/) then a plain apply, so it works whether the
	# source came from apt (already at repo root) or a git checkout.
	if patch -p1 --dry-run < "$PATCH" >/dev/null 2>&1; then
		patch -p1 < "$PATCH"
	else
		git apply "$PATCH"
	fi
	echo "applied rhodep forced-activation patch"
fi

echo "configuring..."
cmake -B build -G Ninja \
	-DCMAKE_BUILD_TYPE=RelWithDebInfo \
	-DCMAKE_INSTALL_PREFIX=/usr \
	-DBUILD_TESTING=OFF

# The patch only touches src/inputmethod.cpp, which lives in libkwin.so -- so
# build JUST that library, not all ~2002 objects (KCMs, effects, wallpapers we
# did not change). On the device with -j2 this is the difference between ~30 min
# and many hours. `-j2` keeps the RAM in check (a full -j8 OOM-kills the Plasma
# session mid-build); raise it with JOBS= if you have headroom.
JOBS="${JOBS:-2}"
echo "building libkwin.so (this is the long part; -j$JOBS)..."
ninja -C build -j"$JOBS" bin/libkwin.so.6

if [ "$INSTALL" -eq 1 ]; then
	# Find the built lib and the system lib it replaces (match the exact soname
	# so a version bump does not need this script edited).
	BUILT=$(find build/bin -name 'libkwin.so.*.*' -type f | head -1)
	SYS=$(find /usr/lib -name "$(basename "$BUILT")" 2>/dev/null | head -1)
	[ -n "$BUILT" ] && [ -n "$SYS" ] || { echo "could not locate built/system libkwin" >&2; exit 1; }
	echo "built:  $BUILT ($(stat -c%s "$BUILT") bytes, with debug info)"
	echo "system: $SYS"

	# Strip the debug info first -- the RelWithDebInfo lib is ~300 MB, the
	# stripped one matches the packaged ~10 MB. Dynamic symbols (forceActivate)
	# survive a --strip-unneeded.
	strip --strip-unneeded -o /tmp/libkwin.stripped "$BUILT"

	PROTECT=/usr/local/sbin/rhodep-protect-files
	# release immutability if a previous run protected it, back up the original
	# once, then install over it.
	[ -x "$PROTECT" ] && sudo "$PROTECT" release "$SYS" 2>/dev/null || true
	[ -e "$SYS.orig-backup" ] || sudo cp -a "$SYS" "$SYS.orig-backup"
	sudo install -m 0644 -o root -g root /tmp/libkwin.stripped "$SYS"
	rm -f /tmp/libkwin.stripped

	# Hold the KWin packages so an upgrade cannot replace the patched lib, and
	# protect the lib itself (immutable + snapshot + auto-restore).
	sudo apt-mark hold kwin-common kwin-wayland libkwin6 kwin-data 2>/dev/null || true
	[ -x "$PROTECT" ] && sudo "$PROTECT" register kwin-keyboard 0644 "$SYS" 2>/dev/null || true

	echo ""
	echo "Installed (backup at $SYS.orig-backup). Reboot to load the new KWin."
	echo "Then, with an X11/Java app (Burp) focused on a text field:  rhodep-keyboard on"
	echo "To revert: sudo $PROTECT release $SYS; sudo cp -a $SYS.orig-backup $SYS; reboot"
else
	echo ""
	echo "Built $WORK/kwin-src/build/bin/libkwin.so.6. Re-run with --install to deploy."
fi
