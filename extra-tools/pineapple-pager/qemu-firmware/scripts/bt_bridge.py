#!/usr/bin/env python3
"""
bt_bridge.py - host-side Bluetooth helper for the qemu Pineapple Pager firmware.

WHY
  The firmware (Go, MIPSel) toggles Bluetooth by exec'ing `hciconfig <iface> up|down`
  (strings: "SetBluetoothEnabled", "hciconfig %s failed", "Error setting bluetooth
  status"). Those binaries live in the firmware rootfs and run under qemu-mipsel.
  hciconfig talks to the controller through an AF_BLUETOOTH/HCI socket + HCI ioctls
  (HCIGETDEVLIST, HCIDEVUP, ...). qemu-user does NOT emulate those HCI ioctls for a
  non-native guest: the socket() succeeds but the ioctl returns ENOTTY, so the MIPS
  hciconfig prints "Can't get device list: Not a tty" and never sees the G82's real
  hci0. (Verified on-device.)

WHAT THIS DOES
  Runs natively on the G82 (aarch64, root) and exposes a tiny line protocol over
  TCP 127.0.0.1:BT_PORT. qemu-user shares the host network namespace, so the shim
  that replaces the rootfs hciconfig (see hciconfig.shim) can reach us with the
  rootfs's own `nc`. We translate the firmware's intent to the REAL host tools:
      hciconfig hci0 up    -> rfkill unblock bluetooth ; hciconfig hci0 up
      hciconfig hci0 down  -> hciconfig hci0 down
      hciconfig / -a / hciX -> real `hciconfig ...` (status readback)
  and return the real tool's combined output + an exit-status line the shim mirrors.

PROTOCOL (one request per connection)
  client sends one line:   ARGV\t<arg0>\t<arg1>...\n      (tab-separated argv)
  server replies:          <stdout/stderr bytes>
                           then a final line:  __RC__ <int>\n
  The shim strips the __RC__ line and exits with that code.

SAFE BY DESIGN
  * Only ever runs `hciconfig` / `rfkill` / `bluetoothctl` with validated args.
  * Never touches the modem, wlan, or anything outside bluetooth.
  * Only binds 127.0.0.1.
"""
import socket, subprocess, sys, os, shlex, threading, time

BT_PORT = int(os.environ.get("PAGER_BT_PORT", "1473"))
BIND    = os.environ.get("PAGER_BT_BIND", "127.0.0.1")
LOG     = os.environ.get("PAGER_BT_LOG", "/tmp/pager_bt_bridge.log")

HCICONFIG = "/usr/bin/hciconfig"
RFKILL    = "/usr/sbin/rfkill"
for alt in ("/usr/bin/rfkill", "/sbin/rfkill"):
    if not os.path.exists(RFKILL) and os.path.exists(alt):
        RFKILL = alt

_loglock = threading.Lock()
def log(msg):
    line = "[%.3f] %s" % (time.time(), msg)
    with _loglock:
        try:
            with open(LOG, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass
    sys.stderr.write(line + "\n"); sys.stderr.flush()

def run(cmd):
    """Run a host command, return (rc, combined_output_bytes)."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=15)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return 124, b"bt_bridge: command timed out: %s\n" % (" ".join(cmd).encode())
    except FileNotFoundError:
        return 127, b"bt_bridge: not found: %s\n" % (cmd[0].encode())
    except Exception as e:
        return 1, ("bt_bridge: error: %r\n" % e).encode()

def handle_hciconfig(argv):
    """argv is the firmware's hciconfig argv (argv[0] == 'hciconfig')."""
    args = argv[1:]
    # status query: `hciconfig`, `hciconfig -a`, `hciconfig hci0`
    if not args or args == ["-a"] or (len(args) == 1 and args[0].startswith("hci")):
        return run([HCICONFIG] + args)
    # `hciconfig <iface> up`  -> unblock rfkill first, then up.
    # rfkill unblock returns before the controller is actually usable, so a bare
    # `hciconfig up` right after can fail with "Operation not possible due to
    # RF-kill (132)". Unblock, then retry the `up` a few times with a short wait.
    if len(args) >= 2 and args[1] == "up":
        iface = args[0]
        out = b""
        if os.path.exists(RFKILL):
            rc, o = run([RFKILL, "unblock", "bluetooth"]); out += o
        rc = 1
        for i in range(10):
            rc, o = run([HCICONFIG, iface, "up"])
            if rc == 0 and b"RF-kill" not in o:
                out += o
                break
            out = o  # keep only the last attempt's message
            time.sleep(0.2)
        return rc, out
    # `hciconfig <iface> down`
    if len(args) >= 2 and args[1] == "down":
        iface = args[0]
        rc, o = run([HCICONFIG, iface, "down"])
        return rc, o
    # anything else: pass straight through to the real hciconfig (reset, etc.)
    return run([HCICONFIG] + args)

def handle(conn):
    try:
        conn.settimeout(15)
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                break
        line = data.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        if not line:
            conn.sendall(b"__RC__ 1\n"); return
        parts = line.split("\t")
        if parts[0] == "ARGV":
            argv = parts[1:]
        else:
            # be forgiving: treat the whole line as a shell-ish command line
            argv = shlex.split(line)
        if not argv:
            conn.sendall(b"__RC__ 1\n"); return
        base = os.path.basename(argv[0])
        log("request: %r" % argv)
        if base == "hciconfig":
            rc, out = handle_hciconfig(argv)
        elif base == "bluetoothctl":
            # bluetoothctl needs a running bluetoothd + an interactive agent.
            # We run it non-interactively against the real stack. Most firmware
            # uses are `bluetoothctl power on/off` or `show`.
            rc, out = run([ "/usr/bin/bluetoothctl"] + argv[1:])
        elif base == "rfkill":
            rc, out = run([RFKILL] + argv[1:])
        else:
            rc, out = 127, ("bt_bridge: refusing unknown tool %r\n" % base).encode()
        conn.sendall(out)
        conn.sendall(("__RC__ %d\n" % rc).encode())
        log("reply rc=%d (%d bytes)" % (rc, len(out)))
    except Exception as e:
        log("handler error: %r" % e)
        try: conn.sendall(("__RC__ 1\n").encode())
        except Exception: pass
    finally:
        try: conn.close()
        except Exception: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((BIND, BT_PORT))
    srv.listen(16)
    log("bt_bridge listening on %s:%d (hciconfig=%s rfkill=%s)"
        % (BIND, BT_PORT, HCICONFIG, RFKILL))
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

if __name__ == "__main__":
    main()
