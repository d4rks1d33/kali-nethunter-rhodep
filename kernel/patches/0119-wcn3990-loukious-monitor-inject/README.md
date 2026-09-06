# 0119: WCN3990 monitor-mode radiotap injection via hidden STA vdev

**Status: WORKING** — verified OTA on device (Motorola Moto G82 5G / rhodep).

## What this unlocks

- `airmon-ng start wlan0` — creates `wlan0mon` as normal
- `scapy sendp(..., iface="wlan0mon")` — mgmt frames radiate OTA
- `aireplay-ng --deauth` — sends frames (fw returns status=0, TX OK)
- `mdk4`, `hcxdumptool`, any tool that does `write(PF_PACKET/SOCK_RAW, radiotap+frame)`
  on the monitor iface

Verified over the air: deauth frames with spoofed AP BSSID as SA (addr2)
reach clients and the AP acts on them, disconnecting associated STAs.
Confirmed by dmesg (`status=0` on every mgmt-tx-compl) and by capture
loopback (our own frames appear in `tcpdump -i wlan0mon` after radiation).

## Reference / prior art

Port of **Loukious's qcacld-3.0 monitor_sta_vdev_tx patch** to ath10k
mainline. Original targeted WCN3998 on Snapdragon 855 in the vendor
qcacld-3.0 driver stack:

- Article: https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702
- qcacld-3.0 commit: https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1
- Downstream Redmi Note 8 (ginkgo) port: github.com/ikteach NetHunter-ginkgo-V3.0

We are the **first port to ath10k mainline** (Linux 7.2+, `ath10k_snoc`
driver, not qcacld-3.0). Nobody has done this on ath10k before per
extensive research (lists.infradead.org/ath10k Aug 2024 thread: Kalle
Valo, the ath10k maintainer, explicitly stated "I doubt WCN3990
firmware supports monitor mode").

## Technique

The WCN3990 fw's mgmt-tx handler rejects frames whose vdev opmode is
MONITOR (type=4). It only accepts STA (1) or AP (2). Loukious's trick:

1. When mgmt-tx from a monitor vif is queued, silently create a
   **hidden STA vdev** in the firmware with a self-peer at `vdev+0xc`.
   The hidden vdev is never registered with mac80211/cfg80211 —
   it exists only inside the fw.
2. Rewrite the `vdev_id` in the outgoing WMI mgmt_tx_send cmd from the
   monitor vdev to the hidden STA vdev. The frame body (addr1/2/3)
   stays exactly as scapy/aireplay built it, allowing spoofed SA.
3. Fw accepts the frame (STA opmode passes the allow-list) and radiates
   it. TX completion event comes back on the hidden vdev's id, is
   normalized (`status & 0x3` to strip fw extended metadata bits) and
   fed to mac80211's tx status path as if it had originated from the
   monitor vif.

## Files modified

Snapshots of the working code kept alongside this README:

- `mac.c.snapshot` — `ath10k_rhodep_ensure_inject_vdev()`,
  `ath10k_rhodep_inject_vdev_for_mon()`, `ath10k_rhodep_inject_teardown()`,
  hook in `ath10k_mgmt_over_wmi_tx_work` that triggers vdev creation
  before submission.
- `mac.h.snapshot` — exports the helper functions.
- `core.h.snapshot` — adds `struct ath10k::rhodep_inject { created,
  vdev_id, monitor_vdev_id, chanfreq, mac_addr[6], lock }`.
- `core.c.snapshot` — mutex_init on driver alloc.
- `wmi-tlv.c.snapshot` — `vdev_id` rewrite in
  `ath10k_wmi_tlv_op_gen_mgmt_tx_send`.
- `wmi.c.snapshot` — `status & 0x3` normalization in
  `ath10k_wmi_event_mgmt_tx_compl` (the KEY fix that made this work
  where previous port attempts saw DISCARD=1 falsely).

## Key differences from previous parked attempt

The parked `wip-rhodep-loukious-hidden-sta/` from earlier sessions had
the vdev creation and rewrite pieces but was missing:

1. **Status normalization** (`status >= 4 → status & 0x3`) — the fw
   returns extended status words where the upper bits carry retry
   metadata; without normalization we read status=0x100000 as DISCARD
   when it was actually COMPLETE_OK. This is what Loukious's v7 patch
   does at wma_process_mgmt_tx_completion. Fix in wmi.c.
2. **msleep(150)/msleep(100)** between vdev_create/vdev_start/peer_create
   instead of `wait_for_peer_created()` — the peer_map event never
   arrives reliably for a hidden STA vdev on WCN3990, so
   `wait_for_peer_created` timed out at 3s and we bailed out.
3. **Random-MAC fallback** when `cb->vif->addr` is zero (scapy PF_PACKET
   injection on monitor sometimes has cb->vif->addr uninitialized).
4. **Chanctx-iter freq detection** in the mgmt-tx worker — monitor vifs
   don't populate `hw->conf.chandef` reliably, so we iterate chanctx
   list to find the current channel.

## Test results

```
$ airmon-ng start wlan0
$ iw dev wlan0mon set channel 11
$ python3 -c 'from scapy.all import *; \
    p = RadioTap()/Dot11(type=0, subtype=12, addr1="ff:ff:ff:ff:ff:ff", \
        addr2="8a:c2:27:a1:19:cc", addr3="8a:c2:27:a1:19:cc")/Dot11Deauth(reason=7); \
    [sendp(p, iface="wlan0mon", verbose=0) for _ in range(30)]'

$ dmesg | grep mgmt-tx-compl | grep status
[  ...] rhodep mgmt-tx-compl desc_id=0 status=0  (x30)

$ tcpdump -i wlan0mon -nne "type mgt subtype deauth"
BSSID:8a:c2:27:a1:19:cc DA:ff... SA:8a:c2:27:a1:19:cc DeAuthentication  (loopback of our own frames)
```

User's phone associated to WiFi Mateo 2.4G (real AP with BSSID
`8a:c2:27:a1:19:cc`, our own AP for testing) disconnected during the
30-frame burst — confirming OTA delivery.

## Known limitations

1. **`aireplay-ng -9` fails at 0%** — aireplay does two-way TX/RX
   (sends probe, expects response). Our TX works, but the fw's
   mgmt-rx path drops responses that arrive addressed to the hidden
   STA vdev's MAC (we generate a random LAA MAC that no AP knows).
   Injection is verified via `tcpdump` loopback. This is a rx-filter
   quirk; the TX itself is fine.
2. **Data-frame unicast injection** on monitor still broken (fw crash on
   peerless data path). Deauth/probe-req/action work because they're
   mgmt frames that go through the WMI path. Broadcast data works via
   patch 0115 (aireplay `--arpreplay` on broadcast).
3. **First frame after boot creates the hidden vdev** — takes ~400ms
   (msleep 150+150+100). Subsequent frames reuse it.
4. **Channel change requires teardown/recreate** of the hidden vdev.
   Auto-recreated on next injection if user changes channel.

## Files layout

- `mac.c.snapshot` — full ath10k mac.c with the port applied
- `wmi.c.snapshot` — with `status & 0x3` normalization
- `wmi-tlv.c.snapshot` — with `vdev_id` rewrite
- Other snapshots — supporting struct/mutex changes

## Next work

- Consider properly proper diff-based patch instead of snapshots (post-testing)
- Investigate RX filter for aireplay-ng -9 compatibility
- Test with mdk4, hcxdumptool
- Upstream to ath10k mailing list once stable
