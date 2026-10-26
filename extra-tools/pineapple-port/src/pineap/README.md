# pineap-ng — native aarch64 WiFi Pineapple CLI

`pineap-ng` is a from-scratch reimplementation, in C for ARM aarch64 (Kali on
the Motorola G82), of the WiFi Pineapple command-line client `pineap`. It is a
**drop-in replacement** for the original MIPS binary: it speaks the exact same
UNIX-socket protocol (byte-for-byte), parses the same argv, prints the same
stdout, and performs the same `hostapd_cli` side effects, so the stock PHP panel
and `pineapd` / `pineapd-ng` cannot tell the difference.

Part of the full WiFi Pineapple port to Kali. The companion daemon is
`../pineapd/` (pineapd-ng).

## Evidence basis

Reverse-engineered from the original binary
(`/usr/bin/pineap`, ELF MIPS32r2/MIPS16e, musl, *PineAP V3.2*, commit
`de3598e095`). Authoritative docs:

- `../../docs/PROTOCOLO-DEFINITIVO.md` — the socket protocol, resolved by live
  execution of both original binaries under qemu-mips.
- `../../docs/INFORME-pineap-RE.md` — the full RE of `pineap` (argv parse,
  command list, wire layout, stdout, side effects).

Byte oracle: `/tmp/opencode/proto-re/recv_*.bin` are the exact bytes the
original client sent per command (captured under qemu-mips).

## Protocol (summary)

- `AF_UNIX` / `SOCK_STREAM` at `/var/run/pineapd.sock`, `connect()` len **110**.
- Request = **4097 bytes fixed**, zero-filled; `buf[0]` = command id; multibyte
  integers are **big-endian**; the client always `send()`s exactly 4097 bytes.
- Response (only `get_status`, `list_ssids`, `list_probes`) = a raw stream the
  server terminates with a **NUL**; the client dumps it verbatim to stdout.

### Command table (`buf[0]`)

| id | command | payload (absolute offsets) | resp |
|---:|---------|----------------------------|------|
| 4  | `run_scan <dur> <freq>` | `[1]=freq, [2..5]=dur u32BE` | no |
| 5  | `pause_scan` | — | no |
| 6  | `unpause_scan` | — | no |
| 7  | `stop_scan` | — | no |
| 8  | `deauth <src> <tgt> <ch> <mult>` | `[1..6]=target, [7..12]=source, [13]=ch, [14..17]=mult u32BE` | no |
| 9  | `handshake_capture_start <bssid> <ch>` | `[1..6]=bssid, [7]=ch` | no |
| 10 | `handshake_capture_stop` | — | no |
| 11 | `reload_config` | — | no |
| 12 | `pause` (channel hop) | — | no |
| 13 | `unpause` (channel hop) | — | no |
| 14 | `add_ssid <ssid>` | `[1..]=SSID+NUL` | no |
| 15 | `del_ssid <ssid>` | `[1..]=SSID+NUL` | no |
| 16 | `clear_ssids` | — | no |
| 17 | `list_ssids` | — | **yes** |
| 18 | `list_probes <mac>` | `[1..6]=MAC (00..=all)` | **yes** |
| 19 | `inject <file> <ch> <count> <delay>` | `[1]=ch, [2..5]=count u32BE, [6..9]=len u32BE, [10..]=frame` | no |
| 20 | `get_status` | — | **yes** (JSON) |
| 21 | setOpt | `[1]=opt, [2..5]=0, [6..]=data` | no |

### setOpt sub-options (`buf[1]`)

| opt | command | data @ offset 6 |
|---:|---------|-----------------|
| 0  | `karma <on/off>` | bool |
| 1  | `beacon_responses <on/off>` | bool |
| 2  | `capture_ssids <on/off>` | bool |
| 3  | `broadcast_pool <on/off>` | bool |
| 4  | `logging <on/off>` | bool |
| 9  | `ap_channel <ch>` | **single byte** (see quirk) |
| 10 | `connect_notifications <on/off>` | bool |
| 11 | `disconnect_notifications <on/off>` | bool |
| 12 | `beacon_interval <LOW/NORMAL/AGGRESSIVE>` | u32BE (`9000`/`5712`/`3500`) |
| 13 | `beacon_response_interval <...>` | u32BE |
| 15 | `set_source <mac>` | 6-byte MAC |
| 16 | `set_target <mac>` | 6-byte MAC |

**Quirks reproduced for byte-exactness:**

- `ap_channel N` sends `N` as a **single byte at offset 6**, not a u32. The
  original server reads it as `lhu[6]<<16 | lhu[8]`, so channel 11 becomes
  `0x0B000000 = 184549376` in the saved config. pineap-ng emits the same single
  byte (`req_opt_ap_channel`), and pineapd-ng reproduces the same stored value.
- `inject` uses the **compact** layout (ch@1, count@2, len@6, frame@10) and the
  4th arg `delay` is parsed but **NOT** placed on the wire.
- `deauth` serializes **target first, then source** on the wire.

## Command routing: socket vs hostapd_cli

Per the RE (`INFORME-pineap-RE.md` §B.5 / §F), not every command goes to the
socket. pineap-ng reproduces this exactly:

