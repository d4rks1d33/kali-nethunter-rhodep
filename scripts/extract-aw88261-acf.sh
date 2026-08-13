#!/bin/sh
# Extracts the AW88261 ACF tuning blob from the stock ROM, which the mainline
# aw88261 driver needs before it will probe.
#
# The file lives in the vendor partition, which on this device is a logical
# partition inside "super", so it has to be located through the liblp metadata
# and mapped before it can be mounted. Run this on the phone as root.
set -e

SUPER=/dev/disk/by-partlabel/super
MNT=/mnt/vendor_ro
DM=vendor_ro

# Extents of vendor_a, read from the liblp metadata at offset 4096*3 of super.
# Recompute these with the parser in docs/ if the ROM is ever updated.
START_SECTOR=6789120
NUM_SECTORS=1235440

dmsetup remove "$DM" 2>/dev/null || true
echo "0 $NUM_SECTORS linear $SUPER $START_SECTOR" | dmsetup create "$DM"

mkdir -p "$MNT"
mount -o ro "/dev/mapper/$DM" "$MNT"

# Named after the chip id, 0x2113, which is what both amplifiers report.
SRC="$MNT/firmware/aw882xx_pid_2113_acf.bin"
if [ ! -f "$SRC" ]; then
	echo "not found: $SRC" >&2
	umount "$MNT"; dmsetup remove "$DM"
	exit 1
fi

install -D -m 0644 "$SRC" /lib/firmware/aw88261_acf.bin
echo "installed /lib/firmware/aw88261_acf.bin ($(stat -c %s "$SRC") bytes)"

umount "$MNT"
dmsetup remove "$DM"
