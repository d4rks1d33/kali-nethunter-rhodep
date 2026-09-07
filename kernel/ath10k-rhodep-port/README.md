# ath10k-rhodep: WCN3990 monitor mode + injection port to ath10k mainline

Full port of Wael Hasnaoui's ("Loukious") hidden-STA-vdev packet
injection technique to `ath10k` mainline (v7.2-rc5) for **WCN3990**
devices. Originally developed for the Motorola Moto G82 5G
(`sm6375`, codename `rhodep`), the mac-only changes are chip-agnostic
and should apply to any WCN3990-based device that uses `ath10k_snoc`
(Redmi Note 8 / ginkgo, Xiaomi POCO M2 Pro, and many other Snapdragon
6xx/7xx-series devices from 2018-2022).

Verified end-to-end:

- `aireplay-ng -9 wlan0mon` prints **"Injection is working!"** at 100%
  response rate on 2.4 GHz ch 11 AND 5 GHz ch 149.
- `aireplay-ng --deauth` disconnects real target clients OTA.
- `airodump-ng` captures beacons, probes, auth, deauth mgmt frames.
- `mdk4 b` (beacon flood), `scapy sendp`, `hcxdumptool` all function.
- `Home-AP-5G` (target BSSID `AA:BB:CC:DD:EE:02`) captured
  passively at -63 dBm on ch 157.
- Full aircrack-ng suite works from the phone's internal radio, no
  USB adapter required.

## Table of contents

