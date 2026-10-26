# Makefile for pineap-ng — native aarch64 reimplementation of the WiFi
# Pineapple CLI (`pineap`).  No external library dependencies (pure POSIX).
#
# Paths / interface are configurable at build time, e.g.:
#   make EXTRA_DEFS='-DPINEAP_SOCK_PATH=\"/opt/pineapple/run/pineapd.sock\" \
#                    -DPINEAP_HOSTAPD_IFACE=\"wlan1\"'
# or at run time via the PINEAP_SOCK / PINEAP_IFACE environment variables.

CC      ?= gcc
CFLAGS  ?= -O2 -g -Wall -Wextra -Wno-unused-parameter -std=gnu11
CFLAGS  += $(EXTRA_DEFS)

SRCS := main.c proto.c comms.c
OBJS := $(SRCS:.c=.o)
BIN  := pineap-ng

.PHONY: all clean test bytes

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS)

%.o: %.c pineap.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(BIN)

# Byte-for-byte oracle validation against /tmp/opencode/proto-re/recv_*.bin
bytes: $(BIN)
	./validate_bytes.sh

# End-to-end test against pineapd-ng (builds the daemon if needed)
test: $(BIN)
	./run_e2e.sh
