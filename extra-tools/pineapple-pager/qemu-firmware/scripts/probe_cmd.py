#!/usr/bin/env python3
# probe_cmd.py - send one command through the bridge (as the firmware would, i.e.
# little-endian 8-byte header) and print the decoded msgpack response.
# Proves the firmware<->native channel end to end for an arbitrary command.
#
#   probe_cmd.py <cmd_number> [sockpath]
import socket, sys, struct

try:
    import msgpack
    HAVE_MP = True
except Exception:
    HAVE_MP = False

cmd  = int(sys.argv[1]) if len(sys.argv) > 1 else 100   # CMD_LIST_INTERFACES
path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/pineap_sock"

# empty msgpack body (nil/none); the firmware sends small maps, but many query
# commands accept an empty/none body. We send a msgpack nil (0xc0) as body.
body = b"\xc0"
# Firmware writes header LITTLE-endian: [u32 cmd][u32 len]
hdr = struct.pack("<II", cmd, len(body))

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(path)
s.sendall(hdr + body)

def recvn(sock, n):
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            break
        buf += c
    return buf

rhdr = recvn(s, 8)
if len(rhdr) < 8:
    print("NO/short response header:", rhdr.hex()); sys.exit(1)
# bridge translated the daemon's BE header back to LE for us
rcmd, rlen = struct.unpack("<II", rhdr)
rbody = recvn(s, rlen)
print("response cmd=%d len=%d" % (rcmd, rlen))
print("raw body hex:", rbody.hex())
if HAVE_MP:
    try:
        print("decoded:", msgpack.unpackb(rbody, raw=False, strict_map_key=False))
    except Exception as e:
        print("msgpack decode err:", repr(e))
s.close()
