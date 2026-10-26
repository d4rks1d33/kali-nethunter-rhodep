# Makefile for pineapd-ng — native aarch64 reimplementation of pineapd
#
# Dependencies (Kali/Debian aarch64):
#   apt install libpcap-dev libsqlite3-dev libnl-3-dev libnl-genl-3-dev
#
# Paths are configurable at build time, e.g. to relocate the socket:
#   make EXTRA_DEFS='-DPINEAPD_SOCK_PATH=\"/opt/pineapple/run/pineapd.sock\"'

CC      ?= gcc
CFLAGS  ?= -O2 -g -Wall -Wextra -Wno-unused-parameter -std=gnu11
CFLAGS  += $(shell pkg-config --cflags libnl-genl-3.0 2>/dev/null)
CFLAGS  += $(EXTRA_DEFS)

LDLIBS  := -lpcap -lsqlite3 -lpthread
LDLIBS  += $(shell pkg-config --libs libnl-genl-3.0 2>/dev/null)
# fall back if pkg-config is unavailable
ifeq ($(strip $(shell pkg-config --exists libnl-genl-3.0 2>/dev/null && echo ok)),)
LDLIBS  += -lnl-genl-3 -lnl-3
CFLAGS  += -I/usr/include/libnl3
endif

SRCS := main.c config.c db.c frames.c parse.c iface.c parser.c karma.c comms.c scan_sta.c
OBJS := $(SRCS:.c=.o)
BIN  := pineapd-ng

.PHONY: all clean test initdbs-test

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDLIBS)

%.o: %.c pineapd.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(BIN) test_proto test_proto.o

# Build + run the protocol test (starts pineapd-ng, drives the socket)
test: $(BIN) test_proto
	./run_tests.sh

test_proto: test_proto.c pineapd.h
	$(CC) $(CFLAGS) -o $@ test_proto.c
