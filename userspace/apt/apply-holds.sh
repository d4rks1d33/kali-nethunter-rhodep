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

# A hold does not stop `apt purge`. Install the hook that does; see
# rhodep-apt-guard for the reasoning and how to override it deliberately.
install -D -m 0755 "$here/rhodep-apt-guard" /usr/local/sbin/rhodep-apt-guard
install -D -m 0644 "$here/50rhodep-protect-holds" \
	/etc/apt/apt.conf.d/50rhodep-protect-holds

echo "held: $(apt-mark showhold | wc -l) packages, purge protection installed"
