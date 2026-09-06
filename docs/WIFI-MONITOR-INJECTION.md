# Wi-Fi monitor + radiotap injection on rhodep (WCN3990 / ath10k_snoc)

**Status: functional end-to-end for the common aircrack-ng / mdk4 /
scapy / hcxdumptool workflows. Not yet 100% burst-reliable — see
"Known rough edges" at the bottom.**

## What you can do today

After `sudo airmon-ng start wlan0`:

- `aireplay-ng -9 wlan0mon` — official aircrack-ng injection test
  reports **"Injection is working!"**.
- `aireplay-ng -D --deauth 0 -a <BSSID> [-c <CLIENT>] wlan0mon` —
  deauth attack radiates OTA, clients drop.
- `mdk4 wlan0mon b -c 11` — beacon flood.
- `scapy sendp(pkt, iface="wlan0mon", ...)` — raw mgmt frame injection.
- `airodump-ng --channel N --bssid <BSSID> -w cap wlan0mon` — capture.

All of the above happen through the standard `PF_PACKET + SOCK_RAW +
write(radiotap+frame)` pattern that every Wi-Fi pentesting tool on
Linux uses. **No per-tool modification needed.**

## Why this was hard

WCN3990's firmware (WLAN.HL.3.3.2-00472, Qualcomm-signed DSP image
`wlanmdsp.mbn` loaded by the modem PIL) silently discards any
management frame submitted from a MONITOR-typed vdev. The check is
inside `_wlan_mgmt_tx_send` in the firmware — an opmode allow-list
that only accepts STA, AP, IBSS, or NDI. Monitor vdev frames hit the
allow-list, get status=1 (DISCARD) in the WMI mgmt-tx completion
event, and never radiate.

Prior state of the art:
- **Every "monitor mode on Snapdragon" tutorial** on XDA / GitHub /
  reddit for the last 5 years is a 1-line qcacld-3.0 defconfig flip
  (`CONFIG_FEATURE_MONITOR_MODE_SUPPORT := y`). That enables RX
  monitor. **Nobody had solved TX injection on `ath10k` mainline for
  WCN3990.**
