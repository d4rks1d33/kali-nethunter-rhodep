#!/usr/bin/python3
"""Send an arbitrary QMI request over QRTR and print the response.

qmicli cannot restrict LTE bands on this modem -- it answers QMI error 25 --
but the message that does it, NAS SET_SYSTEM_SELECTION_PREFERENCE with the
LTE band preference TLV, is three TLVs and a header. This sends it directly.

  ./qmiraw.py get                       read the current selection preference
  ./qmiraw.py bands 0x8                 LTE only, band 4  (bit N-1 per band N)
  ./qmiraw.py bands 0x40                LTE only, band 7
  ./qmiraw.py bands 0x8000000           LTE only, band 28
  ./qmiraw.py bands ~0x8000000          LTE, everything the modem supports but 28
"""
import socket, struct, sys, time

QRTR_PORT_CTRL = 0xFFFFFFFE
QRTR_TYPE_NEW_SERVER = 4
QRTR_TYPE_NEW_LOOKUP = 10
NAS = 3
GET_SYS_SEL_PREF = 0x0034
SET_SYS_SEL_PREF = 0x0033
MODE_LTE = 0x10          # QmiNasRatModePreference: LTE = 1 << 4


def lookup(service, timeout=2.0):
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    node, _ = s.getsockname()
    s.sendto(struct.pack("<IIIII", QRTR_TYPE_NEW_LOOKUP, service, 0, 0, 0),
             (node, QRTR_PORT_CTRL))
    found = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s.settimeout(max(0.05, end - time.monotonic()))
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            break
        if len(d) < 20:
            continue
        cmd, svc, iv, n, p = struct.unpack("<IIIII", d[:20])
        if cmd == QRTR_TYPE_NEW_SERVER and svc == service and (n or p):
            found.append((n, p))
    s.close()
    return found


def tlvs(payload):
    out, i = [], 0
    while i + 3 <= len(payload):
        t = payload[i]
        ln = struct.unpack_from("<H", payload, i + 1)[0]
        out.append((t, payload[i + 3:i + 3 + ln]))
        i += 3 + ln
    return out


def call(node, port, msg_id, body=b"", txn=1, timeout=5.0):
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.sendto(struct.pack("<BHHH", 0x00, txn, msg_id, len(body)) + body, (node, port))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s.settimeout(max(0.05, end - time.monotonic()))
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            break
        if len(d) < 7:
            continue
        rt, rtxn, rmsg, rlen = struct.unpack_from("<BHHH", d, 0)
        if rt == 0x02 and rtxn == txn:
            s.close()
            return d[7:7 + rlen]
    s.close()
    return None


def show(payload):
    if payload is None:
        print("  timeout"); return
    for t, v in tlvs(payload):
        if t == 0x02 and len(v) >= 4:
            r, e = struct.unpack("<HH", v[:4])
            print("  result=%d error=%d%s" % (r, e, "  (OK)" if r == 0 else ""))
        else:
            print("  TLV 0x%02x len %d: %s" % (t, len(v), v.hex()))


srv = lookup(NAS)
if not srv:
    print("NAS service not found"); sys.exit(1)
node, port = srv[0]
print("NAS at node=%d port=%d" % (node, port))

cmd = sys.argv[1] if len(sys.argv) > 1 else "get"
if cmd == "get":
    show(call(node, port, GET_SYS_SEL_PREF))
elif cmd == "bands":
    arg = sys.argv[2]
    mask = (~int(arg[1:], 0) & 0xFFFFFFFFFFFFFFFF) if arg.startswith("~") else int(arg, 0)
    body = (struct.pack("<BHH", 0x11, 2, MODE_LTE) +          # mode preference
            struct.pack("<BHQ", 0x15, 8, mask) +              # lte band preference
            struct.pack("<BHB", 0x17, 1, 0))                  # change duration: power cycle
    print("setting LTE-only, lte_band_preference=0x%016x" % mask)
    show(call(node, port, SET_SYS_SEL_PREF, body, txn=2))