| command | socket (4097-byte request) | `hostapd_cli -i <iface>` |
|---------|:--:|:--|
| `run_scan`, `pause_scan`, `unpause_scan`, `stop_scan` | yes | — |
| `deauth`, `handshake_capture_start/stop`, `reload_config`, `pause`, `unpause` | yes | — |
| `add_ssid`, `del_ssid`, `add_ssid_file`, `del_ssid_file`, `clear_ssids`, `list_ssids`, `list_probes`, `inject`, `get_status` | yes | — |
| `capture_ssids`, `beacon_responses`, `broadcast_pool`, `logging`, `connect_notifications`, `disconnect_notifications`, `beacon_interval`, `beacon_response_interval`, `set_source`, `set_target`, `ap_channel` | yes (setOpt) | — |
| **`karma <on/off>`** | **yes** (setOpt 0) | **yes** (`karma_enable` / `karma_disable`) — HYBRID |
| **`ssid_filter <black/white>`** | **no** | **yes** (`karma_black` / `karma_white`) |
| **`mac_filter <black/white>`** | **no** | **yes** (`karma_mac_black` / `karma_mac_white`) |
| `help`, `version` | — (local only) | — |
| `rts_attack`, `rts_scan_id` | listed in help but have **no handler** → `Unknown command:` (matches original) |

So `karma` hits **both** hostapd and the socket; the two filter commands hit
hostapd **only**; everything else is socket-only. The original hard-codes
`-i wlan0`; pineap-ng makes the interface configurable (see below) so the real
G82 AP interface drives hostapd's karma.

## Usage

Both argv forms are supported, identical to the original:

```
pineap-ng <command> [args]                 # legacy syntax
pineap-ng /tmp/pineap.conf <command> [args] # config syntax (argv[1] has "pineap.conf")
```

The CLI never reads the config file — `argv[1]` only selects config/legacy mode;
the daemon owns the config.

Examples (exactly how the PHP panel calls it):

```
pineap-ng /tmp/pineap.conf get_status
pineap-ng /tmp/pineap.conf add_ssid "Free WiFi"
pineap-ng /tmp/pineap.conf list_ssids
pineap-ng /tmp/pineap.conf run_scan 30 2
pineap-ng /tmp/pineap.conf deauth aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb 06 3
pineap-ng /tmp/pineap.conf ap_channel 11
pineap-ng /tmp/pineap.conf karma on
pineap-ng list_probes aa:bb:cc:dd:ee:ff
```

## Kali adaptations (configurable)

Defaults match the original for drop-in compatibility. Override without code
changes:

- **Socket path** — `-DPINEAP_SOCK_PATH=...` at build time, or `$PINEAP_SOCK`
  at run time (default `/var/run/pineapd.sock`).
- **hostapd AP interface** — `-DPINEAP_HOSTAPD_IFACE=...` at build time, or
  `$PINEAP_IFACE` at run time (default `wlan0`). Set this to the G82's real AP
  interface so `karma`/`ssid_filter`/`mac_filter` drive the right radio.

```
make EXTRA_DEFS='-DPINEAP_SOCK_PATH=\"/opt/pineapple/run/pineapd.sock\" \
                 -DPINEAP_HOSTAPD_IFACE=\"wlan0\"'
# or, no rebuild:
PINEAP_SOCK=/opt/pineapple/run/pineapd.sock PINEAP_IFACE=wlan0 pineap-ng ...
```

## Build

No external libraries (pure POSIX).

```
make                 # -> pineap-ng (native aarch64)
make bytes           # byte-for-byte oracle validation (needs /tmp/opencode/proto-re)
make test            # end-to-end against pineapd-ng
```

## Files

- `pineap.h`   — protocol constants, command/opt enums, builder prototypes.
- `proto.c`    — pure request builders + value maps (oracle-testable, no I/O).
- `comms.c`    — UNIX socket client (connect / send 4097 / stream response).
- `main.c`     — argv parsing, dispatch, help/version, hostapd_cli side effects.
- `validate_bytes.sh` — diff each built request vs `recv_*.bin` oracle.
- `run_e2e.sh` — start pineapd-ng, drive it with the real pineap-ng, check stdout.

A hidden `--dump-request` front flag makes pineap-ng write the 4097-byte request
to stdout instead of sending it (used by the validators).

## Validation status

- **Compiles clean** with `-Wall -Wextra` as native aarch64
  (`ELF 64-bit LSB, ARM aarch64`).
- **Byte-for-byte: 33/33** commands match the original `recv_*.bin` oracle,
  including the ap_channel quirk and the compact inject layout.
- **Live cross-check: 10/10** — the original `pineap` under qemu-mips and
  pineap-ng produce byte-identical 4097-byte requests for a representative
  battery (get_status, add_ssid, run_scan, deauth, ap_channel, beacon_interval,
  set_source, inject, list_probes, karma).
- **End-to-end: 13/13** against pineapd-ng — `get_status` JSON is byte-exact vs
  the original daemon's capture; `add_ssid`/`list_ssids`/`clear_ssids`,
  `capture_ssids`, `ap_channel` (→ 184549376), `beacon_interval`, `set_source`,
  `list_probes`, and legacy-syntax invocation all behave correctly.

## Honesty: what needs real hardware

Everything above is validated on the socket/protocol layer and against both the
original binary (qemu) and pineapd-ng. The parts that **cannot** be validated
without the Pineapple radios on the G82:

- `karma` / `ssid_filter` / `mac_filter` call `hostapd_cli -i <iface>`. Without
  a running hostapd (karma-patched) and the real AP interface, these are no-ops
  (the original behaves identically — in qemu they print
  `hostapd_cli: not found`). The socket half of `karma` is validated; the
  hostapd half requires the actual AP stack.
- `run_scan`, `deauth`, `inject`, `handshake_capture_*` deliver correct requests
  to the daemon, but the actual RF effect (scanning, frame injection, handshake
  capture) depends on pineapd-ng's monitor-mode interface and a capable NIC. The
  CLI's responsibility — build and send the exact request — is fully verified.
- stdout for `list_probes` / recon data is only as populated as the daemon's DBs;
  with no captured traffic these are correctly empty.
