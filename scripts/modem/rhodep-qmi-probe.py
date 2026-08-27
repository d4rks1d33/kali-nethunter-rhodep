#!/usr/bin/python3
"""Ask any QMI service on the modem what it can do.

Written to get at the modem's embedded file system service, which is where
/nv/item_files/conf/diag_bootup.conf lives -- the file the modem's diag reads
at boot, named in the firmware right next to

    Diag_LSM.c:servreg_get_local_domain() returned null

Most Qualcomm QMI services implement message 0x001E, "get supported
messages", which answers with a bitmask of the message IDs they accept. That
is enough to find out whether a service is reachable and what it will talk
about, without knowing its protocol first.

    ./rhodep-qmi-probe.py --service 21            # modem embedded file system
    ./rhodep-qmi-probe.py --service 21 --msg 0x20 # try a specific message

Service numbers come from `qrtr-lookup`. Useful ones on this phone:
    21  modem embedded file system     14  remote file system (ours)
    36  persistent device config       64  service registry locator
"""

import argparse
import socket
import struct
import sys
import time

QRTR_PORT_CTRL = 0xFFFFFFFE
QRTR_TYPE_NEW_SERVER = 4
QRTR_TYPE_NEW_LOOKUP = 10
QMI_GET_SUPPORTED_MSGS = 0x001E


def lookup(service, timeout=3.0):
    """Find node and port for a QMI service.

    Five 32-bit words, not six, and sent to our own node rather than node 1.
    This is lifted from rhodep-qmi-raw.py, which works; the first version here
    invented a six-word control message and got silence.
    """
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


def qmi_request(node, port, service_msg, payload=b"", timeout=3.0):
    """One QMI request, service form: 1-byte flags, 2-byte txn, then the TLVs."""
    # No bind: AF_QIPCRTR assigns a port on first use, and binding (0, 0)
    # explicitly is rejected with EINVAL.
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    body = struct.pack("<HH", service_msg, len(payload)) + payload
    msg = struct.pack("<BH", 0x00, 1) + body
    s.sendto(msg, (node, port))
    try:
        data, _ = s.recvfrom(4096)
    except socket.timeout:
        s.close()
        return None
    s.close()
    return data


def parse_tlvs(blob, off):
    out = []
    while off + 3 <= len(blob):
        t = blob[off]
        ln = struct.unpack_from("<H", blob, off + 1)[0]
        val = blob[off + 3:off + 3 + ln]
        out.append((t, val))
        off += 3 + ln
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", type=lambda x: int(x, 0), required=True)
    ap.add_argument("--msg", type=lambda x: int(x, 0),
                    default=QMI_GET_SUPPORTED_MSGS)
    ap.add_argument("--tlv", action="append", default=[],
                    metavar="ID=VALUE",
                    help="add a TLV. 0x01=text sends a bare string; "
                         "0x01:hex=41424344 sends raw bytes; "
                         "0x01:path=/nv/item_files/... sends a QMI_MFS path "
                         "(16-bit count prefix + bytes), which is what "
                         "service 21 needs -- a bare string answers error 19")
    args = ap.parse_args()

    payload = b""
    for spec in args.tlv:
        ident, _, value = spec.partition("=")
        if ident.endswith(":hex"):
            ident = ident[:-4]
            raw = bytes.fromhex(value)
        elif ident.endswith(":path"):
            # QMI variable-length array with a 16-bit element count prefix
            # (SZ_IS_16), which is how QMI_MFS (service 21) encodes a path.
            # A bare string is wrong: the decoder reads the first two path
            # bytes as the count and answers error 19 (ARGUMENT_TOO_LONG).
            ident = ident[:-5]
            b = value.encode()
            raw = struct.pack("<H", len(b)) + b
        else:
            raw = value.encode()
        payload += struct.pack("<BH", int(ident, 0), len(raw)) + raw
    if payload:
        print("payload: %s" % payload.hex())

    where = lookup(args.service)
    if not where:
        print("service %d did not answer the lookup" % args.service)
        return 1
    for node, port in where:
        print("service %d at node %d port %d" % (args.service, node, port))
        resp = qmi_request(node, port, args.msg, payload)
        if resp is None:
            print("  message %#x: no answer" % args.msg)
            continue
        print("  message %#x: %d bytes: %s" % (args.msg, len(resp), resp[:48].hex()))
        if len(resp) >= 7:
            msg_id, ln = struct.unpack_from("<HH", resp, 3)
            print("  reply id %#x, %d bytes of TLVs" % (msg_id, ln))
            for t, v in parse_tlvs(resp, 7):
                if t == 0x02 and len(v) >= 4:
                    res, err = struct.unpack("<HH", v[:4])
                    print("    result %d error %d" % (res, err))
                elif t == 0x10 and len(v) == 4:
                    # MFS response: efs_err_num (a real EFS errno)
                    print("    tlv 0x10 efs_err_num = %d" %
                          struct.unpack("<I", v)[0])
                elif t in (0x03, 0x11) and len(v) >= 2:
                    # MFS GET data: 16-bit count prefix then the file bytes.
                    # This build returns the data in 0x03; stock IDL uses 0x11.
                    n = struct.unpack("<H", v[:2])[0]
                    body = v[2:2 + n]
                    print("    tlv %#04x MFS data, %d bytes: %s" %
                          (t, n, body[:64].hex()))
                    printable = "".join(chr(c) if 32 <= c < 127 else "."
                                        for c in body[:64])
                    print("      as text: %s" % printable)
                else:
                    print("    tlv %#04x, %d bytes: %s" % (t, len(v), v[:32].hex()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
