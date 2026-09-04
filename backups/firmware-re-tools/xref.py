#!/usr/bin/env python3
# Resolve add(pc,##off) -> absolute addr, and map to strings in the image.
import re, sys

DIS = "/tmp/opencode/fw/disasm.txt"
FW  = "/tmp/opencode/fw/wlanmdsp.mbn"

segs = [ (0x040000,0xb0000000,0x30d06c),(0x380000,0xb0340000,0x20528),(0x3a7000,0xb07a7000,0x24f68) ]
def va2off(va):
    for foff,base,sz in segs:
        if base<=va<base+sz: return foff+(va-base)
    return None
def off2va(off):
    for foff,base,sz in segs:
        if foff<=off<foff+sz: return base+(off-foff)
    return None

fw = open(FW,'rb').read()

def read_cstr(va, maxlen=96):
    off = va2off(va)
    if off is None: return None
    end = fw.find(b'\x00', off, off+maxlen)
    if end<0: return None
    try: s = fw[off:end].decode('ascii')
    except: return None
    if len(s)>=3 and all(32<=ord(c)<127 for c in s): return s
    return None

# Parse disasm: track packet start PC. In Hexagon, add(pc,#X) uses the packet's
# first instruction address (PC of packet start). llvm-objdump prints per-insn addr.
line_re = re.compile(r'^([0-9a-f]{6,8}):\s+(?:[0-9a-f]{2} ){4}\s*[0-9a-f]{8}\s+(.*)$')
addpc_re = re.compile(r'add\(pc,##(0x[0-9a-f]+)\)')

# Determine packet starts: a packet starts after a '}' ended the previous.
lines = open(DIS).read().splitlines()
pkt_start = None
prev_ended = True
results = []  # (insn_addr, pkt_start, target_va, string)
for ln in lines:
    m = line_re.match(ln.strip())
    if not m: 
        continue
    addr = int(m.group(1),16); body = m.group(2)
    if prev_ended:
        pkt_start = addr
    # add(pc,##X)
    for am in addpc_re.finditer(body):
        off = int(am.group(1),16)
        tgt = (pkt_start & 0xffffffff) + off
        s = read_cstr(tgt)
        if s:
            results.append((addr, pkt_start, tgt, s))
    prev_ended = body.rstrip().endswith('}')

# filter by query
q = sys.argv[1] if len(sys.argv)>1 else None
for addr,pk,tgt,s in results:
    if q is None or q in s:
        print(f"insn@{addr:#x} pkt@{pk:#x} -> str@{tgt:#x}  {s!r}")
