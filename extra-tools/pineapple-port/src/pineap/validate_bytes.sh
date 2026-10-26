#!/bin/bash
# validate_bytes.sh — byte-for-byte oracle validation of pineap-ng requests.
#
# For each command, build the 4097-byte request with `pineap-ng --dump-request`
# and compare it to the EXACT bytes the ORIGINAL pineap sent, captured under
# qemu-mips in /tmp/opencode/proto-re/recv_<label>.bin.
#
# The invocations here mirror /tmp/opencode/proto-re/run_all.sh exactly.
set -u
BIN=./pineap-ng
ORACLE=/tmp/opencode/proto-re
CONF=/tmp/pineap.conf
TMP=$(mktemp -d)
PASS=0; FAIL=0; SKIP=0

# inject frame used for the captured oracle = 10x FF + 4x 77 (len 0x0e)
FRAME=/tmp/pineap_ng_frame.hex
printf 'ffffffffffffffffffff77777777' > "$FRAME"

cmp_one() {
    local label="$1"; shift
    local ref="$ORACLE/recv_${label}.bin"
    if [ ! -f "$ref" ]; then
        printf '  %-18s SKIP (no oracle)\n' "$label"; SKIP=$((SKIP+1)); return
    fi
    "$BIN" --dump-request "$@" > "$TMP/$label.bin" 2>/dev/null
    if cmp -s "$TMP/$label.bin" "$ref"; then
        printf '  %-18s OK\n' "$label"; PASS=$((PASS+1))
    else
        printf '  %-18s FAIL\n' "$label"; FAIL=$((FAIL+1))
        echo    "    first diff:"; cmp "$TMP/$label.bin" "$ref" 2>&1 | sed 's/^/      /'
        echo    "    ours : $(xxd -l 24 "$TMP/$label.bin" | head -2 | tr -d '\n')"
        echo    "    orig : $(xxd -l 24 "$ref"            | head -2 | tr -d '\n')"
    fi
}

echo "== Byte-for-byte: pineap-ng request vs original oracle =="

cmp_one getstatus      $CONF get_status
cmp_one listssids      $CONF list_ssids
cmp_one listprobes_all $CONF list_probes 00:00:00:00:00:00
cmp_one listprobes_mac $CONF list_probes aa:bb:cc:dd:ee:ff
cmp_one runscan        $CONF run_scan 30 2
cmp_one pausescan      $CONF pause_scan
cmp_one unpausescan    $CONF unpause_scan
cmp_one stopscan       $CONF stop_scan
cmp_one deauth         $CONF deauth aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb 06 3
cmp_one hcs            $CONF handshake_capture_start de:ad:be:ef:00:11 6
cmp_one hcstop         $CONF handshake_capture_stop
cmp_one reloadconfig   $CONF reload_config
cmp_one pause          $CONF pause
cmp_one unpause        $CONF unpause
cmp_one addssid        $CONF add_ssid TestNet
cmp_one delssid        $CONF del_ssid TestNet
cmp_one clearssids     $CONF clear_ssids
cmp_one karma_on       $CONF karma on
cmp_one karma_off      $CONF karma off
cmp_one beaconresp_on  $CONF beacon_responses on
cmp_one captssids_on   $CONF capture_ssids on
cmp_one bcastpool_on   $CONF broadcast_pool on
cmp_one logging_on     $CONF logging on
cmp_one apchannel      $CONF ap_channel 11
cmp_one connnotif_on   $CONF connect_notifications on
cmp_one disconnotif_on $CONF disconnect_notifications on
cmp_one bi_low         $CONF beacon_interval LOW
cmp_one bi_normal      $CONF beacon_interval NORMAL
cmp_one bi_aggr        $CONF beacon_interval AGGRESSIVE
cmp_one bri_low        $CONF beacon_response_interval LOW
cmp_one setsource      $CONF set_source 11:22:33:44:55:66
cmp_one settarget      $CONF set_target aa:bb:cc:dd:ee:ff
cmp_one inject         $CONF inject "$FRAME" 6 3 100

echo
echo "  PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
rm -rf "$TMP"
[ "$FAIL" -eq 0 ]
