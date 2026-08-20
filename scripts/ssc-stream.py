#!/usr/bin/env python3
"""
ssc-stream - stream raw samples from a Snapdragon Sensor Core sensor.

The sensors on this SoC are behind the SSC inside the ADSP, on buses Linux does
not own, so there is no /dev node and no IIO device to read.  ssc-probe.py
finds a sensor; this one opens it and prints what it produces.

Written to settle the accelerometer's mount matrix by measurement rather than
by argument: iio-sensor-proxy reports orientation buckets, not axes, and the
registry's placement matrix on this device is all zeros.

  sudo ./ssc-stream.py [data_type] [--rate HZ] [--seconds N]

Notes that cost time if you get them wrong, all inherited from ssc-probe.py:
msg_id is a protobuf FIXED32, not a varint; the QMI payload is a
variable-length array so it carries its own 16-bit element count; and TLV 0x10
must be 1 or the sensor core accepts everything and never sends an indication.
"""

import argparse
import socket
import struct
import sys
import time

QMI_SSC_SERVICE = 400
SNS_CLIENT_REQ = 0x20
SUID_LOOKUP = 0xABABABABABABABAB

MSGID_SUID_REQ = 512      # sns_suid_req
MSGID_STD_CONFIG = 513    # sns_std_sensor_config
MSGID_STD_EVENT = 1025    # sns_std_sensor_event


# ---------------------------------------------------------------- protobuf

def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def pb_bytes(num, data):
    return _varint((num << 3) | 2) + _varint(len(data)) + data


def pb_varint(num, val):
    return _varint((num << 3) | 0) + _varint(val)


def pb_fixed64(num, val):
    return _varint((num << 3) | 1) + struct.pack("<Q", val)


def pb_fixed32(num, val):
    return _varint((num << 3) | 5) + struct.pack("<I", val)


def pb_float(num, val):
    return _varint((num << 3) | 5) + struct.pack("<f", val)


def _read_varint(buf, i):
    shift = val = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
    return None, i


def pb_parse(buf):
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


# ---------------------------------------------------------------- messages

def client_request(suid_lo, suid_hi, msg_id, payload):
    """sns_client_request_msg{ suid=1, msg_id=2 (fixed32), susp=3, request=4 }

    Field 4 is an sns_std_request, whose field 2 is the payload -- not the
    payload directly.  ssc-enum.py has it this way and it is the shape that
    produces indications.
    """
    suid = pb_fixed64(1, suid_lo) + pb_fixed64(2, suid_hi)
    susp = pb_varint(1, 1) + pb_varint(2, 0)   # APSS, wakeup delivery
    return (pb_bytes(1, suid)
            + pb_fixed32(2, msg_id)
            + pb_bytes(3, susp)
            + pb_bytes(4, pb_bytes(2, payload)))


def qmi_wrap(txn, payload):
    body = struct.pack("<H", len(payload)) + payload
    tlvs = (b"\x10" + struct.pack("<H", 1) + b"\x01"
            + b"\x01" + struct.pack("<H", len(body)) + body)
    return (b"\x00"
            + struct.pack("<H", txn)
            + struct.pack("<H", SNS_CLIENT_REQ)
            + struct.pack("<H", len(tlvs))
            + tlvs)


def payload_tlv(pkt):
    """Return the protobuf payload out of a QMI message, or None.

    Requests carry it in TLV 0x01 and indications in TLV 0x02, and both put a
    16-bit element count in front of the protobuf because the field is declared
    as a variable-length array.  Looking only at 0x01 finds nothing and reads
    exactly like a sensor that never answers.
    """
    if len(pkt) < 7:
        return None
    body = pkt[7:]
    tlv = {}
    i = 0
    while i + 3 <= len(body):
        t = body[i]
        ln = struct.unpack("<H", body[i + 1:i + 3])[0]
        tlv[t] = body[i + 3:i + 3 + ln]
        i += 3 + ln

    # In an indication TLV 0x01 is the client id and 0x02 is the payload; in a
    # response it is the other way round and 0x02 is the 4-byte QMI result.
    # Taking 0x01 unconditionally hands back the client id and decodes as an
    # empty event list, which looks exactly like a sensor that says nothing.
    for t in (0x02, 0x01):
        val = tlv.get(t)
        if val is not None and len(val) > 4:
            return val[2:]
    return None


def decode_events(pb):
    """sns_client_event_msg{ suid=1, events=2 } -> [(msg_id, payload)]"""
    out = []
    for fn, wt, v in pb_parse(pb):
        if fn == 2 and wt == 2:
            msg_id = None
            data = None
            for efn, ewt, ev in pb_parse(v):
                if efn == 1:
                    msg_id = ev
                elif efn == 3 and ewt == 2:
                    data = ev
            out.append((msg_id, data))
    return out


