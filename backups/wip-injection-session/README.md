# WIP: injection investigation session (NOT merged, kept so it isn't lost)

These are the **experimental** ath10k sources/module from the Wi-Fi injection
session (README item 13). They are **on top of** patch 0117 (the shipped mgmt
capture). They are saved here because the module currently running on the device
is this WIP build, not the clean 0117 — so next session knows exactly what's
loaded. The blobs are git-ignored; only this note is tracked.

## What's in the WIP vs the clean patch 0117

Patch 0117 (in `kernel/patches/`) is the **good, shipped** change: clear
`RX_FLAG_SKIP_MONITOR` for WMI-delivered mgmt frames so monitor capture works.
The WIP adds several **injection experiments that did NOT pan out** and some
debug logging. All are gated behind the existing `mon_mgmt` module param.

In `wmi.c`:
- `mgmt-tx-compl` diagnostic: logs the firmware's mgmt-tx completion `status`
  (this is how we learned every injected frame completes `status=1` = discard).

In `mac.c`:
- `ath10k_mac_tx_h_get_txmode`: for a monitor vif, route **mgmt** frames to
  `ATH10K_HW_TXRX_MGMT` (WMI mgmt-tx) instead of `ATH10K_HW_TXRX_RAW` (HTT).
  This **stops the firmware crash** (real improvement) but frames still don't
  radiate.
- `ath10k_mon_inject_peer_add` / `_del` + hook in `ath10k_mgmt_over_wmi_tx_work`:
  create a temporary peer for the frame's destination on the monitor vdev before
  mgmt-tx, delete after. The peer **is** created but frames still complete
  `status=1` and do not radiate.
- Debug logging in `ath10k_vdev_start_restart`, the beacon-template send, and AP
  `vdev_up` — used to prove the AP path is fully OK on the driver side
  (`vdev-start freq=2462 ret=0`, `bcn-tmpl ret=0`, `vdev-up ret=0`) yet nothing
  beacons OTA.

## Files here

- `mac.c.wip`, `wmi.c.wip` — the modified sources (7.2-rc5 tree,
  `drivers/net/wireless/ath/ath10k/`).
- `ath10k_core.ko.wip` — the built module (md5 `fc0b302f15a30b2ef1b0e675859f6b31`),
  which is the one currently installed at
  `/lib/modules/7.2.0-rc5/kernel/drivers/net/wireless/ath/ath10k/ath10k_core.ko`
  on the device.
- `*.rhodep-lines.txt` — line numbers of the `rhodep`-tagged blocks for quick
  navigation.

## Next session

Two clean options:
1. **Revert to just 0117** (drop the injection experiments): the capture feature
   is unaffected; injection stays "external adapter only".
2. **Keep the mgmt-tx reroute** (it prevents the firmware crash when someone
   tries to inject from the internal radio) but drop the temp-peer + debug noise.

The real path to internal injection is firmware RE — see item 13 and
`backups/firmware/`. The build recipe that produced this .ko:
`make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- M=drivers/net/wireless/ath/ath10k modules`
against the patched `/tmp/ktree/linux-7.2-rc5` tree, then copy the .ko to the
device module path + `depmod -a 7.2.0-rc5` + reboot.