- The ath10k mailing list explicitly confirmed this in Aug 2024:
  Kalle Valo (ath10k maintainer): *"I doubt that WCN3990 firmware
  supports monitor mode"* —
  [lists.infradead.org/pipermail/ath10k/2024-August/016001.html](https://lists.infradead.org/pipermail/ath10k/2024-August/016001.html)

## The trick (Loukious's discovery, ported to ath10k mainline)

**Wael Hasnaoui ("Loukious")** in Tunisia
([medium.com/@loukious](https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702))
reverse-engineered the WCN3998 firmware (same fw family as WCN3990)
in Ghidra in March 2026, identified the opmode gate, and found the
workaround:

1. **Create a hidden STA-type vdev** in the firmware via raw WMI
   commands. Never register it with mac80211 / cfg80211 — it exists
   only inside the fw and has no netdev.
2. **Create a self-peer** on that hidden STA vdev (satisfies the fw's
   `vdev+0xc` peer-required check).
3. **Skip `WMI_VDEV_UP`** — the fw asserts if you try to bring a STA
   vdev "up" without an actual BSS peer to associate with.
4. **Rewrite the `vdev_id`** in outgoing mgmt-tx commands from the
   monitor vdev's id to the hidden STA vdev's id. The fw sees a valid
   STA opmode and radiates. Frame body (addr1/2/3) stays as the user
   built it, so spoofed SA / BSSID work.
5. For unicast frames: create a per-destination temp peer on the
   hidden STA vdev (fw wal_tx needs a peer entry for addr1 to build
   the tx context).

Loukious published his patch for qcacld-3.0 on Xiaomi SDM855 kernels
in March 2026:
- Article: <https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702>
- v1 (WCN3998 / SDM855): <https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1>
- v7 (WCN7750 / SM8635, Aug 2026): <https://github.com/Loukious/vendor_qcom_opensource_wlan/tree/onyx-v-oss-monitor-direct>

Downstream port to another WCN3990 device (Redmi Note 8 / ginkgo) via
qcacld-3.0 by **ikteach (Ikram, India)**:
- <https://xdaforums.com/t/kernel-ginkgo-nethunter-ikteach-kernel-lineageos-23-2-android-16-wifi-qcacld-3-0-injection-supported.4782713/>
- <https://sourceforge.net/projects/nethunter-ginkgo-v3-0/>

**Our port** in this repo is to `ath10k` mainline (not qcacld-3.0),
running on Linux 7.2-rc5, on the Motorola Moto G82 5G (rhodep). This
appears to be the first public port to ath10k mainline — every prior
public implementation is on the vendor qcacld-3.0 downstream stack.

## Where the code lives

- **`kernel/patches/0119-wcn3990-loukious-monitor-inject/`** —
  ath10k driver patch, source snapshots + README with technique
  details, test evidence, prior art audit, and remaining bugs.

Related patches shipping alongside:

| Patch | What it enables |
|-------|-----------------|
| `0115-ath10k-allow-raw-tx-on-monitor-vdev-for-WCN3990` | Broadcast **data** injection on monitor (aireplay `--arpreplay` on bcast) |
| `0117-ath10k-deliver-wmi-mgmt-to-monitor-on-WCN3990` | Mgmt frame **RX** on monitor (airodump/kismet/tshark see mgmt) |
| `0118-ath10k-advertise-AUTH_AND_DEAUTH_RANDOM_TA-ext-feature` | Spoofed SA on the STA-offchan injection path (item 15 `rhodep-inject-lab deauth-swap` mode) |
| `0119-wcn3990-loukious-monitor-inject` | **This patch** — general monitor-mode injection through the hidden STA vdev |

## Firmware situation (important)

**The firmware `wlanmdsp.mbn` is NOT modified by any of these
patches.** All the magic is in the ath10k driver on the host side.
The firmware we ship (or rather, the firmware Motorola ships in
`/vendor/firmware/`) stays pristine:

```
$ md5sum /readonly/firmware/image/wlanmdsp.mbn
f377d18cae70e74610ce386713ecde5c
```

The pristine `wlanmdsp.mbn` is what the modem PIL loads at boot.
Sometimes earlier sessions in this repo did try firmware byte-patches
(see `kernel/patches/wip-rhodep-fw-mgmt-tx/`, `wip-rhodep-loukious-hidden-sta/`,
`wip-rhodep-raw-htt-mgmt/` for the archaeology) — those experiments
are parked, not applied, and not needed with the current driver-only
solution.

If you want to have the exact firmware blobs on hand for reference
or emergency reflashing:

```
# On-device backup (a pristine copy is preserved by the OEM package):
sudo cp /readonly/firmware/image/wlanmdsp.mbn ~/wlanmdsp.mbn.bak

# The firmware also lives in linux-firmware:
#   /lib/firmware/ath10k/WCN3990/hw1.0/wlanmdsp.mbn
# but for Motorola devices the vendor partition ships its own copy
# and the ath10k driver loads that one via QMI / SCM into the modem
# remoteproc PIL region.
```

There is no "modified firmware" you need to install for injection to
work — the current driver patches are pure kernel changes.

## Known rough edges (not yet 100%)

1. **Firmware asserts under sustained high burst rate**.
   `cmnos_thread.c:4005:A` PDM `wlan_process` crash when peer_create
   is called too fast (aireplay bursts 64 fps alternating between
   two addr1 MACs). Mitigated with an 8-slot LRU peer cache in the
   driver but the race is not fully eliminated. When the fw does
   crash, our driver detects it (`ESHUTDOWN` from `peer_create` and
   `ar->state != ATH10K_STATE_ON`) and invalidates the hidden vdev so
   the next injection recreates it — but the in-flight burst loses
   those frames and there's a ~5s recovery hiccup.
2. **Post-restart driver state stale** — fixed via `ar->state != ON`
   check that invalidates `ar->rhodep_inject.created`, but recovery
   still takes ~5 seconds.
3. **First frame after boot has 400ms latency** (msleep 150+150+100
   for vdev_create → vdev_start → peer_create). Subsequent frames
   are fast.
4. **Channel change tears down and recreates the hidden vdev**
   automatically. That's another 400ms hit per channel change.
5. **aireplay's default `--deauth` waits for the target beacon
   forever**. Use `-D` to skip AP detection, or run airodump on the
   BSSID first.
6. **`aireplay-ng` reports `0 ACKs`** even for status=0 frames that
   we know radiated (confirmed by target dropping). The ACKs come
   back addressed to the hidden STA vdev's MAC, and mac80211 doesn't
   route them to `wlan0mon`. This is cosmetic — the attack still
   works — but aireplay users will see zero in the ACK counter.
7. **Data unicast injection to arbitrary DAs** (as opposed to
   broadcast) still crashes the fw. Not covered by this patch —
   broadcast data works via patch 0115. Aireplay's `--arpreplay` on
   broadcast is fine; `--interactive` targeting a specific unicast
   DA is not.

## Roadmap to close remaining bugs

1. **Burst reliability**: instead of trying to keep peer_create in
   sync with aireplay's 64 fps, pre-create peers for a set of
   candidate MACs at hidden-vdev-create time (self-peer + N slots
   for future targets, populated lazily, never destroyed until the
   vdev is torn down).
2. **RX ACK feedback to aireplay**: mac80211 hook or radiotap
   re-injection of received ACKs to `wlan0mon` so aireplay's counter
   matches reality.
3. **Merge snapshots into a single formal `.patch` file** with the
   proper header and signoff, and submit upstream to the `ath10k`
   mailing list.
4. **Add proper systemd/service integration** so `airmon-ng start
   wlan0` triggers the vdev-injection setup once and the state
   persists cleanly across `stop`/`start` cycles.

## The 5+ parked attempts before this worked

If you want the archaeology of what didn't work:

- `kernel/patches/wip-rhodep-fw-mgmt-tx/` — session 5: attempted to
  bypass the fw opmode gate by NOPing the check in the firmware.
  Worked to let the frame through but downstream fw code assumed
  a valid STA-context peer table which monitor didn't have — fw
  crashed after 2-3 frames.
- `kernel/patches/wip-rhodep-loukious-hidden-sta/` — session 6:
  first attempt at Loukious's technique. Got fw to accept the frame
  (COMPLETE_OK sometimes) but with mixed status codes and no
  reliable radiation. Missing pieces: status normalization,
  proper channel detection, INSPECT-as-success, per-DA peer
  creation via raw WMI.
- `kernel/patches/wip-rhodep-raw-htt-mgmt/` — session 7: tried
  the "route mgmt through RAW HTT" approach. Fw crashed on the RAW
  HTT path for mgmt frames (works only for data via patch 0115).

Each parked directory has a `README.md` explaining the specific
theory and why it failed.

## Credits

- **Wael Hasnaoui ("Loukious")** — the reverse engineering,
  discovery of the opmode gate + hidden-STA-vdev workaround in
  qcacld-3.0. This port is entirely his technique applied to the
  ath10k mainline driver.
- **ikteach** — first downstream port to a WCN3990 device (Redmi
  Note 8) via qcacld-3.0, confirming the technique works on WCN3990
  fw (same fw family as Loukious's WCN3998).
- **Kimocoder / aircrack-ng community** — the base
  `enable_monitor_mode.patch` for RX monitor in qcacld-3.0 that all
  the Kali NetHunter phones use.
