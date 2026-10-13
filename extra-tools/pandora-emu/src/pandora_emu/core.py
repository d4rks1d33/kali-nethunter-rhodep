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

# Firmware profiles understood by the core (pe_new argument).
PROFILE_PANDORA = 0
PROFILE_PANDORAMAX = 1

# OLED framebuffer geometry (fixed for the D-605 keyfob). The raw buffer is
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
    """One emulated Pandora D-605 keyfob.

    Typical lifecycle::

        emu = PandoraEmu(PROFILE_PANDORAMAX)
        emu.load_hex("/path/PANDORA_MAX.hex")   # or load_flat(bytes)
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

        lib.pe_get_fb.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

        lib.pe_tx_pop.restype = ctypes.c_int
        lib.pe_tx_pop.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.pe_rx_inject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]

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

    def __init__(self, profile: int = PROFILE_PANDORAMAX) -> None:
        self._lib = self._load()
        self._h = self._lib.pe_new(int(profile))
        if not self._h:
            raise PandoraEmuError("pe_new failed (out of memory)")
        self._loaded = False
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

    # -- run / control -----------------------------------------------------

    def reset_unlock(self) -> bool:
        """Reset and run the full boot + auto-unlock. Returns the unlock flag."""
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
        firmware bit-bangs PB3; each edge is one carrier on/off segment. See
        RE_TX_FRAME_MODEL_D605.md: cell 400us (pandora) / 500us (pmax),
        symbols 1T/2T, repeated 5x/10x.
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
        """Inject one clean RX edge into the firmware demodulator (PB0)."""
        self._lib.pe_rx_inject(self._h, 1 if level else 0, int(duration_us))

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


def guess_profile(path: str) -> int:
    """Deduce the profile from a firmware filename.

    "MAX" anywhere in the name (case-insensitive) -> pandoramax, else pandora.
    """
    base = os.path.basename(path).upper()
    return PROFILE_PANDORAMAX if "MAX" in base else PROFILE_PANDORA
