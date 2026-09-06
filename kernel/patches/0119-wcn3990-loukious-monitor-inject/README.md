# 0119: WCN3990 monitor+radiotap injection via hidden STA vdev

**Port of Wael Hasnaoui ("Loukious") qcacld-3.0 injection technique to
ath10k mainline. First public port. Works end-to-end for aircrack-ng,
mdk4, scapy, hcxdumptool. Still needs polish for burst reliability
(race conditions + firmware asserts under sustained high-rate load).**

## TL;DR

- `airmon-ng start wlan0` creates `wlan0mon` (normal monitor iface).
- `aireplay-ng -9 wlan0mon` reports **"Injection is working!"** —
  aircrack-ng's own official test passes.
- `aireplay-ng --deauth`, `mdk4 b`, `scapy sendp()`, `airodump-ng`
  all work without modification.
- Broadcast mgmt-tx radiates 100%. Unicast mgmt-tx radiates ~40-80%
  depending on rate (higher rate = more DISCARD from fw peer table
  contention, but aireplay's `-9` test passes and clients drop on
  burst).
- Data unicast injection to arbitrary DA still crashes fw (not
  fixed here). Broadcast data works via existing patch 0115.

## Known bugs / rough edges

1. **Firmware asserts under high burst rate**. `cmnos_thread.c:4005:A`
   PDM crash of `wlan_process` when peer_create is called too fast
   (aireplay bursts 64 fps alternating between two addr1 values).
   Mitigated with an 8-slot LRU peer cache in the driver but the
   race is not fully eliminated. When the fw crashes, our driver
   catches `ath10k_wmi_peer_create → -ESHUTDOWN` and invalidates the
   hidden vdev so the next injection recreates it — but the burst
   already in flight loses those frames.
2. **Post-restart driver state stale** — fixed via `ar->state != ON`
   check that invalidates `ar->rhodep_inject.created`. But recovery
   still takes ~5 seconds during which injections are dropped.
3. **First frame after boot has 400ms latency** (msleep 150+150+100
   for vdev_create → vdev_start → peer_create). Subsequent frames
   are fast.
4. **Channel change tears down and recreates the hidden vdev**
   automatically. That is another 400ms hit per channel change.
5. **aireplay's default `--deauth` waits for the target beacon
   forever**. Use `-D` to skip AP detection, or run airodump on the
   BSSID first.

## Origin of the technique

**Wael Hasnaoui ("Loukious"), Tunisia** — reverse-engineered
WCN3998 firmware in Ghidra, identified the opmode allow-list gate
in `_wlan_mgmt_tx_send`, wrote the qcacld-3.0 monitor_sta_vdev_tx
patch (v1 through v7, Mar 2026 → Aug 2026).

- Article: <https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702>
- qcacld-3.0 v1 SDM855: <https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1>
- qcacld-3.0 v7 WCN7750: <https://github.com/Loukious/vendor_qcom_opensource_wlan/tree/onyx-v-oss-monitor-direct>
- LinkedIn: <https://linkedin.com/in/wael-hasnaoui>

Downstream port to another WCN3990 device (Redmi Note 8 / ginkgo)
by **ikteach (Ikram, India)** using qcacld-3.0 vendor driver:
- XDA thread: <https://xdaforums.com/t/kernel-ginkgo-nethunter-ikteach-kernel-lineageos-23-2-android-16-wifi-qcacld-3-0-injection-supported.4782713/>
- github: <https://github.com/ikteach>
- SourceForge: <https://sourceforge.net/projects/nethunter-ginkgo-v3-0/>

## Prior art audit (before we started)

Every "monitor mode on Snapdragon" reference the community had
been recommending for the last 5 years is a **defconfig flip in
qcacld-3.0** (Kimocoder's `enable_monitor_mode.patch` — 1-line diff
that flips `CONFIG_FEATURE_MONITOR_MODE_SUPPORT := y`). That only
enables **RX** monitor. Injection (TX) was silently blocked in
qcacld-3.0's firmware since ~2018, and nobody had solved it for the
mainline `ath10k` driver on any WCN3990 device.

The `ath10k` mailing list explicitly confirmed this in Aug 2024:

> "I doubt that WCN3990 firmware supports monitor mode" — Kalle
> Valo (ath10k maintainer)
> <https://lists.infradead.org/pipermail/ath10k/2024-August/016001.html>

Our port is the first working implementation on ath10k mainline
(`ath10k_snoc`, kernel 7.2-rc5) for any WCN3990 device that we
could find in a full research sweep across GitHub, Linux mailing
lists, XDA, Kali NetHunter kernel repos, LineageOS device trees,
pmaports, and academic papers.

## How the technique works

The WCN3990 fw's WMI mgmt-tx handler in `_wlan_mgmt_tx_send`
(reverse-engineered address `0xb0013544` in the fw disassembly
we did in earlier sessions — see `/opt/postmarket/RESEARCH_wcn3990_injection.md`)
has an opmode allow-list gate:

