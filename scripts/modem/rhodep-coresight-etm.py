#!/usr/bin/python3
"""Read and set the remote ETM state on the modem and the ADSP (QMI service 51).

Why this exists. The silent whole-SoC reset on this port
(`docs/watchdog-ipa-lte-wip/`) is reached by the modem switching on a hardware
engine -- the GNSS measurement engine, or whatever LTE brings up at attach --
while the Q6 running ordinary software is fine. The largest piece of hardware
mainline does not describe on sm6375 is the QDSS/CoreSight fabric: a node diff
of `blair.dtsi` and its `holi-*.dtsi` includes against `sm6375.dtsi` gives 146
vendor-only nodes and about 110 of them are CoreSight, against **none** in
mainline. Meanwhile `qrtr-lookup` shows the modem publishing three instances of
service 51 and the ADSP two more, so the remote side of that fabric is running.

The question this answers is the first one worth asking: **is the remote ETM
actually enabled?** If it is, a trace source is pushing onto an ATB whose
funnels and sinks no one in this kernel has ever configured, and that is a bus
transaction that never completes -- which is the observed signature exactly.

Downstream drives this from `drivers/hwtracing/coresight/coresight-remote-etm.c`
over the same service; the protocol is two messages and is reproduced here:

    service 51 (0x33)
    0x002B  GET_ETM   request: no TLVs
                      response: TLV 0x02 result, optional TLV 0x10 state
    0x002C  SET_ETM   request: TLV 0x01, u32 state (0 disabled, 1 enabled)
                      response: TLV 0x02 result

Usage:

    ./rhodep-coresight-etm.py                 # read every instance (read-only)
    ./rhodep-coresight-etm.py --set 0         # disable remote ETM everywhere
    ./rhodep-coresight-etm.py --set 0 --node 0  # modem only
    ./rhodep-coresight-etm.py --raw           # also print the raw TLV bytes

Reading is safe. Writing is not obviously safe -- it tells another processor to
change its trace configuration -- so `--set` always prints what it is about to
do and to whom.
"""

import argparse
import binascii
import socket
import struct
import sys
import time

QRTR_PORT_CTRL = 0xFFFFFFFE
# From include/uapi/linux/qrtr.h. Getting these wrong is silent: the name
# service simply never answers. scripts/modem/memshare-probe.py has 2 and 8
# here, which are not these numbers, and that is the likeliest reason its
# publish "appeared to succeed" and never showed up in qrtr-lookup.
QRTR_TYPE_NEW_SERVER = 4
QRTR_TYPE_NEW_LOOKUP = 10

CORESIGHT_SERVICE = 0x33          # 51
QMI_GET_ETM = 0x002B
QMI_SET_ETM = 0x002C

STATE_NAMES = {0: "DISABLED", 1: "ENABLED"}


# ------------------------------------------------------------------ discovery

def lookup(service, timeout=2.0):
    """Ask the kernel name service which nodes/ports carry a service.

    net/qrtr/ns.c accepts NEW_LOOKUP only from the local node, which we are, so
    this is the same path libqrtr's qrtr_new_lookup() takes.
    """
    sock = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    # Do not bind: binding to port 0 is rejected outright and a client does not
    # need a fixed port, the kernel auto-binds on the first sendto.
    # getsockname() already reports the local node before any bind.
    local_node, _ = sock.getsockname()
    pkt = struct.pack("<IIIII", QRTR_TYPE_NEW_LOOKUP, service, 0, 0, 0)
    sock.sendto(pkt, (local_node, QRTR_PORT_CTRL))

    found = []
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        sock.settimeout(left)
        try:
            data, _addr = sock.recvfrom(4096)
        except socket.timeout:
            break
        if len(data) < 20:
            continue
        cmd, svc, inst_ver, node, port = struct.unpack("<IIIII", data[:20])
        if cmd != QRTR_TYPE_NEW_SERVER:
            continue
        if svc == 0 and node == 0 and port == 0:
            break                       # end of the lookup burst
        if svc != service:
            continue
        found.append((node, port, inst_ver >> 8, inst_ver & 0xFF))
    sock.close()
    return found


# ----------------------------------------------------------------------- QMI

def tlvs(payload):
    out = []
    i = 0
    while i + 3 <= len(payload):
        t = payload[i]
        ln = struct.unpack_from("<H", payload, i + 1)[0]
        out.append((t, payload[i + 3:i + 3 + ln]))
        i += 3 + ln
    return out


def qmi_call(node, port, msg_id, body=b"", txn=1, timeout=3.0):
    sock = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    # struct qmi_header: u8 type, u16 txn, u16 msg_id, u16 len
    hdr = struct.pack("<BHHH", 0x00, txn, msg_id, len(body))
    sock.sendto(hdr + body, (node, port))

    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            sock.close()
            return None, None
        sock.settimeout(left)
        try:
            data, _addr = sock.recvfrom(4096)
        except socket.timeout:
            sock.close()
            return None, None
        if len(data) < 7:
            continue
        rtype, rtxn, rmsg, rlen = struct.unpack_from("<BHHH", data, 0)
        if rtype == 0x02 and rtxn == txn:
            sock.close()
            return data[7:7 + rlen], data


def decode_result(parsed):
    for t, v in parsed:
        if t == 0x02 and len(v) >= 4:
            result, error = struct.unpack("<HH", v[:4])
            return result, error
    return None, None


def decode_state(parsed):
    for t, v in parsed:
        if t == 0x10:
            if len(v) >= 4:
                return struct.unpack("<I", v[:4])[0]
            if len(v) >= 1:
                return v[0]
    return None


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=int, choices=(0, 1), default=None,
                    help="set the remote ETM state (0 disabled, 1 enabled)")
    ap.add_argument("--node", type=int, default=None,
                    help="restrict to one QRTR node (0 is the modem here)")
    ap.add_argument("--raw", action="store_true", help="dump raw TLVs")
    args = ap.parse_args()

    servers = lookup(CORESIGHT_SERVICE)
    if args.node is not None:
        servers = [s for s in servers if s[0] == args.node]
    if not servers:
        print("no service 51 found (is the modem up?)", file=sys.stderr)
        return 1

    print("%-6s %-6s %-9s %-8s %s" % ("node", "port", "instance", "result",
                                      "state"))
    rc = 0
    for node, port, instance, version in servers:
        payload, whole = qmi_call(node, port, QMI_GET_ETM)
        if payload is None:
            print("%-6d %-6d %-9d %-8s %s" % (node, port, instance,
                                              "timeout", "-"))
            rc = 1
            continue
        parsed = tlvs(payload)
        result, error = decode_result(parsed)
        state = decode_state(parsed)
        print("%-6d %-6d %-9d %-8s %s" % (
            node, port, instance,
            "ok" if result == 0 else "err %s/%s" % (result, error),
            STATE_NAMES.get(state, state)))
        if args.raw:
            print("       raw %s" % binascii.hexlify(whole).decode())

    if args.set is None:
        return rc

    for node, port, instance, version in servers:
        print("SET node=%d port=%d instance=%d -> %s"
              % (node, port, instance, STATE_NAMES[args.set]))
        body = struct.pack("<BHI", 0x01, 4, args.set)
        payload, whole = qmi_call(node, port, QMI_SET_ETM, body, txn=2)
        if payload is None:
            print("       timeout")
            rc = 1
            continue
        result, error = decode_result(tlvs(payload))
        print("       %s" % ("ok" if result == 0
                             else "err %s/%s" % (result, error)))
        if args.raw:
            print("       raw %s" % binascii.hexlify(whole).decode())

    return rc


if __name__ == "__main__":
    sys.exit(main())
