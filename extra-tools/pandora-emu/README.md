# Pandora Emu

A GTK4 / libadwaita touch app that **emulates the Pandora D650 keyfob** (an ARM
Cortex-M3 remote) by running its **real, unmodified firmware** over a pure-C
Thumb-2 interpreter. It renders the keyfob's OLED, gives you a touch button pad
with real long-press (held GPIO), a knock-code / unlock panel, loads any `.hex`
firmware, and bridges radio TX/RX to a **HackRF** over USB-C in real time.

Built for a Motorola G82 running mainline Linux + libadwaita (Wayland, touch).
The emulator core is **agnostic**: one hardware model runs any keyfob `.hex`;
there is no firmware "profile" to pick.

## What it does

* Boots the real firmware on a single agnostic hardware model and (optionally)
  runs a user-entered knock-code **auto-unlock** so the UI appears.
* Shows the **88x64, 2 bits-per-pixel OLED** scaled up and centred, redrawing
  only when the framebuffer changes.
* Provides a **touch button pad** (six keyfob buttons) with a **real long
  press**: pressing holds the raw GPIO pin down for as long as your finger is on
  the button, so the firmware sees a genuinely sustained press.
* Lets you enter the **knock-code sequence** and auto-**unlock** (or unlock by
  hand with the buttons).
* Loads **any `.hex`** firmware from disk.
* Drives a **HackRF** as a faithful Si4432 replacement: continuous streaming RX
  (demodulated and injected in real time) and OOK/FSK TX, following the
  firmware-selected frequency and modulation.

## Architecture (3 layers)

1. **Native core (`src/pandora_emu/native/`)** -- `thumb_core.c` (Thumb-2
   interpreter), `emulator.c` (EFM32 + Si4432 + OLED model + auto-unlock),
   `ihex.c` (Intel HEX parser), and `pandora_ffi.c` (a thin opaque wrapper).
   Compiled into **`libpandora.so`** (pure C, libc only). The wrapper exposes a
   flat `pe_*` ABI (create, load hex/flat, reset+unlock, run, button, nav,
   get framebuffer, tx_pop, rx_inject, free).
2. **Python core (`core.py`)** -- loads `libpandora.so` via **ctypes** (no
   build-time dependency) and wraps it in a pythonic `PandoraEmu` class.
3. **GUI (`window.py`)** -- the Adw.ApplicationWindow with the cairo
   framebuffer, the touch pad, the knock/unlock panel, the firmware loader, and
   the HackRF bridge status. The HackRF bridge itself lives in `hackrf.py`.

## Install (on the phone)

System dependencies first:

```sh
apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
            libgtk-4-1 libadwaita-1-0 gcc make hackrf
```

Then:

```sh
sudo ./install.sh
```

`install.sh` compiles `libpandora.so` next to the Python module, runs
`pip install --break-system-packages .`, installs the `.desktop` + polkit
action, and drops a udev rule so the login user can reach the HackRF without
root. Look for **Pandora Emu** in the app grid.

## Using the button pad (tap vs real long press)

Each button drives the **same GPIO pin** the whole time you touch it:

* **Short tap**: a quick press/release.
* **Long press**: hold the button -- the raw pin stays **held down** across
  emulation ticks (not a fixed-length pulse), so the firmware sees a genuinely
  sustained press. The button turns **amber** while held, and the toast flips to
  "LONG (held)" past ~600 ms. Lifting your finger releases the pin.

Logical buttons and their keyfob mapping (`core.py`):

| UI label | Core index | Keyfob role |
|----------|------------|-------------|
| B1 (UP)   | 0 | On / Menu / Cell + |
| B3 (DOWN) | 1 | Menu / Cell - |
| B2 (OK)   | 2 | Confirm / Mode |
| B4 (BACK) | 3 | RX frequency / back |
| B5        | 4 | AM+FM / Open (knock gate) |
| B6        | 5 | Trunk |

The mapping lives in a single table (`KEYFOB_BUTTONS` in `window.py`), so it is
easy to re-label or re-order.

## Knock-code / Unlock

The **Knock-code / Unlock** panel lets you enter the unlock button sequence
(like the newer Flipper apps): tap the `+B1..+B6` chips to build the sequence,
pick an optional **gate** (final held button), and hit **Unlock**. That calls
`core.set_knock(seq, gate)` then `core.reset_unlock()` on the worker thread.

**Fallback:** leave the sequence empty and unlock by hand with the six buttons.

## Loading a `.hex`

Tap **Load .hex**, pick a firmware. The core is agnostic (one hardware model
runs any `.hex`) -- there is **no profile picker**. On load the core boots, a
configured knock (if any) is re-applied and auto-unlock runs, then the render
loop starts. Sample firmwares ship in `firmwares/`.

## HackRF

**Backend: the `hackrf_transfer` CLI in streaming mode** (one long-lived process
per direction via pipes -- not a process per capture). The bridge makes the
HackRF behave like the firmware's virtual Si4432 and **follows the firmware**:

* **Frequency**: tuned from `core.rf_freq()` (the Si4432 registers the firmware
  programmed), falling back to 433.92 MHz until the band is selected.
* **Modulation**: `core.rf_mod()` picks OOK/ASK vs FSK for both TX synthesis and
  (envelope) RX.
* **Half-duplex arbitration** via `core.rf_mode()`: the bridge listens
  continuously by default; when the firmware goes to **TX** it stops RX, drains
  the bit-banged edges (`tx_pop_edges`), transmits, then resumes RX.
* **RX** (wired, continuous): `hackrf_transfer -r -` streams raw int8 I/Q over a
  pipe into a reader thread that demodulates blocks with **numpy** (magnitude,
  adaptive threshold, run-length-encode) and injects the edges into the firmware
  with `rx_inject()` in real time, so the firmware's own demodulator runs.
* **TX**: the firmware's PB3 edges are rendered to interleaved int8 I/Q
  (`edges_to_iq`, honouring the modulation) and piped to `hackrf_transfer -t -`.
* If no HackRF (or no CLI tools) is present, the app still runs as a pure visual
  emulator; the radio side is a clean no-op (TX queues are still drained).

`numpy` is a hard dependency (the real-time demod/synthesis are vectorised).

## Building / testing the core by hand

```sh
cd src/pandora_emu/native
make                       # -> libpandora.so
make test                  # ctypes smoke test (boot + unlock + render)
# or:
timeout 120 python3 test_ffi.py
```

Cross-compile for the phone from an x86 host: `make CC=aarch64-linux-gnu-gcc`
(the code is arch-neutral; a native build on the phone is simplest).

## Honest limitations

* **No HackRF / no phone was available** during development, so the live radio
  path and the on-screen GUI could not be exercised on hardware. The native core
  is verified (boots, unlocks, renders), all Python modules compile, the RX/TX
  demod round-trip is checked in software with synthetic I/Q, and the bridge is
  exercised against the real `libpandora.so` (no-op without an SDR); the GTK
  widgets have not yet been run against a live display or a real HackRF.
* The TX timing fidelity depends on the emulator feeding exact `delay()`
  microseconds; the core's edge ring carries the per-edge durations the firmware
  requested, and `edges_to_iq` renders them 1:1 at the chosen sample rate.
* FSK/GFSK RX uses an envelope detector (presence/absence), which is enough for
  the firmware's slicer in software but may need a true discriminator on the air.
* The rolling counter is not persisted to NVM between runs.
