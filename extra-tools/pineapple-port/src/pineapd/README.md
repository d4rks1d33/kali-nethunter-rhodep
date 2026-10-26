# pineapd-ng

Native **aarch64 / Kali (NetHunter)** reimplementation of `pineapd`, the WiFi
Pineapple PineAP/Karma daemon. Functionally compatible with the original MIPS
binary so the PHP panel and the `pineap` CLI (`pineap-ng`) talk to it exactly as
to the original: **same socket, same 4097-byte/big-endian protocol, same SQLite
schema, same `get_status` JSON.**

Part of the WiFi Pineapple → Kali port for the Motorola G82 (rhodep).

Written in C from the reverse-engineering evidence in:
- `../../docs/PROTOCOLO-DEFINITIVO.md` — socket protocol (resolved by execution)
- `../../docs/INFORME_pineapd_RE.md`   — config, SQLite schema, threads, karma, deauth

---

## Build

Dependencies (Kali/Debian aarch64):

```sh
apt install build-essential libpcap-dev libsqlite3-dev libnl-3-dev libnl-genl-3-dev
```

Then:

```sh
make
file pineapd-ng       # => ELF 64-bit ... ARM aarch64
```

### Relocating paths (optional)

All paths default to the **original** locations for panel/CLI compatibility.
Override at build time, e.g. to move the socket under `/opt/pineapple/run/`:

```sh
make EXTRA_DEFS='-DPINEAPD_SOCK_PATH=\"/opt/pineapple/run/pineapd.sock\" \
                 -DPINEAPD_SSID_DB=\"/opt/pineapple/db/pineapple.db\"'
```

Available defines (see `pineapd.h`): `PINEAPD_SOCK_PATH`, `PINEAPD_LOCK_PATH`,
`PINEAPD_CONF_PATH`, `PINEAPD_RECON_DB`, `PINEAPD_LOG_DB`, `PINEAPD_SSID_DB`,
`PINEAPD_HANDSHAKE_DIR`, `PINEAPD_DEFAULT_IFACE`.

---

## Usage

```
pineapd-ng <config> [--initdbs] [--debug] [--foreground]
```

- `<config>`      — path to `pineap.conf` (default `/tmp/pineap.conf`)
- `--initdbs`     — create the SQLite schema and exit (also accepts `--initdb`)
- `--debug`       — enable the `[!! ..] [TAG]` logger on stderr
- `--foreground`  — accepted for convenience (the daemon never forks; the init
                    script backgrounds it with `&`, matching the original)

Runtime config is read from `pineap.conf` (`key = value`, tolerant parser) and
is **re-serialized on every `setOpt`** exactly like the original, so the panel's
reads stay consistent.

---

## Architecture (mirrors the original — INFORME §H)

| Thread              | File        | Role                                                    |
|---------------------|-------------|---------------------------------------------------------|
| capture + inject    | `iface.c`   | pcap capture, channel hopping, drains the inject queue  |
| parser + results    | `parser.c`  | decode frames, write DB rows, assemble handshake pcaps  |
| main / comms        | `main.c` / `comms.c` | accept + recv(4097) + dispatch, beaconing, scan timing |

Supporting modules: `config.c` (PineAPConfig/ScanState/MAC/Logger), `db.c`
(SQLite), `frames.c` (802.11 frame building), `parse.c` (802.11 parsing),
`karma.c` (SSID-pool beaconing).

---

## Protocol (byte-for-byte — PROTOCOLO-DEFINITIVO.md)

- `AF_UNIX`/`SOCK_STREAM` at `/var/run/pineapd.sock`, `bind` length **110**.
- Request = **4097 bytes fixed**, zero-filled, multibyte integers **big-endian**,
  `buf[0]` = command.
- Server reads 4097 in a loop (EAGAIN → `usleep(20)`, up to 25001 tries) and
  dispatches `switch(buf[0]-1)`.