1. [Quick start](#quick-start)
2. [What this port adds vs upstream ath10k](#what-this-port-adds-vs-upstream-ath10k)
3. [The technique in one paragraph](#the-technique-in-one-paragraph)
4. [Architecture](#architecture)
5. [Applying to a fresh kernel](#applying-to-a-fresh-kernel)
6. [Building the modules](#building-the-modules)
7. [Runtime configuration](#runtime-configuration)
8. [Testing](#testing)
9. [Known limitations](#known-limitations)
10. [File layout](#file-layout)
11. [Origin and credits](#origin-and-credits)

## Quick start

```sh
# 1. Apply the patch to a stock 7.2-rc5 tree
cd linux-7.2-rc5
patch -p1 < path/to/ath10k-rhodep-port/patches/0001-ath10k-rhodep-wcn3990-monitor-injection-5ghz.patch

# 2. Build
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     M=drivers/net/wireless/ath/ath10k modules
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     M=drivers/net/wireless/ath modules

# 3. Install ath.ko + ath10k_core.ko + ath10k_snoc.ko

# 4. On the device: set the country
echo 'options ath country=US' > /etc/modprobe.d/ath-country.conf

# 5. Reload:
rmmod ath10k_snoc ath10k_core ath
modprobe ath10k_snoc

# 6. Use:
airmon-ng start wlan0
aireplay-ng -9 wlan0mon
# → "Injection is working!"
```

## What this port adds vs upstream ath10k

Session-by-session, in the order they landed:

1. **Passive mgmt-RX on monitor iface** (patch 0117, pre-session-7).
   WCN3990 fw does not expose the classic `WMI_PDEV_PARAM_PROMISC_MODE`
   command that other ath10k chips use. Instead, it forwards *some*
   mgmt frames to the host via `WMI_MGMT_RX_EVENTID` regardless — but
   ath10k clears `RX_FLAG_SKIP_MONITOR` unconditionally, hiding them.
   Fix: only clear `SKIP_MONITOR` when a monitor vif is present (the
   `ath10k_mon_mgmt` module param).

2. **Hidden AP vdev for TX** (session 9, item 16.5). Loukious's
   original technique uses `WMI_VDEV_TYPE_STA`; that works on WCN3998
   but WCN3990 fw's `wal_tx` unicast-mgmt path is stricter and
   DISCARDs unicast whose addr1 has no matching peer. Per-DA
   `peer_create` on a peerless STA vdev asserts `cmnos_thread.c:4005:A`
   (fw's cmnos inter-thread pool limit). Solution: use
   `WMI_VDEV_TYPE_AP` + `VDEV_UP` with self-BSSID — AP self-peer
   serves as wildcard tx-peer for ANY `addr1` unicast, no per-DA
   `peer_create` needed. `bcn_intval=0` + `hidden_ssid=true` + dummy
   ssid + `disable_hw_ack=true` prevent any accidental beacon TX.

3. **Fix A: drop non-mgmt frames on monitor vif** (session 9, item
   16.5). aireplay's directed burst emits probe-req (mgmt) + RTS
   (ctl) + null-data (data) + auth (mgmt). Frames 1 and 4 go through
   the WMI mgmt-tx path with vdev-rewrite to the hidden AP vdev.
   Frames 2 and 3 go through HTT raw with `vdev_id=monitor(0)` and
   crash fw at `WLAN BE:0x4708a` when combined with a sibling AP
   vdev being UP. Fix: silently drop non-mgmt frames on monitor vif
   before they reach HTT.

4. **Atomic idr_remove for mgmt_pending_tx** (session 9, item 16.3).
   Original `wmi_process_mgmt_tx_comp` did `idr_find()` + late
   `idr_remove()` with `kfree(pkt_addr)` in between. WCN3990 fw
   occasionally emits duplicate completion events, which UAF'd the
   freed `pkt_addr`. Fix: use `idr_remove()` atomically as both
   lookup and unhook.

5. **Retry-only mgmt-tx-compl event drop** (session 9, item 16.3).
   WCN3990 fw emits ext-status codes for retry metadata that our
   original `raw & 0x3` normalization mis-classified as fake
   COMPLETE_OK. Fix: drop `(raw & 0x3) == 0 && (raw & ~0x3U)` events
   entirely.

6. **mgmt_pending_tx GC + HTC credit recovery** (session 9, item
   16.4). WCN3990 has ONLY ONE HTC WMI credit. Fw dropped completions
   leak the credit forever unless GC'd. Without recovery,
   `ath10k_wmi_cmd_send` times out at 3s → `ath10k_core_start_recovery`
   → `CRASH_FLUSH` → `-108` avalanche on every subsequent tx. Fix:
   `ath10k_rhodep_mgmt_tx_gc_one` reaps stale IDR entries older than
   300ms AND refunds the HTC credit. Runs on `-ENOSPC` retry only
   (NOT at the top of every burst, which races real completions).

7. **Chan-scan defense** (session 9, item 16.6). aireplay-ng without
   `-D` scans channels 1..13 looking for the AP beacon. Each `iw set
   channel` used to teardown+recreate the hidden AP vdev = 10 WMI
   cmds × 13 channels = credit exhaustion → recovery. Fix: three
   layers: (A) fw-dead early bailout, (B) rate-limit 1/sec, (C)
   in-place `vdev_restart` instead of delete+recreate.

8. **Chanctx retune hook** (session 9, item 16.8). WCN3990 has ONE
   radio. When the hidden AP vdev is UP on 2.4 GHz and mac80211 moves
   the monitor vif to 5 GHz, the fw arbitrates in favor of the
   AP-typed vdev and pins the radio to 2.4 GHz. Fix:
   `ath10k_rhodep_inject_retune()` runs from
   `ath10k_mac_update_vif_chan()` and does `vdev_down → vdev_restart
   → vdev_up` on the hidden vdev to the new freq.

9. **Driver-side invalidate on fw crash** (session 9, item 16.5).
   `ath10k_rhodep_inject_invalidate()` called from
   `ath10k_core_restart` BEFORE `CRASH_FLUSH`. Clears `->created`
   flag driver-side (no WMI, no sleep). Next mgmt-tx worker
   iteration recreates the hidden vdev cleanly.

10. **5 GHz TX/RX unlock via `ath.country=` module param** (session
    9, item 16.7). WCN3990 ships EEPROM regdomain 0x406c → world
    regd → `NL80211_RRF_NO_IR` on all 5 GHz channels. `iw reg set
    US` silently rejected because
    `CONFIG_ATH_REG_DYNAMIC_USER_REG_HINTS` defaults to N. Fix: new
    `ath.country=US` module param on the parent `ath` driver that
    (a) rewrites `current_rd`, (b) makes runtime `iw reg set` also
    work, (c) applies a permissive custom regdom covering all 25
    common 5 GHz channels (UNII-1/2/2e/3). Backward-compat: when
    the param is empty, behavior is identical to stock upstream.

## The technique in one paragraph

Loukious discovered that WCN3998 fw's mgmt-tx allow-list only checks
`vdev.opmode ∈ {STA, AP, IBSS, NDI}` — it does NOT check whether the
vdev was registered by mac80211 or where the frame came from. So he
creates a hidden vdev directly via raw WMI (never seen by
mac80211/cfg80211), with a self-peer to satisfy the fw's peer
requirement, skipping VDEV_UP (fw asserts on STA-UP without a BSS
peer). When a mgmt frame is submitted through the monitor vif, the
driver rewrites the `vdev_id` in the outgoing WMI `mgmt_tx_send` cmd
from the monitor vdev id to the hidden vdev id. Fw sees a valid STA
opmode with a peer, passes the allow-list, radiates the frame with
`addr1/2/3` verbatim.

**This port** adapts the technique to WCN3990 (stricter than
WCN3998), using an AP vdev instead of STA + a self-BSSID VDEV_UP.

## Architecture

```
                    mac80211
                        |
                        v
              ath10k_mac_op_tx (monitor vif)
                        |
                        +--- if !is_mgmt(frame): DROP (Fix A)
                        |    else:
                        |
                        v
              ath10k_wmi_mgmt_tx_queue    (skb_queue_tail)
                        |
                        v
              ath10k_mgmt_over_wmi_tx_work    (workqueue)
                        |
                        v
              ath10k_wmi_tlv_op_gen_mgmt_tx_send
                        |
                        +--- ath10k_rhodep_inject_vdev_for_mon()
                        |        returns hidden_vdev_id  <-- vdev_id rewrite
                        v
              WMI MGMT_TX_SEND_CMDID  ->  fw's wal_send_mgmt
                                             (allow-list gate passes)
                                             (AP self-peer used as tx-peer)
                                             (frame radiates on air)
```

Setup path:

```
first mgmt frame arrives on monitor vif
  -> ath10k_rhodep_ensure_inject_vdev()
        - WMI_VDEV_CREATE(WMI_VDEV_TYPE_AP)
        - WMI_VDEV_START (freq, mode=11A or 11G, hidden_ssid=1,
                          disable_hw_ack=1, bcn_intval=0)
        - WMI_PEER_CREATE (self MAC)
        - WMI_VDEV_UP (bssid=self MAC)
        - publish rhodep_inject.created = true
```

Channel change:

```
mac80211 hops the monitor vif to a new channel
  -> ath10k_mac_update_vif_chan()
        - unassign vif chanctx
        - remove old chanctx
        - add new chanctx
        - reassign vif chanctx (does WMI_VDEV_START for monitor)
        - ath10k_rhodep_inject_retune(new_freq):     <-- item 16.8
              if hidden vdev.freq != new_freq:
                    WMI_VDEV_DOWN(hidden)
                    WMI_VDEV_RESTART(hidden, new_freq)
                    WMI_VDEV_UP(hidden, bssid=self)
```

Fw crash recovery:

```
fw asserts on any WMI cmd
  -> ath10k_core_start_recovery
        -> ath10k_rhodep_inject_invalidate()         <-- item 16.5
              clear rhodep_inject.created
              clear peer_cache
              release vdev_id back to free_vdev_map
        -> set_bit(ATH10K_FLAG_CRASH_FLUSH)
        -> fw reboots via QMI
        -> next mgmt frame triggers ensure_inject_vdev() again
```

## Applying to a fresh kernel

The port is a single unified patch that touches 5 files in
`drivers/net/wireless/ath/` and 4 files in
`drivers/net/wireless/ath/ath10k/`:

```
drivers/net/wireless/ath/regd.c            (5 GHz TX/RX unlock)
drivers/net/wireless/ath/ath10k/core.c     (init + fw-crash invalidate)
drivers/net/wireless/ath/ath10k/core.h     (rhodep_inject struct)
drivers/net/wireless/ath/ath10k/mac.c      (main port logic)
drivers/net/wireless/ath/ath10k/mac.h      (exports)
drivers/net/wireless/ath/ath10k/wmi.c      (mgmt-tx-compl fixes)
drivers/net/wireless/ath/ath10k/wmi.h      (dummy for future work)
drivers/net/wireless/ath/ath10k/wmi-tlv.c  (mgmt_pending_tx GC)
drivers/net/wireless/ath/ath10k/wmi-tlv.h  (alloc_jiffies field)
```

Apply:

```sh
cd path/to/linux-7.2-rc5
patch -p1 < path/to/ath10k-rhodep-port/patches/0001-ath10k-rhodep-wcn3990-monitor-injection-5ghz.patch
```

Verify:

```sh
grep -c "rhodep" drivers/net/wireless/ath/ath10k/mac.c
# expect ~50+
grep -c "ath_country" drivers/net/wireless/ath/regd.c
# expect 3
```

The patch is against `v7.2-rc5`. For newer trees rebase against
whatever version you're using — the WMI cmd names have been stable
for years so drift should be minimal.

## Building the modules

```sh
cd linux-7.2-rc5
# assumes you have a .config for your device
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- oldconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     M=drivers/net/wireless/ath modules

# module output:
#   drivers/net/wireless/ath/ath.ko                     (5 GHz unlock)
#   drivers/net/wireless/ath/ath10k/ath10k_core.ko      (main port)
#   drivers/net/wireless/ath/ath10k/ath10k_snoc.ko      (WCN3990 SoC glue)
```

Strip and install:

```sh
aarch64-linux-gnu-strip --strip-debug \
    drivers/net/wireless/ath/ath.ko \
    drivers/net/wireless/ath/ath10k/ath10k_core.ko \
    drivers/net/wireless/ath/ath10k/ath10k_snoc.ko

scp drivers/net/wireless/ath/ath.ko \
    drivers/net/wireless/ath/ath10k/ath10k_{core,snoc}.ko \
    root@device:/lib/modules/$(uname -r)/kernel/drivers/net/wireless/ath/{,ath10k/}
ssh root@device 'depmod -a && modprobe ath10k_snoc'
```

## Runtime configuration

The module param that opens 5 GHz TX/RX:

```sh
# /etc/modprobe.d/ath-country.conf
options ath country=US
```

Change `US` to your country if the CTL/EIRP profile matters. Any ISO
3166 alpha2 code recognized by wireless-regdb works.

Optional: enable verbose rhodep logging (dmesg):

```sh
# see rhodep event log
echo 8 > /proc/sys/kernel/printk

# in dmesg during aireplay:
#   rhodep vdev-start id=0 type=4 freq=2462 cf1=2462 mode=1 restart=0
#   rhodep inject: creating AP vdev id=2 inj_mac=... freq=2462
#   rhodep inject: helper vdev 2 ready (mac=...)
#   rhodep: mgmt-tx vdev 0 -> hidden STA 2
#   rhodep mgmt-tx-compl desc_id=0 status=0
```

## Testing

### Regression test (should always pass)

```sh
airmon-ng start wlan0
iw dev wlan0mon set channel 11
timeout 5 aireplay-ng -9 wlan0mon
# expected: "Injection is working!" + "Found N APs" + directed phase 80%+
```

### 5 GHz TX (should always pass)

```sh
iw dev wlan0mon set channel 149
timeout 5 aireplay-ng -9 wlan0mon
# expected: "Injection is working!" + N APs on ch 149
```

### 5 GHz RX (should always pass)

```sh
iw dev wlan0mon set channel 157
sleep 3
timeout 4 tcpdump -i wlan0mon -e -n 'type mgt subtype beacon'
# expected: beacons at 5785 MHz from local 5 GHz APs
```

### Deauth OTA (verified with target)

```sh
iw dev wlan0mon set channel <AP_CHAN>
aireplay-ng -D --deauth 10 -a <AP_BSSID> -c <CLIENT_MAC> wlan0mon
# expected: client disconnects; no fw crash in dmesg
```

### Fw stability (long-running)

```sh
timeout 60 aireplay-ng -9 wlan0mon
dmesg | grep -c "firmware crashed"
# expected: 0
```

## Known limitations

Documented in detail: [`docs/5ghz-mgmt-forwarder-bug.md`](docs/5ghz-mgmt-forwarder-bug.md).

Summary: `airodump-ng --band abg` (auto-hopping both bands) misses
some 5 GHz APs because WCN3990 fw's mgmt-forwarder gets stuck across
5G↔2.4G phymode transitions. Workaround: fix the channel manually
before running airodump / wifite.

Two driver-side fixes attempted (DOWN+UP bounce on monitor vdev;
dummy STA vdev create+delete+barrier), both crashed fw. Root cause
requires either downstream qcacld-3.0 RE to find the fw-safe cmd
sequence, or a fw patch.

## File layout

```
kernel/ath10k-rhodep-port/
├── README.md                              This file
├── src/                                   The current source (post-port)
│   ├── regd.c                             ath common regdomain override
│   ├── core.c
│   ├── core.h
│   ├── mac.c
│   ├── mac.h
│   ├── wmi.c
│   ├── wmi.h
│   ├── wmi-tlv.c
│   └── wmi-tlv.h
├── patches/
│   └── 0001-ath10k-rhodep-wcn3990-monitor-injection-5ghz.patch
│                                          Single unified patch, apply
│                                          with -p1 to a stock 7.2-rc5
│                                          kernel.
└── docs/
    ├── 5ghz-mgmt-forwarder-bug.md         The one known limitation
    └── EVOLUTION.md                       Sessions 1..9 timeline
```

## Origin and credits

**Wael Hasnaoui** ("Loukious"), Tunisia. Original technique:
reverse-engineered WCN3998 fw in Ghidra, identified the opmode
allow-list gate in `_wlan_mgmt_tx_send`, wrote the qcacld-3.0
`monitor_sta_vdev_tx` patch v1 through the Xiaomi Onyx tree.

- Medium article: <https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702>
- qcacld-3.0 v1 SDM855: <https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1>
- Onyx WCN7750 port: <https://github.com/Loukious/vendor_qcom_opensource_wlan/tree/onyx-v-oss-monitor-direct>
- LinkedIn: <https://linkedin.com/in/wael-hasnaoui>

**ikteach** (Ikram, India): downstream port to Redmi Note 8 / ginkgo
(also WCN3990) using qcacld-3.0. Injection-supported claim on XDA;
we could not verify it works on the internal radio (XDA thread
mentions external USB adapters).

- XDA: <https://xdaforums.com/t/kernel-ginkgo-nethunter-ikteach-kernel-lineageos-23-2-android-16-wifi-qcacld-3-0-injection-supported.4782713/>
- github: <https://github.com/ikteach>
- SourceForge: <https://sourceforge.net/projects/nethunter-ginkgo-v3-0/>

**Kalle Valo** (ath10k maintainer) on the ath10k mailing list (Aug
2024): *"I doubt that WCN3990 firmware supports monitor mode"* —
<https://lists.infradead.org/pipermail/ath10k/2024-August/016001.html>.
This port disproves that.

**kimocoder**: `enable_monitor_mode.patch` for qcacld-3.0
(RX-monitor only, not injection) —
<https://github.com/kimocoder/qualcomm_android_monitor_mode>. Not
applicable to ath10k mainline directly but was the starting point of
the RX-mgmt-hijack idea.

**The `rhodep` port maintainers** (this port): first working ath10k
mainline implementation for any WCN3990 device we could find in a
research sweep of GitHub, Linux ML, XDA, Kali NetHunter, LineageOS
device trees, pmaports, and academic papers.

## Upstreaming

This port is a candidate for upstreaming to the `ath10k` mailing list
once a couple of remaining rough edges are addressed:

- Split the single unified patch into a proper `01xx-*.patch` series
  by topic (one patch per feature).
- Investigate the `airodump-ng --band abg` 5 GHz consistency issue
  (see `docs/5ghz-mgmt-forwarder-bug.md`).
- Address style / SoB / test coverage feedback from ath10k
  maintainers.

Pull requests, feedback, alternative ideas welcome. This is a
proof-of-concept that the WCN3990 firmware DOES support injection
via the hidden-vdev trick; the code shape is up for negotiation.

## License

Same as ath10k (dual BSD/GPL). Port additions are Signed-off-by
the rhodep port maintainers.
</content></function_results>

Ahora escribo el EVOLUTION.md — el archivo que llené arriba con timeline por sesión:

<function_calls>
<invoke name="bash">
<parameter name="command">mv /opt/postmarket/nethunter-rhodep-repo/kernel/ath10k-rhodep-port/README.md /tmp/README-tmp.md
ls /opt/postmarket/nethunter-rhodep-repo/kernel/ath10k-rhodep-port/