#!/usr/bin/python3
"""AT client over the modem's glink DS/DATA1 port.

Found by opening the channels the modem advertises and nobody binds: DS and
DATA1 answer AT with OK. The port echoes, so each command is drained first and
then read until a final result code.

The interesting one is AT$QCDMG, which on Qualcomm modems switches the port
into DIAG/QCDM mode -- which would give the modem's own F3 log, the thing the
debug fuses deny through CoreSight.
"""
import fcntl, os, select, signal, struct, sys, time, glob

RPMSG_CREATE_EPT_IOCTL = (1 << 30) | (40 << 16) | (0xb5 << 8) | 0x01
CTRL = "/dev/rpmsg_ctrl2"


class TO(Exception):
    pass


signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TO()))


def open_channel(name):
    before = set(glob.glob("/dev/rpmsg[0-9]*"))
    buf = struct.pack("32sII", name.encode(), 0xFFFFFFFF, 0xFFFFFFFF)
    fd = os.open(CTRL, os.O_RDWR)
    fcntl.ioctl(fd, RPMSG_CREATE_EPT_IOCTL, buf)
    os.close(fd)
    time.sleep(0.4)
    new = sorted(set(glob.glob("/dev/rpmsg[0-9]*")) - before)
    if not new:
        raise RuntimeError("no device for %s" % name)
    return os.open(new[-1], os.O_RDWR), new[-1]


def drain(f, secs=0.4):
    end = time.monotonic() + secs
    out = b""
    while time.monotonic() < end:
        r, _, _ = select.select([f], [], [], 0.1)
        if not r:
            continue
        try:
            signal.alarm(2); out += os.read(f, 4096); signal.alarm(0)
        except (TO, OSError):
            signal.alarm(0); break
    return out


def at(f, cmd, wait=3.0):
    drain(f, 0.3)
    try:
        signal.alarm(3); os.write(f, (cmd + "\r\n").encode()); signal.alarm(0)
    except (TO, OSError) as e:
        signal.alarm(0); return "WRITE-FAIL %s" % e
    end = time.monotonic() + wait
    buf = b""
    while time.monotonic() < end:
        r, _, _ = select.select([f], [], [], 0.2)
        if not r:
            continue
        try:
            signal.alarm(2); buf += os.read(f, 4096); signal.alarm(0)
        except (TO, OSError):
            signal.alarm(0); break
        if b"OK" in buf or b"ERROR" in buf:
            break
    return buf.decode("ascii", "replace").strip()


chan = sys.argv[1] if len(sys.argv) > 1 else "DS"
cmds = sys.argv[2:] or ["AT", "ATI", "AT+CGMR", "AT+CGMM", "AT$QCSIMSTAT?"]
f, dev = open_channel(chan)
print("channel %s -> %s" % (chan, dev))
for c in cmds:
    print("\n>>> %s" % c)
    print(at(f, c))
os.close(f)