- Response = raw stream terminated by a **NUL**; only `get_status`,
  `list_ssids`, `list_probes` send a payload.

### Commands (4..21) — all implemented

| id | command                    | id | command                 |
|---:|----------------------------|---:|-------------------------|
| 4  | run_scan                   | 14 | add_ssid                |
| 5  | pause_scan                 | 15 | del_ssid                |
| 6  | unpause_scan               | 16 | clear_ssids             |
| 7  | stop_scan                  | 17 | list_ssids *(response)* |
| 8  | deauth                     | 18 | list_probes *(response)*|
| 9  | handshake_capture_start    | 19 | inject                  |
| 10 | handshake_capture_stop     | 20 | get_status *(JSON)*     |
| 11 | reload_config              | 21 | setOpt                  |
| 12 | pause (channel hop)        |    |                         |
| 13 | unpause (channel hop)      |    |                         |

### setOpt sub-options (buf[1]) — all implemented

`karma(0)`, `beacon_responses(1)`, `capture_ssids(2)`, `broadcast_pool(3)`,
`logging(4)`, `ap_channel(9)`, `connect_notifications(10)`,
`disconnect_notifications(11)`, `beacon_interval(12)`,
`beacon_response_interval(13)`, `set_source(15)`, `set_target(16)`.

**ap_channel quirk reproduced:** the CLI sends the channel as a *single byte* at
offset 6, which the original reads as `lhu[6]<<16 | lhu[8]`, so channel 11 (0x0b)
is stored in the conf as `184549376`. `pineapd-ng` reproduces this exactly for
byte-for-byte panel compatibility (`comms.c:handle_setopt`).

### get_status JSON

Byte-identical to `ScanState::toJSON`. Idle output (verified against the
original under qemu):

```
{\n\t"captureRunning": false,\n\t"scanRunning": false\n}\n
```

