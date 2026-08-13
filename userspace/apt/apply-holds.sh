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
#
# The modem, wifi, kernel and firmware holds predate audio and are kept.
set -e
here=$(dirname "$0")
xargs -r apt-mark hold < "$here/apt-holds.txt"
echo "held: $(apt-mark showhold | wc -l) packages"
