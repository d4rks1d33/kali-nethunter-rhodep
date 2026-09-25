#!/bin/bash
# rhodep-iq-cleanboot.sh - the unite-both-halves attempt.
#
# The dilemma: the tool's --restart-modem CRASHES the modem (leaves RF driver
# unregistered -> RFTEST SSR), while no-restart keeps RF but the handshake is
# unreliable. A CLEAN remoteproc0 stop/start boots the modem with healthy RF
# (0 crashes) AND is a fresh boot (connected==0), so if our diag server is
# already published and listening, the modem should do its modem-first handshake
# with US during that fresh boot - giving handshake + RF at once.
#
#   sudo setsid bash rhodep-iq-cleanboot.sh </dev/null >/tmp/iqrun/cleanboot.log 2>&1 &
#   then read /tmp/iqrun/cleanboot.log and /tmp/iqrun/diag.log
#
# Sequence:
#   - stop ModemManager (competing peer)
#   - start the diag server in BACKGROUND (publishes CNTL/DATA/DCI, listens),
#     WITHOUT --restart-modem and WITHOUT --kick: it just waits, publishing.
#   - clean stop/start of remoteproc0: the modem re-boots and, seeing our diag
#     servers, does the modem-first handshake to us.
#   - once handshake seen, the same server (via a fed command file) sends the
#     IQ sequence. Simpler: run the server for the whole window with a --raw-seq
#     scheduled by --cmd-after timed to land AFTER the modem is back up.

set +e
OUT=/tmp/iqrun
DIAG=${DIAG:-/home/kali/rhodep-diag-server.py}
mkdir -p "$OUT"
exec > "$OUT/cleanboot.log" 2>&1
ts() { date +%H:%M:%S; }
log() { echo "[$(ts)] $*"; }

TE="4b0b27000d000300010004000000000002000400010000000300040000000000"
RC="4b0b27000d10050000000000000000010004000000000019000400010000000500040003000000060004002706000007000400030000000000"
IQ="4b0b27000d10000000000005000000000000"

log "=== clean-boot IQ attempt ==="
log "stop ModemManager"
systemctl stop ModemManager 2>/dev/null; pkill -9 ModemManager 2>/dev/null
sleep 1

# Start the diag server publishing + listening, with the IQ sequence scheduled to
# fire well after the modem comes back (cmd-after 30s). No --restart-modem (we do
# the clean remoteproc restart ourselves), no --kick.
log "start diag server (publish + listen), IQ seq scheduled at +30s"
python3 "$DIAG" --seconds 75 --cmd-peer --set-ftm-after \
	--raw-seq "$TE,$RC,$IQ" --cmd-after 30 --log "$OUT/diag.log" >/dev/null 2>&1 &
DPID=$!
sleep 3

log "clean stop/start remoteproc0 (fresh boot, RF registers, connected==0)"
dmesg -C
echo stop  > /sys/class/remoteproc/remoteproc0/state; log "stop rc=$?"
sleep 4
echo start > /sys/class/remoteproc/remoteproc0/state; log "start rc=$?"

# watch bring-up
for i in $(seq 1 6); do
	sleep 4
	log "  +$((i*4))s state=$(cat /sys/class/remoteproc/remoteproc0/state 2>/dev/null) crashes=$(dmesg | grep -cE 'crash detected in modem') rf1x=$(dmesg | grep -cE 'rf_1x_mdsp|Potential Memory')"
done

log "waiting for diag server to finish its window..."
wait $DPID 2>/dev/null
sleep 2

log "--- handshake markers ---"
grep -aE "feature mask, 3|DIAGID from|CNTL peer" "$OUT/diag.log" 2>/dev/null | head -6
log "--- STEP results (SSR per step) ---"
for s in 1 2 3; do
	echo "  STEP $s SSR=$(awk "/STEP $s /{f=1} /STEP $((s+1)) /{f=0} f&&/status 0x06|status 0x03/{c++} END{print c+0}" "$OUT/diag.log" 2>/dev/null)"
done
log "--- replies (filtered) ---"
grep -aE "STEP|CMD reply|REPACK" "$OUT/diag.log" 2>/dev/null | grep -avE "status 0x06|status 0x03|0600000" | head -12
log "=== DONE ==="
