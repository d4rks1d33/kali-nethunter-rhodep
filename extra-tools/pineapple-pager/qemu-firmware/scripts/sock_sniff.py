#!/usr/bin/env python3
# sock_sniff.py - transparent AF_UNIX relay that logs the first N bytes each way.
# Listens at LISTEN (what the firmware connects to), forwards to UPSTREAM (native daemon).
# Dumps hex of the initial traffic so we can determine the real header format.
import socket, os, sys, threading, time, binascii

LISTEN   = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pineap_sock"
UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else "/opt/pineapple-pager/run/pineap_sock"
LOG      = sys.argv[3] if len(sys.argv) > 3 else "/tmp/sock_sniff.log"
DUMP_MAX = 512

def log(msg):
    line = "[%.3f] %s\n" % (time.time(), msg)
    with open(LOG, "a") as f:
        f.write(line)

def hexdump(tag, data):
    h = binascii.hexlify(data[:DUMP_MAX]).decode()
    pairs = " ".join(h[i:i+2] for i in range(0, len(h), 2))
    log("%s len=%d hex=%s" % (tag, len(data), pairs))

def pump(src, dst, tag, state):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                log("%s EOF" % tag)
                break
            if state[tag] < 3:
                hexdump(tag, data)
                state[tag] += 1
            try:
                dst.sendall(data)
            except Exception as e:
                log("%s forward err: %r" % (tag, e)); break
    except Exception as e:
        log("%s recv err: %r" % (tag, e))
    finally:
        try: dst.shutdown(socket.SHUT_WR)
        except Exception: pass

def handle(client):
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(UPSTREAM)
    except Exception as e:
        log("UPSTREAM connect fail: %r" % e); client.close(); return
    log("=== new connection; upstream connected ===")
    state = {"C2S":0, "S2C":0}
    t1 = threading.Thread(target=pump, args=(client, up, "C2S", state), daemon=True)
    t2 = threading.Thread(target=pump, args=(up, client, "S2C", state), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()
    client.close(); up.close()

def main():
    try: os.unlink(LISTEN)
    except FileNotFoundError: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(LISTEN)
    os.chmod(LISTEN, 0o777)
    srv.listen(16)
    log("sniff relay listening at %s -> %s" % (LISTEN, UPSTREAM))
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

if __name__ == "__main__":
    main()
