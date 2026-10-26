#!/bin/sh
# Capture an inline remoteproc devcoredump for remoteproc0 (modem).
# Run as root. Watches /sys/class/devcoredump for a new devcdN whose
# failing device is the modem remoteproc, copies data to $OUT, then frees it
# (echo 0 > .../data) so recovery can proceed.
OUT=${1:-/tmp/iqexp/modem_core.elf}
mkdir -p "$(dirname "$OUT")"
echo "waiting for devcoredump..."
for i in $(seq 1 120); do
  for d in /sys/class/devcoredump/devcd*; do
    [ -e "$d/data" ] || continue
    # identify the failing device
    fd=$(readlink -f "$d/failing_device" 2>/dev/null)
    case "$fd" in
      *remoteproc0*)
        sz=$(stat -c %s "$d/data" 2>/dev/null)
        echo "found $d (failing=$fd) data_size=$sz; reading..."
        # Reading .../data streams the inline dump; this unblocks recovery.
        cat "$d/data" > "$OUT"
        rsz=$(stat -c %s "$OUT" 2>/dev/null)
        echo "captured $rsz bytes to $OUT"
        # free the devcoredump
        echo 0 > "$d/data" 2>/dev/null
        echo "freed $d"
        exit 0
        ;;
    esac
  done
  sleep 0.25
done
echo "timed out waiting for devcoredump"
exit 1