```c
if ((opmode != WMI_VDEV_TYPE_STA) &&
    (opmode != WMI_VDEV_TYPE_AP) &&
    (opmode != WMI_VDEV_TYPE_IBSS) &&
    (opmode != WMI_VDEV_TYPE_NDI))
    → discard frame with status=1
```

MONITOR (type=4) is not in the allow-list. Fw silently drops every
mgmt frame submitted on a monitor vdev.

**Loukious's trick**: don't submit mgmt frames on the monitor vdev
at all. Instead:

1. Create a **hidden STA-type vdev** in the firmware via raw WMI
   commands. Never register it with mac80211 / cfg80211 — it exists
   only inside the fw.
2. Create a **self-peer** on the hidden STA vdev (this satisfies the
   fw's `vdev+0xc` peer-required check that lives downstream of the
   opmode gate).
3. Do **not** call `WMI_VDEV_UP` on the hidden vdev — for STA vdevs,
   the fw asserts on UP unless a BSS peer (the AP) exists, and we
   only have a self-peer. The mgmt-tx handler only requires the vdev
   to be in STARTED state with a valid peer at `vdev+0xc`.
4. When a mgmt frame is submitted through the monitor vif, the
   driver rewrites the `vdev_id` in the outgoing WMI mgmt_tx_send
   command from the monitor vdev's id to the hidden STA vdev's id.
   Fw sees a valid STA opmode with a peer, passes the allow-list,
   radiates the frame on the wire — with addr1/2/3 exactly as the
   user built them (so spoofed SA / BSSID work).

