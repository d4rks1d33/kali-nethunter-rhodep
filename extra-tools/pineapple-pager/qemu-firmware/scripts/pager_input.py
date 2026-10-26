#!/usr/bin/env python3
"""pager_input.py - write evdev-style input_event records to the host FIFO
that the patched qemu-mipsel forwards to the WiFi Pineapple Pager firmware.

The firmware uses go-evdev and reads struct input_event records:

    struct input_event {
        struct timeval time;  /* __kernel_long: on the MIPSel target this is
                                 two 32-bit words -> 8 bytes total */
        __u16 type;
        __u16 code;
        __s32 value;
    };  /* 16 bytes, little-endian (target == host byte order) */

We emit button presses as the kernel would: EV_KEY <code> 1, EV_SYN, then
EV_KEY <code> 0, EV_SYN (press + release), which drives one navigation step.

Usage:
    python3 pager_input.py down          # one D-pad down tap
    python3 pager_input.py up a b left right power enter esc   # sequence
    PAGER_INPUT_FIFO=/tmp/pager_input.fifo python3 pager_input.py down
"""
import os, sys, struct, time

FIFO = os.environ.get("PAGER_INPUT_FIFO", "/tmp/pager_input.fifo")

# event types
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0x00

# Linux input-event-codes used by the Pager firmware
KEY_POWER      = 116
BTN_A          = 0x130  # 304  (BTN_GAMEPAD/BTN_SOUTH alias BTN_A)
BTN_B          = 0x131  # 305
BTN_DPAD_UP    = 0x220  # 544
BTN_DPAD_DOWN  = 0x221  # 545
BTN_DPAD_LEFT  = 0x222  # 546
BTN_DPAD_RIGHT = 0x223  # 547

# friendly name -> keycode
NAMES = {
    "up":    BTN_DPAD_UP,
    "down":  BTN_DPAD_DOWN,
    "left":  BTN_DPAD_LEFT,
    "right": BTN_DPAD_RIGHT,
    "a":     BTN_A,
    "enter": BTN_A,
    "ok":    BTN_A,
    "b":     BTN_B,
    "esc":   BTN_B,
    "back":  BTN_B,
    "power": KEY_POWER,
}

# 16-byte little-endian input_event (2x u32 time, u16 type, u16 code, s32 value)
_PACK = struct.Struct("<IIHHi")


def _ev(fd, etype, code, value):
    t = time.time()
    sec = int(t)
    usec = int((t - sec) * 1_000_000)
    os.write(fd, _PACK.pack(sec, usec, etype, code, value))


def tap(fd, code, hold=0.02):
    """press + release a key, each followed by a SYN_REPORT."""
    _ev(fd, EV_KEY, code, 1)
    _ev(fd, EV_SYN, SYN_REPORT, 0)
    time.sleep(hold)
    _ev(fd, EV_KEY, code, 0)
    _ev(fd, EV_SYN, SYN_REPORT, 0)


def open_fifo():
    # Try non-blocking first (fast path when qemu is reading). If the reader is
    # momentarily reopening the FIFO (ENXIO), retry briefly, then fall back to a
    # blocking open so a press is never silently dropped.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            return os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            time.sleep(0.05)
    return os.open(FIFO, os.O_WRONLY)  # blocking last resort


def main(argv):
    if len(argv) < 2:
        print("usage: pager_input.py <name> [<name> ...]")
        print("names:", ", ".join(sorted(NAMES)))
        return 2
    names = argv[1:]
    for n in names:
        if n.lower() not in NAMES:
            print("unknown button:", n, "(known:", ", ".join(sorted(NAMES)), ")")
            return 2
    fd = open_fifo()
    try:
        for n in names:
            tap(fd, NAMES[n.lower()])
            print("sent", n)
            time.sleep(0.15)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
