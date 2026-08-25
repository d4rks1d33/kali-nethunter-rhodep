#!/usr/bin/python3
"""Route the modem's ETM trace into a DDR buffer that survives the reset.

Every CoreSight block on this SoC is reachable from userspace through
/dev/mem -- mainline describes none of them, so there is no driver in the way
and nothing to unbind. The path comes from the vendor's holi-coresight.dtsi:

    modem_etm1 (QMI 51 inst 11) -> funnel_modem0@8804000 port 1
                                -> funnel_in1@8042000    port 4
                                -> funnel_merg@8045000   port 1
                                -> tmc_etf@8047000       (hardware FIFO, a link)
                                -> replicator@8046000    port 0
                                -> tmc_etr@8048000       -> DDR

The sink buffer is tzlog_dump@aefa2000, 192 KB: reserved no-map by patch 0067,
never written by anything on this port, and measured to survive a reset byte
for byte. That is what makes this worth doing -- the buffer is still there
after the SoC goes down.

  ./rhodep-modem-trace.py setup     program the path and start capturing
  ./rhodep-modem-trace.py status    read the sink pointers and buffer fill
  ./rhodep-modem-trace.py dump N    hexdump the first N bytes of the buffer
  ./rhodep-modem-trace.py scan      look for modem code addresses in the buffer
  ./rhodep-modem-trace.py stop      stop capture and disable the funnels
"""
import mmap, os, struct, sys, time

CS_LAR, CS_UNLOCK_KEY = 0xfb0, 0xc5acce55
FUNNEL_FUNCTL = 0x000
TMC_RSZ, TMC_STS, TMC_RRP, TMC_RWP, TMC_CTL = 0x004, 0x00c, 0x014, 0x018, 0x020
TMC_MODE, TMC_BUFWM, TMC_RWPHI = 0x028, 0x034, 0x03c
TMC_AXICTL, TMC_DBALO, TMC_DBAHI, TMC_FFSR, TMC_FFCR = 0x110, 0x118, 0x11c, 0x300, 0x304
REP_IDFILTER0, REP_IDFILTER1 = 0x000, 0x004

FUNNEL_MODEM0, FUNNEL_IN1, FUNNEL_MERG = 0x8804000, 0x8042000, 0x8045000
TMC_ETF, REPLICATOR, TMC_ETR = 0x8047000, 0x8046000, 0x8048000
BUF_PA, BUF_SZ = 0xaefa2000, 0x30000

# modem code lives here, from modem.mdt's program headers
MODEM_LO, MODEM_HI = 0x8b800000, 0x9b800000


class Blk:
    def __init__(self, pa, size=0x1000, write=True):
        self.pa = pa
        prot = mmap.PROT_READ | (mmap.PROT_WRITE if write else 0)
        self.fd = os.open("/dev/mem", (os.O_RDWR if write else os.O_RDONLY) | os.O_SYNC)
        self.m = mmap.mmap(self.fd, size, mmap.MAP_SHARED, prot, offset=pa)

    def rd(self, off):
        return struct.unpack("<I", self.m[off:off + 4])[0]

    def wr(self, off, val):
        self.m[off:off + 4] = struct.pack("<I", val & 0xffffffff)

    def unlock(self):
        self.wr(CS_LAR, CS_UNLOCK_KEY)

    def lock(self):
        self.wr(CS_LAR, 0)

    def close(self):
        self.m.close(); os.close(self.fd)


def tmc_wait_ready(b, what):
    for _ in range(200):
        if b.rd(TMC_STS) & 0x4:          # TMCReady
            return True
        time.sleep(0.001)
    print("  WARNING: %s never reported TMCReady (STS=0x%08x)" % (what, b.rd(TMC_STS)))
    return False


def funnel_enable(pa, port, name):
    b = Blk(pa); b.unlock()
    old = b.rd(FUNNEL_FUNCTL)
    b.wr(FUNNEL_FUNCTL, old | (1 << port) | 0x300)
    new = b.rd(FUNNEL_FUNCTL)
    b.lock(); b.close()
    print("  %-16s FUNCTL 0x%08x -> 0x%08x (port %d)" % (name, old, new, port))
    return new & (1 << port)


