#!/usr/bin/env python3
"""Parse the .replies sidecar from rhodep-diag-server.py --raw-seq.

Decodes FTM mode-105 (FTM_RF_TEST) responses, in particular IQ_CAPTURE (cmd=6),
extracting status / error_code / num_captures and any inline IQ sample blob.

Response body (QMSL qlib_defines.h, mode-105, little-endian, packed):
  off 0 : 4B 0B                 cmdCode, subsysId (echo)
  off 2 : subsysCmdCode u16     = mode_id (echo, 105)
  off 4 : cmd_id        u16     (echo)
  off 6 : data_len      u16
  off 8 : rsp_pkt_size  u16
  off10 : status        u16     (0 = OK)   [some builds: 1 byte status + pad]
  off12 : technology    u32
  off16 : num_properties/num_captures u32
  ...
Also tolerant decode of the per-capture layout:
  <error_code u32> [<specific_err u32>] <num_captures u32>
  per capture: <num_props u32> props[] <num_iq_data u32> <iq_data bytes>
"""
import struct
import sys


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def parse_rf_test_response(d):
    """Return a dict describing a 4b0b mode-105 response, best-effort."""
    out = {"len": len(d), "hex": d.hex()}
    if len(d) < 10 or d[0] != 0x4B or d[1] != 0x0B:
        out["note"] = "not a 4b0b subsys response"
        return out
    out["mode_id"] = u16(d, 2)
    out["cmd_id"] = u16(d, 4)
    out["data_len"] = u16(d, 6)
    out["rsp_pkt_size"] = u16(d, 8)
    # status: try u16 at 10
    if len(d) >= 12:
        out["status_u16@10"] = u16(d, 10)
    if len(d) >= 16:
        out["tech@12"] = u32(d, 12)
    # The field we care about: scan a plausible window for num_captures and
    # for an error_code. We print the first several u32 after the header so a
    # human can confirm the layout against qlib_defines.h.
    words = []
    o = 10
    while o + 4 <= len(d) and len(words) < 12:
        words.append((o, u32(d, o)))
        o += 4
    out["u32_window"] = words
    # Heuristic: in the fac4 responses error_code followed num_captures. Look
    # for a run where error_code==0 and the next u32 is a small count (<1e6).
    for i in range(len(words) - 1):
        off_e, ec = words[i]
        off_n, nc = words[i + 1]
        if ec == 0 and 0 < nc < 1_000_000:
            out["likely_error_code_off"] = off_e
            out["likely_num_captures_off"] = off_n
            out["likely_num_captures"] = nc
            break
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: rhodep-iq-parse.py <file.replies> [out_iq.bin]")
        sys.exit(1)
    path = sys.argv[1]
    out_iq = sys.argv[2] if len(sys.argv) > 2 else None
    iq_accum = bytearray()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            kind, step, hx = parts[0], parts[1], parts[2]
            try:
                d = bytes.fromhex(hx)
            except ValueError:
                continue
            tag = "%s step %s" % (kind, step)
            if d[:2] == b"\x4b\x0b" and u16(d, 2) == 105:
                r = parse_rf_test_response(d)
                print("=== %s : mode-105 cmd=%d len=%d ===" % (tag, r.get("cmd_id", -1), len(d)))
                print("   data_len=0x%x rsp=0x%x status@10=0x%x tech@12=%s"
                      % (r.get("data_len", 0), r.get("rsp_pkt_size", 0),
                         r.get("status_u16@10", -1), r.get("tech@12", "?")))
                print("   u32 window:", ["@%d=%d" % (o, v) for o, v in r.get("u32_window", [])])
                if "likely_num_captures" in r:
                    print("   >>> LIKELY num_captures=%d (off %d), error_code=0 (off %d)"
                          % (r["likely_num_captures"], r["likely_num_captures_off"],
                             r["likely_error_code_off"]))
                if r.get("cmd_id") == 6 and len(d) > 40:
                    # heuristic IQ blob: tail after the counters is the sample data
                    print("   (cmd=6 full hex) %s" % d.hex())
                    # accumulate anything that looks like a payload tail
                    iq_accum += d[20:]
            elif d[:2] == b"\x4b\x0b":
                print("=== %s : 4b0b mode=%d cmd=%d len=%d ===" % (tag, u16(d, 2), u16(d, 4), len(d)))
            else:
                print("=== %s : status 0x%02x len=%d %s ===" % (tag, d[0] if d else -1, len(d), d.hex()[:60]))
    if out_iq and iq_accum:
        with open(out_iq, "wb") as g:
            g.write(bytes(iq_accum))
        print("wrote %d bytes of candidate IQ tail to %s" % (len(iq_accum), out_iq))


if __name__ == "__main__":
    main()
