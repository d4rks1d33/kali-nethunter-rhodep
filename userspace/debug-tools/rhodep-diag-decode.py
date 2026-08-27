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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--tail", type=int, default=40)
    args = ap.parse_args()

    msgs, logs = [], []
    for ts, payload in records(args.path):
        if args.since is not None and ts < args.since:
            continue
        for ss_id, line, fmt, fname in scan_ext_msgs(payload):
            msgs.append((ts, ss_id, line, fmt, fname))
        for code, ln in scan_logs(payload):
            logs.append((ts, code, ln))

    print("=== extended messages: %d" % len(msgs))
    for ts, ss_id, line, fmt, fname in msgs[-args.tail:]:
        print("[%9.2f] ss=%-5d %s:%d  %s" % (ts, ss_id, fname or "?", line, fmt))

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
