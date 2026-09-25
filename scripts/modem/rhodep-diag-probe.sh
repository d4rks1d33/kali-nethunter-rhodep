#!/bin/bash
# rhodep-diag-probe.sh - validate the no-restart DIAG handshake in isolation and
# log everything to /tmp/iqrun/, so SSH drops don't lose the result.
#
#   sudo setsid bash rhodep-diag-probe.sh </dev/null >/tmp/iqrun/probe.log 2>&1 &
#
# Safe: stops ModemManager (the competing COMMAND peer), sets factory-test, does
# --kick, then sends whatever CMD is passed (default 00 = version). No modem
# restart, no IQ_CAPTURE unless RAW is given.
#
# Env:
#   CMD=00                 single DIAG cmd to send (hex), default version
#   RAWSEQ=""              if set, use --raw-seq instead of --cmd (comma hex list)
#   KEEP_MM=0              1 = don't stop ModemManager
#   FTM=1                  1 = set factory-test first

set +e
OUT=/tmp/iqrun
DIAG=${DIAG:-/home/kali/rhodep-diag-server.py}
CMD=${CMD:-00}
RAWSEQ=${RAWSEQ:-}
KEEP_MM=${KEEP_MM:-0}
FTM=${FTM:-1}
SECONDS_RUN=${SECONDS_RUN:-45}
CMD_AFTER=${CMD_AFTER:-8}

mkdir -p "$OUT"
exec > "$OUT/probe.log" 2>&1
ts() { date +%H:%M:%S; }
log() { echo "[$(ts)] $*"; }

log "=== diag probe ==="
if [ "$KEEP_MM" != 1 ]; then
	log "stopping ModemManager (competing COMMAND peer)"
	systemctl stop ModemManager 2>/dev/null
	pkill -9 ModemManager 2>/dev/null
	sleep 1
fi
# In restart mode the tool sets FTM itself via --set-ftm-after; only pre-set FTM
# in kick mode (no restart), where we need the modem already in FTM and live.
if [ "$FTM" = 1 ] && [ "${MODE:-kick}" != restart ]; then
	log "set factory-test"
	qmicli -d qrtr://0 --dms-set-operating-mode=factory-test 2>&1 | head -1
	sleep 2
fi
qmicli -d qrtr://0 --dms-get-operating-mode 2>&1 | grep -i mode

# MODE=kick (default, no restart, preserve RF state) or MODE=restart (crash the
# modem so the handshake is 100% reliable; --set-ftm-after re-enters FTM).
MODE=${MODE:-kick}
if [ "$MODE" = restart ]; then
	HS="--restart-modem --set-ftm-after"
else
	HS="--kick"
fi

if [ -n "$RAWSEQ" ]; then
	log "diag $HS --cmd-peer --raw-seq $RAWSEQ"
	python3 "$DIAG" --seconds "$SECONDS_RUN" $HS --cmd-peer \
		--raw-seq "$RAWSEQ" --cmd-after "$CMD_AFTER" --log "$OUT/diag.log" >/dev/null 2>&1
else
	log "diag $HS --cmd-peer --cmd $CMD"
	python3 "$DIAG" --seconds "$SECONDS_RUN" $HS --cmd-peer \
		--cmd "$CMD" --cmd-after "$CMD_AFTER" --log "$OUT/diag.log" >/dev/null 2>&1
fi

log "--- handshake markers ---"
grep -aE "CNTL peer|feature mask, 3|DIAGID from|sent .* on CNTL" "$OUT/diag.log" 2>/dev/null | head -8
log "--- replies (CMD/DATA/REPACK), control packets filtered ---"
grep -aE "CMD REPLY|DATA REPLY|CMD reply|REPACK|MATCH" "$OUT/diag.log" 2>/dev/null | grep -avE "status 0x06|status 0x03|0600000" | head -10
log "--- SSR count in the run ---"
echo "SSR(0x06/0x03) packets: $(grep -acE 'status 0x06|status 0x03' "$OUT/diag.log" 2>/dev/null)"
log "=== DONE ==="
