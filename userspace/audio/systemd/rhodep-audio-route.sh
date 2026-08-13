#!/bin/sh
# Enables the speaker route before the audio server starts.
#
# The card's PCMs are DPCM front ends: hw:0,0 cannot be opened at all until a
# route is enabled. PipeWire opens its sink while starting up, so if this has
# not run first PipeWire exits and the machine has no audio whatsoever.
set -e

CARD="Motorola-Moto-G82-5G"

# The card only appears once the ADSP is up and apr has probed, which takes
# appreciably longer than this unit's own start, so wait properly.
i=0
while [ $i -lt 450 ]; do
	if [ -e /proc/asound/card0/id ] && alsaucm -c "$CARD" list _verbs >/dev/null 2>&1; then
		alsaucm -c "$CARD" set _verb HiFi set _enadev Speaker
		exit 0
	fi
	i=$((i + 1))
	sleep 0.2
done

echo "sound card $CARD did not appear" >&2
exit 1
