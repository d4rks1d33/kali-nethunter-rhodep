#!/usr/bin/env python3
# Minimal Android A/B OTA payload.bin extractor (full-OTA only: REPLACE/BZ/XZ/ZERO).
# Extracts one partition (default: vendor) to <out>.
import struct, sys, bz2, lzma, hashlib, os

PAYLOAD = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lineage/payload.bin"
WANT    = sys.argv[2] if len(sys.argv) > 2 else "vendor"
OUT     = sys.argv[3] if len(sys.argv) > 3 else "/tmp/lineage/%s.img" % WANT

# update_metadata_pb2 is protobuf; parse the minimal fields we need by hand.
# payload header: magic "CrAU"(4) version(8 BE) manifest_size(8 BE)
#                 metadata_signature_size(4 BE, v2+) then manifest(protobuf)
f = open(PAYLOAD, "rb")
magic = f.read(4)
assert magic == b"CrAU", magic
version = struct.unpack(">Q", f.read(8))[0]
manifest_size = struct.unpack(">Q", f.read(8))[0]
metadata_sig_size = 0
if version >= 2:
    metadata_sig_size = struct.unpack(">I", f.read(4))[0]
manifest = f.read(manifest_size)
f.read(metadata_sig_size)
data_offset = f.tell()
print("version", version, "manifest", manifest_size, "data_offset", data_offset)

# --- tiny protobuf reader ---
def rd_varint(b, i):
    r = 0; s = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s
        if not (x & 0x80): break
        s += 7
    return r, i

def fields(b):
    # yield (field_number, wire_type, value_bytes_or_int, new_index)
    i = 0; n = len(b)
    while i < n:
        key, i = rd_varint(b, i)
        fn = key >> 3; wt = key & 7
        if wt == 0:
            v, i = rd_varint(b, i); yield fn, wt, v
        elif wt == 2:
            ln, i = rd_varint(b, i); yield fn, wt, b[i:i+ln]; i += ln
        elif wt == 5:
            yield fn, wt, b[i:i+4]; i += 4
        elif wt == 1:
            yield fn, wt, b[i:i+8]; i += 8
        else:
            raise ValueError("wt %d" % wt)

# DeltaArchiveManifest: partitions = field 13 (repeated PartitionUpdate)
# PartitionUpdate: partition_name = field 1 (string), operations = field 8 (repeated InstallOperation)
# InstallOperation: type=1(enum), data_offset=2, data_length=3, dst_extents=6(repeated Extent)
# Extent: start_block=1, num_blocks=2
BLOCK = 4096
OP_REPLACE=0; OP_REPLACE_BZ=1; OP_MOVE=2; OP_REPLACE_XZ=8; OP_ZERO=6; OP_DISCARD=7

part = None
for fn, wt, val in fields(manifest):
    if fn == 13 and wt == 2:  # partition_update
        name = None; ops = []
        for pfn, pwt, pval in fields(val):
            if pfn == 1 and pwt == 2:
                name = pval.decode()
            elif pfn == 8 and pwt == 2:
                ops.append(pval)
        if name == WANT:
            part = ops
            print("found partition", name, "with", len(ops), "ops")
            break

if part is None:
    # list available
    print("available partitions:")
    for fn, wt, val in fields(manifest):
        if fn == 13 and wt == 2:
            for pfn, pwt, pval in fields(val):
                if pfn == 1 and pwt == 2:
                    print("  ", pval.decode())
    sys.exit(1)

out = open(OUT, "wb")
for opbytes in part:
    op_type=None; d_off=0; d_len=0; extents=[]
    for ofn, owt, oval in fields(opbytes):
        if ofn == 1: op_type = oval
        elif ofn == 2: d_off = oval
        elif ofn == 3: d_len = oval
        elif ofn == 6 and owt == 2:
            sb=0; nb=0
            for efn, ewt, eval_ in fields(oval):
                if efn == 1: sb = eval_
                elif efn == 2: nb = eval_
            extents.append((sb, nb))
    f.seek(data_offset + d_off)
    raw = f.read(d_len)
    if op_type == OP_REPLACE:
        data = raw
    elif op_type == OP_REPLACE_BZ:
        data = bz2.decompress(raw)
    elif op_type == OP_REPLACE_XZ:
        data = lzma.decompress(raw)
    elif op_type == OP_ZERO:
        data = b"\0" * sum(nb*BLOCK for _, nb in extents)
    else:
        print("unsupported op_type", op_type); sys.exit(2)
    # write to the dst extent (full OTA: usually one extent starting at 0)
    pos = 0
    for (sb, nb) in extents:
        out.seek(sb*BLOCK)
        out.write(data[pos:pos+nb*BLOCK]); pos += nb*BLOCK
out.close()
print("wrote", OUT, os.path.getsize(OUT), "bytes")
