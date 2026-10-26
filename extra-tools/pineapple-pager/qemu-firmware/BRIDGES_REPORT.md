# Pineapple Pager (qemu firmware) — hardware bridges + viewer fix

Work done over SSH on the Motorola G82 (Kali NetHunter aarch64). The REAL firmware
runs under `qemu-mipsel -L /opt/pineapple-pager/qemu/rootfs` (service
`pager-qemu-firmware`, API/Virtual Pager on :1471, viewer on :8471). The firmware
writes to FAKE sysfs/dev nodes that `prepare.sh` created in its rootfs; these had
to be bridged to the G82's real hardware.

Rules respected: never touched red/wlan/airmon; GPS is WiFi-only (modem never
touched); all long commands had timeouts.

## Summary

| Task | Status | Result |
|------|--------|--------|
| 1. Vibrator bridge | **WORKING** | firmware vibrate -> real G82 vibrator |
| 2. GPS bridge | **WORKING** | firmware gets real WiFi fix via gpsd (no modem) |
| 3. Bluetooth | **PARTIAL / documented** | real hci0 exists; host helper works; guest wiring blocked by qemu-user; toggle fails gracefully (no crash) |
| 4. Viewer 502 fix | **WORKING** | proxy + client retry; recovers from transient cuts |

---

## TASK 1 — VIBRATOR  (WORKING)

**Real HW:** the G82 vibrator is the kernel `gpio-vibrator` driver, exposed as a
**force-feedback evdev device** (NOT a `/sys` LED): `/dev/input/event3`
(`/sys/devices/platform/vibrator/input/input3/event3`, advertises `FF_RUMBLE`,
`capabilities/ff = 107030000`). There is no `brightness`/`enable` file; you drive
it with the evdev FF protocol (`EVIOCSFF` upload + `EV_FF` play).

**Fake node:** firmware writes `1`/`0` to
`ROOTFS/sys/class/gpio/vibrator/value` (endpoint `/pager/vibrate/play`,
"SetVibrate", "Enabling vibration mode").

**Bridge:** `vibrator_bridge.py` watches the fake value file (raw inotify via
ctypes, polling fallback) and, on `->nonzero`, uploads+plays an `FF_RUMBLE`
effect on the real evdev node (pure stdlib, no python-evdev); on `->0` it stops.
Has a safety auto-stop (`PAGER_VIB_MAXMS`, default 8s) for a stuck "1".
Device auto-detected by name "gpio-vibrator".

**Service:** `pager-qemu-vibrator.service` (runs as root — the FF node is
`root:input` and the only non-root access is a login-session uaccess ACL a system
service does not get). Part of `pager-qemu.target`.

**Verified:** `echo 1 > .../vibrator/value` -> bridge logs `ON (value=1)` ->
the real FF device is driven; `echo 0` -> `OFF`. (Confirmed the G82 physically
vibrates on the direct FF test.)

Files: `scripts/vibrator_bridge.py`, `systemd/pager-qemu-vibrator.service`.

---

## TASK 2 — GPS  (WORKING)

**It was already a bridge, but misconfigured.** The native `pineapd-pager`
(aarch64) is configured in `/opt/pineapple-pager/etc/pineapd.conf` with
`gpsd_host=127.0.0.1 gpsd_port=2947` and connects to the real gpsd (verified by
strace: `connect(... sin_port=2947 ...)=0`). gpsd is fed by `rhodep-gnss`. So the
firmware gets GPS via: `rhodep-gnss -> gpsd:2947 -> pineapd-pager -> pineap-bridge
-> firmware`. No per-firmware change was needed on that path.

**The actual bug:** `rhodep-gnss` ran with `--source auto`, which selected
`qmi-cellid` mode and emitted **void NMEA** (`$GPGGA,...,,,,,0,...`,
`$GPRMC,...,V,...`), so gpsd reported `mode:1` (no fix) even though the daemon's
own log showed a good WiFi fix. `auto`/`cellid` also touches the modem.

**Fix (WiFi-only, modem untouched):** systemd drop-in
`/etc/systemd/system/rhodep-gnss.service.d/20-wifi-only.conf` overrides
ExecStart to `--source wifi`. The daemon then logs *"source: wifi (the modem is
not touched)"* and emits valid NMEA with the fix.

**Verified:** gpsd now reports `mode:2` with real `lat/lon` (e.g.
`-32.9542,-60.6442`) — the WiFi-derived location. Fully within "GPS only WiFi".

File: `systemd/rhodep-gnss.service.d/20-wifi-only.conf` (drop-in; see below to
copy it into the repo).

---

## TASK 3 — BLUETOOTH  (PARTIAL, honest)

**Real HW:** `hci0` exists (real controller, `BD Address 02:00:60:3B:D3:94`,
Bus UART). `bluetoothd` is active. By default it is DOWN + rfkill soft-blocked.
The host CAN control it: `rfkill unblock bluetooth; hciconfig hci0 up` ->
`UP RUNNING` (verified).

