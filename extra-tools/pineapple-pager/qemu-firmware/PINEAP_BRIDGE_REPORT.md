# PineAP bridge: firmware (qemu MIPS) -> native pineapd-pager (aarch64)

Target: Moto G82 (Kali NetHunter aarch64). Firmware `rootfs/pineapple/pineapple`
(Go, MIPSel, stripped) runs under patched `qemu-mipsel`. Goal: make the real
firmware's PineAP use our NATIVE `pineapd-pager` for WiFi instead of its own MIPS
`pineapd` (which can't touch real radios under qemu).

## TL;DR - what works now

- Setup wizard gate PASSED: firmware log now prints **`[MAIN] Starting PineAP`**
  (previously stuck on `[MAIN] Deferring PineAP start until setup complete`).
- Firmware CONNECTS to our native daemon: **0** `could not connect to pineap`,
  **0** `[PineAp] Error writing`.
- Protocol handshake is LIVE in both directions. Verified exchanges:
  - `CMD_GPSD_REPORT` (10): FW -> daemon -> `{lat,lon,alt,fix,spd,pps,...,message}`.
  - status/count poll (208): FW `{window}` -> daemon `{aps,devs,total,...,message:"ok"}`.
  - (314, `{time}`) -> 51-byte msgpack reply.
  - Manual `CMD_LIST_INTERFACES` (100) through the bridge returns:
    `{'interfaces':[{'ifname':'wlan0','type':'managed','enable':True,'uplink':True},
     {'ifname':'wlan1mon','type':'absent',...}], 'error':0, 'message':'ok'}`
- The MIPS `pineapd` is neutralised so it can't compete for the socket.

## 1. Completing / skipping the setup wizard

The server defers PineAP until setup is complete. Reverse engineering the Go
binary (strings + pclntab symbols) showed:

- `models.(*DevicePrefs).GetSetupComplete` / `SetSetupComplete`, with struct tag
  **`json:"setup_complete,omitempty" uci:"setup_complete,omitempty"`**.
- `utils.CheckSetupRequired`, `utils.SetSetupCompleteConfig`,
  `pineapplepager.(*SetupWizard).MarkCompleteAndClose`.
- EULA pref: `GetEulaAccepted`, tag `json:"eula_accepted" uci:"eula,omitempty"`.
- `DevicePrefs` are persisted in the **`config pager`** section of
  `/etc/config/system` (same section as the ringtones/brightness it already reads).

Fix (reversible, done in the rootfs so it survives reboots):
added to the `config pager` section of `rootfs/etc/config/system`:

    option setup_complete '1'
    option eula '1'

Result: the server transitioned from `Deferring PineAP start until setup complete`
to `Starting PineAP` / `Starting PineAP related services`.
(Backup: `rootfs/etc/config/system.bak-presetup`.)

Root password: a known SHA-512 hash was already injected in `rootfs/etc/shadow`
(pw `pineapplepager`) by the earlier Virtual Pager work; `CheckSetupRequired` is
satisfied by setup_complete + a set root password.

## 2. Bridging the socket

### 2a. KEY qemu-user fact (how the path resolves)
qemu-user does **NOT** prepend the `-L <rootfs>` prefix to AF_UNIX socket paths.
Proof: the firmware's own trusted API socket appears at **host** `/tmp/api.sock`
(not `rootfs/tmp/api.sock`). Therefore the firmware's `connect("/tmp/pineap_sock")`
hits **host `/tmp/pineap_sock`**. (Symlinks were placed at both host `/tmp` and
`rootfs/tmp` to be safe; the host one is the one that matters.)

### 2b. The protocol and the real problem (endianness)
Framing (both sides): an 8-byte header `[u32 cmd][u32 body_len]` followed by
`body_len` bytes of msgpack (`github.com/vmihailenco/msgpack/v5` on the firmware;
`msg.c`/`msg_read_frame` on the native daemon).

A raw sniff of the wire (see `sock_sniff.py`) showed the firmware writes the two
header words **little-endian** (it's MIPSel), e.g. cmd=314 len=15 =
`3a 01 00 00  0f 00 00 00` + 15 bytes msgpack. The native daemon's
`msg_read_frame` does `rev` on both words, i.e. it reads/writes the header
**big-endian** (confirmed by disassembly and by its reply header
`.. 00 00 00 2a` = BE 42). So the daemon mis-read the firmware's length as
`0x11000000` (285212672) -> `frame body too large`, and the firmware couldn't
parse the daemon's BE reply -> `[PineAp] Error writing`.

The msgpack body is endian-neutral; only the 8-byte header needed swapping.

### 2c. Chosen method: an endianness-translating relay
`pineap_bridge.py` listens on `/tmp/pineap_sock` and forwards to the native
`/opt/pineapple-pager/run/pineap_sock`, byte-swapping the two header words in
each direction (FW little-endian <-> daemon big-endian), passing the msgpack body
through untouched. Run as a systemd service `pineap-bridge.service` (enabled).

(Options not chosen: a plain symlink/bind-mount would connect but would NOT fix
the endianness, so the firmware and daemon still couldn't understand each other.
Recompiling either binary was unnecessary.)

### 2d. Disabling the MIPS pineapd (no competition)
Under qemu there's no procd/ubus, so the server's `/sbin/service pineapd start`
path can't really spawn pineapd; still, to be safe the rootfs MIPS pineapd is
neutralised so it can never bind `/tmp/pineap_sock`:
- `rootfs/etc/init.d/pineapd` -> stub (start/stop = success no-op).
- `rootfs/usr/sbin/pineapd` -> `#!/bin/sh; exit 0` stub.
(Backups: `pineapd.orig` next to each.)

## 3. Validation (channel firmware <-> native pineapd is ALIVE)

Firmware log (`/home/kali/pager-qemu/fw_boot.log`):
- `Starting PineAP` present; `Deferring PineAP` = 0.
- `could not connect to pineap` = 0; `[PineAp] Error writing` = 0.

Bridge log (`/tmp/pineap_bridge.log`) in steady state shows repeated, valid,
decoded request/response pairs (cmd 10, 208, 314) each ending in a clean reply
with `message:"ok"` / GPS struct. Independent probe (`probe_cmd.py 100`) returns
a decoded interface list from the daemon.

## 4. What is NOT / cannot be tested without the Archer (honest)

- The TP-Link Archer (wlan1mon) is **not connected** (its USB port carries the
  SSH cable). The native daemon reports `wlan1mon type=absent` / `monitor=False`,
  which is correct. So deauth / real off-channel scan / AP injection CANNOT be
  exercised here. That is expected and must be tested by the user later with the
  Archer attached.
- The control channel itself (firmware <-> native pineapd, framing + msgpack in
  both directions) IS verified working; the WiFi-specific commands will flow over
  this same, now-working channel once the Archer is present.
- Unrelated noise in the firmware log: `[POWER] Unable to open power monitor`
  (missing `/sys/class/power_supply/bq27546-0` in the emulated sysfs) - pre-existing,
  harmless to the socket channel.

## 5. Files (all on the G82)

Scripts in `/home/kali/pager-qemu/qemu-firmware/scripts/`:
- `pineap_bridge.py`  - the endianness-translating AF_UNIX relay (the bridge).
- `probe_cmd.py`      - send one command through the bridge and decode the reply
                        (e.g. `probe_cmd.py 100` = CMD_LIST_INTERFACES).
- `sock_sniff.py`     - passive byte-dumping relay used during RE to capture the
                        raw wire header (how the endianness bug was found).
- `fw_start.sh`       - start the firmware under qemu; now also ensures
                        pineapd-pager.service + pineap-bridge.service are up.

systemd (both enabled):
- `/etc/systemd/system/pineap-bridge.service`           - runs the bridge.
- `/etc/systemd/system/pineapd-pager.service` (+ `.d/10-debug.conf` adds --debug).

Rootfs changes (with backups):
- `rootfs/etc/config/system` : `config pager` += `setup_complete '1'`, `eula '1'`
  (backup `system.bak-presetup`).
- `rootfs/etc/init.d/pineapd` : stub (backup `pineapd.orig`).
- `rootfs/usr/sbin/pineapd`   : stub (backup `pineapd.orig`).

Bridge log: `/tmp/pineap_bridge.log`. Native daemon log: `journalctl -u pineapd-pager`.

## 6. How to reproduce from scratch

    sudo systemctl start pineapd-pager.service     # native WiFi daemon (listens)
    sudo systemctl start pineap-bridge.service     # the endianness bridge
    sudo /home/kali/pager-qemu/qemu-firmware/scripts/fw_start.sh   # firmware
    # verify:
    grep -E "Starting PineAP|could not connect|Error writing" /home/kali/pager-qemu/fw_boot.log
    sudo tail /tmp/pineap_bridge.log
    sudo python3 .../scripts/probe_cmd.py 100      # CMD_LIST_INTERFACES round-trip
