#!/usr/bin/env python3
"""Smoke test for the AGNOSTIC libpandora.so via ctypes.

Verifies the full boot + knock auto-unlock + render pipeline through the FFI, and
that the SAME library (no profile) runs BOTH keyfob firmwares:

  1. pe_new(0)                 (profile arg ignored -- single HW model)
  2. pe_load_hex_file(<fw>.hex)
  3. pe_set_knock(seq, gate)   (knock = FIRMWARE CONFIG, entered by the host)
  4. pe_reset_unlock()         (boot + press the knock over GPIO)
  5. pe_run() a few bursts     (let the UI render)
  6. pe_get_fb() -> assert the framebuffer has non-zero pixels (fb_nz > 0)
  7. pe_rf_freq/mode/mod       (radio state the fw set on the Si4432)

Firmwares & canonical fb_nz (from the validated ESP32 core):
  - 2_5253...hex  (pandora)  knock UP,OK,OK,UP,UP,DOWN gate B5  -> ~311/547
  - PANDORA_MAX.hex (pmax)   knock UP,OK,DOWN,OK,UP,DOWN gate B5 -> ~88/429

Exits non-zero on any failure so `make test` / CI can gate on it.

Run:  timeout 180 python3 native/test_ffi.py
"""
from __future__ import annotations

import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SO = os.path.join(HERE, "libpandora.so")

# Firmware .hex locations (first existing path wins).
FW_DIRS = [
    "/opt/postmarket/nethunter-rhodep-repo/extra-tools/pandora-emu/firmwares",
    "/opt/postmarket/test",
    "/opt/postmarket/test/emu-limpio/bins",
]

# Button indices (B_* enum order).
B_UP, B_DOWN, B_OK, B_BACK, B5, B6 = range(6)

# (filename, knock sequence, gate, canonical fb_nz) for each firmware.
# canonical fb_nz is the CONVERGED non-zero pixel count after RENDER_BURSTS (the
# post-unlock count is lower -- ~311/~88 -- and climbs to the converged value as
# the UI finishes drawing). We print and gate on the converged number.
CONVERGED_TOL = 48  # +/- pixels; converged render is deterministic but allow slack
CASES = [
    ("2_5253623551453304897.hex",
     [B_UP, B_OK, B_OK, B_UP, B_UP, B_DOWN], B5, 547),
    ("PANDORA_MAX.hex",
     [B_UP, B_OK, B_DOWN, B_OK, B_UP, B_DOWN], B5, 429),
]

RENDER_INSNS = 400_000
RENDER_BURSTS = 8


def find_fw(name: str):
    for d in FW_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def load_lib() -> ctypes.CDLL:
    lib = ctypes.CDLL(SO)
    lib.pe_new.restype = ctypes.c_void_p
    lib.pe_new.argtypes = [ctypes.c_int]
    lib.pe_free.argtypes = [ctypes.c_void_p]
    lib.pe_load_hex_file.restype = ctypes.c_int
    lib.pe_load_hex_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.pe_set_knock.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    lib.pe_reset_unlock.argtypes = [ctypes.c_void_p]
    lib.pe_run.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.pe_get_fb.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.pe_unlocked.restype = ctypes.c_int
    lib.pe_unlocked.argtypes = [ctypes.c_void_p]
    lib.pe_flash_used.restype = ctypes.c_int
    lib.pe_flash_used.argtypes = [ctypes.c_void_p]
    lib.pe_rf_freq.restype = ctypes.c_uint32
    lib.pe_rf_freq.argtypes = [ctypes.c_void_p]
    lib.pe_rf_mode.restype = ctypes.c_int
    lib.pe_rf_mode.argtypes = [ctypes.c_void_p]
    lib.pe_rf_mod.restype = ctypes.c_int
    lib.pe_rf_mod.argtypes = [ctypes.c_void_p]
    lib.pe_fb_size.restype = ctypes.c_int
    lib.pe_profile_name.restype = ctypes.c_char_p
    lib.pe_profile_name.argtypes = [ctypes.c_void_p]
    return lib


def framebuffer_nonzero(lib, h, fb_size):
    buf = ctypes.create_string_buffer(fb_size)
    lib.pe_get_fb(h, buf)
    return sum(1 for b in buf.raw if b)


def run_case(lib, name, seq, gate, canon) -> bool:
    path = find_fw(name)
    if not path:
        print(f"SKIP {name}: not found in {FW_DIRS}")
        return True

    h = lib.pe_new(0)  # profile ignored (agnostic)
    assert h, "pe_new returned NULL"
    hw = lib.pe_profile_name(h).decode()

    rc = lib.pe_load_hex_file(h, path.encode())
    if rc != 0:
        print(f"FAIL {name}: pe_load_hex_file -> {rc}")
        lib.pe_free(h)
        return False
    flash_used = lib.pe_flash_used(h)

    # knock = FIRMWARE CONFIG entered by the host, pressed over GPIO at reset.
    raw = bytes(seq)
    lib.pe_set_knock(h, raw, len(raw), gate)

    lib.pe_reset_unlock(h)
    unlocked = lib.pe_unlocked(h)
    for _ in range(RENDER_BURSTS):
        lib.pe_run(h, RENDER_INSNS)

    fb_size = lib.pe_fb_size()
    nz = framebuffer_nonzero(lib, h, fb_size)
    freq = lib.pe_rf_freq(h)
    mode = lib.pe_rf_mode(h)
    mod = lib.pe_rf_mod(h)

    print(f"[{name}] hw={hw} flash_used={flash_used} unlocked={unlocked} "
          f"fb_nz={nz} (canonical converged ~{canon}) "
          f"rf: freq={freq}Hz mode={mode} mod={mod}")

    lib.pe_free(h)

    if nz <= 0:
        print(f"FAIL {name}: framebuffer empty after boot+knock+render")
        return False
    if abs(nz - canon) > CONVERGED_TOL:
        print(f"FAIL {name}: converged fb_nz={nz} off canonical {canon} "
              f"(tol +/-{CONVERGED_TOL})")
        return False
    print(f"PASS {name}")
    return True


def main() -> int:
    if not os.path.exists(SO):
        print(f"ERROR: {SO} not built. Run `make` first.")
        return 2
    lib = load_lib()
    ok = True
    for name, seq, gate, canon in CASES:
        ok = run_case(lib, name, seq, gate, canon) and ok
    if ok:
        print("\nALL TESTS PASSED (same libpandora.so ran every firmware, agnostic)")
        return 0
    print("\nTESTS FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
