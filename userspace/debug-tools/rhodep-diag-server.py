#!/usr/bin/env python3
"""Publish the diag QRTR services the AP is supposed to publish, and listen.

The downstream diag driver has two transports. Everything on this port so far
has looked at the glink one, where diag is a set of named channels the modem
advertises. There is a second, diagfwd_socket.c, where diag runs over QRTR --
and there the direction is different in a way that matters:

    the AP is the SERVER for CNTL, DATA and DCI, and the modem connects to it
    the AP is a client for the modem's CMD and DCI_CMD services

    DIAG_SVC_ID       0x1001 (4097)
    MODEM_INST_BASE   0
    INST_ID_CNTL 0   CMD 1   DATA 2   DCI_CMD 3   DCI 4

So on a stock system the AP advertises service 4097 instances 0, 2 and 4 before
the modem has anything to connect to. This port never has: qrtr-lookup shows no
4097 from anyone. If the modem's diag waits to see somewhere to connect, that
absence is a candidate for why it never comes up -- and it is the one AP-side
trigger nobody has tried.

This publishes those services and then waits, printing anything that arrives.
It creates no glink endpoint and opens no channel, so it cannot assert the
modem the way rhodep-diag-probe.py does.

    sudo rhodep-diag-server.py --seconds 120 --restart-modem

Success looks like the modem connecting to one of these ports, or a DIAG
channel appearing on the glink edge while this is running. Both are watched.
"""

import argparse
import os
import socket
import struct
import sys
import time

QRTR_PORT_CTRL = 0xFFFFFFFE
QRTR_TYPE_NEW_SERVER = 4
DIAG_SVC_ID = 0x1001
MODEM_INST_BASE = 0
INSTANCES = {0: "CNTL", 2: "DATA", 4: "DCI"}
EDGE = "6080000.remoteproc"
CRASH = "/sys/kernel/debug/remoteproc/remoteproc0/crash"


def uptime():
    with open("/proc/uptime") as f:
        return f.read().split()[0]


def channels():
    out = set()
    try:
        for n in os.listdir("/sys/bus/rpmsg/devices"):
            if n.startswith(EDGE) and "glink-edge." in n:
                out.add(n.split("glink-edge.", 1)[-1].rsplit(".", 2)[0])
    except OSError:
        pass
    return out


def publish(instance, say):
    """Announce ourselves as service 0x1001, one instance, and keep the port."""
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    node, port = s.getsockname()
    pkt = struct.pack("<IIIII", QRTR_TYPE_NEW_SERVER,
                      DIAG_SVC_ID, MODEM_INST_BASE + instance, node, port)
    s.sendto(pkt, (node, QRTR_PORT_CTRL))
    say("published service %#x instance %d (%s) on node %d port %d"
        % (DIAG_SVC_ID, instance, INSTANCES[instance], node, port))
    s.setblocking(False)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--restart-modem", action="store_true",
                    help="crash the modem after publishing, so its diag sees "
                         "the services during its own boot")
    ap.add_argument("--log", default="/var/log/rhodep-diag-server.log")
    args = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("needs root")

    out = open(args.log, "w")

    def say(m):
        out.write("[%s] %s\n" % (uptime(), m))
        out.flush()
        os.fsync(out.fileno())

    say("publishing the diag services the AP normally provides")
    socks = {}
    for inst in sorted(INSTANCES):
        try:
            socks[inst] = publish(inst, say)
        except OSError as e:
            say("could not publish instance %d: %s" % (inst, e))
    if not socks:
        return 1

    before = channels()
    say("glink channels before: %s" % " ".join(sorted(before)))

    if args.restart_modem:
        say("crashing the modem so its diag boots with the services present")
        try:
            with open(CRASH, "w") as f:
                f.write("1")
        except OSError as e:
            say("cannot crash it: %s" % e)

    seen = before
    end = time.time() + args.seconds
    got = 0
    while time.time() < end:
        for inst, s in socks.items():
            try:
                data, addr = s.recvfrom(4096)
            except BlockingIOError:
                continue
            except OSError as e:
                say("instance %d read error: %s" % (inst, e))
                continue
            got += 1
            say("RX on %s from %s: %d bytes: %s"
                % (INSTANCES[inst], addr, len(data), data[:64].hex()))
        now = channels()
        if now != seen:
            new = now - seen
            if new:
                say("glink APPEARED: %s" % " ".join(sorted(new)))
            gone = seen - now
            if gone:
                say("glink withdrawn: %s" % " ".join(sorted(gone)))
            seen = now
        time.sleep(0.05)

    diag = sorted(n for n in seen if "DIAG" in n.upper())
    say("done. %d packets received on the diag services." % got)
    say("DIAG glink channels now: %s" % (" ".join(diag) if diag else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
