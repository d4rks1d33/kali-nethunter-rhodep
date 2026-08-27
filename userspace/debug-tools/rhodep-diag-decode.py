#!/usr/bin/env python3
"""Decode the DIAG packets in a capture: log ids, and extended messages.

Qualcomm's message database is not public, so a general DIAG decoder is out of
reach. Two things do not need it:

  * **Log packets** (command 0x10) carry a 16-bit log code and a timestamp.
    Even unnamed, "which log codes fired in the last second, and when" is a
    usable signal.
  * **Extended messages** (command 0x92) carry the source file name, the line
    number, the subsystem id and the format string *inline*, so they decode
    completely without any database.

    u8 cmd 0x92 | u8 ts_type | u8 num_args | u8 drop_cnt | u64 ts
    u16 line | u16 ss_id | u32 ss_mask | u32 args[num_args]
    then the format string and the file name, both NUL-terminated

The capture is scanned for both rather than parsed as a stream, because the
peripheral's own framing varies and a scan is robust against getting it wrong.
Each candidate is validated -- lengths must fit, strings must be printable --
so false positives are rare.

    rhodep-diag-decode.py /var/log/diag-death.bin --since 141
"""

import argparse
import struct
import sys


def records(path):
    with open(path, "rb") as f:
        blob = f.read()
    off = 0
    while off + 12 <= len(blob):
        if blob[off:off + 4] != b"DGPK":
            off += 1
            continue
        ln, ms = struct.unpack_from("<II", blob, off + 4)
        yield ms / 1000.0, blob[off + 12:off + 12 + ln]
        off += 12 + ln


def printable(b):
    return all(32 <= c < 127 for c in b) and len(b) > 0


def scan_ext_msgs(payload):
    """Every plausible 0x92 extended message in this payload."""
    out = []
    for i in range(len(payload) - 24):
        if payload[i] != 0x92:
            continue
        num_args = payload[i + 2]
        if num_args > 12:
            continue
        base = i + 12
        if base + 8 + 4 * num_args > len(payload):
            continue
        line, ss_id = struct.unpack_from("<HH", payload, base)
        rest = payload[base + 8 + 4 * num_args:]
        parts = rest.split(b"\0")
        if len(parts) < 2:
            continue
        fmt, fname = parts[0], parts[1]
        if not fmt or not printable(fmt) or len(fmt) < 4:
            continue
        if fname and not printable(fname):
            continue
        out.append((ss_id, line, fmt.decode(), fname.decode()))
    return out


def scan_logs(payload):
    """Log packets: 0x10, more, u16 len, then u16 len, u16 code, u64 ts."""
    out = []
    for i in range(len(payload) - 16):
        if payload[i] != 0x10 or payload[i + 1] != 0x00:
            continue
        ln1, ln2, code = struct.unpack_from("<HHH", payload, i + 2)
        if ln1 != ln2 or ln1 < 12 or ln1 > 4096:
            continue
        if i + 4 + ln1 > len(payload) + 8:
            continue
        out.append((code, ln1))
    return out


# LTE RRC over-the-air log, code 0xB0C0. The payload after the log header is
#
#     u8 version | u8 rrc_rel | u8 rrc_ver | u8 rbid | u16 phy_cell_id
#     freq (2 bytes on old versions, 4 on newer) | u16 sfn_subfn
#     u8 pdu_num | u16 len | the ASN.1 PDU
#
# The freq width varies by version and is not worth a version table: try both
# and keep whichever makes the declared length match the bytes that are
# actually there. That self-check is what keeps this honest.
PDU_NAMES = {
    1: "BCCH_BCH", 2: "BCCH_DL_SCH", 3: "MCCH", 4: "PCCH",
    5: "DL_CCCH", 6: "DL_DCCH", 7: "UL_CCCH", 8: "UL_DCCH",
}


def decode_rrc_ota(body):
    """Return (pdu_num, pdu_len, cell_id) or None if it does not check out.

    This does NOT decode cleanly yet. Two real packets from this phone:

        1b 10 10 0f a0 00 65 00 90 24 00 00 05 2d 03 ...   (len 55)
        1b 10 10 0f a0 01 65 00 90 24 00 00 00 00 0b ...   (len 142)

    Version 27, and no arrangement of the documented v2/v8/v13 layouts makes
    the frequency and length fields come out sane -- a u32 at the expected
    offset reads 0x24900065, which is not an EARFCN. The two packets differ
    only in byte 5, so the fields are not where the older layouts put them.

    Left returning None rather than guessing: a decoder that reports confident
    nonsense is worse than one that reports nothing. Getting this right needs
    the version 27 header definition, which is in Qualcomm's own tooling.
    """
    for freq_width in (4, 2):
        need = 6 + freq_width + 2 + 1 + 2
        if len(body) < need:
            continue
        cell = struct.unpack_from("<H", body, 4)[0]
        off = 6 + freq_width + 2
        pdu_num = body[off]
        pdu_len = struct.unpack_from("<H", body, off + 1)[0]
        if pdu_num in PDU_NAMES and 0 < pdu_len <= len(body) - (off + 3) + 4:
            return pdu_num, pdu_len, cell
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--tail", type=int, default=40)
    args = ap.parse_args()

    msgs, logs, rrc = [], [], []
    for ts, payload in records(args.path):
        if args.since is not None and ts < args.since:
            continue
        for ss_id, line, fmt, fname in scan_ext_msgs(payload):
            msgs.append((ts, ss_id, line, fmt, fname))
        for code, ln in scan_logs(payload):
            logs.append((ts, code, ln))
        # 0xB0C0 needs the body, so find it again with the offset kept
        for i in range(len(payload) - 16):
            if payload[i] != 0x10 or payload[i + 1] != 0x00:
                continue
            ln1, ln2, code = struct.unpack_from("<HHH", payload, i + 2)
            if ln1 != ln2 or code != 0xB0C0:
                continue
            # The log entry is cmd(1) more(1) len(2) len(2) code(2) ts(8),
            # so the payload starts at +16. Using +12 fed the decoder the last
            # four bytes of the timestamp and it rejected everything.
            got = decode_rrc_ota(payload[i + 16:i + 4 + ln1])
            if got:
                rrc.append((ts, got))

    print("=== extended messages: %d" % len(msgs))
    for ts, ss_id, line, fmt, fname in msgs[-args.tail:]:
        print("[%9.2f] ss=%-5d %s:%d  %s" % (ts, ss_id, fname or "?", line, fmt))

    if rrc:
        print()
        print("=== LTE RRC over-the-air messages: %d" % len(rrc))
        for ts, (pdu_num, pdu_len, cell) in rrc:
            print("[%9.2f] %-12s %3d bytes, cell %d"
                  % (ts, PDU_NAMES[pdu_num], pdu_len, cell))

    print()
    print("=== log codes, most recent first: %d packets" % len(logs))
    seen = {}
    for ts, code, ln in logs:
        seen.setdefault(code, [0, 0.0])
        seen[code][0] += 1
        seen[code][1] = max(seen[code][1], ts)
    for code, (n, last) in sorted(seen.items(), key=lambda kv: -kv[1][1])[:20]:
        print("  code %#06x  %4d packets, last at %.2f" % (code, n, last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
