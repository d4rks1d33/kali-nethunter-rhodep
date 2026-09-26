#!/bin/bash
# rhodep-ftm-coredump.sh - capture a modem RAM coredump in the FTM/TECH_ENTER
# state and read the three RF-cal gate globals from it, to see whether TECH_ENTER
# / RADIO_CONFIG move cal_gate/rf_ctx/carrier off zero.
#
#   sudo setsid bash rhodep-ftm-coredump.sh </dev/null >/tmp/iqrun/coredump.log 2>&1 &
#   then read /tmp/iqrun/coredump.log and /tmp/iqrun/gates.txt
#
# Method: enable async (enabled) coredump, put the modem in FTM, run the diag sequence
# TECH_ENTER -> RADIO_CONFIG (which SSRs on the null carrier ptr). The crash
# produces a devcoredump captured at exactly the moment of the null-deref, i.e.
# with cal_gate/rf_ctx/carrier in whatever state the FTM+TECH_ENTER flow left them.
# Then parse the dump's ELF program headers and read the three globals.

set +e
OUT=/tmp/iqrun
DIAG=${DIAG:-/home/kali/rhodep-diag-server.py}
mkdir -p "$OUT"
exec > "$OUT/coredump.log" 2>&1
ts() { date +%H:%M:%S; }
log() { echo "[$(ts)] $*"; }

# gate globals: PA = VA - 0x35000000
# carrier 0xca79c494 -> 0x9579c494 ; cal_gate 0xcbf4f740 -> 0x96f4f740 ; rf_ctx 0xcbf56000 -> 0x96f56000
TE="4b0b27000d000300010004000000000002000400010000000300040000000000"
RC="4b0b27000d10050000000000000000010004000000000019000400010000000500040003000000060004002706000007000400030000000000"

log "=== FTM coredump capture ==="
log "stop ModemManager"
systemctl stop ModemManager 2>/dev/null; pkill -9 ModemManager 2>/dev/null
sleep 1

log "enable async (enabled) coredump, keep recovery on"
echo enabled > /sys/class/remoteproc/remoteproc0/coredump 2>&1; log "coredump=$(cat /sys/class/remoteproc/remoteproc0/coredump)"

# clear any stale devcoredump
for c in /sys/class/devcoredump/devcd*; do [ -e "$c/data" ] && echo 1 > "$c/data" 2>/dev/null; done

log "set factory-test"
qmicli -d qrtr://0 --dms-set-operating-mode=factory-test 2>&1 | head -1
sleep 2

log "diag: restart-modem handshake, TECH_ENTER -> RADIO_CONFIG (expected to SSR)"
python3 "$DIAG" --seconds 40 --restart-modem --set-ftm-after --cmd-peer \
	--raw-seq "$TE,$RC" --cmd-after 8 --log "$OUT/diag.log" >/dev/null 2>&1 &
DPID=$!

# poll for a devcoredump to appear (the SSR triggers it)
log "waiting for devcoredump..."
DUMP=""
for i in $(seq 1 40); do
	sleep 2
	for c in /sys/class/devcoredump/devcd*; do
		if [ -e "$c/data" ]; then
			sz=$(stat -c%s "$c/data" 2>/dev/null || echo 0)
			if [ "$sz" -gt 100000 ]; then DUMP="$c"; break; fi
		fi
	done
	[ -n "$DUMP" ] && break
done

if [ -z "$DUMP" ]; then
	log "no devcoredump appeared (inline coredump may not be wired, or no crash)"
	log "crashes in dmesg: $(dmesg | grep -cE 'crash detected in modem')"
	dmesg | grep -iE 'devcoredump|coredump|crash detected|rf_1x' | tail -6
else
	log "devcoredump at $DUMP, size $(stat -c%s "$DUMP/data") - copying"
	cp "$DUMP/data" "$OUT/mss-ftm.elf"
	# free it so recovery proceeds
	echo 1 > "$DUMP/data" 2>/dev/null
	log "saved $OUT/mss-ftm.elf ($(stat -c%s "$OUT/mss-ftm.elf") bytes)"
	log "--- reading the 3 gate globals from the FTM dump ---"
	python3 - "$OUT/mss-ftm.elf" > "$OUT/gates.txt" 2>&1 << 'PY'
import struct,sys
f=open(sys.argv[1],'rb'); d=f.read(52)
cls=d[4]
if cls==1:
    e_phoff=struct.unpack('<I',d[28:32])[0]; e_phentsize=struct.unpack('<H',d[42:44])[0]; e_phnum=struct.unpack('<H',d[44:46])[0]
    f.seek(e_phoff); ph=f.read(e_phentsize*e_phnum)
    def rd(pa,n):
        for i in range(e_phnum):
            o=i*e_phentsize
            if o+32>len(ph): break
            t,off,va,paddr,fsz,msz,fl,al=struct.unpack('<8I',ph[o:o+32])
            for base in (paddr,va):
                if base and base<=pa<base+fsz:
                    f.seek(off+(pa-base)); return f.read(n)
        return None
else:
    e_phoff=struct.unpack('<Q',d[32:40])[0]; e_phentsize=struct.unpack('<H',d[54:56])[0]; e_phnum=struct.unpack('<H',d[56:58])[0]
    f.seek(e_phoff); ph=f.read(e_phentsize*e_phnum)
    def rd(pa,n):
        for i in range(e_phnum):
            o=i*e_phentsize
            if o+48>len(ph): break
            t,fl=struct.unpack('<II',ph[o:o+8]); off,va,paddr,fsz,msz=struct.unpack('<QQQQQ',ph[o+8:o+48])
            for base in (paddr,va):
                if base and base<=pa<base+fsz:
                    f.seek(off+(pa-base)); return f.read(n)
        return None
for name,pa in (('carrier',0x9579c494),('cal_gate',0x96f4f740),('rf_ctx',0x96f56000)):
    v=rd(pa,8)
    print(f"{name} PA {hex(pa)}: {'NOT-IN-DUMP' if v is None else v.hex()}")
PY
	cat "$OUT/gates.txt"
fi

# reset coredump to disabled to avoid filling storage on future crashes
echo disabled > /sys/class/remoteproc/remoteproc0/coredump 2>&1
log "coredump reset to disabled"
log "=== DONE ==="
