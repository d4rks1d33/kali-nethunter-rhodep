#!/usr/bin/python3
"""Publish QMI service 52 (memshare) and log whatever the modem sends to it.

This is a diagnostic, not an implementation. The question it answers is whether
the modem actually asks the application processor for memory, which the stock
device tree implies it does (qcom,memshare with a qcom,allocate-boot-time client
for the modem) and which nothing in mainline provides.

If the modem is stalled waiting for that allocation, publishing the service
should make it talk: QRTR notifies waiting clients when a new server appears.

STATUS: **this does not work yet, and its result so far means nothing.** The
script reports that it published, and the modem stays silent, but service 52
never actually appears in `qrtr-lookup` while it runs, whereas rmtfs (14) does.
So the announcement is being dropped somewhere and the modem was never offered
anything to talk to. Do not read the silence as evidence about memshare.

Binding to a fixed port before announcing (below) was one cause of exactly this
and is fixed; something else is still wrong. The obvious next move is to stop
hand rolling the control protocol and use libqrtr's qrtr_publish() from a small
C program, since that is known to work for rmtfs, tqftpserv and pd-mapper on
this very device, and only come back to raw sockets if the C version registers
correctly and more control is needed.
"""

import binascii
import socket
import struct
import sys
import time

QRTR_PORT_CTRL = 0xFFFFFFFE
QRTR_TYPE_NEW_SERVER = 2

MEMSHARE_SERVICE = 52
MEMSHARE_VERSION = 1
MEMSHARE_INSTANCE = 0

# Arbitrary, just needs to be free and non-zero.
LOCAL_PORT = 5052

CTRL_NAMES = {
    0: "HELLO", 1: "BYE", 2: "NEW_SERVER", 3: "DEL_SERVER",
    4: "DEL_CLIENT", 5: "RESUME_TX", 6: "EXIT", 7: "PING",
    8: "NEW_LOOKUP", 9: "DEL_LOOKUP",
}


def publish(sock, node, service, version, instance):
    """Announce a server on this port, the same way libqrtr's qrtr_publish does."""
    pkt = struct.pack("<IIIII",
                      QRTR_TYPE_NEW_SERVER,
                      service,
                      (instance << 8) | version,
                      0,   # node, filled in by the kernel
                      0)   # port, filled in by the kernel
    sock.sendto(pkt, (node, QRTR_PORT_CTRL))


def describe(data):
    """Best effort decode of a QMI header, enough to see what is being asked."""
    if len(data) < 7:
        return "(too short for a QMI header)"
    flags = data[0]
    txn, msg_id, msg_len = struct.unpack_from("<HHH", data, 1)
    kind = {0: "REQUEST", 2: "RESPONSE", 4: "INDICATION"}.get(flags, "type 0x%02x" % flags)
    return "QMI %s txn=%d msg_id=0x%04x len=%d" % (kind, txn, msg_id, msg_len)


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 60

    sock = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    node, _ = sock.getsockname()

    # Bind to a fixed port before announcing anything. This is not optional:
    # the kernel fills the node and port of a NEW_SERVER message from the
    # sending socket's own address, so publishing from a socket that has no
    # port yet registers a server on port 0. The call appears to succeed and
    # the service simply never shows up in qrtr-lookup, which is a good way to
    # convince yourself the modem ignored you when in fact you never published.
    # Binding to port 0 is rejected outright, so pick one.
    sock.bind((node, LOCAL_PORT))
    node, port = sock.getsockname()
    print("listening as node=%d port=%d" % (node, port), flush=True)

    publish(sock, node, MEMSHARE_SERVICE, MEMSHARE_VERSION, MEMSHARE_INSTANCE)
    print("published QMI service %d (memshare) v%d instance %d"
          % (MEMSHARE_SERVICE, MEMSHARE_VERSION, MEMSHARE_INSTANCE), flush=True)

    sock.settimeout(1.0)
    deadline = time.time() + seconds
    packets = 0

    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(65536)
        except socket.timeout:
            continue

        packets += 1
        src_node, src_port = addr
        print("\n--- packet %d from node=%d port=%d, %d bytes"
              % (packets, src_node, src_port, len(data)), flush=True)

        if src_port == QRTR_PORT_CTRL and len(data) >= 4:
            cmd = struct.unpack_from("<I", data, 0)[0]
            print("    control: %s" % CTRL_NAMES.get(cmd, "cmd %d" % cmd), flush=True)
        else:
            print("    %s" % describe(data), flush=True)

        print("    %s" % binascii.hexlify(data[:64]).decode(), flush=True)

    print("\ndone: %d packets in %ds" % (packets, seconds), flush=True)
    if not packets:
        print("nothing arrived; the modem did not take up the service", flush=True)


if __name__ == "__main__":
    main()
