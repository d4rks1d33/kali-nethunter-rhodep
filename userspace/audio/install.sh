#!/bin/sh
# Installs everything userspace needs for sound on the Moto G82. Run as root
# on the phone. The kernel side must already be in place, and the AW88261
# tuning blob must have been extracted with scripts/extract-aw88261-acf.sh.
set -e

here=$(dirname "$0")

install -D -m 0644 "$here/ucm2/conf.d/sm6375/Motorola-Moto-G82-5G.conf" \
	/usr/share/alsa/ucm2/conf.d/sm6375/Motorola-Moto-G82-5G.conf
install -D -m 0644 "$here/ucm2/conf.d/sm6375/Motorola-Moto-G82-5G-HiFi.conf" \
	/usr/share/alsa/ucm2/conf.d/sm6375/Motorola-Moto-G82-5G-HiFi.conf

install -D -m 0644 "$here/wireplumber/51-rhodep-no-acp.conf" \
	/etc/wireplumber/wireplumber.conf.d/51-rhodep-no-acp.conf
install -D -m 0644 "$here/pipewire/51-rhodep-sink.conf" \
	/etc/pipewire/pipewire.conf.d/51-rhodep-sink.conf

install -D -m 0755 "$here/systemd/rhodep-audio-route.sh" \
	/usr/local/bin/rhodep-audio-route.sh
install -D -m 0644 "$here/systemd/rhodep-audio-route.service" \
	/etc/systemd/system/rhodep-audio-route.service

systemctl daemon-reload
systemctl enable rhodep-audio-route.service

# Apply now so the current session does not have to wait for a reboot.
/usr/local/bin/rhodep-audio-route.sh
alsactl store

echo "done; restart pipewire/wireplumber or reboot"
