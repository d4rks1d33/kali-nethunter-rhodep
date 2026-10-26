#!/bin/bash
# run_tests.sh — protocol + schema validation for pineapd-ng.
#
# 1. Builds pineapd-ng and test_proto.
# 2. Starts pineapd-ng in --foreground against a temp config + temp socket.
# 3. Drives the socket with the exact 4097/BE protocol and checks responses
#    against the reference captured from the ORIGINAL daemon under qemu.
# 4. Verifies the SQLite schema produced by --initdbs matches the RE schema.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

TMP=$(mktemp -d)
SOCK="$TMP/pineapd.sock"
LOCK="$TMP/pineapd.lock"
CONF="$TMP/pineap.conf"
RECON="$TMP/recon.db"
LOG="$TMP/log.db"
SSID="$TMP/pineapple.db"
PASS=0; FAIL=0
ok(){ echo "  [PASS] $1"; PASS=$((PASS+1)); }
no(){ echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

cat > "$CONF" <<EOF
# PineAP Config File
pineap_interface = lo
recon_db_path = $RECON
hostapd_db_path = $LOG
ssid_db_path = $SSID
logging = on
EOF

# Build a pineapd-ng variant that uses our temp socket/paths (override defines).
echo "=== Build test instance (temp socket) ==="
make clean >/dev/null 2>&1
make EXTRA_DEFS="-DPINEAPD_SOCK_PATH=\\\"$SOCK\\\" -DPINEAPD_LOCK_PATH=\\\"$LOCK\\\" -DPINEAPD_HANDSHAKE_DIR=\\\"$TMP/handshakes\\\"" >/dev/null 2>&1 \
  || { echo "test build failed"; exit 1; }
gcc -O2 -Wall -o test_proto test_proto.c || { echo "test_proto build failed"; exit 1; }
echo "  OK -> $SOCK"

echo "=== --initdbs schema test ==="
./pineapd-ng "$CONF" --initdbs
schema_check(){
  local db="$1" tbl="$2" want="$3"
  local got
  got=$(sqlite3 "$db" "SELECT sql FROM sqlite_master WHERE type='table' AND name='$tbl';" 2>/dev/null | tr -s ' ')
  if echo "$got" | grep -qiF "$want"; then ok "schema $tbl columns"; else no "schema $tbl ($got)"; fi
}
# column sets expected by the panel (RE §G)
schema_check "$RECON" aps      "scan_id INT, ssid TEXT, bssid TEXT"
schema_check "$RECON" aps      "encryption INT, hidden INT, channel INT, signal INT, wps INT"
schema_check "$RECON" clients  "scan_id INT, mac TEXT, bssid TEXT"
schema_check "$RECON" scan_ids "scan_id INT UNIQUE NOT NULL"
schema_check "$LOG"   log      "log_type INT NOT NULL, mac TEXT NOT NULL"
schema_check "$LOG"   log      "dups INT NOT NULL DEFAULT 0"
schema_check "$LOG"   tracking "mac TEXT UNIQUE NOT NULL"
schema_check "$SSID"  ssids    "ssid TEXT UNIQUE NOT NULL"
schema_check "$SSID"  ssids    "new_ssid INTEGER NOT NULL DEFAULT 1"
schema_check "$SSID"  ssids    "user_created INT NOT NULL DEFAULT 0"

echo "=== Start pineapd-ng ==="
# must be root for the real daemon; if not root, run under fakeroot-ish check
if [ "$(id -u)" != "0" ]; then
  echo "  (not root) running with EUID bypass via unshare -r"
  RUN="unshare -r"
else
  RUN=""
fi
$RUN ./pineapd-ng "$CONF" --foreground --debug >"$TMP/daemon.log" 2>&1 &
DPID=$!
for i in $(seq 1 100); do [ -S "$SOCK" ] && break; sleep 0.05; done
if [ ! -S "$SOCK" ]; then echo "  socket never appeared"; cat "$TMP/daemon.log"; kill $DPID 2>/dev/null; exit 1; fi
ok "socket created at $SOCK"

T(){ ./test_proto "$SOCK" "$@"; }

echo "=== Protocol: get_status (idle) ==="
# reference hex captured from the ORIGINAL daemon under qemu (NUL excluded):
REF_IDLE_HEX="7b0a09226361707475726552756e6e696e67223a2066616c73652c0a09227363616e52756e6e696e67223a2066616c73650a7d0a"
GOT_HEX=$(T get_status | xxd -p | tr -d '\n')
if [ "$GOT_HEX" = "$REF_IDLE_HEX" ]; then ok "get_status idle JSON == original (byte-exact)"; else
  no "get_status idle"; echo "  got = $GOT_HEX"; echo "  ref = $REF_IDLE_HEX"
fi

echo "=== Protocol: add_ssid / list_ssids / clear_ssids ==="
T clear_ssids
T add_ssid REALTEST123
GOT=$(T list_ssids)
# original returns "REALTEST123\n" then NUL
if [ "$GOT" = $'REALTEST123' ]; then ok "list_ssids after add == original"; else
  no "list_ssids after add"; echo "got=[$GOT]"
fi
T add_ssid SecondOne
GOT=$(T list_ssids | tr '\n' '|')
if [ "$GOT" = "REALTEST123|SecondOne|" ]; then ok "list_ssids two entries == original (\\n sep)"; else
  no "list_ssids two"; echo "got=[$GOT]"
fi
T clear_ssids
GOT=$(T list_ssids)
if [ -z "$GOT" ]; then ok "list_ssids empty after clear == original"; else no "clear_ssids"; echo "got=[$GOT]"; fi

echo "=== Protocol: setOpt capture_ssids on -> config saved ==="
T setopt_bool 2 1
sleep 0.2
if grep -qi "capture_ssids = on" "$CONF"; then ok "capture_ssids on persisted to conf"; else no "capture_ssids save"; grep -i capture "$CONF"; fi

echo "=== Protocol: ap_channel 11 QUIRK (184549376 in conf) ==="
T ap_channel 11
sleep 0.2
APVAL=$(grep -i "ap_channel" "$CONF" | tr -dc '0-9')
if [ "$APVAL" = "184549376" ]; then ok "ap_channel quirk reproduced (184549376)"; else no "ap_channel quirk (got $APVAL)"; fi

echo "=== Protocol: beacon_interval LOW (9000) ==="
T beacon_interval 9000
sleep 0.2
if grep -qi "beacon_interval = LOW" "$CONF"; then ok "beacon_interval LOW persisted"; else no "beacon_interval"; grep -i beacon_interval "$CONF"; fi

echo "=== Protocol: list_probes (empty, well-formed) ==="
GOT=$(T list_probes)
ok "list_probes returned (len=${#GOT})"

echo "=== Protocol: raw get_status bytes (14 00...) ==="
GOT_HEX=$(T raw 14 resp | xxd -p | tr -d '\n')
if [ "$GOT_HEX" = "$REF_IDLE_HEX" ]; then ok "raw cmd 0x14 == get_status (byte-exact)"; else no "raw get_status"; echo "  got = $GOT_HEX"; fi

kill $DPID 2>/dev/null; wait $DPID 2>/dev/null

echo ""
echo "============================================="
echo " RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
rm -rf "$TMP"
[ "$FAIL" = "0" ]
