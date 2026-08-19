#!/usr/bin/env python3
"""Sweep the remaining unknowns now that the wire format is confirmed.

Field types come from SerializeWithCachedSizes in libsnsapi.so:
  sns_client_request_msg: 1=message 2=fixed32 3=message 4=message
  suspend_config:         1=enum 2=enum 3=fixed32
  sns_suid_req:           1=string 2=bool 3=bool
What is still guessed: the enum numbering, whether suspend_config field 3 is
required, and the exact data_type string.
"""
import socket, struct, sys, time

SUID = 0xABABABABABABABAB


def _v(n):
    o = b""
    while True:
        b = n & 0x7F
        n >>= 7
        o += bytes([b | (0x80 if n else 0)])
        if not n:
            return o


def pbb(n, d): return _v((n << 3) | 2) + _v(len(d)) + d
def pbv(n, v): return _v((n << 3) | 0) + _v(v)
def pbf64(n, v): return _v((n << 3) | 1) + struct.pack("<Q", v)
def pbf32(n, v): return _v((n << 3) | 5) + struct.pack("<I", v)


def build(dt, proc, deliv, reg, f3=None, msgid=512):
    req = pbb(1, dt.encode())
    if reg is not None:
        req += pbv(2, reg)
    susp = pbv(1, proc) + pbv(2, deliv)
    if f3 is not None:
        susp += pbf32(3, f3)
    suid = pbf64(1, SUID) + pbf64(2, SUID)
    return pbb(1, suid) + pbf32(2, msgid) + pbb(3, susp) + pbb(4, req)


def qmi(txn, p):
    body = struct.pack("<H", len(p)) + p
    tlv = b"\x01" + struct.pack("<H", len(body)) + body
    return b"\x00" + struct.pack("<H", txn) + struct.pack("<H", 0x20) \
        + struct.pack("<H", len(tlv)) + tlv


def attempt(label, pb, wait=3.0):
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.bind((1, 0))
    s.settimeout(1.0)
    s.sendto(qmi(1, pb), (5, 14))
    ind = None
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            pkt, _ = s.recvfrom(65536)
        except socket.timeout:
            continue
        if pkt and pkt[0] != 0x02:
            ind = pkt
            break
    s.close()
    if ind:
        print("  *** %-42s INDICACION %d bytes" % (label, len(ind)))
        print("      %s" % ind[:80].hex())
        return True
    print("      %-42s -" % label)
    return False


print("[1] delivery_type = 1 (NO_WAKEUP), barriendo proc_type")
for p in range(5):
    if attempt("proc=%d deliv=1" % p, build("accel", p, 1, 1)):
        sys.exit(0)

print("[2] suspend_config field 3 presente")
for f3 in (0, 1):
    for p in (0, 1):
        if attempt("proc=%d deliv=0 f3=%d" % (p, f3), build("accel", p, 0, 1, f3=f3)):
            sys.exit(0)

print("[3] register_updates variantes")
for reg in (None, 0):
    if attempt("register_updates=%s" % reg, build("accel", 0, 0, reg)):
        sys.exit(0)

print("[4] data_type variantes")
for dt in ("accel", "gyro", "sensor_suid", "suid", "lsm6dso", "proximity",
           "ambient_light", "mag", "interrupt", "resampler"):
    if attempt("data_type=%r" % dt, build(dt, 0, 0, 1)):
        sys.exit(0)

print("nada respondio")
