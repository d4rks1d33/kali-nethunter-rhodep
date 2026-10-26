#!/usr/bin/env python3
# Mini socket test for pineapd-pager.
# Framing: [u32 cmd BE][u32 len BE][msgpack body]. Response = same framing.
import socket, struct, sys
import msgpack

SOCK = sys.argv[1] if len(sys.argv) > 1 else "/opt/pineapple-pager/run/pineap_sock"

def call(cmd, body_obj):
    body = msgpack.packb(body_obj, use_bin_type=True) if body_obj is not None else b""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(struct.pack(">II", cmd, len(body)) + body)
    hdr = b""
    while len(hdr) < 8:
        chunk = s.recv(8 - len(hdr))
        if not chunk:
            raise RuntimeError("short header")
        hdr += chunk
    rcmd, rlen = struct.unpack(">II", hdr)
    data = b""
    while len(data) < rlen:
        chunk = s.recv(rlen - len(data))
        if not chunk:
            break
        data += chunk
    s.close()
    obj = msgpack.unpackb(data, raw=False) if data else None
    return rcmd, obj

def main():
    rc = 0
    for cmd, body, label in [
        (100, {}, "list_interfaces"),
        (10,  {}, "gpsd_report"),
        (300, {}, "fetch_ssid_pool"),
        (200, {}, "count_devices"),
        (99999, {}, "unknown"),
    ]:
        try:
            rcmd, obj = call(cmd, body)
            ok = (rcmd == cmd) and isinstance(obj, dict)
            print(f"[{'OK ' if ok else 'ERR'}] cmd {cmd} ({label}) -> rcmd={rcmd} body={obj}")
            if not ok:
                rc = 1
        except Exception as e:
            print(f"[ERR] cmd {cmd} ({label}) -> exception: {e}")
            rc = 1
    sys.exit(rc)

if __name__ == "__main__":
    main()
