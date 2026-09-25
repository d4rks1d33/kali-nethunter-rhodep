#!/bin/bash
# rhodep-iq-memshare-run.sh - drive the whole memshare + IQ-capture bring-up on
# the phone and log every step to /tmp/iqrun/, so we don't fight SSH glitches.
#
# Runs entirely on the device (as root). Reboot-safe against SSH drops: launch it
# with setsid and read /tmp/iqrun/*.log afterwards.
#
#   sudo setsid bash rhodep-iq-memshare-run.sh </dev/null >/tmp/iqrun/driver.log 2>&1 &
#
# Stages, each logged:
#   0 preflight   - kernel, DT reservation, module, daemon detection
#   1 assign      - load rhodep_memassign (reserved 0x8ab00000, dual-VMID) + chardev
#   2 daemon      - start memshare-daemon (offers QMI service 52)
#   3 modem       - restart remoteproc0 so it re-queries memshare at bring-up
#   4 watch       - capture dmesg for the ALLOC / rf_1x_mdsp / crashes
#   5 diag-seq    - TECH_ENTER -> RADIO_CONFIG -> IQ_CAPTURE via rhodep-diag-server
#   6 samples     - hexdump the head of /dev/rhodep_memshare
#
# Nothing here reboots the AP. remoteproc0 restart is the only modem-touching
# step and only runs once the region is assigned at the stock address.

set +e
OUT=/tmp/iqrun
KO=${KO:-/tmp/diag-modules/rhodep_memassign.ko}
DAEMON=${DAEMON:-/home/kali/memshare-daemon}
DIAG=${DIAG:-/home/kali/rhodep-diag-server.py}
DO_MODEM_RESTART=${DO_MODEM_RESTART:-1}
DO_DIAG=${DO_DIAG:-1}

mkdir -p "$OUT"
ts() { date +%H:%M:%S; }
log() { echo "[$(ts)] $*"; }

rm -f "$OUT"/*.log
exec > "$OUT/driver.log" 2>&1

log "=== rhodep IQ/memshare run ==="
uname -r

# ---- stage 0: preflight ----
log "--- stage 0 preflight ---"
echo "DT reservation:"
hexdump -C /proc/device-tree/reserved-memory/memshare@8ab00000/reg 2>/dev/null | head -1
grep -iE "8ab00000|memshare" /proc/iomem 2>/dev/null | head
echo "remoteproc0 state: $(cat /sys/class/remoteproc/remoteproc0/state 2>/dev/null)"
"$DAEMON" check > "$OUT/daemon-check.log" 2>&1
echo "daemon check ->"; cat "$OUT/daemon-check.log"

# ---- stage 1: assign ----
log "--- stage 1 assign (rhodep_memassign, reserved region, dual-VMID) ---"
rmmod rhodep_memassign 2>/dev/null
dmesg -C
insmod "$KO"
sleep 1
dmesg | grep -iE "memassign|memshare" | tee "$OUT/assign.log"
echo "chardev: $(ls -la /dev/rhodep_memshare 2>&1)"
echo "sysfs base/size: $(cat /sys/kernel/rhodep_memshare/base 2>/dev/null) $(cat /sys/kernel/rhodep_memshare/size 2>/dev/null)"

# ---- stage 2: daemon ----
log "--- stage 2 memshare-daemon ---"
pkill -9 memshare-daemon 2>/dev/null
sleep 1
setsid "$DAEMON" > "$OUT/daemon.log" 2>&1 < /dev/null &
sleep 2
log "daemon started, log:"; cat "$OUT/daemon.log"

# ---- stage 3: modem restart ----
if [ "$DO_MODEM_RESTART" = 1 ]; then
	log "--- stage 3 restart remoteproc0 (modem re-queries memshare) ---"
	dmesg -C
	echo stop  > /sys/class/remoteproc/remoteproc0/state; log "stop rc=$?"
	sleep 4
	echo "state after stop: $(cat /sys/class/remoteproc/remoteproc0/state 2>/dev/null)"
	echo start > /sys/class/remoteproc/remoteproc0/state; log "start rc=$?"
	# watch the bring-up for 35s
	for i in $(seq 1 7); do
		sleep 5
		echo "  +$((i*5))s state=$(cat /sys/class/remoteproc/remoteproc0/state 2>/dev/null) crashes=$(dmesg | grep -cE 'crash detected in modem') rf1x=$(dmesg | grep -cE 'rf_1x_mdsp|Potential Memory')"
	done
else
	log "--- stage 3 SKIPPED (DO_MODEM_RESTART=0) ---"
fi

# ---- stage 4: watch ----
log "--- stage 4 dmesg (modem bring-up + memshare) ---"
dmesg | grep -iE "remoteproc0|rf_1x_mdsp|Potential Memory|crash detected|modem is now up|memassign|ipa .*modem" | tail -25 | tee "$OUT/dmesg.log"
log "daemon log after modem restart:"; cat "$OUT/daemon.log"

# ---- stage 5: diag sequence ----
if [ "$DO_DIAG" = 1 ]; then
	log "--- stage 5 diag: put FTM, TECH_ENTER -> RADIO_CONFIG -> IQ_CAPTURE ---"
	qmicli -d qrtr://0 --dms-set-operating-mode=factory-test 2>&1 | head -1
	sleep 2
	TE="4b0b27000d000300010004000000000002000400010000000300040000000000"
	# RADIO_CONFIG TLVs: RX_CARRIER=0, TECH_MODE=1, BAND=3, CHANNEL=1575, BANDWIDTH=3
	RCTLV="0100040000000000190004000100000005000400030000000600040027060000070004000300000000"
	# header: 4b0b 2700 0d10(sub 0x100D) 0500(tlv_count=5) 0000 0000(bytes8,9,10=cid0,11) then TLVs
	RC="4b0b27000d1005000000000000000000${RCTLV}"
	IQ="4b0b27000d10000000000005000000000000"
	python3 "$DIAG" --seconds 45 --kick --cmd-peer \
		--raw-seq "$TE,$RC,$IQ" --cmd-after 8 \
		--log "$OUT/diag.log" >/dev/null 2>&1
	log "diag sequence done; SSR per step:"
	for s in 1 2 3 4; do
		echo "  STEP $s SSR=$(awk "/STEP $s /{f=1} /STEP $((s+1)) /{f=0} f&&/status 0x06|status 0x03/{c++} END{print c+0}" "$OUT/diag.log" 2>/dev/null)"
	done
	grep -aE "CMD reply|REPACK|CNTL peer|feature mask, 3" "$OUT/diag.log" 2>/dev/null | grep -avE "status 0x06|status 0x03" | head -8
else
	log "--- stage 5 SKIPPED (DO_DIAG=0) ---"
fi

# ---- stage 6: samples ----
log "--- stage 6 /dev/rhodep_memshare head (are there samples?) ---"
if [ -e /dev/rhodep_memshare ]; then
	dd if=/dev/rhodep_memshare bs=256 count=1 2>/dev/null | hexdump -C | head -16 | tee "$OUT/samples.log"
	echo "nonzero bytes in first 64KiB:"
	dd if=/dev/rhodep_memshare bs=65536 count=1 2>/dev/null | tr -d '\0' | wc -c
else
	echo "no /dev/rhodep_memshare"
fi

log "=== DONE ==="
