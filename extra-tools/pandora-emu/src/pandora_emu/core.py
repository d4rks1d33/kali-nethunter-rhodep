"""Pythonic wrapper around libpandora.so (the EFM32 emulator core).

Loads the shared library via ctypes (no build-time dependency beyond the
compiled .so) and wraps the flat ``pe_*`` FFI surface in a PandoraEmu class with
idiomatic methods.

Why ctypes and not cffi: ctypes ships with CPython, needs no compile step at
import time, and the FFI surface here is small and purely by-value / byte-buffer.
cffi (ABI mode / dlopen) would work identically; ctypes keeps the install.sh
dependency list to just "gcc + python3-gi".

Button indices mirror the C enum (emulator.h):
    B_UP=0, B_DOWN=1, B_OK=2, B_BACK=3, B5=4, B6=5
"""
from __future__ import annotations

import ctypes
import os
from typing import List, Tuple

# Logical button indices, kept in sync with emulator.h's enum.
B_UP = 0
B_DOWN = 1
B_OK = 2
B_BACK = 3
B5 = 4
B6 = 5

BUTTON_NAMES = {
    B_UP: "UP",
    B_DOWN: "DOWN",
    B_OK: "OK",
    B_BACK: "BACK",
    B5: "B5",
    B6: "B6",
}

# RF operating mode (PROTO_RF_*), as reported by the core's Si4432 model.
RF_IDLE = 0
RF_READY = 1
RF_RX = 2
RF_TX = 3

# RF modulation (PROTO_MOD_*).
MOD_UNMOD = 0
MOD_OOK = 1
MOD_FSK = 2
MOD_GFSK = 3

# The emulator is AGNOSTIC: there is ONE hardware model that runs ANY .hex for
# this MCU, so there is NO firmware "profile" selection any more. pe_new() still
# takes an int for ABI compatibility but ignores it; we pass this sentinel.
PROFILE_AGNOSTIC = 0

# OLED framebuffer geometry (fixed for the D650 keyfob). The raw buffer is
# FB_COLS x FB_PAGES bytes; each page byte holds 4 logical pixels (2bpp). Only
# FB_VISIBLE_COLS columns are on the physical glass (the rest are off-screen).
FB_COLS = 92
FB_VISIBLE_COLS = 88
FB_PAGES = 16


