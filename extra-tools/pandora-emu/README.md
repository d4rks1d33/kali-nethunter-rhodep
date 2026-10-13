# Pandora Emu

A GTK4 / libadwaita touch app that **emulates the Pandora D-605 keyfob** (an ARM
Cortex-M3 / EFM32 remote) by running its **real, unmodified firmware** over a
pure-C Thumb-2 interpreter. It renders the keyfob's OLED, gives you a touch
button pad with short/long press detection, loads any `.hex` firmware, and
bridges radio TX/RX to a **HackRF** over USB-C.

Built for a Motorola G82 5G running Kali Linux (ARM64) + Plasma Mobile (Wayland,
touch), following the same packaging pattern as `nethunter-pro-app`.

## What it does

* Boots the real firmware (`pandora` or `pandoramax` profile) and runs the
  knock-code **auto-unlock** so the UI appears.
* Shows the **88x64, 2 bits-per-pixel OLED** scaled up and centred, redrawing
  only when the framebuffer changes.
* Provides a **touch button pad** (UP / DOWN / OK / BACK + B5 / B6) that tells
  **short taps** from **long presses** -- the firmware acts differently on each.
* Loads **any `.hex`** firmware from disk.
* Drives a **HackRF** for OOK/ASK TX of the captured waveform and for RX.

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
   framebuffer, the touch pad, the firmware loader, and the HackRF controls.
   The HackRF bridge lives in `hackrf.py`.

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

## Using the button pad (short vs long)

The pad detects, per button, how long you hold it:

* **Short tap** (< 600 ms): a brief press/release -- a quick input.
* **Long press** (>= 600 ms): the raw button level is held across emulation
  ticks, so the firmware sees a sustained press. The button turns amber while
  the long press is held.

Logical buttons and their keyfob mapping (`core.py`):

| Button | Index | Keyfob role |
|--------|-------|-------------|
| UP     | 0     | navigate up / primary |
| DOWN   | 1     | navigate down |
| OK     | 2     | confirm / transmit |
| BACK   | 3     | back / cancel |
| B5     | 4     | aux 5 (gate button in the unlock knock-code) |
| B6     | 5     | aux 6 |

The mapping is a plain dict (`PAD_BUTTONS` in `window.py`), so it is easy to
re-label or re-order.

## Loading a `.hex`

Tap **Load .hex**, pick a firmware. The profile is deduced from the filename
(`MAX` -> `pandoramax`, otherwise `pandora`), or you can force it with the
**Profile** picker first. On load the core boots and auto-unlocks, then the
render loop starts. Sample firmwares ship in `firmwares/`.

## HackRF

**Primary backend (active): the `hackrf_transfer` CLI.**

* **TX**: the emulator captures the firmware bit-banging PB3 as edges
  (level, duration_us). TX renders those edges into an interleaved int8 I/Q
  file (carrier ON while level == 1 -- OOK/ASK) and runs
  `hackrf_transfer -t file.iq -f <freq> -s 2000000 -a 1 -x <gain>`.
  Waveform parameters (cell 400 us pandora / 500 us pmax, 1T/2T symbols,
  5x/10x repeats) come from `RE_TX_FRAME_MODEL_D605.md`.
* **RX**: `hackrf_transfer -r file.iq -f <freq> -s 2000000`, then the IQ
  magnitude is thresholded and run-length-encoded into clean edges which are
  injected into the emulator with `pe_rx_inject`.
* Bands: 433.92 (default), 434.42, 868.35, 313.87 MHz.
* If no HackRF (or no CLI tools) is present, the app still runs as a pure
  visual emulator; TX/RX just report "no device".

**Future backend (present but disabled): direct `pyhackrf` bindings.**
`hackrf.py` contains a full `pyhackrf` implementation behind `USE_PYHACKRF`
(default `False`) as a commented block. To enable: `pip install pyhackrf`,
uncomment the block, and set `USE_PYHACKRF = True`.

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

* **No HackRF / no phone was available** during development, so the radio path
  and the on-screen GUI could not be exercised on hardware. The native core is
  verified (`test_ffi.py` boots, unlocks, and renders both a flat image and a
  `.hex`), and all Python modules compile; the GTK widgets follow the
  `nethunter-pro-app` pattern but have not been run against a live display.
* The TX timing fidelity depends on the emulator feeding exact `delay()`
  microseconds (see `RE_TX_FRAME_MODEL_D605.md` B.3); the core's edge ring
  carries the per-edge durations the firmware requested.
* The rolling counter is not persisted to NVM between runs.
