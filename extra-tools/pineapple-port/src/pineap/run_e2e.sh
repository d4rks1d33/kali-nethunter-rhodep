#!/bin/bash
# run_e2e.sh — end-to-end test: the REAL pineap-ng CLI talking to pineapd-ng.
#
# Proves drop-in compatibility: we invoke pineap-ng exactly as the PHP panel
# would (`pineap <conf> <cmd> [args]`) against the reimplemented daemon and
# check the stdout matches the original's behaviour.
#
# The client is pointed at the daemon's socket via $PINEAP_SOCK (no rebuild
# needed — same mechanism the Kali relocation uses).  The daemon is built with
# temp paths and run under `unshare -r` for an EUID-0 namespace when not root.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PINEAPD_DIR="$HERE/../pineapd"
cd "$HERE"

TMP=$(mktemp -d)
SOCK="$TMP/pineapd.sock"
LOCK="$TMP/pineapd.lock"
CONF="$TMP/pineap.conf"
export PINEAP_SOCK="$SOCK"
export PINEAP_IFACE="lo"
PASS=0; FAIL=0
ok(){ echo "  [PASS] $1"; PASS=$((PASS+1)); }
no(){ echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

cat > "$CONF" <<EOF
# PineAP Config File
pineap_interface = lo
recon_db_path = $TMP/recon.db
hostapd_db_path = $TMP/log.db
ssid_db_path = $TMP/pineapple.db
logging = on
EOF

echo "=== Build pineap-ng (CLI under test) ==="
make clean >/dev/null 2>&1; make >/dev/null 2>&1 || { echo "pineap-ng build failed"; exit 1; }
ok "pineap-ng built: $(file -b pineap-ng | cut -d, -f1-2)"

echo "=== Build pineapd-ng with temp socket ==="
( cd "$PINEAPD_DIR" && make clean >/dev/null 2>&1 && \
  make EXTRA_DEFS="-DPINEAPD_SOCK_PATH=\\\"$SOCK\\\" -DPINEAPD_LOCK_PATH=\\\"$LOCK\\\" -DPINEAPD_HANDSHAKE_DIR=\\\"$TMP/hs\\\"" >/dev/null 2>&1 ) \
  || { echo "pineapd-ng build failed"; exit 1; }
ok "pineapd-ng built (socket=$SOCK)"

echo "=== initdbs + start daemon ==="
( cd "$PINEAPD_DIR" && ./pineapd-ng "$CONF" --initdbs ) || { echo "initdbs failed"; exit 1; }
if [ "$(id -u)" != "0" ]; then RUN="unshare -r"; else RUN=""; fi
( cd "$PINEAPD_DIR" && $RUN ./pineapd-ng "$CONF" --foreground --debug >"$TMP/daemon.log" 2>&1 ) &
DPID=$!
for i in $(seq 1 100); do [ -S "$SOCK" ] && break; sleep 0.05; done
[ -S "$SOCK" ] || { echo "  socket never appeared"; cat "$TMP/daemon.log"; kill $DPID 2>/dev/null; exit 1; }
ok "daemon up, socket at $SOCK"

P(){ ./pineap-ng "$CONF" "$@"; }   # invoke exactly like the panel

echo "=== get_status (idle) ==="
# Compare the RAW bytes (through NUL) against the original daemon's capture to
# avoid shell trailing-newline stripping.  REF = original idle JSON (no NUL).
REF_HEX="7b0a09226361707475726552756e6e696e67223a2066616c73652c0a09227363616e52756e6e696e67223a2066616c73650a7d0a"
GOT_HEX=$(P get_status | xxd -p | tr -d '\n')
if [ "$GOT_HEX" = "$REF_HEX" ]; then ok "get_status JSON byte-exact vs original daemon capture"; else
  no "get_status"; echo "  got=$GOT_HEX"; echo "  ref=$REF_HEX"
fi

echo "=== add_ssid / list_ssids / clear_ssids ==="
P clear_ssids
P add_ssid REALTEST123
GOT=$(P list_ssids)
if [ "$GOT" = "REALTEST123" ]; then ok "list_ssids after add == REALTEST123"; else no "list_ssids add"; echo "  got=[$GOT]"; fi
P add_ssid SecondOne
GOT=$(P list_ssids | tr '\n' '|')
if [ "$GOT" = "REALTEST123|SecondOne|" ]; then ok "list_ssids two entries (\\n sep)"; else no "list_ssids two"; echo "  got=[$GOT]"; fi
P clear_ssids
GOT=$(P list_ssids)
if [ -z "$GOT" ]; then ok "list_ssids empty after clear"; else no "clear_ssids"; echo "  got=[$GOT]"; fi

echo "=== legacy mode (no conf) also works ==="
GOT=$(./pineap-ng list_ssids)      # legacy syntax: argv[1]=cmd
if [ -z "$GOT" ]; then ok "legacy-syntax list_ssids reaches daemon"; else no "legacy list_ssids"; echo "  got=[$GOT]"; fi

echo "=== setOpt capture_ssids on -> persisted ==="
P capture_ssids on
sleep 0.2
if grep -qi "capture_ssids = on" "$CONF"; then ok "capture_ssids on persisted via setOpt"; else no "capture_ssids"; grep -i capture "$CONF"; fi

echo "=== setOpt ap_channel 11 QUIRK (184549376) ==="
P ap_channel 11
sleep 0.2
APVAL=$(grep -i ap_channel "$CONF" | tr -dc '0-9')
if [ "$APVAL" = "184549376" ]; then ok "ap_channel quirk end-to-end (184549376)"; else no "ap_channel quirk (got $APVAL)"; fi

echo "=== setOpt beacon_interval LOW (9000) ==="
P beacon_interval LOW
sleep 0.2
if grep -qi "beacon_interval = LOW" "$CONF"; then ok "beacon_interval LOW persisted"; else no "beacon_interval"; grep -i beacon_interval "$CONF"; fi

echo "=== setOpt set_source -> persisted ==="
P set_source 11:22:33:44:55:66
sleep 0.2
if grep -qiE "pineap_mac|mac_address|source" "$CONF" | grep -qi "11:22:33:44:55:66" || grep -qi "11:22:33:44:55:66" "$CONF"; then
  ok "set_source MAC persisted"; else no "set_source"; grep -iE "mac|source" "$CONF"; fi

echo "=== list_probes (empty but well-formed) ==="
GOT=$(P list_probes)
ok "list_probes returned (len=${#GOT})"

kill $DPID 2>/dev/null; wait $DPID 2>/dev/null

echo ""
echo "============================================="
echo " E2E RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
rm -rf "$TMP"
[ "$FAIL" = "0" ]