def _find_library() -> str:
    """Locate libpandora.so.

    Search order: next to this module (where install.sh / build drops it),
    then the native/ source dir (developer in-tree build), then an
    LD_LIBRARY_PATH-style bare name as a last resort.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "libpandora.so"),
        os.path.join(here, "native", "libpandora.so"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # Fall back to the loader's own search path.
    return "libpandora.so"


class PandoraEmuError(RuntimeError):
    """Raised when the native core reports a failure."""


class PandoraEmu:
    """One emulated Pandora D650 keyfob.

    Typical lifecycle::

        emu = PandoraEmu()                      # agnostic: no profile
        emu.load_hex("/path/any_keyfob.hex")    # or load_flat(bytes)
        emu.set_knock([B_UP, B_OK, ...], gate=B5)  # optional firmware knock
        emu.reset_unlock()
        while running:
            emu.run(200_000)
            fb = emu.get_framebuffer()          # 1472 bytes
            ...render...
    """

    _lib = None  # shared CDLL handle, loaded once per process

    @classmethod
    def _load(cls) -> ctypes.CDLL:
        if cls._lib is not None:
            return cls._lib
        lib = ctypes.CDLL(_find_library())

        lib.pe_new.restype = ctypes.c_void_p
        lib.pe_new.argtypes = [ctypes.c_int]
        lib.pe_free.argtypes = [ctypes.c_void_p]

        lib.pe_load_flat.restype = ctypes.c_int
        lib.pe_load_flat.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        lib.pe_load_hex_file.restype = ctypes.c_int
        lib.pe_load_hex_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

        lib.pe_reset_unlock.argtypes = [ctypes.c_void_p]
        lib.pe_run.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.pe_button.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.pe_nav.restype = ctypes.c_int
        lib.pe_nav.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

        # Knock-code is FIRMWARE CONFIG set by the host (not baked into the core).
        # pe_set_knock(h, const uint8_t* seq, int len, int gate).
        lib.pe_set_knock.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]

        lib.pe_get_fb.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

        lib.pe_tx_pop.restype = ctypes.c_int
        lib.pe_tx_pop.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.pe_rx_inject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]

        # Radio state the firmware configured on the Si4432 (for the HackRF bridge
        # to follow). These are read-only observables computed by the core.
        lib.pe_rf_freq.restype = ctypes.c_uint32
        lib.pe_rf_freq.argtypes = [ctypes.c_void_p]
        lib.pe_rf_mode.restype = ctypes.c_int
        lib.pe_rf_mode.argtypes = [ctypes.c_void_p]
        lib.pe_rf_mod.restype = ctypes.c_int
        lib.pe_rf_mod.argtypes = [ctypes.c_void_p]
        lib.pe_buzzer_event.restype = ctypes.c_int
        lib.pe_buzzer_event.argtypes = [ctypes.c_void_p]

        lib.pe_unlocked.restype = ctypes.c_int
        lib.pe_unlocked.argtypes = [ctypes.c_void_p]
        lib.pe_flash_used.restype = ctypes.c_int
        lib.pe_flash_used.argtypes = [ctypes.c_void_p]
        lib.pe_profile_name.restype = ctypes.c_char_p
        lib.pe_profile_name.argtypes = [ctypes.c_void_p]

        lib.pe_fb_size.restype = ctypes.c_int
        lib.pe_fb_cols.restype = ctypes.c_int
        lib.pe_fb_visible_cols.restype = ctypes.c_int
        lib.pe_fb_pages.restype = ctypes.c_int

        cls._lib = lib
        return lib

    def __init__(self, profile: int = PROFILE_AGNOSTIC) -> None:
        # `profile` is accepted for backwards call-site compatibility but is
        # IGNORED by the agnostic core (single hardware model). Pass nothing.
        self._lib = self._load()
        self._h = self._lib.pe_new(int(profile))
        if not self._h:
            raise PandoraEmuError("pe_new failed (out of memory)")
        self._loaded = False
        # Knock-code last set by set_knock(); None = none configured yet.
        self._knock = None  # type: tuple[list[int], int] | None
        # Cache geometry once; it is fixed by the core (92x16, 88 visible).
        self.fb_size = self._lib.pe_fb_size()
        self.fb_cols = self._lib.pe_fb_cols()
        self.fb_visible_cols = self._lib.pe_fb_visible_cols()
        self.fb_pages = self._lib.pe_fb_pages()
        # Reusable framebuffer buffer to avoid per-frame allocation.
        self._fb_buf = ctypes.create_string_buffer(self.fb_size)

    # -- firmware loading --------------------------------------------------

    def load_flat(self, data: bytes) -> None:
        """Load a flat firmware image (flash.bin) and (re)init the core."""
        rc = self._lib.pe_load_flat(self._h, data, len(data))
        if rc != 0:
            raise PandoraEmuError(f"pe_load_flat failed (rc={rc})")
        self._loaded = True

    def load_hex(self, path: str) -> None:
        """Parse an Intel HEX file and (re)init the core from it."""
        rc = self._lib.pe_load_hex_file(self._h, str(path).encode())
        if rc != 0:
            raise PandoraEmuError(
                f"pe_load_hex_file failed (rc={rc}) for {path}")
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def profile_name(self) -> str:
        return self._lib.pe_profile_name(self._h).decode()

    # -- unlock knock-code (FIRMWARE CONFIG, set by the host) ---------------

    def set_knock(self, seq, gate: int = -1) -> None:
        """Set the unlock knock-code pressed at reset_unlock().

        The knock-code is FIRMWARE CONFIG (the user enters it), NOT hardware: the
        agnostic core does not know it. ``seq`` is a sequence of button indices
        (B_UP/B_DOWN/B_OK/B_BACK/B5/B6); ``gate`` is the index of the final held
        gate button (-1 = none). Pass an empty ``seq`` to disable auto-unlock (the
        user then unlocks manually with the buttons).

        Call this BEFORE reset_unlock(). It is pure config; it does not run the
        core.
        """
        seq = list(seq or [])
        raw = bytes(int(b) & 0xFF for b in seq)
        self._lib.pe_set_knock(self._h, raw, len(raw), int(gate))
        self._knock = (seq, int(gate))

    # -- run / control -----------------------------------------------------

    def reset_unlock(self) -> bool:
        """Reset and run the boot, then the configured knock (if any).

        If a knock-code was installed with set_knock(), it is pressed purely over
        GPIO as an autotest after boot. If no knock was set, auto_unlock does
        nothing beyond settling the boot (the user then unlocks with the buttons).
        Returns the OBSERVABLE unlock flag (framebuffer has content).
        """
        self._require_loaded()
        self._lib.pe_reset_unlock(self._h)
        return self.unlocked

    def run(self, insns: int) -> None:
        """Run one burst of `insns` instructions."""
        self._require_loaded()
        self._lib.pe_run(self._h, int(insns))

    @property
    def unlocked(self) -> bool:
        return bool(self._lib.pe_unlocked(self._h))

    # -- buttons -----------------------------------------------------------

    def press(self, btn: int) -> None:
        """Set a button to the pressed level (raw; held until release)."""
        self._lib.pe_button(self._h, int(btn), 1)

    def release(self, btn: int) -> None:
        """Set a button to the released level (raw)."""
        self._lib.pe_button(self._h, int(btn), 0)

    def nav(self, btn: int, hold: bool = False) -> bool:
        """Inject a synchronized nav press POST-unlock.

        This is the blocking, PC-synchronized press+hold+release the firmware
        expects on its nav_reader. hold=True is a long press. Returns True if
        the cycle completed. The GUI uses this for taps; for sustained long
        presses it can instead use press()/run()/release() around run bursts.
        """
        return bool(self._lib.pe_nav(self._h, int(btn), 1 if hold else 0))

    # -- framebuffer -------------------------------------------------------

    def get_framebuffer(self) -> bytes:
        """Copy the VISIBLE OLED framebuffer (fb_size bytes, default 1472)."""
        self._lib.pe_get_fb(self._h, self._fb_buf)
        return self._fb_buf.raw

    # -- radio -------------------------------------------------------------

    def tx_pop_edges(self, limit: int = 100000) -> List[Tuple[int, int]]:
        """Drain the captured TX edge ring.

        Returns a list of (level, duration_us) tuples in emission order. The
        firmware bit-bangs PB3; each edge is one carrier on/off segment. The
        firmware chooses the cell timing (~400-500us), the 1T/2T symbols and the
        repeat count; the edge ring carries the exact per-edge microseconds.
        """
        edges: List[Tuple[int, int]] = []
        level = ctypes.c_int(0)
        us = ctypes.c_uint32(0)
        for _ in range(limit):
            if not self._lib.pe_tx_pop(
                    self._h, ctypes.byref(level), ctypes.byref(us)):
                break
            edges.append((level.value, us.value))
        return edges

    def rx_inject(self, level: int, duration_us: int) -> None:
        """Inject one clean RX edge into the firmware demodulator (PB0 + TIMER1)."""
        self._lib.pe_rx_inject(self._h, 1 if level else 0, int(duration_us))

    def rf_freq(self) -> int:
        """Carrier frequency (Hz) the firmware configured on the Si4432, 0 if unset."""
        return int(self._lib.pe_rf_freq(self._h))

    def rf_mode(self) -> int:
        """Si4432 operating mode (RF_IDLE / RF_READY / RF_RX / RF_TX)."""
        return int(self._lib.pe_rf_mode(self._h))

    def rf_mod(self) -> int:
        """Si4432 modulation (MOD_UNMOD / MOD_OOK / MOD_FSK / MOD_GFSK)."""
        return int(self._lib.pe_rf_mod(self._h))

    def buzzer_event(self) -> bool:
        """Consume the buzzer-activity flag (True if the fw toggled the buzzer)."""
        return bool(self._lib.pe_buzzer_event(self._h))

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        if getattr(self, "_h", None):
            self._lib.pe_free(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- internals ---------------------------------------------------------

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise PandoraEmuError("no firmware loaded (call load_hex/load_flat)")
