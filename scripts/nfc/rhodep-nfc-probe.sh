#!/bin/bash
# rhodep-nfc-probe.sh - capture the NFC listen NCI conversation to a file, robustly
# (no systemctl, pkill neard; runs to completion even if SSH drops). Answers the two
# open questions without a reader: which CORE_SET_CONFIG tag is rejected (status 0x9)
# and the eSE NFCEE id + supported protocols (NFCEE_DISCOVER_NTF), and whether
# NFCEE_MODE_SET is accepted.
#
#   sudo bash rhodep-nfc-probe.sh [uid] [sak] [secs]
#   reads: /tmp/nfctest/probe.log  (self-contained, flushed)

set +e
OUT=/tmp/nfctest
TOOL=${TOOL:-/tmp/rhodep-nfc}
UID_ARG=${1:-04:35:3d:6a:f7:54:80}
SAK=${2:-08}
SECS=${3:-12}
mkdir -p "$OUT"
LOG="$OUT/probe.log"
: > "$LOG"
say() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG"; sync; }

say "=== NFC probe uid=$UID_ARG sak=$SAK secs=$SECS ==="

# stop neard without systemctl (systemctl stop can hang here). Kill only neard by
# exact name, and any *other* rhodep-nfc than ourselves -- never pkill -f a pattern
# that matches this very script (that would kill the probe itself under setsid).
for p in $(pidof neard 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
say "neard killed"
sleep 1

# nci dynamic debug on
echo -n "module nci +p"     > /sys/kernel/debug/dynamic_debug/control 2>/dev/null
echo -n "module s3fwrn5 +p" > /sys/kernel/debug/dynamic_debug/control 2>/dev/null
dmesg -C 2>/dev/null

say "running listen for ${SECS}s"
timeout "$SECS" python3 "$TOOL" listen "$UID_ARG" "$SAK" >> "$OUT/listen_userspace.log" 2>&1
say "listen done"

dmesg > "$OUT/dmesg_full.log" 2>&1

{
echo "--- NCI TX/RX summary ---"
grep -aE "nci_send_cmd: opcode|_rsp_packet: status|_ntf_packet|nfcee|rf_intf_activated|deactivate" "$OUT/dmesg_full.log" | tail -50
echo "--- NFCEE_DISCOVER_NTF (eSE id + protocols) ---"
grep -aiE "nfcee_discover|nfcee_id|supported.*proto|nfcee" "$OUT/dmesg_full.log" | tail -20
echo "--- SET_CONFIG results (look for status 0x9) ---"
grep -aiE "set_config_rsp|core_set_config" "$OUT/dmesg_full.log" | tail -10
echo "--- ACTIVATION ---"
if grep -aq "rf_intf_activated_ntf" "$OUT/dmesg_full.log"; then
  grep -a "activation_rf_tech_and_mode" "$OUT/dmesg_full.log" | tail -1
  echo "(bit 0x80 = LISTEN/emulation; 0x00 = poll)"
else
  echo "no RF_INTF_ACTIVATED_NTF"
fi
} >> "$LOG"
say "=== DONE ==="
cat "$LOG"