**How the firmware does BT:** Go `exec.Command("hciconfig", "hci0", "up"|"down")`
(strings: "SetBluetoothEnabled", "hciconfig %s failed", "Error setting bluetooth
status"). The rootfs `hciconfig` is MIPS.

**Why it can't reach real HW directly:** `hciconfig` opens an `AF_BLUETOOTH`/HCI
socket and uses HCI ioctls (`HCIGETDEVLIST`, `HCIDEVUP`). **qemu-user does not
emulate those HCI ioctls for a non-native guest** — the socket() succeeds but the
ioctl returns ENOTTY, so the MIPS hciconfig prints *"Can't get device list: Not a
tty"* and never sees hci0. (Verified.)

**What was built (works):** a native host helper `bt_bridge.py`
(`pager-qemu-bt.service`, TCP 127.0.0.1:1473) that translates the firmware's
intent to the REAL tools: `hciconfig hci0 up` -> `rfkill unblock bluetooth` +
`hciconfig hci0 up` (with a retry loop for the RF-kill(132) race), `down` ->
`hciconfig hci0 down`, status -> real `hciconfig`. **Verified driving real hci0
UP/DOWN end-to-end** (via direct socket and via the MIPS `nc` under qemu).

**What blocks completion:** the front-end that the firmware would exec
(`hciconfig.shim`, a `#!/bin/sh` script that forwards argv to the helper over the
MIPS `nc`) **cannot be wired in**: **qemu-user cannot exec a shebang script from
inside the guest** — a guest `execve()` of a `#!` file returns ENOENT ("not
found"), independent of interpreter or `QEMU_LD_PREFIX`. (Verified with trivial
scripts.) The fix would be a *native MIPS ELF* front-end, but **there is no MIPS
cross-compiler on the device and no compiler in the rootfs**, so one can't be
built here.

**Current state (no crash):** the original MIPS `hciconfig` is kept in the rootfs
(`hciconfig.real` is the backup; `prepare.sh` restores it if a prior shim was
installed). The firmware's BT toggle therefore fails **gracefully** (ENOTTY ->
"Error setting bluetooth status", a handled error path — not a panic; firmware
stays healthy, login 200 after the call).

**To finish later:** build a tiny static MIPS ELF that connects to
127.0.0.1:1473 and speaks the one-line `ARGV\t...` protocol (replace the shell
shim), install it at `rootfs/usr/bin/hciconfig`, then
`systemctl enable --now pager-qemu-bt.service` and add it back to
`pager-qemu.target`'s `Wants`. The helper + protocol are ready and tested.

Files kept for that: `scripts/bt_bridge.py`, `scripts/hciconfig.shim`
(reference), `systemd/pager-qemu-bt.service` (installed, NOT auto-started).

---

## TASK 4 — VIEWER 502 / RECONNECT  (WORKING)

**Cause:** `viewer/proxy.py` `forward_http()` had no retry — any transient
firmware hiccup (busy / connection reset during an internal restart / token just
expired) raised, and the handler returned a bare **502**. The WS firmware-connect
also returned 502 on the first failure. `app.js` `login()` threw on any non-ok
(so a 502 showed as "login HTTP 502") and `scheduleReconnect()` gave up after one
failed try ("reconexion fallo: ...").

**Fix — proxy.py:**
- `forward_http()` now retries transient failures (ConnectionError, timeouts,
  RemoteDisconnected/BadStatusLine, OSError) **and** retriable upstream 5xx
  (502/503/504 for GET/HEAD and `/api/login`) with exponential backoff
  (0.3s..cap 2.5s, 5 attempts, ~5s budget). It **always returns a tuple** now
  (never raises), so the handler's generic 502 is no longer hit. Only after the
  budget is exhausted does it return a legible 502.
- The WS upstream connect retries with the same backoff before giving up.
- All tunables via env (`PAGER_PROXY_HTTP_RETRIES`, `_BACKOFF`, `_BACKOFF_CAP`,
  `_TIMEOUT`).

**Fix — app.js:**
- `login()` retries on network error + 502/503/504 with exponential backoff
  (4 attempts); 401/403 (bad creds) still fail fast.
- `scheduleReconnect()` now retries **indefinitely** with exponential backoff
  (cap 10s) instead of surfacing the error; it shows an informative
  "reconectando…" overlay and resets the backoff once a stream reconnects.

**Fix — unit:** `pager-qemu-viewer.service` changed `Requires=` ->
`Wants=pager-qemu-firmware.service` so the proxy **survives** a firmware restart
(with Requires, a firmware restart tore the viewer down too, dropping the
browser's in-flight request). `PartOf=pager-qemu.target` keeps target lifecycle.

**Verified:**
- Normal: login via proxy 200, static 200, /api forwarded.
- Transient (firmware SIGSTOP 1.5s mid-login): proxy waited and returned the
  firmware's real response (**200**), **not 502**. Viewer stayed active.
- Full firmware kill+restart: viewer survives; proxy rides ~5s; longer outages
  are covered by the client retry loop (re-login within ~1s of firmware return).

Files: `viewer/proxy.py`, `viewer/app.js`, `systemd/pager-qemu-viewer.service`.

---

## FILES (repo: extra-tools/pineapple-pager/qemu-firmware/)

NEW:
- `scripts/vibrator_bridge.py`           (task 1)
- `systemd/pager-qemu-vibrator.service`  (task 1)
- `scripts/bt_bridge.py`                 (task 3, works; not auto-wired)
- `scripts/hciconfig.shim`               (task 3, reference front-end)
- `systemd/pager-qemu-bt.service`        (task 3, installed, not auto-started)
- `systemd/rhodep-gnss.service.d/20-wifi-only.conf`  (task 2 drop-in — copy from
  /etc/systemd/system/rhodep-gnss.service.d/20-wifi-only.conf)

CHANGED:
- `scripts/prepare.sh`                    (+bt note / restore real hciconfig,
                                           vibrator node comment)
- `systemd/pager-qemu.target`            (+vibrator in Wants; bt intentionally out)
- `install-qemu.sh`                       (install/uninstall vibrator + bt units)
- `viewer/proxy.py`                       (retry/backoff; never-raise forward)
- `viewer/app.js`                         (login + reconnect retry/backoff)
- `systemd/pager-qemu-viewer.service`    (Requires -> Wants)

Deployed live under /opt/pineapple-pager/qemu/{scripts,viewer} and
/etc/systemd/system; the GPS drop-in is under /etc/systemd/system.
