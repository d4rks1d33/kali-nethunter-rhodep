# Build the Pandora emulator core as a shared library (libpandora.so) that the
# Python GUI loads via ctypes.
#
#   make           -> libpandora.so (host arch; works on x86_64 and arm64)
#   make test      -> build + run the ctypes smoke test (test_ffi.py)
#   make clean
#
# The sources are pure C (libc only): thumb_core.c (Thumb-2 interpreter),
# emulator.c (EFM32 + Si4432 + OLED model), ihex.c (Intel HEX parser) and
# pandora_ffi.c (the opaque FFI wrapper). emu_log() in emulator.c is a weak,
# no-op symbol so the library is self-contained.
#
# Cross-compiling for the phone (arm64) from an x86 host:
#   make CC=aarch64-linux-gnu-gcc
# The .so has no arch-specific code, so a native build on the arm64 phone
# itself (what install.sh does) is the simplest path.

CC      ?= gcc
CFLAGS  ?= -O2 -fPIC -Wall -Wextra -std=c11
LDFLAGS ?= -shared

SRCS = pandora_ffi.c emulator.c thumb_core.c ihex.c
HDRS = emulator.h thumb_core.h ihex.h protocol.h
OUT  = libpandora.so

.PHONY: all clean test

all: $(OUT)

$(OUT): $(SRCS) $(HDRS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $(OUT) $(SRCS)

test: $(OUT)
	python3 test_ffi.py

clean:
	rm -f $(OUT)
