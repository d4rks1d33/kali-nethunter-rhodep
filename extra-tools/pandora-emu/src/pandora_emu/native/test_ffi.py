#!/usr/bin/env python3
"""Smoke test for libpandora.so via ctypes.

Verifies the full boot + auto-unlock + render pipeline through the FFI:
  1. pe_new(pandoramax)
  2. pe_load_flat(PANDORA_MAX.flash.bin)  (flat image path)
  3. pe_reset_unlock()                    (boot + knock-code auto-unlock)
  4. pe_run() for a few hundred thousand insns (let the UI render)
  5. pe_get_fb() and assert the framebuffer has non-zero pixels

Then repeats (2) with pe_load_hex_file(PANDORA_MAX.hex) to exercise the Intel
HEX path. Exits non-zero on any failure so `make test` / CI can gate on it.

Run:  timeout 120 python3 native/test_ffi.py
"""
from __future__ import annotations

import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SO = os.path.join(HERE, "libpandora.so")

FLAT_BIN = "/opt/postmarket/test/bins/PANDORA_MAX.flash.bin"
HEX_FILE = "/opt/postmarket/test/PANDORA_MAX.hex"

PROFILE_PANDORAMAX = 1

# Number of post-unlock instructions to run so the firmware paints the UI.
RENDER_INSNS = 400_000
RENDER_BURSTS = 8


def load_lib() -> ctypes.CDLL:
    lib = ctypes.CDLL(SO)
    lib.pe_new.restype = ctypes.c_void_p
    lib.pe_new.argtypes = [ctypes.c_int]
    lib.pe_free.argtypes = [ctypes.c_void_p]
    lib.pe_load_flat.restype = ctypes.c_int
    lib.pe_load_flat.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.pe_load_hex_file.restype = ctypes.c_int
    lib.pe_load_hex_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.pe_reset_unlock.argtypes = [ctypes.c_void_p]
    lib.pe_run.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.pe_get_fb.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.pe_unlocked.restype = ctypes.c_int
    lib.pe_unlocked.argtypes = [ctypes.c_void_p]
    lib.pe_fb_size.restype = ctypes.c_int
    lib.pe_profile_name.restype = ctypes.c_char_p
    lib.pe_profile_name.argtypes = [ctypes.c_void_p]
    return lib


def framebuffer_nonzero(lib, h, fb_size) -> tuple[bool, int]:
    buf = ctypes.create_string_buffer(fb_size)
    lib.pe_get_fb(h, buf)
    data = buf.raw
    nonzero = sum(1 for b in data if b)
    return nonzero > 0, nonzero


def boot_and_render(lib, h) -> tuple[bool, int, int]:
    lib.pe_reset_unlock(h)
    unlocked = lib.pe_unlocked(h)
    for _ in range(RENDER_BURSTS):
        lib.pe_run(h, RENDER_INSNS)
    fb_size = lib.pe_fb_size()
    ok, nz = framebuffer_nonzero(lib, h, fb_size)
    return ok, nz, unlocked


def test_flat(lib) -> bool:
    if not os.path.exists(FLAT_BIN):
        print(f"SKIP flat: {FLAT_BIN} not found")
        return True
    with open(FLAT_BIN, "rb") as f:
        data = f.read()
    h = lib.pe_new(PROFILE_PANDORAMAX)
    assert h, "pe_new returned NULL"
    name = lib.pe_profile_name(h).decode()
    rc = lib.pe_load_flat(h, data, len(data))
    print(f"[flat] pe_load_flat({name}, {len(data)} B) -> {rc}")
    if rc != 0:
        lib.pe_free(h)
        print("FAIL flat: pe_load_flat")
        return False
    ok, nz, unlocked = boot_and_render(lib, h)
    print(f"[flat] unlocked={unlocked} framebuffer nonzero bytes={nz}")
    lib.pe_free(h)
    if not ok:
        print("FAIL flat: framebuffer is empty after boot+unlock+render")
        return False
    print("PASS flat")
    return True


def test_hex(lib) -> bool:
    if not os.path.exists(HEX_FILE):
        print(f"SKIP hex: {HEX_FILE} not found")
        return True
    h = lib.pe_new(PROFILE_PANDORAMAX)
    assert h, "pe_new returned NULL"
    rc = lib.pe_load_hex_file(h, HEX_FILE.encode())
    print(f"[hex] pe_load_hex_file({HEX_FILE}) -> {rc}")
    if rc != 0:
        lib.pe_free(h)
        print("FAIL hex: pe_load_hex_file")
        return False
    ok, nz, unlocked = boot_and_render(lib, h)
    print(f"[hex] unlocked={unlocked} framebuffer nonzero bytes={nz}")
    lib.pe_free(h)
    if not ok:
        print("FAIL hex: framebuffer is empty after boot+unlock+render")
        return False
    print("PASS hex")
    return True


def main() -> int:
    if not os.path.exists(SO):
        print(f"ERROR: {SO} not built. Run `make` first.")
        return 2
    lib = load_lib()
    ok_flat = test_flat(lib)
    ok_hex = test_hex(lib)
    if ok_flat and ok_hex:
        print("\nALL TESTS PASSED")
        return 0
    print("\nTESTS FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
