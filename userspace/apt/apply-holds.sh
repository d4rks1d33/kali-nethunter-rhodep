#!/bin/sh
# Reapplies the apt holds this port depends on. Run as root on the phone.
#
# Nothing here is owned by a package, so apt cannot delete our files; dpkg only
# removes what is in its own manifest. The risk these holds address is
# behavioural, not deletion:
#
#   alsa-ucm-conf   the UCM lookup path and syntax our config relies on
#   alsa-utils      alsa-restore, the fallback that puts the route back at boot
#   pipewire/spa    a major version could change the conf.d rule format; if our
#                   rule stops applying, ACP comes back, and because the PCMs
#                   cannot be opened without a route, PipeWire fails to start
#                   and the machine has no audio at all
#   wireplumber     same reasoning
#   gstreamer1.0-pipewire
#                   the only route from GStreamer to PipeWire. Most recording
#                   applications, the GNOME sound recorder included, capture
#                   through GStreamer, so without this plugin the microphone is
#                   fine at every level this port controls and every app still
#                   records silence
#   kali-menu       owns /usr/share/kali-menu/exec-in-shell, which hundreds of
#                   Kali menu launchers call; without it they fail with "could
#                   not find the program". See userspace/plasma-apps.
#   libqrtr-dev     needed to rebuild the memshare responder in userspace/modem;
#                   losing it does not stop the installed binary from running,
#                   but it does stop anyone from rebuilding it
#   squeekboard     the on screen keyboard once KWin is pointed at it, and the
#                   only one here with a terminal layout; losing it on a phone
#                   means losing text input entirely
#   plasma-keyboard kept installed purely as the fallback to switch back to
#
# The modem, wifi, kernel and firmware holds predate audio and are kept.
#
# Deliberately NOT held: python3, which rhodep-jack-watch needs. Pinning the
# interpreter of a rolling distribution to keep one small script working would
# block security updates and hold back half the system, which is a much worse
# trade than the script breaking loudly and visibly.
set -e
here=$(dirname "$0")
xargs -r apt-mark hold < "$here/apt-holds.txt"

# Anything below may be replacing files that were made immutable by a previous
# run, so clear that first. Harmless if they are not.
for f in /usr/local/sbin/rhodep-apt-guard \
	 /etc/apt/apt.conf.d/50rhodep-protect-holds \
	 /usr/local/share/rhodep/apt-holds.txt; do
	[ -e "$f" ] && chattr -i "$f" 2>/dev/null
done

# A hold does not stop `apt purge`. Install the hook that does; see
# rhodep-apt-guard for the reasoning and how to override it deliberately.
install -D -m 0755 "$here/rhodep-apt-guard" /usr/local/sbin/rhodep-apt-guard
install -D -m 0644 "$here/50rhodep-protect-holds" \
	/etc/apt/apt.conf.d/50rhodep-protect-holds

# Nothing protects the holds themselves: one apt-mark unhold and the port is
# defenceless, with no record. rhodep-holds-enforce puts back any hold, and any
# of the guard's own files, that goes missing, and says so in the journal. The
# canonical copies live where the enforcer can find them.
install -D -m 0644 "$here/apt-holds.txt"          /usr/local/share/rhodep/apt-holds.txt
install -D -m 0644 "$here/rhodep-apt-guard"       /usr/local/share/rhodep/rhodep-apt-guard
install -D -m 0644 "$here/50rhodep-protect-holds" /usr/local/share/rhodep/50rhodep-protect-holds
install -D -m 0755 "$here/rhodep-holds-enforce"   /usr/local/sbin/rhodep-holds-enforce
install -D -m 0755 "$here/rhodep-dpkg-protect"   /usr/local/sbin/rhodep-dpkg-protect
install -D -m 0755 "$here/rhodep-hold-override"   /usr/local/sbin/rhodep-hold-override
install -D -m 0644 "$here/60rhodep-enforce-holds" /etc/apt/apt.conf.d/60rhodep-enforce-holds
install -D -m 0644 "$here/systemd/rhodep-holds-enforce.service" \
	/etc/systemd/system/rhodep-holds-enforce.service
install -D -m 0644 "$here/systemd/rhodep-holds-enforce.timer" \
	/etc/systemd/system/rhodep-holds-enforce.timer
install -d -m 0755 /etc/rhodep
[ -e /etc/rhodep/apt-holds-exceptions ] || : > /etc/rhodep/apt-holds-exceptions
install -D -m 0644 "$here/apply-holds.sh" /usr/share/doc/rhodep-apt/apply-holds.sh

# Make the guard's own files immutable. Root can still take them out, but only
# by deciding to: `chattr -i <file>` first. That is the difference between a
# deliberate change and a script or an agent removing them in passing.
for f in /usr/local/sbin/rhodep-apt-guard \
	 /etc/apt/apt.conf.d/50rhodep-protect-holds \
	 /usr/local/share/rhodep/apt-holds.txt; do
	chattr +i "$f" 2>/dev/null || true
done

# apt holds mean nothing to dpkg: `dpkg -P <held>` removes the package without
# a word. dpkg's own Protected field stops it, so set that too. Not done in a
# chroot, where dpkg's status file belongs to the image being built.
if [ -d /run/systemd/system ]; then
	/usr/local/sbin/rhodep-dpkg-protect apply || true
fi

if [ -d /run/systemd/system ]; then
	systemctl daemon-reload
	systemctl enable --now rhodep-holds-enforce.timer >/dev/null 2>&1
	systemctl enable rhodep-holds-enforce.service >/dev/null 2>&1
else
	install -d /etc/systemd/system/multi-user.target.wants /etc/systemd/system/timers.target.wants
	ln -sf /etc/systemd/system/rhodep-holds-enforce.service \
		/etc/systemd/system/multi-user.target.wants/rhodep-holds-enforce.service
	ln -sf /etc/systemd/system/rhodep-holds-enforce.timer \
		/etc/systemd/system/timers.target.wants/rhodep-holds-enforce.timer
fi

echo "held: $(apt-mark showhold | wc -l) packages"
echo "purge protection installed and made immutable"
echo "dpkg-level protection: $(/usr/local/sbin/rhodep-dpkg-protect status 2>/dev/null | grep -c 'dpkg-protected=yes') packages carry dpkg's Protected flag"
echo "hold enforcement installed; release one deliberately with:"
echo "  sudo rhodep-hold-override release <package> \"<reason>\""
