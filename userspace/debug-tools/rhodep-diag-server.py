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

# DIAG control protocol, from diagfwd_cntl.h / diag_masks.c
DIAG_CTRL_MSG_FEATURE = 8
FEATURE_MASK_LEN = 4

# The bits the downstream AP sets, from diag_send_feature_mask_update():
#   0  FEATURE_MASK_SUPPORT      2  LOG_ON_DEMAND_APPS
#   4  REQ_RSP_SUPPORT           9  STM
#  11  MASK_CENTRALIZATION      13  SOCKETS_ENABLED
#  14  DCI_EXTENDED_HEADER      15  DIAGID_SUPPORT
#  20  MULTI_SIM_SUPPORT
# APPS_HDLC_ENCODE (6) is deliberately not set: over sockets we want the
# packets unframed, and downstream only sets it when the apps side does its own
# HDLC for the USB path.
AP_FEATURE_BITS = [0, 2, 4, 9, 11, 13, 14, 15, 20]


def ap_feature_mask():
    m = bytearray(FEATURE_MASK_LEN)
    for b in AP_FEATURE_BITS:
        m[b // 8] |= 1 << (b & 7)
    return bytes(m)


# The rest of diag_send_updates_peripheral(), in the order diag_masks.c sends
# them. A peripheral whose masks are unset has been told to report nothing, so
# the feature mask alone buys silence; these are what make it talk.
DIAG_CTRL_MSG_EQUIP_LOG_MASK = 9
DIAG_CTRL_MSG_EVENT_MASK_V2 = 10
DIAG_CTRL_MSG_F3_MASK_V2 = 11
DIAG_CTRL_MSG_DIAGMODE = 3
DIAG_CTRL_MSG_DIAGID = 33
DIAGID_VERSION_1 = 1
DIAG_ID_APPS = 1
DIAG_CTRL_MASK_ALL_ENABLED = 2
DIAG_CTRL_MASK_VALID = 3
STREAM_1 = 1
# The peripheral pushes these up unrequested when it has mask centralization,
# which this modem advertises (feature bit 11 of f7fe1b). They are how the AP
# learns which ssid ranges exist and what severities each was built with --
# which this server needs and had been throwing away unparsed.
DIAG_CTRL_MSG_LOG_RANGE_REPORT = 23
DIAG_CTRL_MSG_SSID_RANGE_REPORT = 24
DIAG_CTRL_MSG_BUILD_MASK_REPORT = 25
# Every command code that carries an F3 message. 0x79 is the plain extended
# message; 0x92, 0x99 are the QSR and QSR4 hash-compressed forms, and a 2024
# build is at least as likely to use QSR4 as plain text. Counting only 0x79
# and 0x92 would report "no F3" for a modem that is emitting plenty.
F3_CMD_CODES = (0x79, 0x92, 0x99, 0x9C, 0x9D)
# The ssid ranges this modem reported on 2026-09-06, used until its own
# SSID_RANGE_REPORT arrives -- which it does *after* the first DIAGID, i.e.
# after the masks would otherwise already have gone out. The stock driver does
# not have this problem because it has a msg_mask_tbl compiled in.
DEFAULT_SSID_RANGES = ((0, 134), (500, 506), (1000, 1007), (2000, 2008),
                       (3000, 3014), (4000, 4010), (4500, 4584), (4600, 4616),
                       (5000, 5036), (5500, 5517), (6000, 6081), (6500, 6521))


def event_mask_all():
    """struct diag_ctrl_event_mask, status ALL_ENABLED.

    With ALL_ENABLED the driver sets event_config 1 and event_mask_size 0, so
    no mask body is needed -- which is why this can be built without knowing
    the modem's event numbering.
    """
    body = struct.pack("<BBBI", STREAM_1, DIAG_CTRL_MASK_ALL_ENABLED, 1, 0)
    return struct.pack("<II", DIAG_CTRL_MSG_EVENT_MASK_V2, len(body)) + body


def log_mask_all():
    """struct diag_ctrl_log_mask, status ALL_ENABLED, equip_id 0."""
    body = struct.pack("<BBBII", STREAM_1, DIAG_CTRL_MASK_ALL_ENABLED,
                       0, 0, 0)
    return struct.pack("<II", DIAG_CTRL_MSG_EQUIP_LOG_MASK, len(body)) + body


def msg_mask_range(ssid_first, ssid_last, body=b"\x01\x01\x01\x01"):
    """One struct diag_ctrl_msg_mask per ssid RANGE, as the driver emits it.

    This is the packet msg_mask_all() should always have been, and the reason
    no F3 message has ever arrived on this port.

    diag_send_msg_mask_update() does NOT use the size-0 shortcut for messages.
    For DIAG_CTRL_MASK_ALL_ENABLED it sets mask_size = 1, multiplies by
    sizeof(u32), memcpy's four bytes of mask body, and sets data_len to
    MSG_MASK_CTRL_HEADER_LEN + 4 = 15. Then it loops: one packet per entry of
    msg_mask_tbl, each carrying that entry's own ssid_first/ssid_last, and it
    only breaks out early when the caller named a single range. The log mask
    and the event mask DO use the size-0 shortcut -- which is why those two
    have always worked here and this one never has. Copying their shape onto
    this path produced a 19-byte packet where the driver sends 23.

    ALL_SSID (-1) is an AP-internal sentinel meaning "walk every range". It
    never reaches the wire: the driver always writes mask->ssid_first. Sending
    0xffff/0xffff, as this did briefly, names a range the modem does not have.

    The four body bytes are what diag_cmd_set_all_msg_mask() memsets, so 0x01
    per byte for the usual runtime mask. Severity is per-ssid and is what
    msg_mask_build() sends instead once the modem has reported its own.
    """
    b = struct.pack("<BBBHHI", STREAM_1, DIAG_CTRL_MASK_ALL_ENABLED, 0,
                    ssid_first, ssid_last, 1) + body
    return struct.pack("<II", DIAG_CTRL_MSG_F3_MASK_V2, len(b)) + b


def msg_mask_build(ssid_first, levels):
    """status VALID, one u32 severity per ssid, from the modem's build mask.

    What SCAT does over the legacy 0x7d/0x04 command, and the one approach
    demonstrated to get F3 out of real hardware: do not ask for "everything",
    ask each ssid for exactly the severities its build was compiled with. The
    modem hands those over unrequested in DIAG_CTRL_MSG_BUILD_MASK_REPORT, so
    this costs nothing to send and cannot ask for a level that does not exist.
    """
    b = struct.pack("<BBBHHI", STREAM_1, DIAG_CTRL_MASK_VALID, 0,
                    ssid_first, ssid_first + len(levels) - 1, len(levels))
    b += b"".join(struct.pack("<I", x) for x in levels)
    return struct.pack("<II", DIAG_CTRL_MSG_F3_MASK_V2, len(b)) + b


def diagmode_packet():
    """struct diag_ctrl_msg_diagmode: put the peripheral in memory-device mode
    with real-time reporting, which is what tells it to stream at all."""
    # version, sleep_vote, real_time, use_nrt_values, commit_threshold,
    # sleep_threshold, sleep_time, drain_timer_val, event_stale_timer_val
    body = struct.pack("<IIIIIIIII", 1, 0, 1, 0, 0, 0, 0, 0, 0)
    return struct.pack("<II", DIAG_CTRL_MSG_DIAGMODE, len(body)) + body


def diagid_reply(diag_id, process_name):
    """Echo a DIAGID back with the id the AP assigns.

    This is the step that was missing. process_diagid() in diagfwd_cntl.c
    answers every DIAGID the peripheral sends, and its own comment says the
    masks only take effect once it has:

        "Masks (F3, logs and events) will be sent to peripheral ... only if
         diag_id support is not present or diag_id support is present and
         diag_id has been sent to peripheral."

    We advertise DIAGID_SUPPORT in the feature mask, so the modem waits for
    this. Sending masks before it, as the previous attempt did, is sending them
    into a peripheral that is not listening for them yet.

        header: pkt_id, len, version    then diag_id, then the name with a NUL
        len = 4 (version) + 4 (diag_id) + len(name) + 1
    """
    name = process_name.encode() + b"\0"
    ln = 4 + 4 + len(name)
    return struct.pack("<III", DIAG_CTRL_MSG_DIAGID, ln,
                       DIAGID_VERSION_1) + struct.pack("<I", diag_id) + name


def feature_mask_packet():
    """struct diag_ctrl_feature_mask: id, data_len, mask_len, then the mask."""
    mask = ap_feature_mask()
    return struct.pack("<III", DIAG_CTRL_MSG_FEATURE,
                       4 + len(mask), len(mask)) + mask
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
    ap.add_argument("--cmd", metavar="HEX",
                    help="after the handshake, send this DIAG request to the "
                         "modem's CMD service and print what comes back. "
                         "0x00 is version, 0x0c verno, 0x7c extended build id")
    ap.add_argument("--cmd-after", type=float, default=8.0,
                    help="seconds to wait for the handshake before sending")
    ap.add_argument("--dump", metavar="FILE",
                    help="append every DATA packet from the modem to FILE, "
                         "raw, fsync'd per packet. This is what survives the "
                         "reset: the modem's own log stream up to the instant "
                         "the SoC is switched off. Each record is a 12-byte "
                         "header -- magic 'DGPK', u32 length, u32 uptime in "
                         "milliseconds -- then the payload.")
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

    dump = open(args.dump, "wb") if args.dump else None

    next_diag_id = [DIAG_ID_APPS + 1]
    masks_sent = [False]

    before = channels()
    say("glink channels before: %s" % " ".join(sorted(before)))

    if args.restart_modem:
        say("crashing the modem so its diag boots with the services present")
        try:
            with open(CRASH, "w") as f:
                f.write("1")
        except OSError as e:
            say("cannot crash it: %s" % e)

    def modem_cmd_port():
        """Where the modem publishes its DIAG CMD service, instance 1.

        It only exists once the modem's diag is up, which is the whole point:
        before anyone published, there was no 4097 from node 0 at all.
        """
        lk = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
        node, _ = lk.getsockname()
        lk.sendto(struct.pack("<IIIII", 10, DIAG_SVC_ID, 0, 0, 0),
                  (node, QRTR_PORT_CTRL))
        lk.settimeout(2.0)
        found = None
        end_l = time.time() + 2.0
        while time.time() < end_l:
            try:
                d, _ = lk.recvfrom(4096)
            except socket.timeout:
                break
            if len(d) < 20:
                continue
            cmd, svc, inst, n, prt = struct.unpack("<IIIII", d[:20])
            if cmd == QRTR_TYPE_NEW_SERVER and svc == DIAG_SVC_ID and n == 0:
                if inst == MODEM_INST_BASE + 1:
                    found = (n, prt)
        lk.close()
        return found

    ssid_ranges = [list(DEFAULT_SSID_RANGES)]
    resent = [False]

    def send_msg_masks(sock, addr, ranges, why):
        """One F3 control packet per ssid range. See msg_mask_range()."""
        for a, z in ranges:
            mp = msg_mask_range(a, z)
            try:
                sock.sendto(mp, addr)
                time.sleep(0.01)
            except OSError as e:
                say("  could not send msg mask %d-%d: %s" % (a, z, e))
                return
        say("  sent %d msg mask packets (%s), first: %s"
            % (len(ranges), why, msg_mask_range(*ranges[0]).hex()))

    def handle_cntl(sock, addr, pid, pkt):
        """The three reports a mask-centralizing peripheral pushes up.

        With feature bit 11 the peripheral does not own its masks: it uploads
        its tables and expects the master to send masks back down. This server
        was sending masks down without ever reading the tables up, which is
        half a protocol.
        """
        if pid == DIAG_CTRL_MSG_SSID_RANGE_REPORT and len(pkt) >= 16:
            ver, count = struct.unpack_from("<II", pkt, 8)
            rngs = [struct.unpack_from("<HH", pkt, 16 + 4 * i)
                    for i in range(count) if 16 + 4 * i + 4 <= len(pkt)]
            if not rngs:
                return
            say("  modem ssid ranges (%d): %s"
                % (count, " ".join("%d-%d" % r for r in rngs)))
            ssid_ranges[0] = rngs
            if not resent[0]:
                resent[0] = True
                send_msg_masks(sock, addr, rngs, "modem's own ranges")
        elif pid == DIAG_CTRL_MSG_BUILD_MASK_REPORT and len(pkt) >= 20:
            # Per range: u16 first, u16 last, then (last-first+1) u32 levels.
            # 0x1f is LOW|MED|HIGH|ERROR|FATAL, i.e. the strings are compiled
            # in -- which settles, from the modem's own mouth, that F3 silence
            # here is a protocol problem and not a stripped build.
            a, z = struct.unpack_from("<HH", pkt, 16)
            n = z - a + 1
            if n <= 0 or 20 + 4 * n > len(pkt):
                return
            lv = list(struct.unpack_from("<%dI" % n, pkt, 20))
            say("  modem build mask %d-%d: %s%s"
                % (a, z, " ".join("%#x" % x for x in lv[:12]),
                   " ..." if n > 12 else ""))
            mp = msg_mask_build(a, lv)
            try:
                sock.sendto(mp, addr)
                say("  sent build-derived msg mask %d-%d (%d bytes)"
                    % (a, z, len(mp)))
            except OSError as e:
                say("  could not send build mask: %s" % e)
        elif pid == DIAG_CTRL_MSG_LOG_RANGE_REPORT and len(pkt) >= 16:
            ver, last_eq, nr = struct.unpack_from("<III", pkt, 8)[:3]
            say("  modem log ranges: last_equip=%d num_ranges=%d"
                % (last_eq, nr))

    cmd_sock = None
    cmd_deadline = time.time() + args.cmd_after if args.cmd else None

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
            if dump is not None and INSTANCES[inst] == "DATA":
                # fsync per packet on purpose. The reset is a power cycle and
                # anything still in the page cache is gone; the whole point of
                # this file is the last few packets before it.
                ms = int(float(uptime()) * 1000)
                dump.write(b"DGPK" + struct.pack("<II", len(data), ms) + data)
                dump.flush()
                os.fsync(dump.fileno())
            # CNTL is never truncated. It is where the modem reports its ssid
            # ranges, its build masks and the command codes it handles, and
            # clipping at 64 bytes hid all three for the whole life of this
            # tool. DATA is still clipped; it is 4 KB a packet and it is in
            # the dump anyway.
            say("RX on %s from %s: %d bytes: %s"
                % (INSTANCES[inst], addr, len(data),
                   data.hex() if INSTANCES[inst] == "CNTL"
                   else data[:64].hex()))
            # The modem concatenates several control packets into one
            # datagram -- the 4020, 3692 and 2888 byte reads are all
            # multi-packet -- and this used to parse only the first, so
            # everything after it was silently dropped. Walk the datagram the
            # way diag_cntl_process_read_data() does.
            if INSTANCES[inst] == "CNTL" and addr[0] != 1:
                pos = 0
                while pos + 8 <= len(data):
                    pid, dl = struct.unpack_from("<II", data, pos)
                    end_p = pos + 8 + dl
                    if dl == 0 or end_p > len(data):
                        break
                    handle_cntl(s, addr, pid, data[pos:end_p])
                    pos = end_p
            # The handshake is modem-first: it sends its feature mask and only
            # then will it talk. Answer with ours, to the port it came from.
            if len(data) >= 12 and addr[0] != 1:
                pkt_id, dlen = struct.unpack_from("<II", data, 0)
                if pkt_id == DIAG_CTRL_MSG_FEATURE:
                    mlen = struct.unpack_from("<I", data, 8)[0]
                    peer = data[12:12 + mlen]
                    say("  that is the modem's feature mask, %d bytes: %s"
                        % (mlen, peer.hex()))
                    pkt = feature_mask_packet()
                    try:
                        s.sendto(pkt, addr)
                        say("  sent feature mask   %s" % pkt.hex())
                    except OSError as e:
                        say("  could not reply: %s" % e)
                elif pkt_id == DIAG_CTRL_MSG_DIAGID and len(data) >= 16:
                    ver, peer_id = struct.unpack_from("<II", data, 8)
                    pname = data[16:].split(b"\0")[0].decode("ascii", "replace")
                    assigned = next_diag_id[0]
                    next_diag_id[0] += 1
                    say("  DIAGID from the modem: id %#x name %r" % (peer_id, pname))
                    ack = diagid_reply(assigned, pname)
                    try:
                        s.sendto(ack, addr)
                        say("  acked with our diag_id %d: %s" % (assigned, ack.hex()))
                    except OSError as e:
                        say("  could not ack: %s" % e)
                        continue
                    # Only now are the masks meaningful, per the driver's own
                    # comment. Send them once, after the first ack.
                    if not masks_sent[0]:
                        masks_sent[0] = True
                        # msg first, then log, then event: the order
                        # diag_send_updates_peripheral() uses. The msg mask is
                        # now one packet per ssid range and is sent by
                        # send_msg_masks(), then sent again from
                        # handle_cntl() once the modem reports its real
                        # ranges, which it does after this point.
                        send_msg_masks(s, addr, ssid_ranges[0], "default ranges")
                        for nm, mp in (("diagmode", diagmode_packet()),
                                       ("log mask all", log_mask_all()),
                                       ("event mask all", event_mask_all())):
                            try:
                                s.sendto(mp, addr)
                                say("  sent %-14s %s" % (nm, mp.hex()))
                                time.sleep(0.05)
                            except OSError as e:
                                say("  could not send %s: %s" % (nm, e))
        if cmd_deadline and time.time() > cmd_deadline:
            cmd_deadline = None
            where = modem_cmd_port()
            if not where:
                say("the modem is not publishing its CMD service; no handshake?")
            else:
                say("modem CMD service at node %d port %d" % where)
                req = bytes.fromhex(args.cmd.replace("0x", ""))
                cmd_sock = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
                cmd_sock.setblocking(False)
                try:
                    cmd_sock.sendto(req, where)
                    say("sent DIAG request %s" % req.hex())
                except OSError as e:
                    say("could not send: %s" % e)
        if cmd_sock is not None:
            try:
                d, a = cmd_sock.recvfrom(4096)
                say("DIAG RESPONSE from %s: %d bytes: %s" % (a, len(d), d[:96].hex()))
                say("   as text: %s" % "".join(chr(c) if 32 <= c < 127 else "."
                                               for c in d[:96]))
            except (BlockingIOError, OSError):
                pass

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
