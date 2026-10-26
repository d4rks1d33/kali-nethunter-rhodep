#!/usr/bin/env python3
"""pager-reload-supervisor.py

Environment fix for the WiFi Pineapple Pager "Reloading UI" hang under qemu-user.

Root cause (verified by reverse engineering the firmware):
  pineapplepager.SetThemes.func1.1 (settings_general.go) applies a theme by
  (1) saving the theme config, (2) showing a "Reloading UI" dialog, and
  (3) spawning a worker goroutine that sleeps briefly and then writes the
  /root/properShutdown flag. The firmware then EXPECTS AN EXTERNAL SUPERVISOR
  TO RESTART THE PROCESS so the new theme is applied on a fresh boot -- exactly
  what the init system does on the real device. Under the qemu-user emulation
  there is no such supervisor, so the firmware sits on "Reloading UI" forever.
  (The emulated time.Sleep made the dialog linger even longer; that sleep has
  also been shortened in the patched binary.)

This supervisor follows the firmware's OWN journal and watches for the
deterministic "[SYSTEM] Setting theme {...}" line the firmware logs the instant
a theme is applied. A short settle later it restarts the firmware via systemd
(systemctl kill -> systemd relaunches), which boots into the newly selected
theme. It keys only off the explicit theme-apply log line, so it never fires
during normal boot or operation.
"""
import os, re, subprocess, sys, time

UNIT = os.environ.get("PAGER_UNIT", "pager-qemu-firmware")
# Wait this long after the "Setting theme" log before restarting, so the
# firmware has finished writing the theme config + flag and has rendered the
# "Reloading UI" screen.
SETTLE = float(os.environ.get("PAGER_RELOAD_SETTLE", "3.0"))
# Debounce: ignore repeated triggers within this window (one restart per apply).
DEBOUNCE = float(os.environ.get("PAGER_RELOAD_DEBOUNCE", "20.0"))

TRIGGER = re.compile(r"\[SYSTEM\]\s+Setting theme\b")

def restart():
    subprocess.run(["systemctl", "kill", "-s", "SIGKILL", UNIT], check=False)

def main():
    # Follow the unit journal from "now" so we never act on historical lines.
    p = subprocess.Popen(
        ["journalctl", "-u", UNIT, "-f", "-n", "0", "--no-pager", "-o", "cat"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    last = 0.0
    print(f"[supervisor] watching {UNIT} journal for theme-apply", flush=True)
    for line in p.stdout:
        if TRIGGER.search(line):
            now = time.time()
            if now - last < DEBOUNCE:
                continue
            last = now
            print(f"[supervisor] theme applied: {line.strip()[:80]}", flush=True)
            time.sleep(SETTLE)
            print("[supervisor] restarting firmware to complete UI reload", flush=True)
            restart()

if __name__ == "__main__":
    main()
