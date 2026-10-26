# Makefile for pineapd-pager — native aarch64 reimplementation of the WiFi
# Pineapple Pager PineAP daemon. Links the vendored mpack amalgamation plus
# libpcap, libsqlite3, libnl-genl-3 and pthread.
#
# Dependencies (Kali/Debian aarch64):
#   apt install libpcap-dev libsqlite3-dev libnl-3-dev libnl-genl-3-dev
#
# Relocate paths at build time, e.g.:
#   make EXTRA_DEFS='-DPINEAPD_SOCK_PATH=\"/tmp/pineap_sock\"'

CC      ?= gcc
CFLAGS  ?= -O2 -g -Wall -Wextra -Wno-unused-parameter -std=gnu11
CFLAGS  += $(shell pkg-config --cflags libnl-genl-3.0 2>/dev/null)
CFLAGS  += $(EXTRA_DEFS)

# mpack vendored amalgamation lives two levels up in vendor/.
VENDOR_DIR := ../../vendor/mpack
# Quiet mpack's own -Wextra noise without hiding warnings in our code.
MPACK_CFLAGS := -O2 -g -std=gnu11 -w -I$(VENDOR_DIR)

LDLIBS  := -lpcap -lsqlite3 -lpthread
LDLIBS  += $(shell pkg-config --libs libnl-genl-3.0 2>/dev/null)
ifeq ($(strip $(shell pkg-config --exists libnl-genl-3.0 2>/dev/null && echo ok)),)
LDLIBS  += -lnl-genl-3 -lnl-3
CFLAGS  += -I/usr/include/libnl3
endif

SRCS := main.c config.c db.c frames.c iface.c scan_sta.c recon_mon.c msg.c sock.c handlers.c openap.c
OBJS := $(SRCS:.c=.o)
MPACK_OBJ := mpack.o
BIN  := pineapd-pager

.PHONY: all clean test

all: $(BIN)

$(BIN): $(OBJS) $(MPACK_OBJ)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(MPACK_OBJ) $(LDLIBS)

# Our sources depend on pager.h/msg.h; keep it simple with a blanket prereq.
%.o: %.c pager.h msg.h
	$(CC) $(CFLAGS) -I$(VENDOR_DIR) -c -o $@ $<

# mpack amalgamation — compiled with -w so its internal warnings don't fail us.
$(MPACK_OBJ): $(VENDOR_DIR)/mpack.c $(VENDOR_DIR)/mpack.h
	$(CC) $(MPACK_CFLAGS) -c -o $@ $(VENDOR_DIR)/mpack.c

clean:
	rm -f $(OBJS) $(MPACK_OBJ) $(BIN) test_client

# Build the C test client (used by the mini socket test). Links msg.c + config.c
# (for pineap_log/mac helpers) + the mpack object.
test_client: test_client.c pager.h msg.h $(MPACK_OBJ)
	$(CC) $(CFLAGS) -I$(VENDOR_DIR) -o $@ test_client.c msg.c config.c $(MPACK_OBJ) -lpthread
