#!/usr/bin/env python3
"""
pdr-tool - list Qualcomm protection domains and start one.

Sensors on this SoC live behind the Snapdragon Sensor Core, which runs in a
protection domain on the ADSP ("msm/adsp/sensor_pd"). The sensor core answers
QMI but reports no sensors, which is what you see when that domain was never
started. This talks the SERVREG protocol to look at the domains and, if asked,
to start one.

The wire format is not guessed: it is the same protocol mainline implements in
drivers/soc/qcom/pdr_interface.c, with the TLV layout taken from
qcom_pdr_msg.c. Strings at the top level carry no length prefix - the TLV
length is the length.

  list            ask the locator (service 64) which domains exist
  state <path>    ask the notifier (service 66) for a domain's state
  restart <path>  ask the notifier to (re)start a domain

Example:
  sudo ./pdr-tool.py list
  sudo ./pdr-tool.py restart msm/adsp/sensor_pd
"""

import socket
import struct
import sys
import time

QMI_SERVREG_LOC = 64
QMI_SERVREG_NOTIF = 66

SERVREG_REGISTER_LISTENER_REQ = 0x20
SERVREG_GET_DOMAIN_LIST_REQ = 0x21
SERVREG_STATE_UPDATED_IND = 0x22
SERVREG_RESTART_PD_REQ = 0x24

CTRL_PORT = 0xFFFFFFFE

# enum servreg_service_state, from include/linux/soc/qcom/pdr.h
STATES = {
    0x1: "LOCATOR_ERR",
    0x0FFFFFFF: "DOWN",
    0x1FFFFFFF: "UP",
    0x2FFFFFFF: "EARLY_DOWN",
    0x7FFFFFFF: "UNINIT",
}


# ------------------------------------------------------------------ qrtr

def lookup(service):
    """Find every node/port publishing a QMI service.

    The NEW_LOOKUP control message gets no reply on this device, so fall back
    to parsing qrtr-lookup, which reads the same name server.
    """
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.bind((1, 0))
    s.settimeout(1.0)
    s.sendto(struct.pack("<IIIII", 3, service, 0, 0, 0), (1, CTRL_PORT))
    found = []
    t0 = time.time()
    while time.time() - t0 < 2:
        try:
            pkt, _ = s.recvfrom(4096)
        except socket.timeout:
            continue
        if len(pkt) >= 20 and struct.unpack("<I", pkt[0:4])[0] == 2:
            svc, inst, node, port = struct.unpack("<IIII", pkt[4:20])
            if svc == service:
                found.append((node, port, inst))
    s.close()
    if found:
        return found

    import subprocess
    try:
        out = subprocess.run(["qrtr-lookup"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].isdigit() and int(f[0]) == service:
            # service version instance node port
            found.append((int(f[3]), int(f[4]), int(f[2])))
    return found


def qmi_msg(txn, msg_id, tlvs):
    body = b""
    for t, v in tlvs:
        body += bytes([t]) + struct.pack("<H", len(v)) + v
    return (b"\x00" + struct.pack("<H", txn) + struct.pack("<H", msg_id)
            + struct.pack("<H", len(body)) + body)


def parse_tlvs(pkt):
    """Return {type: value} from a QMI message body."""
    out, body, i = {}, pkt[7:], 0
    while i + 3 <= len(body):
        t = body[i]
        ln = struct.unpack("<H", body[i + 1:i + 3])[0]
        out[t] = body[i + 3:i + 3 + ln]
        i += 3 + ln
    return out


def request(node, port, msg_id, tlvs, wait=4.0):
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    s.bind((1, 0))
    s.settimeout(1.0)
    s.sendto(qmi_msg(1, msg_id, tlvs), (node, port))
    resp = None
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            pkt, _ = s.recvfrom(65536)
        except socket.timeout:
            continue
        if len(pkt) >= 7 and struct.unpack("<H", pkt[3:5])[0] == msg_id:
            resp = pkt
            break
    s.close()
    return resp


def show_result(tlvs):
    r = tlvs.get(0x02)
    if r and len(r) >= 4:
        res, err = struct.unpack("<HH", r[:4])
        print("   result=%d error=%d %s" % (res, err, "OK" if res == 0 else "FAIL"))
        return res == 0
    print("   sin TLV de resultado")
    return False


# ---------------------------------------------------------------- actions

def do_list():
    locs = lookup(QMI_SERVREG_LOC)
    if not locs:
        sys.exit("el servicio locator (64) no esta en el bus - pd-mapper caido?")
    node, port, inst = locs[0]
    print("locator en node=%d port=%d" % (node, port))

    # get_domain_list_req: TLV 1 = service name, TLV 0x10 = offset
    offset = 0
    total = None
    while True:
        tlvs = [(0x01, b"tms/servreg"), (0x10, struct.pack("<I", offset))]
        pkt = request(node, port, SERVREG_GET_DOMAIN_LIST_REQ, tlvs)
        if not pkt:
            sys.exit("sin respuesta del locator")
        t = parse_tlvs(pkt)
        if not show_result(t):
            return
        if 0x10 in t and len(t[0x10]) >= 4:
            total = struct.unpack("<I", t[0x10][:4])[0]
        lst = t.get(0x12)
        if not lst or len(lst) < 1:
            break
        n = lst[0]
        print("   %d dominios en este bloque (total %s)" % (n, total))
        # each entry: name[65] + service_data_valid(1) + service_data(4) + instance(4)
        p = 1
        for _ in range(n):
            if p + 74 > len(lst):
                break
            name = lst[p:p + 65].split(b"\x00")[0].decode(errors="replace")
            sdv = lst[p + 65]
            sd, instance = struct.unpack("<II", lst[p + 66:p + 74])
            print("     %-34s instance=%-4d service_data=%d" % (name, instance, sd))
            p += 74
        offset += n
        if total is None or offset >= total or n == 0:
            break


def notif_targets():
    tg = lookup(QMI_SERVREG_NOTIF)
    if not tg:
        sys.exit("el notifier (66) no esta en el bus")
    return tg


def do_restart(path):
    for node, port, inst in notif_targets():
        print("restart %r via node=%d port=%d (instance %d)" % (path, node, port, inst))
        pkt = request(node, port, SERVREG_RESTART_PD_REQ,
                      [(0x01, path.encode())])
        if not pkt:
            print("   sin respuesta")
            continue
        show_result(parse_tlvs(pkt))


def do_state(path):
    for node, port, inst in notif_targets():
        print("register listener %r via node=%d port=%d (instance %d)"
              % (path, node, port, inst))
        # register_listener_req: TLV 1 = enable (u8), TLV 2 = service_path.
        # That order is not the obvious one; it is what qcom_pdr_msg.c says.
        pkt = request(node, port, SERVREG_REGISTER_LISTENER_REQ,
                      [(0x01, b"\x01"), (0x02, path.encode())])
        if not pkt:
            print("   sin respuesta")
            continue
        t = parse_tlvs(pkt)
        show_result(t)
        if 0x10 in t and len(t[0x10]) >= 4:
            st = struct.unpack("<I", t[0x10][:4])[0]
            print("   estado actual: %s (%d)" % (STATES.get(st, "?"), st))


if __name__ == "__main__":
    if not hasattr(socket, "AF_QIPCRTR"):
        sys.exit("este kernel/python no tiene AF_QIPCRTR")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        do_list()
    elif cmd == "state":
        do_state(sys.argv[2])
    elif cmd == "restart":
        do_restart(sys.argv[2])
    else:
        sys.exit(__doc__)
