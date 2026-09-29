#!/bin/bash
# rhodep-nfc-listen-test.sh - drive an NFC card-emulation (listen) test on the
# phone and log everything to /tmp/nfctest/, so SSH drops don't lose the result.
#
#   sudo setsid bash rhodep-nfc-listen-test.sh [uid] [sak] </dev/null \
#        >/tmp/nfctest/driver.log 2>&1 &
#   then read /tmp/nfctest/driver.log and /tmp/nfctest/dmesg.log
#
# It stops neard, enables nci dynamic debug, runs `rhodep-nfc listen`, and dumps
# the NCI conversation + whether RF_INTF_ACTIVATED_NTF arrives in LISTEN mode
# (activation_rf_tech_and_mode with bit 0x80 = listen; 0x00 = poll).

set +e
OUT=/tmp/nfctest
TOOL=${TOOL:-/tmp/rhodep-nfc}
UID_ARG=${1:-04:35:3d:6a:f7:54:80}
SAK=${2:-08}
SECS=${SECS:-15}
mkdir -p "$OUT"
exec > "$OUT/driver.log" 2>&1
log() { echo "[$(date +%H:%M:%S)] $*"; }

log "=== NFC listen test: uid=$UID_ARG sak=$SAK ==="
systemctl stop neard 2>/dev/null
pkill -9 -f rhodep-nfc 2>/dev/null
sleep 1

# fresh dmesg + nci debug
dmesg -C
echo -n "module nci +p" > /sys/kernel/debug/dynamic_debug/control 2>/dev/null
echo -n "module s3fwrn5 +p" > /sys/kernel/debug/dynamic_debug/control 2>/dev/null

log "starting listen for ${SECS}s (present the reader now)"
timeout "$SECS" python3 "$TOOL" listen "$UID_ARG" "$SAK" > "$OUT/listen.log" 2>&1
log "listen exited"

log "--- userspace listen.log ---"
cat "$OUT/listen.log"

# capture dmesg
dmesg > "$OUT/dmesg.log" 2>&1

log "--- NCI command/response summary ---"
grep -aE "nci_send_cmd: opcode|_rsp_packet: status|rf_intf_activated_ntf|deactivate_ntf" "$OUT/dmesg.log" | tail -40

log "--- ACTIVATION CHECK ---"
if grep -aq "rf_intf_activated_ntf" "$OUT/dmesg.log"; then
	mode=$(grep -a "activation_rf_tech_and_mode" "$OUT/dmesg.log" | tail -1 | grep -oE "0x[0-9a-f]+")
	log "RF_INTF_ACTIVATED_NTF present, activation_rf_tech_and_mode=$mode"
	log "(0x80..0x83 = LISTEN mode = EMULATION WORKING; 0x00..0x05 = POLL = read a tag, not emulation)"
else
	log "no RF_INTF_ACTIVATED_NTF - did not activate"
fi
log "=== DONE ==="