def setup():
    print("funnels:")
    ok = True
    ok &= bool(funnel_enable(FUNNEL_MODEM0, 1, "funnel_modem0"))
    ok &= bool(funnel_enable(FUNNEL_IN1, 4, "funnel_in1"))
    ok &= bool(funnel_enable(FUNNEL_MERG, 1, "funnel_merg"))

    print("etf (as a link, hardware FIFO):")
    b = Blk(TMC_ETF); b.unlock()
    b.wr(TMC_CTL, 0); tmc_wait_ready(b, "etf")
    b.wr(TMC_MODE, 0x2)
    b.wr(TMC_FFCR, 0x3)
    b.wr(TMC_BUFWM, 0)
    b.wr(TMC_CTL, 1)
    print("  MODE=0x%x CTL=0x%x STS=0x%08x" % (b.rd(TMC_MODE), b.rd(TMC_CTL), b.rd(TMC_STS)))
    b.lock(); b.close()

    print("replicator:")
    b = Blk(REPLICATOR); b.unlock()
    b.wr(REP_IDFILTER0, 0x00)
    b.wr(REP_IDFILTER1, 0xff)
    print("  IDFILTER0=0x%02x IDFILTER1=0x%02x" % (b.rd(REP_IDFILTER0), b.rd(REP_IDFILTER1)))
    b.lock(); b.close()

    print("etr -> 0x%08x (%d KiB):" % (BUF_PA, BUF_SZ // 1024))
    b = Blk(TMC_ETR); b.unlock()
    b.wr(TMC_CTL, 0); tmc_wait_ready(b, "etr")
    b.wr(TMC_AXICTL, 0xfbc)
    b.wr(TMC_DBALO, BUF_PA)
    b.wr(TMC_DBAHI, 0)
    b.wr(TMC_RSZ, BUF_SZ // 4)
    b.wr(TMC_MODE, 0x0)
    b.wr(TMC_FFCR, 0x133)
    b.wr(TMC_CTL, 1)
    print("  DBALO=0x%08x RSZ=0x%x MODE=0x%x CTL=0x%x STS=0x%08x RWP=0x%08x"
          % (b.rd(TMC_DBALO), b.rd(TMC_RSZ), b.rd(TMC_MODE), b.rd(TMC_CTL),
             b.rd(TMC_STS), b.rd(TMC_RWP)))
    b.lock(); b.close()
    print("setup done" if ok else "setup done WITH FUNNEL PROBLEMS")


def status():
    b = Blk(TMC_ETR, write=False)
    print("etr  CTL=0x%x MODE=0x%x STS=0x%08x RSZ=0x%x" % (b.rd(TMC_CTL), b.rd(TMC_MODE), b.rd(TMC_STS), b.rd(TMC_RSZ)))
    print("     DBALO=0x%08x RWP=0x%08x RRP=0x%08x FFSR=0x%08x"
          % (b.rd(TMC_DBALO), b.rd(TMC_RWP), b.rd(TMC_RRP), b.rd(TMC_FFSR)))
    b.close()
    b = Blk(TMC_ETF, write=False)
    print("etf  CTL=0x%x MODE=0x%x STS=0x%08x RWP=0x%08x" % (b.rd(TMC_CTL), b.rd(TMC_MODE), b.rd(TMC_STS), b.rd(TMC_RWP)))
    b.close()
    for pa, name, port in ((FUNNEL_MODEM0, "funnel_modem0", 1), (FUNNEL_IN1, "funnel_in1", 4), (FUNNEL_MERG, "funnel_merg", 1)):
        b = Blk(pa, write=False)
        print("%-14s FUNCTL=0x%08x port%d=%s" % (name, b.rd(FUNNEL_FUNCTL), port,
              "on" if b.rd(FUNNEL_FUNCTL) & (1 << port) else "OFF"))
        b.close()
    nz = buffer_nonzero()
    print("buffer at 0x%08x: %d of %d bytes non-zero" % (BUF_PA, nz, BUF_SZ))


def buffer_bytes():
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    m = mmap.mmap(fd, BUF_SZ, mmap.MAP_SHARED, mmap.PROT_READ, offset=BUF_PA)
    data = m[:]
    m.close(); os.close(fd)
    return data


def buffer_nonzero():
    return sum(1 for c in buffer_bytes() if c)


def dump(n):
    data = buffer_bytes()[:n]
    for i in range(0, len(data), 32):
        print("%06x  %s" % (i, data[i:i + 32].hex()))


def scan():
    data = buffer_bytes()
    hits = {}
    for i in range(0, len(data) - 4):
        v = struct.unpack_from("<I", data, i)[0]
        if MODEM_LO <= v < MODEM_HI:
            hits[v] = hits.get(v, 0) + 1
    print("distinct modem-range words: %d" % len(hits))
    for v, c in sorted(hits.items(), key=lambda kv: -kv[1])[:40]:
        print("  0x%08x  x%d" % (v, c))


def stop():
    for pa, name in ((TMC_ETR, "etr"), (TMC_ETF, "etf")):
        b = Blk(pa); b.unlock(); b.wr(TMC_CTL, 0); b.lock(); b.close()
        print("%s stopped" % name)
    for pa, name in ((FUNNEL_MODEM0, "funnel_modem0"), (FUNNEL_IN1, "funnel_in1"), (FUNNEL_MERG, "funnel_merg")):
        b = Blk(pa); b.unlock(); b.wr(FUNNEL_FUNCTL, 0x300); b.lock(); b.close()
        print("%s ports disabled" % name)


cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
{"setup": setup, "status": status, "stop": stop,
 "scan": scan}.get(cmd, lambda: dump(int(sys.argv[2]) if len(sys.argv) > 2 else 512))()
