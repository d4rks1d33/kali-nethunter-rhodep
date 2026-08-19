#!/usr/bin/env python3
"""
ssc_probe - ask the Snapdragon Sensor Core which sensors it has.

The SSC is a QMI service (400) published by the ADSP, and its payloads are
protobuf. There is no kernel driver and no /dev node: sensors on this SoC hang
off buses the application processor does not own (the IMU is on I3C bus 1, the
magnetometers on the sensor core's own I2C bus 2), so the only way to reach
them from Linux is to speak this protocol.

This does the first step: a SUID lookup. Every sensor is identified by a 128-bit
SUID, and there is one well-known SUID - all 0xAB bytes - belonging to the
lookup sensor itself. Asking it for a data type comes back with the SUIDs of the
sensors that provide it.

Usage:  sudo ./ssc_probe.py [data_type]
"""

import socket
import struct
import sys
import time

QMI_SSC_SERVICE = 400
SNS_CLIENT_REQ = 0x20          # QMI message id for sns_client_request_msg
SUID_LOOKUP = 0xABABABABABABABAB
SNS_SUID_MSGID_SNS_SUID_REQ = 512

DATA_TYPE = sys.argv[1] if len(sys.argv) > 1 else "accel"


# ---------------------------------------------------------------- protobuf

def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def pb_field(num, wire, payload):
    return _varint((num << 3) | wire) + payload


def pb_bytes(num, data):
    return pb_field(num, 2, _varint(len(data)) + data)


def pb_varint(num, val):
    return pb_field(num, 0, _varint(val))


def pb_fixed64(num, val):
    return pb_field(num, 1, struct.pack("<Q", val))


def pb_fixed32(num, val):
    return pb_field(num, 5, struct.pack("<I", val))


def pb_parse(buf):
    """Minimal protobuf reader -> list of (field, wire, value)."""
    out, i = [], 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        if key is None:
            break
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _read_varint(buf, i)
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            if ln is None or i + ln > len(buf):
                break
            v, i = buf[i:i + ln], i + ln
        elif wt == 1:
            if i + 8 > len(buf):
                break
            v, i = struct.unpack("<Q", buf[i:i + 8])[0], i + 8
        elif wt == 5:
            if i + 4 > len(buf):
                break
            v, i = struct.unpack("<I", buf[i:i + 4])[0], i + 4
        else:
            break
        out.append((fn, wt, v))
    return out


def _read_varint(buf, i):
    shift, val = 0, 0
    while i < len(buf):
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
    return None, i


# ---------------------------------------------------------------- messages

def build_suid_request(data_type):
    # sns_suid_req { data_type = 1, register_updates = 2 }
    suid_req = pb_bytes(1, data_type.encode()) + pb_varint(2, 1)

    # sns_std_suid { suid_low = 1, suid_high = 2 }
    suid = pb_fixed64(1, SUID_LOOKUP) + pb_fixed64(2, SUID_LOOKUP)

    # suspend_config { client_proc_type = 1 (APSS), delivery_type = 0 (wakeup) }
    susp = pb_varint(1, 1) + pb_varint(2, 0)

    # sns_client_request_msg { suid=1, msg_id=2, susp_config=3, payload=4 }
    # msg_id is a fixed32, not a varint - SerializeWithCachedSizes in
    # libsnsapi.so calls WriteFixed32 for field 2. Encoding it as a varint
    # still produces a well-formed QMI TLV, so the service answers SUCCESS,
    # but the sensor core reads a different field and never replies.
    return (pb_bytes(1, suid)
            + pb_fixed32(2, SNS_SUID_MSGID_SNS_SUID_REQ)
            + pb_bytes(3, susp)
            + pb_bytes(4, suid_req))


def qmi_wrap(txn, msg_id, payload, count_prefix=True):
    """QMI service message: header + one TLV carrying the protobuf.

    sns_client_req_msg_v01 declares the payload as a variable-length array,
    and the QMI encoder puts an element count in front of the data. The max
    length is over 255, so that count is 16-bit.
    """
    body = struct.pack("<H", len(payload)) + payload if count_prefix else payload
    tlv = bytes([0x01]) + struct.pack("<H", len(body)) + body
    return (bytes([0x00])                       # control flags: request
            + struct.pack("<H", txn)
            + struct.pack("<H", msg_id)
            + struct.pack("<H", len(tlv))
            + tlv)


# ---------------------------------------------------------------- transport

def main():
    if not hasattr(socket, "AF_QIPCRTR"):
        sys.exit("this kernel/python has no AF_QIPCRTR")

    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.settimeout(5)

    # Find the SSC service. sockaddr_qrtr is (family, node, port).
    CTRL_PORT = 0xFFFFFFFE
    # struct qrtr_ctrl_pkt: cmd + {service, instance, node, port} = 20 bytes.
    # cmd 3 = NEW_LOOKUP, replies come back as cmd 2 = NEW_SERVER.
    lookup = struct.pack("<IIIII", 3, QMI_SSC_SERVICE, 0, 0, 0)
    s.sendto(lookup, (1, CTRL_PORT))

    node = port = None
    t0 = time.time()
    while time.time() - t0 < 3:
        try:
            pkt, addr = s.recvfrom(4096)
        except socket.timeout:
            break
        if len(pkt) >= 20:
            cmd, = struct.unpack("<I", pkt[0:4])
            if cmd == 2:  # NEW_SERVER
                svc, ins, nd, pt = struct.unpack("<IIII", pkt[4:20])
                if svc == QMI_SSC_SERVICE:
                    node, port = nd, pt
                    print("SSC service found: node=%d port=%d" % (nd, pt))
                    break
    if node is None:
        # qrtr-lookup already showed it at node 5 port 14; use that.
        node, port = 5, 14
        print("dynamic lookup failed, falling back to node=%d port=%d" % (node, port))

    pb = build_suid_request(DATA_TYPE)
    req = qmi_wrap(1, SNS_CLIENT_REQ, pb)
    print("asking for data_type=%r (protobuf %d bytes, qmi %d bytes)"
          % (DATA_TYPE, len(pb), len(req)))
    print("   %s" % req.hex())
    s.sendto(req, (node, port))

    # The response above only acks the request and hands back a client id;
    # the actual SUIDs arrive afterwards as asynchronous indications.
    s.settimeout(2)
    t0 = time.time()
    got = 0
    while time.time() - t0 < 12:
        try:
            pkt, addr = s.recvfrom(65536)
        except socket.timeout:
            continue
        got += 1
        print("\n<- %d bytes from node=%s port=%s" % (len(pkt), addr[0], addr[1]))
        print("   %s" % pkt[:48].hex())
        if len(pkt) > 7:
            body = pkt[7:]
            if body[:1] == b"\x02":            # TLV type 2 = result
                pass
            # walk TLVs looking for the protobuf payload
            i = 0
            while i + 3 <= len(body):
                t = body[i]
                ln = struct.unpack("<H", body[i + 1:i + 3])[0]
                val = body[i + 3:i + 3 + ln]
                if t == 0x01 and ln > 4:
                    print("   protobuf payload (%d bytes):" % ln)
                    for fn, wt, v in pb_parse(val):
                        show = v.hex() if isinstance(v, bytes) and len(v) <= 24 else v
                        print("     field %d wire %d = %s" % (fn, wt, show))
                        if isinstance(v, bytes) and len(v) == 20:
                            lo, hi = struct.unpack("<QQ", v[2:18])
                            print("       -> SUID %016x%016x" % (hi, lo))
                i += 3 + ln
    if not got:
        print("no reply")
    s.close()


if __name__ == "__main__":
    main()
