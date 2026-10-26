#!/usr/bin/env python3
"""
vibrator_bridge.py - bridge the qemu Pineapple Pager firmware's FAKE vibrator
sysfs node to the Motorola G82's REAL vibrator.

The firmware (running under qemu-mipsel with -L ROOTFS) writes "1" / "0" to
    ROOTFS/sys/class/gpio/vibrator/value
to turn vibration on/off (endpoint /pager/vibrate/play, "SetVibrate",
"Enabling vibration mode"). That path is a plain file prepare.sh created in the
rootfs -- it never reaches hardware.

The G82's real vibrator is the kernel `gpio-vibrator` driver, exposed as a
force-feedback evdev device (NOT a /sys LED). It lives at:
    /sys/devices/platform/vibrator/input/inputN/eventN  ->  /dev/input/eventN
and advertises FF_RUMBLE. We drive it with the standard evdev force-feedback
protocol (EVIOCSFF upload + EV_FF play), pure stdlib (no python-evdev).

Bridge logic:
  * watch the firmware's fake value file (inotify, polling fallback)
  * on transition to a non-zero value  -> start the real rumble
  * on transition to "0"                -> stop the real rumble

Env overrides:
  PAGER_VIB_WATCH   fake value file (default /opt/pineapple-pager/qemu/rootfs/sys/class/gpio/vibrator/value)
  PAGER_VIB_DEV     real FF evdev node (default: auto-detect gpio-vibrator, else /dev/input/event3)
  PAGER_VIB_STRONG  rumble strong magnitude 0..65535 (default 65535)
  PAGER_VIB_MAXMS   safety auto-stop for a stuck "1" (default 8000 ms, 0=never)
  PAGER_VIB_MINMS   minimum pulse so a brief 1->0 is actually felt (default 220 ms)
"""
import os, sys, time, fcntl, struct, array, glob, errno, signal

WATCH = os.environ.get(
    "PAGER_VIB_WATCH",
    "/opt/pineapple-pager/qemu/rootfs/sys/class/gpio/vibrator/value")
STRONG = int(os.environ.get("PAGER_VIB_STRONG", "65535"))
MAXMS  = int(os.environ.get("PAGER_VIB_MAXMS", "8000"))
# The firmware writes a VERY brief 1->0 pulse; without a minimum the rumble
# stops in milliseconds and is imperceptible. Hold the motor at least MINMS.
MINMS  = int(os.environ.get("PAGER_VIB_MINMS", "220"))

def log(msg):
    sys.stderr.write("[vibrator_bridge] %s\n" % msg)
    sys.stderr.flush()

# ---------------------------------------------------------------- FF evdev ----
_IOC_WRITE = 1
def _IOC(d, t, nr, size): return (d << 30) | (size << 16) | (ord(t) << 8) | nr
EVIOCSFF  = _IOC(_IOC_WRITE, 'E', 0x80, 48)   # struct ff_effect == 48 bytes
EVIOCRMFF = _IOC(_IOC_WRITE, 'E', 0x81, 4)
EV_FF = 0x15
FF_RUMBLE = 0x50

def find_ff_device():
    want = os.environ.get("PAGER_VIB_DEV")
    if want:
        return want
    # auto-detect the gpio-vibrator event node
    for namef in glob.glob("/sys/class/input/event*/device/name"):
        try:
            with open(namef) as f:
                if "vibrator" in f.read().strip().lower():
                    ev = namef.split("/")[4]  # event*
                    return "/dev/input/" + ev
        except OSError:
            pass
    # fall back to the known node on this device
    return "/dev/input/event3"

