#!/usr/bin/env python3
# Extended round-trip test: ssid pool mutate + fetch, deauth graceful-fail.
import socket, struct, sys, msgpack
SOCK = sys.argv[1]

def call(cmd, body_obj):
    body = msgpack.packb(body_obj, use_bin_type=True) if body_obj is not None else b""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(struct.pack(">II", cmd, len(body)) + body)
    hdr = b""
    while len(hdr) < 8:
        hdr += s.recv(8 - len(hdr))
    rcmd, rlen = struct.unpack(">II", hdr)
    data = b""
    while len(data) < rlen:
        c = s.recv(rlen - len(data))
        if not c: break
        data += c
    s.close()
    return rcmd, (msgpack.unpackb(data, raw=False) if data else None)

rc = 0
# add two ssids via 302 (mod, flags=0 add)
print(call(302, {"ssids": ["FreeWiFi", "Starbucks"], "flags": 0}))
# fetch pool (300)
_, pool = call(300, {})
print("pool:", pool)
if sorted(pool.get("ssids", [])) != ["FreeWiFi", "Starbucks"]:
    print("FAIL: ssid pool mismatch"); rc = 1
# remove one via 303 (flags=1 remove)
print(call(303, {"ssid": "Starbucks", "flags": 1}))
_, pool = call(300, {})
print("pool after remove:", pool)
if pool.get("ssids", []) != ["FreeWiFi"]:
    print("FAIL: ssid remove mismatch"); rc = 1
# deauth (400) with no Archer -> expect error=1, graceful message
_, d = call(400, {"bssid": "aa:bb:cc:dd:ee:ff", "target": "11:22:33:44:55:66", "channel": 6, "count": 3})
print("deauth:", d)
if d.get("error") != 1:
    print("FAIL: deauth should have failed gracefully"); rc = 1
# clear pool (301)
print(call(301, {}))
_, pool = call(300, {})
if pool.get("ssids", []) != []:
    print("FAIL: clear failed"); rc = 1
print("=== rc", rc, "===")
sys.exit(rc)
