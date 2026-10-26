# Open AP — what the Pager firmware REALLY does (captured) + the native bridge

Goal: when the user enables **PineAP → Open AP** on the real Pager firmware
(Go/MIPS under qemu-user on the G82), raise a **real** AP on the phone's radio,
faithfully mirroring the firmware's intent, with native host hostapd/iw.

## 1. The firmware's exact sequence on the Open AP toggle (EVIDENCE)

Captured two ways while toggling Open AP via the Virtual Pager websocket
(`ws://127.0.0.1:1471/api/pager/input/keys.ws`, key `Enter` on the Open AP row).

### (a) strace of the qemu firmware (execve/connect/open/openat/socket)

On each toggle the firmware ONLY does UCI file I/O — **no** execve of
`wifi`/`hostapd`/`wpad`/`ubus`/`netifd`, **no** connect to the pineap socket:

```
148490 openat(".../rootfs/etc/config/pineapd", O_RDONLY)          = 28   # uci load
148490 openat(".../rootfs/etc/config/pineapd", O_RDONLY)          = 28   # uci reload
148898 openat("/etc/config/.3024070230.pineapd",
              O_RDWR|O_CREAT|O_EXCL, 0600)   = -1 ENOENT (No such file or directory)
```

Key fact: the UCI atomic write targets the **absolute host path** `/etc/config/`
(qemu's `-L rootfs` only rewrites the ELF loader path, NOT runtime opens). On a
Kali host there is no `/etc/config`, so the write fails with **ENOENT** and the
enable is silently lost → nothing ever reaches a real radio.

### (b) UCI diff (after making the write succeed)

We pointed `/etc/config` at the firmware rootfs config dir
(`ln -s .../rootfs/etc/config /etc/config`) so the firmware's UCI writes land
somewhere real, then diffed `/etc/config/wireless` across a toggle:

```
OFF -> ON   (wifi-iface 'wlan0open'):
-   option disabled '1'
+   option disabled '0'
                   (+ option hidden '0')
ON  -> OFF:
-   option disabled '0'
+   option disabled '1'
```

So the **entire** firmware intent for Open AP is: flip
`wifi-iface 'wlan0open'` `disabled 0/1` in `/etc/config/wireless` (and set
`hidden`), then rely on an OpenWRT `wifi reload`/netifd to bring it up. That
reload does not exist under qemu-user.

### The Open AP parameters the firmware persists (verbatim UCI)

```
config wifi-device 'radio0'          # the Pager's internal 2.4GHz radio
    option band '2g'
    option channel '1'
    option htmode 'HT20'

config wifi-iface 'wlan0open'        # <-- the Open AP VAP
    option device 'radio0'
    option ifname 'wlan0open'
    option mode 'ap'
    option ssid 'pager-open'         # == $_OPEN_AP_SSID (editable in UI)
    option disabled '0'              # 0 = Open AP ON, 1 = OFF
    option hidden '0'
```

Sibling AP VAPs on the same radio (same mechanism): `wlan0wpa` (Evil WPA, has
`encryption`/`key`) and `wlan0mgmt` (Management AP). The socket handlers
`hostap 901–905` and `set_interface_type 105/106` are **not** used for Open AP.

## 2. Hardware mapping (faithful, non-destructive)

- Firmware `radio0` = internal **2.4GHz** radio.
- G82 `wlan0` (ath10k) is the **SSH uplink STA on 5GHz ch60** — must not be
  disturbed (chip is `#channels<=1`; an AP on ch1 would drop the STA and kill
  SSH). usb0 lifeline is currently down, so wlan0 is off-limits.
- G82 `wlan1` (RTL8821AU) is **free, AP-capable on 2.4GHz ch1–11** → the faithful
  target for the firmware's 2.4GHz Open AP.

We therefore mirror the firmware's `wlan0open` onto **wlan1**. Overridable via
`OPENAP_TARGET_IFACE`. The bridge refuses to build the AP on the uplink/SSH
radio.

## 3. The native bridge (what we implemented)

`src/pineapd/openap.c` (+ `openap.h`), wired into `main.c`:

- A background watcher polls `OPENAP_WIRELESS_PATH`
  (`.../rootfs/etc/config/wireless`, the file the firmware writes).
- On change it parses `radio0` + the AP VAPs, resolves the first ENABLED VAP
  (priority Evil WPA > Open AP > Mgmt AP), and reconciles:
  - enabled  → generate `run/hostapd-openap.conf` (open, or WPA2/SAE if the VAP
    has `encryption`+`key`) with the firmware's ssid/channel/hidden, and start
    **native `/usr/sbin/hostapd`** on wlan1 (PID tracked).
  - disabled → SIGTERM/▶SIGKILL hostapd, return wlan1 to managed.
- On daemon shutdown (`openap_shutdown()` from `main.c`) it tears everything
  down, so stopping `pager-qemu.target` frees the radio + battery.

`qemu-firmware/scripts/fw_run.sh` creates the `/etc/config → rootfs/etc/config`
symlink on every firmware start (idempotent, won't clobber a real dir), so the
firmware's UCI write SUCCEEDS and the watcher sees the toggle.

## 4. Validation (measured on the G82)

- Toggle ON (via Virtual Pager): `wlan0open disabled 0` →
  `iw dev wlan1` = `type AP, ssid pager-open, channel 1 (2412 MHz)`,
  `pgrep hostapd` present.
- **Over-the-air** scan from wlan0 (non-destructive, STA stays associated):
  `iw dev wlan0 scan` shows `SSID: pager-open` on freq 2412 → real beacon.
- Toggle OFF: hostapd killed, wlan1 back to `type managed`.
- `systemctl stop pager-qemu.target`: hostapd killed, wlan1 managed (radio freed).
- SSH (192.168.1.53 via wlan0 / "WiFi Mateo 5G") never dropped.

## 5. Honest limitations

- Verified visible from the G82's own wlan0 scan; the user's RZR phone test is
  theirs to confirm.
- Mapped to **wlan1** (not wlan0) because wlan0 carries SSH. This matches the
  firmware's *functional* intent (an open 2.4GHz AP named pager-open) on a real
  radio; it is not literally the same netdev name. If the firmware is ever
  required to use wlan0 specifically, that needs the usb0 lifeline up first and
  a user decision (documented, SSH-preserving).
- Open AP SSID/BSSID edits made in the UI are read live from UCI on the next
  reconcile. Connected-client reporting (hostap 904/905 / GetPineAPClients) is
  not yet wired to `iw station dump`; AP visibility/connectivity was prioritized.