class Rumble:
    def __init__(self, dev):
        self.dev = dev
        self.fd = None
        self.eid = None
        self.playing = False

    def _open(self):
        if self.fd is None:
            self.fd = os.open(self.dev, os.O_RDWR)

    def start(self, length_ms):
        self._open()
        # upload a fresh effect (id=-1 -> kernel assigns), replay length caps it
        effect = struct.pack('<HhHHHHHHH',
                             FF_RUMBLE, -1, 0,
                             0, 0,                       # trigger button,interval
                             length_ms & 0xffff, 0,      # replay length, delay
                             STRONG & 0xffff, STRONG & 0xffff)
        effect += b'\x00' * (48 - len(effect))
        buf = array.array('B', effect)
        fcntl.ioctl(self.fd, EVIOCSFF, buf, True)
        self.eid = struct.unpack_from('<h', buf, 2)[0]
        os.write(self.fd, struct.pack('<qqHHi', 0, 0, EV_FF, self.eid, 1))
        self.playing = True

    def stop(self):
        if self.fd is None or self.eid is None:
            self.playing = False
            return
        try:
            os.write(self.fd, struct.pack('<qqHHi', 0, 0, EV_FF, self.eid, 0))
        except OSError:
            pass
        try:
            fcntl.ioctl(self.fd, EVIOCRMFF, self.eid)
        except OSError:
            pass
        self.eid = None
        self.playing = False

    def close(self):
        self.stop()
        if self.fd is not None:
            try: os.close(self.fd)
            except OSError: pass
            self.fd = None

# ---------------------------------------------------------------- watcher -----
def read_value(path):
    try:
        with open(path, "r") as f:
            s = f.read().strip()
        if s == "":
            return 0
        # accept "1", "0", or any nonzero int
        try:
            return int(s)
        except ValueError:
            return 1 if s not in ("0", "false", "off") else 0
    except OSError:
        return None

def inotify_setup(path):
    """Return (fd, wd) using raw inotify via ctypes, or (None,None) on failure."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        ino_fd = libc.inotify_init1(0o4000)  # IN_NONBLOCK
        if ino_fd < 0:
            return None, None
        IN_MODIFY = 0x00000002
        IN_CLOSE_WRITE = 0x00000008
        wd = libc.inotify_add_watch(ino_fd.__index__() if hasattr(ino_fd,'__index__') else ino_fd,
                                    path.encode(), IN_MODIFY | IN_CLOSE_WRITE)
        if wd < 0:
            os.close(ino_fd)
            return None, None
        return ino_fd, wd
    except Exception as e:
        log("inotify unavailable (%r), using polling" % e)
        return None, None

def main():
    dev = find_ff_device()
    log("watching %s -> real vibrator %s (strong=%d, maxms=%d)"
        % (WATCH, dev, STRONG, MAXMS))
    if not os.path.exists(WATCH):
        log("WARNING: watch file does not exist yet: %s" % WATCH)

    rum = Rumble(dev)
    stop = {"flag": False}
    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    last = read_value(WATCH) or 0
    started_at = 0.0

    ino_fd, _wd = inotify_setup(WATCH)
    use_inotify = ino_fd is not None
    log("mode: %s" % ("inotify" if use_inotify else "polling"))

    import select
    try:
        while not stop["flag"]:
            if use_inotify:
                try:
                    r, _, _ = select.select([ino_fd], [], [], 0.5)
                    if r:
                        try:
                            os.read(ino_fd, 4096)  # drain events
                        except OSError as e:
                            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                                raise
                except (OSError, ValueError):
                    use_inotify = False
                    log("inotify failed mid-run, switching to polling")
            else:
                time.sleep(0.05)

            cur = read_value(WATCH)
            if cur is None:
                continue
            if cur != last:
                if cur != 0:
                    length = MAXMS if MAXMS > 0 else 60000
                    rum.start(length)
                    started_at = time.time()
                    log("ON  (value=%s)" % cur)
                else:
                    # Minimum pulse: the firmware often toggles 1->0 within a few
                    # ms, which would stop the motor before it's felt. Keep it on
                    # until at least MINMS elapsed since the ON edge.
                    elapsed_ms = (time.time() - started_at) * 1000
                    if rum.playing and elapsed_ms < MINMS:
                        time.sleep((MINMS - elapsed_ms) / 1000.0)
                    rum.stop()
                    log("OFF")
                last = cur

            # safety: auto-stop a stuck ON after MAXMS even if we never see 0
            if MAXMS > 0 and rum.playing and (time.time() - started_at) * 1000 > MAXMS:
                rum.stop()
                log("auto-stop after %dms (stuck ON)" % MAXMS)
                # keep 'last' as-is; a new nonzero write will re-trigger
    finally:
        rum.close()
        if ino_fd is not None:
            try: os.close(ino_fd)
            except OSError: pass
        log("exiting")

if __name__ == "__main__":
    main()
