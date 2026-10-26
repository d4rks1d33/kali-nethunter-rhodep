#!/usr/bin/env python3
# pineap_bridge.py - endianness-translating AF_UNIX relay between the qemu MIPSel
# firmware (pineapple/pineapple) and the native aarch64 pineapd-pager.
#
# WHY THIS EXISTS
#   Both sides speak the same framing: an 8-byte header =
#       [uint32 cmd][uint32 body_len]   followed by body_len bytes of msgpack.
#   BUT the firmware (MIPSel, little-endian Go) writes the two header words in
#   LITTLE-endian, while the native pineapd-pager reads/writes them in BIG-endian
#   (its msg_read_frame does `rev` on both words). The msgpack body itself is
#   endian-neutral (self-describing), so ONLY the 8-byte header needs swapping.
#
#   This relay transparently byte-swaps the two header words in each direction:
#     firmware(LE) --swap--> daemon(BE)       (client -> server)
#     daemon(BE)   --swap--> firmware(LE)     (server -> client)
#
# It listens at LISTEN (the path the firmware connects to, e.g. /tmp/pineap_sock)
# and forwards to UPSTREAM (the native daemon's socket).
#
# Usage: pineap_bridge.py [LISTEN] [UPSTREAM] [LOGFILE]
import socket, os, sys, struct, threading, time

LISTEN   = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pineap_sock"
UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else "/opt/pineapple-pager/run/pineap_sock"
LOG      = sys.argv[3] if len(sys.argv) > 3 else "/tmp/pineap_bridge.log"
MAX_BODY = 8 * 1024 * 1024  # sanity cap

_loglock = threading.Lock()
def log(msg):
    with _loglock:
        with open(LOG, "a") as f:
            f.write("[%.3f] %s\n" % (time.time(), msg))

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def swap_header(hdr8):
    # hdr8 = 8 bytes: word0 (cmd) + word1 (len). Swap each 4-byte word's endianness.
    cmd_in  = hdr8[0:4]
    len_in  = hdr8[4:8]
    return cmd_in[::-1] + len_in[::-1]

def pump(src, dst, tag, count):
    # Reads framed messages from src, swaps the header, forwards to dst.
    # The body length is taken from the *source* header in source-endianness.
    try:
        while True:
            hdr = recv_exact(src, 8)
            if hdr is None:
                log("%s: EOF (clean)" % tag); break
            # interpret length in SOURCE endianness
            if tag == "FW->DMN":
                # firmware is little-endian
                cmd  = struct.unpack("<I", hdr[0:4])[0]
                blen = struct.unpack("<I", hdr[4:8])[0]
            else:
                # daemon is big-endian
                cmd  = struct.unpack(">I", hdr[0:4])[0]
                blen = struct.unpack(">I", hdr[4:8])[0]
            if blen > MAX_BODY:
                log("%s: INSANE body len=%d cmd=%d hdr=%s -- dropping conn"
                    % (tag, blen, cmd, hdr.hex())); break
            body = b""
            if blen:
                body = recv_exact(src, blen)
                if body is None:
                    log("%s: EOF mid-body (wanted %d)" % (tag, blen)); break
            if count[tag] < 6:
                log("%s cmd=%d len=%d body=%s" % (tag, cmd, blen, body[:64].hex()))
                count[tag] += 1
            out = swap_header(hdr) + body
            try:
                dst.sendall(out)
            except Exception as e:
                log("%s: forward error %r" % (tag, e)); break
    except Exception as e:
        log("%s: pump error %r" % (tag, e))
    finally:
        try: dst.shutdown(socket.SHUT_WR)
        except Exception: pass

def handle(client):
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(UPSTREAM)
    except Exception as e:
        log("upstream connect failed: %r" % e); client.close(); return
    count = {"FW->DMN": 0, "DMN->FW": 0}
    log("=== connection open (upstream connected) ===")
    t1 = threading.Thread(target=pump, args=(client, up, "FW->DMN", count), daemon=True)
    t2 = threading.Thread(target=pump, args=(up, client, "DMN->FW", count), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()
    try: client.close()
    except Exception: pass
    try: up.close()
    except Exception: pass
    log("=== connection closed ===")

def main():
    try: os.unlink(LISTEN)
    except FileNotFoundError: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(LISTEN)
    os.chmod(LISTEN, 0o777)
    srv.listen(32)
    log("pineap_bridge listening at %s -> %s (header endianness translation ON)"
        % (LISTEN, UPSTREAM))
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

if __name__ == "__main__":
    main()
