#!/usr/bin/env python3
# =============================================================================
# battery_bridge.py -- bridge the G82's REAL battery to the fake bq27546-0 the
# firmware expects.
#
# Part of: extra-tools/pineapple-pager/qemu-firmware/scripts/
#
# The firmware (under qemu) reads /sys/class/power_supply/bq27546-0/{uevent,
# capacity,status}. Those nodes don't exist in the fake rootfs sysfs, so the
# firmware spams "[POWER] Unable to open power monitor" hundreds of times/sec,
# which saturates the process and (observed) leaves "Reloading UI" stuck after
# a theme change. This bridge mirrors the phone's REAL battery (cw2217-battery
# on the G82) into the firmware's fake bq27546-0 node, refreshing periodically.
#
# Env:
#   PAGER_BAT_ROOTFS   rootfs root (default /opt/pineapple-pager/qemu/rootfs)
#   PAGER_BAT_SRC      real battery sysfs dir (default: autodetect, cw2217 first)
#   PAGER_BAT_INTERVAL seconds between refreshes (default 5)
# =============================================================================
import os, sys, time, glob

ROOTFS = os.environ.get("PAGER_BAT_ROOTFS", "/opt/pineapple-pager/qemu/rootfs")
INTERVAL = float(os.environ.get("PAGER_BAT_INTERVAL", "5"))
FAKE = os.path.join(ROOTFS, "sys/class/power_supply/bq27546-0")


def log(*a):
    print("[battery_bridge]", *a, file=sys.stderr, flush=True)


def find_real_battery():
    want = os.environ.get("PAGER_BAT_SRC")
    if want and os.path.isdir(want):
        return want
    # prefer a fuel-gauge with a capacity (cw2217 on the G82)
    cands = []
    for d in sorted(glob.glob("/sys/class/power_supply/*")):
        try:
            with open(os.path.join(d, "capacity")) as f:
                int(f.read().strip())
            cands.append(d)
        except Exception:
            pass
    # cw2217 / fuel-gauge first
    for d in cands:
        if "cw2217" in d or "fuel" in d.lower():
            return d
    return cands[0] if cands else None


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def refresh(src):
    os.makedirs(FAKE, exist_ok=True)
    cap = read(os.path.join(src, "capacity"), "100")
    status = read(os.path.join(src, "status"), "Discharging")
    health = read(os.path.join(src, "health"), "Good")
    volt = read(os.path.join(src, "voltage_now"), "4000000")
    curr = read(os.path.join(src, "current_now"), "0")
    temp = read(os.path.join(src, "temp"), "300")  # deci-degC
    # capacity level bucket the firmware may read
    try:
        c = int(cap)
    except ValueError:
        c = 100
    level = ("Critical" if c <= 5 else "Low" if c <= 15 else
             "Normal" if c <= 85 else "High" if c < 100 else "Full")

    # the firmware reads bq27546-0/{capacity,status,uevent}
    _write(os.path.join(FAKE, "capacity"), cap)
    _write(os.path.join(FAKE, "status"), status)
    _write(os.path.join(FAKE, "health"), health)
    uevent = "\n".join([
        "POWER_SUPPLY_NAME=bq27546-0",
        "POWER_SUPPLY_TYPE=Battery",
        "POWER_SUPPLY_STATUS=%s" % status,
        "POWER_SUPPLY_HEALTH=%s" % health,
        "POWER_SUPPLY_PRESENT=1",
        "POWER_SUPPLY_TECHNOLOGY=Li-poly",
        "POWER_SUPPLY_CAPACITY=%s" % cap,
        "POWER_SUPPLY_CAPACITY_LEVEL=%s" % level,
        "POWER_SUPPLY_VOLTAGE_NOW=%s" % volt,
        "POWER_SUPPLY_CURRENT_NOW=%s" % curr,
        "POWER_SUPPLY_TEMP=%s" % temp,
        "",
    ])
    _write(os.path.join(FAKE, "uevent"), uevent)


def _write(path, val):
    try:
        with open(path, "w") as f:
            f.write(val if val.endswith("\n") else val + "\n")
    except Exception as e:
        log("write %s failed: %s" % (path, e))


def main():
    src = find_real_battery()
    if not src:
        log("no real battery found; writing a static 100%% node")
        os.makedirs(FAKE, exist_ok=True)
        _write(os.path.join(FAKE, "capacity"), "100")
        _write(os.path.join(FAKE, "status"), "Full")
        _write(os.path.join(FAKE, "uevent"),
               "POWER_SUPPLY_NAME=bq27546-0\nPOWER_SUPPLY_CAPACITY=100\n"
               "POWER_SUPPLY_STATUS=Full\n")
        return
    log("bridging real battery %s -> %s (every %ss)" % (src, FAKE, INTERVAL))
    while True:
        refresh(src)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
