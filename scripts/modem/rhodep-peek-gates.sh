#!/bin/bash
# rhodep-peek-gates.sh - read the RF-cal gate globals live from modem DDR via
# rhodep_mpeek, and log to /tmp/iqrun/gates-live.txt. Optionally do it before and
# after a diag sequence (pass a MODE/RAWSEQ to run in between).
#
#   sudo setsid bash rhodep-peek-gates.sh </dev/null >/tmp/iqrun/peek.log 2>&1 &
#
# Env:
#   KO=/tmp/diag-modules/rhodep_mpeek.ko
#   TAG=baseline           label for this snapshot

set +e
OUT=/tmp/iqrun
KO=${KO:-/tmp/diag-modules/rhodep_mpeek.ko}
TAG=${TAG:-snap}
mkdir -p "$OUT"
DBG=/sys/kernel/debug/rhodep_mpeek

# name:PA:len  (PAs = VA - 0x35000000; per dump_targets.md / gate_globals_phys.md)
PEEKS="
gate_a_cal_mode:0x96f4f740:4
gate_b_rf_ctx:0x96f56000:4
carrier_ptr:0x9579c494:4
ftm_phase:0x968dbec8:4
session_0xc:0x9579a82c:8
session_0x89a8:0x957a31c8:8
carrier_class:0x969eb074:8
"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# load the module if not present
if [ ! -d "$DBG" ]; then
	log "loading rhodep_mpeek"
	insmod "$KO" 2>&1
	sleep 1
fi
[ -d "$DBG" ] || { log "rhodep_mpeek not available"; exit 1; }

{
echo "=== gate peek: $TAG @ $(date +%H:%M:%S) ==="
for line in $PEEKS; do
	[ -z "$line" ] && continue
	name=${line%%:*}; rest=${line#*:}; p=${rest%%:*}; l=${rest#*:}
	echo "$p" > "$DBG/pa" 2>/dev/null
	echo "$l" > "$DBG/len" 2>/dev/null
	val=$(cat "$DBG/data" 2>/dev/null | sed -n '2p')
	printf "%-20s %s -> %s\n" "$name" "$p" "$val"
done
echo "=== end $TAG ==="
} >> "$OUT/gates-live.txt" 2>&1

cat "$OUT/gates-live.txt"
