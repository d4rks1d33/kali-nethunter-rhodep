#!/bin/sh
# Builds and installs the memshare responder. Run as root on the phone.
#
# See userspace/modem/memshare-daemon.c and HANDOFF-SESSION4.md "Session 13" for
# what this is and what is still missing. Short version: the modem asks the
# application processor for shared memory during bring-up and mainline answers
# nothing at all, so at minimum it should get a definite reply.
set -e

here=$(dirname "$0")

if ! [ -e /usr/include/libqrtr.h ]; then
	echo "libqrtr-dev is needed to build this: apt install libqrtr-dev" >&2
	exit 1
fi

cc -O2 -Wall -o /usr/local/bin/rhodep-memshare "$here/memshare-daemon.c" -lqrtr
install -D -m 0644 "$here/systemd/rhodep-memshare.service" \
	/etc/systemd/system/rhodep-memshare.service

systemctl daemon-reload
systemctl enable rhodep-memshare.service
systemctl restart rhodep-memshare.service
echo "memshare responder installed; check: journalctl -u rhodep-memshare -b"