# ---------------------------------------------------------------- transport

def find_service(s):
    """The sensor core's port moves across an ADSP restart (5:14 -> 5:13 was
    seen here), so it must be looked up rather than hardcoded.

    The QRTR control-plane NEW_LOOKUP does not answer a plain socket on this
    kernel -- net/qrtr/ns.c accepts lookups only from the local node and the
    reply never lands here -- so qrtr-lookup(1) is the reliable route.
    """
    CTRL_PORT = 0xFFFFFFFE
    try:
        s.sendto(struct.pack("<IIIII", 3, QMI_SSC_SERVICE, 0, 0, 0),
                 (1, CTRL_PORT))
        t0 = time.time()
        while time.time() - t0 < 1.5:
            try:
                pkt, _ = s.recvfrom(4096)
            except socket.timeout:
                break
            if len(pkt) >= 20 and struct.unpack("<I", pkt[0:4])[0] == 2:
                svc, ins, nd, pt = struct.unpack("<IIII", pkt[4:20])
                if svc == QMI_SSC_SERVICE:
                    return nd, pt
    except OSError:
        pass

    try:
        import subprocess
        out = subprocess.run(["qrtr-lookup"], capture_output=True,
                             text=True, timeout=10).stdout
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 5 and f[0] == str(QMI_SSC_SERVICE):
                return int(f[3]), int(f[4])
    except Exception:
        pass
    return None


def lookup_suid(s, addr, data_type, txn):
    req = pb_bytes(1, data_type.encode()) + pb_varint(2, 0) + pb_varint(3, 0)
    s.sendto(qmi_wrap(txn, client_request(SUID_LOOKUP, SUID_LOOKUP,
                                          MSGID_SUID_REQ, req)), addr)
    t0 = time.time()
    while time.time() - t0 < 5:
        try:
            pkt, _ = s.recvfrom(65536)
        except socket.timeout:
            continue
        if pkt[0] != 0x04:                    # indications only
            continue
        pb = payload_tlv(pkt)
        if not pb:
            continue
        for msg_id, data in decode_events(pb):
            if not data:
                continue
            # sns_suid_event { data_type = 1, suid = 2 }; an sns_std_suid on
            # the wire is 18 bytes: 09 <u64 low> 11 <u64 high>.
            for fn, wt, v in pb_parse(data):
                if fn == 2 and wt == 2 and len(v) == 18:
                    lo = struct.unpack("<Q", v[1:9])[0]
                    hi = struct.unpack("<Q", v[10:18])[0]
                    return lo, hi
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_type", nargs="?", default="accel")
    ap.add_argument("--rate", type=float, default=25.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args()

    if not hasattr(socket, "AF_QIPCRTR"):
        sys.exit("this kernel/python has no AF_QIPCRTR")

    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.bind((1, 0))
    s.settimeout(1.0)

    addr = find_service(s)
    if not addr:
        sys.exit("sensor core (QMI 400) not found -- is rhodep-sscrpcd running?")
    print("sensor core at node=%d port=%d" % addr)

    suid = lookup_suid(s, addr, args.data_type, 1)
    if not suid:
        sys.exit("no sensor for data type %r" % args.data_type)
    print("%s suid = %016x%016x" % (args.data_type, suid[1], suid[0]))

    # sns_std_sensor_config { float sample_rate = 1 }
    cfg = pb_float(1, args.rate)
    s.sendto(qmi_wrap(2, client_request(suid[0], suid[1],
                                        MSGID_STD_CONFIG, cfg)), addr)
    print("streaming at %g Hz for %g s ...\n" % (args.rate, args.seconds))

    t0 = time.time()
    n = 0
    while time.time() - t0 < args.seconds:
        try:
            pkt, _ = s.recvfrom(65536)
        except socket.timeout:
            continue
        if pkt[0] != 0x04:
            continue
        pb = payload_tlv(pkt)
        if not pb:
            continue
        for msg_id, data in decode_events(pb):
            if msg_id != MSGID_STD_EVENT or not data:
                continue
            for fn, wt, v in pb_parse(data):
                if fn == 1 and wt == 2 and len(v) % 4 == 0 and v:
                    vals = struct.unpack("<%df" % (len(v) // 4), v)
                    n += 1
                    print("  %8.3f  %s" % (
                        time.time() - t0,
                        "  ".join("%+9.4f" % x for x in vals)))
    print("\n%d samples" % n)
    s.close()


if __name__ == "__main__":
    sys.exit(main())
