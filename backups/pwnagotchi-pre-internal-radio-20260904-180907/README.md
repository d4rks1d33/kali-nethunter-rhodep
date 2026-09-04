# pwnagotchi backup — pre "internal-radio" investigation

Snapshot taken **before** any change aimed at making pwnagotchi work with the
internal WCN3990 (`wlan0`) instead of / in addition to the external TP-Link
(`wlan1mon`). Kept here so the working setup can be restored at any time.

The blobs are git-ignored; only this note is tracked.

## Why the current setup is external-only

The port's monstart / launcher explicitly refuse to touch `wlan0` and hardcode
the external TP-Link as `wlan1mon`. Comment in
`bin/rhodep-pwn-monstart` at the time of this backup:

> the internal wlan0 (ath10k / WCN3990) is the only real WiFi and cannot do
> monitor mode at all -- its firmware reports raw 0 -- so it must keep running
> as a managed client.

That claim was **true when it was written** and is **only partially true now**:

- passive management-frame capture on `wlan0` DOES work (patch 0117),
- effective deauth injection on `wlan0` DOES work over the STA offchannel path
  (patch 0118 + `rhodep-inject-lab`), verified OTA,
- but raw monitor-mode TX / arbitrary frame injection on `wlan0` still crashes
  the firmware (documented in README item 13).

So a "wlan0 pwnagotchi" would need to avoid the classic monitor+radiotap
injection that bettercap uses under the hood and route deauths through the
offchannel path — that is the point of the investigation this backup precedes.

## What's here

- `repo/` — the exact `extra-tools/pwnagotchi/` tree from the repo at the time
  of the backup (scripts + systemd units + config + install.sh).
- `device-etc/pwn-etc.tar.gz` — `/etc/pwnagotchi/` from the device (config,
  fingerprint, keys, plugins dir, backups dir, default.toml). `handshakes/` and
  `log/` are excluded to keep the tarball small.
- `device-etc/pwn-scripts.tar.gz` — the launchers actually installed in
  `/usr/local/sbin/rhodep-pwn-*` and the three systemd units in
  `/etc/systemd/system/rhodep-pwn*.service` + `rhodep-pwnagotchi.service`.

## Restore

```sh
# on the device:
sudo tar -C / -xzf pwn-etc.tar.gz
sudo tar -C / -xzf pwn-scripts.tar.gz
sudo systemctl daemon-reload
# then re-enable whichever units were on:
sudo systemctl enable --now rhodep-pwnagotchi.service
sudo systemctl enable --now rhodep-pwn-bettercap.service
```

Verify the config kept `iface = "wlan1mon"` and `mon_start_cmd` /
`mon_stop_cmd` point at the wlan1-only rhodep-pwn-monstart / monstop.