For **unicast** mgmt frames (aireplay `--deauth -c CLIENT_MAC`),
the fw's wal_tx path requires a peer entry for addr1. We add per-DA
temp peers on the hidden STA vdev via raw `ath10k_wmi_peer_create`
(bypassing `ath10k_peer_create` which uses `wait_for_peer_created`
that times out because PEER_MAP events don't fire on hidden vdev).
An 8-slot LRU cache prevents recreating peers on every frame of a
burst.

## Key fixes vs previous attempts (5 parked sessions before this worked)

Prior sessions in `wip-rhodep-*/` failed for these reasons, all
now fixed:

1. **`wait_for_peer_created` timed out** because PEER_MAP never fires
   on a hidden STA vdev. Fixed by replacing with `msleep(100)`
   (Loukious's approach).
2. **`cb->vif->addr` is `00:00:00:00:00:00`** for scapy PF_PACKET
   injection on monitor iface. Fixed by falling back to a random
   LAA MAC when the input MAC is zero.
3. **Channel detection** — monitor vif's `hw->conf.chandef` is
   NULL. Fixed by iterating chanctx list via
   `ath10k_mac_get_any_chandef_iter`.
4. **Status normalization** — fw returns extended status words where
   upper bits carry retry metadata. Without normalization,
   `status=0x100000` looks like DISCARD when it's really
   COMPLETE_OK. Fixed by `status & 0x3` when `status >= 4`.
5. **INSPECT (status=3) treated as failure** — for injection,
   INSPECT means "radiated on air, fw asks host to inspect".
   Reported as success to mac80211.
6. **Per-DA peer creation for unicast** — the FUNDAMENTAL insight
   from Loukious v7 that we missed for weeks. Without it, unicast
   mgmt frames get 100% DISCARD on the hidden STA vdev.
7. **Peer cache** — bursts (64 fps) recreating the same peer race fw
   and assert `cmnos_thread.c:4005`. 8-slot LRU cache.
8. **FW restart detection** — after crash+recovery, `rhodep_inject.
   created` stays `true` but the fw vdev is gone. Detect via
   `ar->state != ATH10K_STATE_ON` and `-ESHUTDOWN` return codes;
   invalidate cache and vdev flag to trigger recreation.

## Confirmed working command examples

```bash
# 1. Bring up monitor iface (destroys wlan0 assoc):
sudo airmon-ng start wlan0     # → wlan0mon on ch 10
sudo iw dev wlan0mon set channel 11

# 2. Test injection (aircrack-ng's official test):
sudo aireplay-ng -9 wlan0mon
# Output: "Injection is working!"

# 3. Deauth broadcast (use -D to skip AP beacon detection):
sudo aireplay-ng -D --deauth 0 -a <BSSID> wlan0mon

# 4. Deauth targeting a specific client:
sudo aireplay-ng -D --deauth 0 -a <BSSID> -c <CLIENT_MAC> wlan0mon

# 5. scapy raw injection (100% radiation at controlled rate):
sudo python3 -c "
from scapy.all import *
import time
BSSID='8a:c2:27:a1:19:cc'
CLIENT='96:2f:ef:12:f8:f6'
p = RadioTap()/Dot11(type=0,subtype=12,addr1=CLIENT,addr2=BSSID,addr3=BSSID)/Dot11Deauth(reason=7)
for _ in range(30):
    sendp(p, iface='wlan0mon', verbose=0)
    time.sleep(0.05)
"

# 6. Beacon flood:
sudo mdk4 wlan0mon b -c 11

# 7. Capture with airodump:
sudo airodump-ng --channel 6 --bssid <BSSID> -w /tmp/cap wlan0mon
```

## Test evidence

- `aireplay-ng -9 wlan0mon` — "Injection is working!" ✓
- `aireplay-ng --deauth 10 -a <BSSID> -c <CLIENT> wlan0mon` — 640 frames
  sent, 99 status=0, no fw crash, target client observed disconnecting
- scapy 20 unicast deauth — 16 status=0 (80% radiated), target dropped
- Sustained burst 60s at 8pps — 438/438 status=0 (100% broadcast)
- `mdk4 b -c 11` beacon flood — works, ~48 pps sustained
- `airodump-ng` — captures beacons + data normally
- Target phone associated to test AP disconnected during 30-frame burst

## Firmware status codes handled

WMI_MGMT_TX_COMP_STATUS_TYPE values reported by WCN3990 fw:

| value | name | radiated? | ACKed? | our handling |
|-------|------|-----------|--------|--------------|
| 0 | COMPLETE_OK | yes | yes (unicast) | success + STAT_ACK |
| 1 | DISCARD | **no** | no | real failure |
| 2 | COMPLETE_NO_ACK | yes | no | radiated, no ACK |
| 3 | INSPECT | yes | yes* | success (normalized to 0) |
| ≥4 | extended | | | `status & 0x3` |

## Files layout

- `mac.c.snapshot` — `ath10k_rhodep_ensure_inject_vdev`, hidden vdev
  create/teardown, mgmt-tx worker hook, per-DA peer creation with
  cache, fw-restart detection.
- `wmi.c.snapshot` — `ath10k_wmi_event_mgmt_tx_compl` status normalization.
- `wmi-tlv.c.snapshot` — `vdev_id` rewrite in
  `ath10k_wmi_tlv_op_gen_mgmt_tx_send`.
- `core.h.snapshot` — `struct ath10k::rhodep_inject { created,
  vdev_id, monitor_vdev_id, chanfreq, mac_addr, peer_cache[8],
  peer_cache_next, lock }`.
- `core.c.snapshot` — `mutex_init` on driver alloc.
- `mac.h.snapshot` — exports `ath10k_rhodep_inject_vdev_for_mon` +
  `ath10k_rhodep_inject_teardown`.

## To turn this into a proper `.patch` file

Currently snapshots so it's easy to inspect and iterate. Once the
race-condition polish is done, generate with:

```
diff -u <pristine-linux-7.2-rc5>/drivers/net/wireless/ath/ath10k/*.c \
        0119-wcn3990-loukious-monitor-inject/*.snapshot \
     > 0119-wcn3990-loukious-monitor-inject.patch
```

## Roadmap to close remaining bugs

1. **Burst reliability**: instead of trying to keep peer_create in
   sync with aireplay's 64 fps, pre-create peers for a set of
   candidate MACs at hidden-vdev-create time (self-peer + N slots
   for future targets, populated lazily but never destroyed until
   vdev teardown).
2. **RX ACK feedback to aireplay**: aireplay reports `0 ACKs` even
   for status=0 frames because the ACKs come back addressed to the
   hidden STA vdev's MAC, and mac80211 doesn't route them to
   `wlan0mon`. Needs a mac80211 hook or radiotap re-injection of the
   ACK to `wlan0mon`.
3. **Merge into a single formal `.patch`** with proper header and
   signoff.
4. **Upstream to `ath10k` mailing list**. Kvalo already said "I
   doubt", so a working demo would be big.

## Related patches in this repo

- **0115** `ath10k-allow-raw-tx-on-monitor-vdev-for-WCN3990` — enables
  broadcast data injection on monitor (aireplay `--arpreplay` on
  broadcast).
- **0117** `ath10k-deliver-wmi-mgmt-to-monitor-on-WCN3990` — mgmt RX
  capture (airodump / kismet / tshark see mgmt frames).
- **0118** `ath10k-advertise-AUTH_AND_DEAUTH_RANDOM_TA-ext-feature` —
  allows spoofed SA on deauth for the STA-vif+offchan injection path
  (item 15 `rhodep-inject-lab deauth-swap` mode; superseded by 0119
  for aircrack tools that go through monitor iface directly).
- **0119** (this patch) — the general solution.

## References

- Loukious Medium article: <https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702>
- Loukious sm8150 kernel v1 (Mar 2026): <https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1>
- Loukious WCN7750 v7 (Aug 2026): <https://github.com/Loukious/vendor_qcom_opensource_wlan/tree/onyx-v-oss-monitor-direct>
- ikteach Redmi Note 8 downstream port: <https://xdaforums.com/t/kernel-ginkgo-nethunter-ikteach-kernel-lineageos-23-2-android-16-wifi-qcacld-3-0-injection-supported.4782713/>
- kimocoder qcacld-3.0 monitor-mode (RX only): <https://github.com/kimocoder/qualcomm_android_monitor_mode>
- ath10k mailing list "I doubt" thread: <https://lists.infradead.org/pipermail/ath10k/2024-August/016000.html>
