#!/usr/bin/env python3
"""
qdb_resolve.py - Decode a Qualcomm QDB (Qshrink-4 Hash Database) and resolve
32-bit F3 message ids to their format-strings.

File layout discovered:
  [0x00..0x3F]  64-byte plaintext header (magic 7f 51 44 42 5b "QDB[", GUID, build tag)
  [0x40..EOF]   single zlib stream (0x78 0xDA) -> ~27 MB ASCII text

The decompressed text has three sections, each one entry per line:
  <Content>          : <hash>:<ss_mask>:<ssid>:<line>:<file>:<string>   (F3 debug)
  <MtraceContent>    : <hash>:<...>                                     (Mtrace)
  <QtraceStrContent> : <hash>:<string>                                  (Qtrace strings)

The <hash> field IS the 32-bit message id used in Qshrink-4 (0x99) F3 frames.
"""
import sys, zlib, struct

def load(path):
    raw = open(path, 'rb').read()
    assert raw[:5] == b'\x7fQDB[', "not a QDB file"
    # zlib stream starts at 0x40
    text = zlib.decompress(raw[0x40:])
    return raw, text

def section(text, name):
    start = text.find(b'<%s> \n' % name.encode())
    if start < 0:
        start = text.find(b'<%s>\n' % name.encode())
    start = text.find(b'\n', start) + 1
    end = text.find(b'\n<\\%s>' % name.encode())
    return text[start:end]

def parse_content(text):
    """Return dict: id -> (ss_mask, ssid, line, file, fmt_string)."""
    d = {}
    for ln in section(text, 'Content').split(b'\n'):
        if not ln:
            continue
        p = ln.split(b':', 5)
        if len(p) < 6:
            continue
        try:
            h = int(p[0])
        except ValueError:
            continue
        d[h] = (int(p[1]), int(p[2]), int(p[3]),
                p[4].decode('latin1', 'replace'),
                p[5].decode('latin1', 'replace'))
    return d

def parse_simple(text, name):
    """For sections of form <hash>:<string>."""
    d = {}
    for ln in section(text, name).split(b'\n'):
        if not ln:
            continue
        p = ln.split(b':', 1)
        if len(p) < 2:
            continue
        try:
            h = int(p[0])
        except ValueError:
            continue
        d[h] = p[1].decode('latin1', 'replace')
    return d

def main():
    if len(sys.argv) < 2:
        print("usage: qdb_resolve.py <qdb> [id ...]"); return
    raw, text = load(sys.argv[1])
    content = parse_content(text)
    if len(sys.argv) == 2:
        print("[header] magic=%r build_tag=0x%04X" %
              (raw[:5], struct.unpack_from('<H', raw, 0x12)[0]))
        print("[Content] %d entries, id range %d..%d (0x%X)" %
              (len(content), min(content), max(content), max(content)))
        return
    for a in sys.argv[2:]:
        i = int(a, 0)
        e = content.get(i)
        if e:
            print("0x%X (%d): [%s:%d] %s" % (i, i, e[3], e[2], e[4]))
        else:
            print("0x%X (%d): <not found>" % (i, i))

if __name__ == '__main__':
    main()