Full branches include `bssid`, `continuous`, `scanID`, `scanPercent` when
capture/scan are running (reconstructed from the binary's string fragments).

---

## SQLite schema (EXACT — INFORME §G, verified column-for-column)

`--initdbs` creates:

- **recon.db**: `aps`, `clients`, `scan_ids`
- **log.db**:   `log`, `tracking`
- **pineapple.db**: `ssids`

The `CREATE TABLE` statements match the literals extracted from the original
binary, so the PHP panel reads/writes these tables unchanged.

---

## Kali / aarch64 adaptations (vs original OpenWrt MIPS)

1. **Channel control via nl80211** (`iface.c`): the original used WEXT ioctls
   (`SIOCSIWFREQ` via `iwreq`). On modern Kali/mac80211 WEXT is deprecated, so
   `pineapd-ng` sets the channel through **libnl/nl80211**
   (`NL80211_CMD_SET_CHANNEL`), falling back to `iw dev <if> set channel` which
   is always present on Kali.
2. **Monitor mode via `iw`**: best-effort `iw dev <if> set type monitor` + `ip
   link set up` instead of the OpenWrt `airmon-ng`/ioctl path.
3. **Configurable interface name**: the original assumes `wlan1mon`. Set
   `pineap_interface` in `pineap.conf`. On the G82 this is the internal WiFi in
   monitor mode or an external adapter (e.g. the TP-Link Archer). Default is
   `wlan1mon` (override with `-DPINEAPD_DEFAULT_IFACE`).
4. **Configurable paths** (socket, DBs, handshakes, config) via `-D` defines,
   defaulting to the originals for panel compatibility.
5. **libwifi fork replaced** by a self-contained 802.11 + radiotap builder
   (`frames.c`) and parser (`parse.c`). The Hak5 libwifi fork was MIPS-only; the
   subset pineapd actually used (beacon / deauth / disassoc build; beacon /
   probe-req / EAPOL parse) is reimplemented natively — more robust on aarch64
   and ABI-independent. Capture/injection still use libpcap, identical to the
   original.
6. **Karma responses stay in hostapd** (INFORME §F.1): the real karma
   probe-responses are produced by hostapd (`hostapd_cli ... karma_*`), not
   pineapd. `pineapd-ng` does the SSID-pool beaconing + SSID harvest + recon +
   handshakes + deauth, and forwards `karma on/off` to `hostapd_cli` like the CLI
   did.

---

## Testing

```sh
./run_tests.sh          # schema + protocol validation (20 checks)
```

What it validates:

- **Schema**: `--initdbs` output matches the RE schema column-for-column.
- **Protocol** (byte-exact vs the original captured under qemu):
  - `get_status` idle JSON — **identical bytes**
  - `add_ssid` / `list_ssids` (`\n` separator, NUL terminator) — identical
  - `clear_ssids` → empty list — identical
  - `setOpt capture_ssids on` → persisted to conf
  - `ap_channel 11` → `184549376` in conf (quirk)
  - `beacon_interval LOW` → persisted
  - raw `0x14` bytes == `get_status`

A stress replay (`run_tests.sh` plus the standalone replay) feeds **all 32 real
captured client requests** (`/tmp/opencode/proto-re/recv_*.bin`, exact bytes
from the original `pineap` CLI) through the daemon; it handles every one and
stays alive.

`compare_original.sh` is a template for live A/B testing against the original
MIPS daemon under qemu. Note: the original's `run_scan` path segfaults under
qemu when pointed at a non-WiFi interface (no radio), so the dynamic
scan/capture JSON branches are validated against the captured reference and the
binary's string fragments rather than a live qemu run.

---

## What is validated vs what needs the G82 + real WiFi

**Validated on this host (aarch64):**
- Compiles clean to aarch64 (`-Wall -Wextra`, zero warnings).
- Socket protocol byte-for-byte: get_status, add/del/list/clear ssids, setOpt
  (incl. ap_channel quirk, beacon_interval), list_probes, inject, deauth,
  handshake start/stop, scan control — all accepted and dispatched.
- SQLite schema matches the panel's expectations exactly.
- 802.11 frame builders emit correct beacon/deauth/disassoc frames (verified by
  decoding frame-control and tagged params).

**Requires the G82 + a monitor-capable adapter to validate live:**
- Actual monitor-mode capture and channel hopping (nl80211 path).
- Beacon injection of the SSID pool and deauth/disassoc injection
  (`pcap_inject`).
- WPA handshake capture to `/tmp/handshakes/<mac>_{full,partial}.pcap`.
- End-to-end karma with hostapd.

These paths are implemented and compile; they cannot be exercised without a
real radio in monitor mode.

---

## Dependencies on the G82 (runtime)

```sh
apt install libpcap0.8 libsqlite3-0 libnl-3-200 libnl-genl-3-200 iw
# hostapd / hostapd-wpe for karma probe-responses (as in the original design)
```

Build-time (if compiling on-device):
`libpcap-dev libsqlite3-dev libnl-3-dev libnl-genl-3-dev`.

---

## Files

```
pineapd.h      shared types, configurable paths, protocol constants
main.c         entry point, args, single-instance lock, threads, main loop
config.c       PineAPConfig + ScanState singletons, MACAddress, Logger
db.c           SQLite databases (EXACT schema), queries
frames.c       802.11 beacon/deauth/disassoc builders + radiotap
parse.c        802.11 parsing (radiotap, mgmt, probe, EAPOL handshake)
iface.c        pcap capture/inject + nl80211/iw channel control (Kali adaptation)
parser.c       parser/result threads, handshake pcap writer, inject queue
karma.c        SSID-pool beaconing (karma responses are hostapd's job)
comms.c        UNIX socket server (4097/BE protocol, full dispatch)
Makefile       build
test_proto.c   protocol test client (sends exact 4097/BE requests)
run_tests.sh   schema + protocol validation suite
compare_original.sh  A/B template vs original under qemu
```
