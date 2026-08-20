#!/usr/bin/env python3
import socket, struct, sys, time
def _v(n):
    o=b""
    while True:
        b=n&0x7F; n>>=7; o+=bytes([b|(0x80 if n else 0)])
        if not n: return o
def pbb(n,d): return _v((n<<3)|2)+_v(len(d))+d
def pbv(n,v): return _v((n<<3)|0)+_v(v)
def pf64(n,v): return _v((n<<3)|1)+struct.pack("<Q",v)
def pf32(n,v): return _v((n<<3)|5)+struct.pack("<I",v)
S=0xABABABABABABABAB

def build(dt, txn):
    inner = pbb(1, dt.encode()) + pbv(2,0) + pbv(3,0)
    pb = (pbb(1, pf64(1,S)+pf64(2,S)) + pf32(2,512)
          + pbb(3, pbv(1,1)+pbv(2,0)) + pbb(4, pbb(2,inner)))
    body = struct.pack("<H",len(pb)) + pb
    tlvs = b"\x10"+struct.pack("<H",1)+b"\x01"+b"\x01"+struct.pack("<H",len(body))+body
    return b"\x00"+struct.pack("<H",txn)+struct.pack("<H",0x20)+struct.pack("<H",len(tlvs))+tlvs

TYPES = sys.argv[1:] or ["registry","timer","accel","interrupt","island",
                         "low_lat_stream","amd","gravity","sensor_pd"]
for i,dt in enumerate(TYPES):
    s=socket.socket(socket.AF_QIPCRTR,socket.SOCK_DGRAM); s.bind((1,0)); s.settimeout(1.5)
    s.sendto(build(dt,i+1),(5,14))
    n=None; t0=time.time()
    while time.time()-t0<3:
        try: p,_=s.recvfrom(65536)
        except socket.timeout: continue
        if p[0]==0x04:
            n=p.count(b"\x12\x12\x09"); break
    s.close()
    print("  %-20s %s" % (dt, "sin indicacion" if n is None else
                          ("%d suids%s" % (n, "   <<<<" if n else ""))))
