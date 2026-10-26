# Pager live visor + input (Task A + Task B)

Added to `qemu-firmware/` on the G82 (`/home/kali/pager-qemu/`).

## Files

Scripts (`qemu-firmware/scripts/`):
- `pager_viewer.py`  - TASK A live visor (pygame/SDL on Wayland) + TASK B on-screen
                       D-pad / keyboard -> writes input_event records to the FIFO.
- `pager_input.py`   - CLI input injector (down/up/left/right/a/b/power).
- `run_visor.sh`     - launcher: starts firmware under patched qemu (with the input
                       FIFO exposed) then launches the viewer on the user's Wayland.
- `prepare.sh`       - now also mkfifo's /tmp/pager_input.fifo and defaults ROOTFS
                       to /home/kali/pager-qemu/rootfs.
- `run.sh`           - ROOTFS default fixed to the real rootfs.
- `fbrender.py`      - unchanged (RGB565 222x480 -> landscape 480x222 PNG).

Patches (`qemu-firmware/patches/`):
- `02-evdev-input-shim.patch` - adds the evdev input shim to qemu-user
  `linux-user/syscall.c` (on top of the existing netlink/mounts patch).

## TASK A - live visor (WORKS)

The firmware mmaps and continuously redraws `rootfs/dev/fb0` (RGB565, stored
222x480 because the panel is mounted rotated 90deg; 213120 active bytes in a
426240-byte buffer). `pager_viewer.py` mmaps the SAME file read-only, decodes
RGB565 with numpy, rotates -90 to landscape 480x222, scales nearest-neighbor to
fill the phone screen, and refreshes at ~20 fps. Verified: decode produces a
clean 480x222 image of the firmware's real UI (the "WELCOME, HACKER" setup
wizard). See proof PNGs in /home/kali/pager-qemu/ (shot_before.png etc).

Run on the phone's Phosh/Wayland session:
    sudo WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 \
        ./qemu-firmware/scripts/run_visor.sh

## TASK B - input (PARTIAL: reaches the firmware's evdev reader, does NOT navigate)

### Why not uinput
The G82 kernel is built **without** uinput: `CONFIG_INPUT_UINPUT is not set`
(`/proc/config.gz`), and there is no loadable `uinput.ko`. So a real uinput
`/dev/input/eventX` cannot be created and bind-mounted into the rootfs. Confirmed.

### What was built instead (in the patched qemu-user)
The firmware (go-evdev / holoplot) opens every `/dev/input/eventN` O_RDWR, sets
it O_NONBLOCK, registers it with go's epoll poller and reads 16-byte
`struct input_event` records. With the old fake rootfs nodes, `read()` returned
EINVAL -> `[BUTTONS] error reading event ... invalid argument`.

The shim (`02-evdev-input-shim.patch`) intercepts, in `do_guest_openat`, every
guest open of `/dev/input/eventN` and returns one end of an `AF_UNIX`
socketpair (looks like a pollable char device, works with epoll + O_NONBLOCK).
A qemu background thread reads injected `input_event` records from a host FIFO
(`/tmp/pager_input.fifo`) and **broadcasts** each 16-byte record to all the
qemu-side socketpair ends, so whichever device the firmware listens on gets it.

Injector / viewer write 16-byte LE records to the FIFO:
  u32 sec, u32 usec, u16 type, u16 code, s32 value   (target mipsel == host LE)
codes: BTN_DPAD_UP/DOWN/LEFT/RIGHT = 0x220..0x223, BTN_A=0x130, BTN_B=0x131,
KEY_POWER=116.

### What is PROVEN to work
- `opened_evdev=6`, `invalid_arg_errors=0`: firmware opens all six devices with
  NO "invalid argument" (original bug fixed).
- Events flow end-to-end: injector -> FIFO -> qemu broadcaster -> socketpairs.
  `qemu -strace` shows the firmware's goroutines doing `read(fd,...,16) = 16`
  with the CORRECT decode (e.g. BTN_A press = type=1 code=0x130 value=1).

### What does NOT work (honest)
After the firmware reads the FIRST event from a device it does not keep reading:
across a 3-tap (12-event) injection only 1-3 reads of 16 bytes occur, and the
framebuffer is byte-for-byte identical before/after (`fb_differing_bytes=0`).
No button handling is ever logged (no "SM: Down pressed", no "send event to
btnCh", no "IGNORE event", no wake). So the selection arrow never leaves
"Welcome".

Most likely cause (not yet fixed): go's netpoller is **edge-triggered**
(EPOLLET). go-evdev reads one event per ReadOne; the firmware's per-device
goroutine appears to stop calling ReadOne after the first event (it blocks
forwarding to its internal `btnCh`, whose UI consumer is not draining on the
setup-wizard screen, or it is filtered by device identity). Because the
socketpair is then left non-empty, no new EPOLLIN edge fires and the remaining
events are never read. This is firmware-side event-routing behaviour above the
syscall layer; fixing it needs reverse-engineering the Hak5 button dispatch
(btnCh consumer / device selection), not more qemu plumbing.

## Reproduce the validation
    # start firmware detached (exposes the FIFO):
    sudo rm -f /tmp/pager_shim_dbg.log
    cd /home/kali/pager-qemu/rootfs
    sudo env PAGER_INPUT_FIFO=/tmp/pager_input.fifo \
        nohup setsid ../qemu-8.2.2/build/qemu-mipsel -L . ./pineapple/pineapple \
        >/tmp/fw.log 2>&1 </dev/null & disown
    sleep 9
    # capture, inject, capture:
    sudo cp dev/fb0 /tmp/a.dump
    python3 ../qemu-firmware/scripts/pager_input.py down
    sleep 2
    sudo cp dev/fb0 /tmp/b.dump
    python3 ../qemu-firmware/scripts/fbrender.py /tmp/a.dump /tmp/a.png
    python3 ../qemu-firmware/scripts/fbrender.py /tmp/b.dump /tmp/b.png
    # set PAGER_DBG=1 in the env to get /tmp/pager_shim_dbg.log (broadcast trace).
