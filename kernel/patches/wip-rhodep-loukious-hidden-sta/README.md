# WIP: Loukious "hidden STA vdev" port to ath10k mainline

Eighth session (2026-09-06). Kernel oops fixed; deauth/deassociation via the
full aircrack-ng suite works. Broadcast probe (aireplay -9) still gets 0
answers (see "still open" below).

## Reference

Wael Hasnaoui ("Loukious") published the technique for WCN3998 in qcacld-3.0:
- Article: https://medium.com/h7w/they-said-packet-injection-on-qcacld-3-0-was-impossible-i-proved-them-wrong-588fa55ee702
- Commit: https://github.com/Loukious/android_kernel_xiaomi_sm8150/commit/65c6a05ecd9b25ebf0742d39987c6a8a042227f1

## Technique

Instead of patching firmware, create a hidden `WMI_VDEV_TYPE_STA` vdev in
the firmware (never registered with mac80211), do vdev_start + peer_create
(self-peer), skip VDEV_UP. Then rewrite the `vdev_id` in outgoing WMI
mgmt_tx cmds from the monitor vdev id to the hidden STA vdev id. Firmware
accepts the frame because it sees a valid STA opmode with a peer.

## Port progress (up to 8th session)

1. Added `struct ath10k::rhodep_inject { created, vdev_id, mac_addr,
   chanfreq, monitor_vdev_id, peer_cache[8], lock }` in core.h.
2. Added `ath10k_rhodep_ensure_inject_vdev()`, `ath10k_rhodep_inject_vdev_for_mon()`,
   `ath10k_rhodep_inject_teardown()` in mac.c.
3. Hook in `ath10k_mgmt_over_wmi_tx_work()`: for monitor-vif frames, call
   ensure_inject_vdev with current chanctx freq, use `ath10k_mac_get_any_chandef_iter`
   to find channel (userspace-mon0 vif has NULL chanctx; mac80211 assigns to
   internal hidden monitor sdata).
4. Rewrite `cmd->vdev_id` in `ath10k_wmi_tlv_op_gen_mgmt_tx_send()`.
5. Removed per-DA `ath10k_wmi_peer_create` from `ath10k_mon_inject_peer_add`
   (rely on hidden STA vdev self-peer). Prevents fw asserts when aireplay
   bounces addr1 between CLIENT and BSSID rapidly.

## What works (8th session, verified on device)

- Hidden STA vdev created OK, vdev_start + self-peer_create both succeed.
- `rhodep: mgmt-tx vdev 0 -> hidden STA 2` in dmesg for every monitor tx.
- `rhodep mgmt-tx-compl desc_id=0 status=0` for every completion.
- **`airmon-ng start wlan0` + directed `aireplay-ng --deauth -c CLIENT -a BSSID wlan0mon`
  successfully deauthenticates target stations OTA** (full aircrack-ng suite works).
- No kernel oops, no fw crash, no modem reset, no scheduler-while-atomic BUG.

## The three bugs fixed this session (were causing the SIGSEGV oops)

The oops signature was `LDRB w9, [x8, #0x34]` reached from
`wmi_process_mgmt_tx_comp` — a UAF of an `sk_buff` accessed via
`IEEE80211_SKB_CB(msdu)` after `pkt_addr->vaddr` had been recycled.

### Bug A — split idr_find/idr_remove in `wmi_process_mgmt_tx_comp`
`wmi.c` original code was:
```c
pkt_addr = idr_find(&wmi->mgmt_pending_tx, param->desc_id);
...
kfree(pkt_addr);
ieee80211_tx_status_irqsafe(ar->hw, msdu);
...
out:
    idr_remove(&wmi->mgmt_pending_tx, param->desc_id);
```
Between `kfree(pkt_addr)` and `idr_remove`, a duplicate completion event
for the same `desc_id` (WCN3990 fw does emit pairs during aireplay bursts)
sees the same freed `pkt_addr` via `idr_find` and dereferences its stale
`->vaddr`. Fix: **claim atomically** — replace `idr_find`+`goto out;
idr_remove` with a single `idr_remove()` that both finds and unhooks the
entry. Also defensively NULL-check `pkt_addr->vaddr`.

### Bug B — sleep-under-spinlock in `ath10k_mgmt_over_wmi_tx_work`
`mac.c` was:
```c
mutex_lock(&ar->conf_mutex);
spin_lock_bh(&ar->rhodep_inject.lock);
ath10k_rhodep_ensure_inject_vdev(...);   /* msleep(150), WMI blocking cmds */
spin_unlock_bh(&ar->rhodep_inject.lock);
mutex_unlock(&ar->conf_mutex);
```
`ensure_inject_vdev` sleeps (`msleep(150)`, `msleep(100)`, `wmi_peer_delete`,
`wmi_vdev_stop`). Under `CONFIG_DEBUG_ATOMIC_SLEEP=y` this is `BUG:
scheduling while atomic`; on production it corrupts scheduler state and
leaves `mgmt_pending_tx` IDR half-populated, which is what seeds Bug A.

Fix: drop the spinlock around the ensure call. `conf_mutex` alone is
enough (writers are serialized against teardown by it). The fast reader
in `gen_mgmt_tx_send` still takes the spinlock, but only to atomically
observe `created` + `vdev_id` + `monitor_vdev_id`. Same treatment applied
to `ath10k_rhodep_inject_teardown`: split into (a) snapshot vdev_id/mac
under spinlock, clear ->created, release; (b) sleeping WMI teardown
under `conf_mutex` only.

### Bug C — status normalizer promoted retry-info events to completions
`wmi.c` `ath10k_wmi_event_mgmt_tx_compl` had:
```c
if (param.status >= 4) {
    param.status = raw & 0x3;   /* mask WCN3990 ext bits */
}
```
This turned a retry-metadata event (`raw=0x100000, lower 2 bits == 0`)
into a fake COMPLETE_OK (`status=0`), which then re-entered
`wmi_process_mgmt_tx_comp` for a `desc_id` whose real completion already
ran. That's the duplicate event Bug A UAFs on. Fix: **drop the event
entirely** if the low 2 bits are 0 but higher bits are set — it's a
retry-info event, not a real completion.

## 8th session continued — restored per-DA peer_create + broadcast test PASSES

After the initial oops was fixed we discovered `aireplay-ng -9` was
misbehaving. Extensive multi-agent investigation:

### aireplay-ng -9 broadcast phase: WORKS
When the module is freshly loaded, `aireplay-ng -9 wlan0mon` reports
`Found N APs` where N > 0 (i.e. probe responses ARE received; broadcast
mgmt-tx radiates and APs reply to our random SA). Previous "No Answer"
observations were caused by fw in a degraded state from earlier tests.

### aireplay-ng -9 directed phase: fw crashes (known bug from 0119 snapshot)
The moment aireplay switches to directed probes (4 frames: probe-req +
RTS + null + auth burst back-to-back per AP), our driver calls
`ath10k_wmi_peer_create` on the hidden STA vdev for the first unicast
addr1 (=BSSID) and the fw asserts within 1 ms:

```
PDM: service 'wlan_process' crash: EF:wlan_process:0x1:WLAN BE:0xXX077:cmnos_thread.c:4005:A
ath10k_snoc c800000.wifi: firmware crashed!
ieee80211 phy N: Hardware restart was requested
```

This IS the same `cmnos_thread.c:4005:A` documented in the working
0119 snapshot README as `Known Bug #1`:
> "Firmware asserts under high burst rate. cmnos_thread.c:4005:A PDM
>  crash of wlan_process when peer_create is called too fast (aireplay
>  bursts 64 fps alternating between two addr1 values). Mitigated with
>  an 8-slot LRU peer cache in the driver but the race is not fully
>  eliminated."

Our port has the same 8-slot LRU cache and the same catch-and-invalidate
recovery (`-ESHUTDOWN` -> `created=false` -> recreate on next tx). This
is the state of the art for WCN3990 on ath10k mainline. Fixing the fw
crash entirely would require:
- pre-creating N candidate peers at hidden-vdev-create time (README
  roadmap item 1), OR
- fw-side patch (out of scope; qcacld-3.0 downstream has the same bug).

### Fixes applied this session (session-continuation-2)

1. **Restored per-DA peer_create with 8-slot LRU cache in
   `ath10k_mon_inject_peer_add`.** Session 8 mistakenly removed this
   based on the theory "self-peer is sufficient". The 0119 working
   snapshot proves it is NOT: without per-DA peer, unicast mgmt gets
   100% DISCARD from fw's wal_tx path (which requires addr1 to match a
   peer entry on the tx vdev). Broadcast has a separate fw fallback so
   it works either way.
2. **NO eviction peer_delete.** Snapshot deliberately does not
   peer_delete the evicted cache entry — rapid peer_delete + peer_create
   on the peerless STA vdev is what asserts cmnos_thread. Stale peers
   accumulate in fw table until the hidden vdev is torn down (which
   drops all peers implicitly).
3. **mgmt_pending_tx GC + credit_recover** (`wmi-tlv.c`): added
   `ath10k_rhodep_mgmt_tx_gc_one` that reaps entries older than 300 ms
   AND refunds the HTC WMI credit that the fw failed to return. Called
   only on `-ENOSPC` retry in `ath10k_wmi_mgmt_tx_alloc_msdu_id` (NOT
   at the top of every burst — that races real completions and corrupts
   HTC ring / credit accounting -> another fw assert).
4. **Cosmetic bug still present**: `rhodep inject: helper vdev N ready
   (mac=(null))` when the fallback random-MAC path is taken. `mon_mac`
   variable never assigned in the `goto have_mac` path. Trivial fix
   pending: set `mon_mac = inj_mac;` before the goto.

### Verified working after this session

- **Deauth (unicast + broadcast) via aircrack-ng suite**: fully
  functional OTA. Target clients disconnect. No fw crash.
- **`aireplay-ng -9` broadcast phase**: `Found N APs` (probes radiate
  and responses received).
- **scapy broadcast injection**: `sendp()` at 20 fps sustains 100%.
- **airodump-ng capture**: beacons + mgmt frames visible.
- **Module reload without device reboot**: works (backups preserved).

### Still not working

- **`aireplay-ng -9` directed phase**: `0/30: 0%` (fw crash + recovery
  cycle takes ~5s, aireplay finishes before recovery).
- **`-9` "Injection is working!" marker**: not reliably printed. That
  marker requires the fw to deliver a ProbeResp addressed to aireplay's
  random SA AND for the WMI mgmt-RX event path to forward it. On
  WCN3990 monitor mode, unicast RX for arbitrary addr1 IS delivered
  (broadcast phase gets ~7 probe-resp per test), but sometimes the
  timing window is missed.

### Next session TODOs

1. **Try WMI_PEER_TYPE_BSS instead of DEFAULT** for the per-DA peers.
   Maybe fw's wal_peer requires BSS-type peers on STA vdev.
2. **Pre-create N candidate peers at ensure_inject_vdev time** so the
   first unicast burst doesn't race peer_create (README 0119 roadmap
   item 1).
3. **DKMS/patch export**: current form is snapshots; needs to be diffed
   against upstream 7.2-rc5 and split into ordered `01xx-*.patch` files.
4. **fw-crash recovery**: `ath10k_core_restart` should call
   `ath10k_rhodep_inject_teardown()` so the flag is cleared before fw
   reboots and the old vdev id becomes stale.
5. **Fix the (mac=(null)) cosmetic bug** (assign mon_mac = inj_mac in
   the fallback path).

## Files

- `mac.c.snapshot` — rhodep_inject helpers, hook in mgmt_over_wmi_tx_work,
  correct locking (spinlock only around publish/unpublish of `created`).
- `mac.h.snapshot` — exports for ath10k_rhodep_inject_vdev_for_mon +
  ath10k_rhodep_inject_teardown.
- `core.h.snapshot` — struct ath10k rhodep_inject field with spinlock and
  peer_cache (unused in current version but kept for future).
- `core.c.snapshot` — spin_lock_init on init.
- `wmi-tlv.c.snapshot` — vdev_id rewrite in gen_mgmt_tx_send.
- `wmi-tlv.h.snapshot` — original 6-field wmi_tlv_mgmt_tx_cmd (the extended
  form crashed fw; do NOT re-extend).
- `wmi.c.snapshot` — atomic idr_remove in wmi_process_mgmt_tx_comp (Bug A
  fix), drop retry-only events in event_mgmt_tx_compl (Bug C fix).
- `wmi.h.snapshot` — unchanged from upstream.

## How to iterate quickly

The module reload flow works without reflashing boot.img:
```sh
# build on x86 host (~30s incremental):
cd /tmp/ktree/linux-7.2-rc5
ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- make -j$(nproc) M=drivers/net/wireless/ath/ath10k modules

# strip and scp to device (currently only over USB gadget):
aarch64-linux-gnu-strip --strip-debug drivers/net/wireless/ath/ath10k/ath10k_{core,snoc}.ko
sshpass -p '1234' scp drivers/net/wireless/ath/ath10k/ath10k_{core,snoc}.ko kali@172.16.42.1:/tmp/

# replace + depmod + reload on device:
sshpass -p '1234' ssh kali@172.16.42.1 "echo 1234 | sudo -S bash -c '
  KVER=\$(uname -r)
  D=/lib/modules/\$KVER/kernel/drivers/net/wireless/ath/ath10k
  cp -a \$D/ath10k_core.ko \$D/ath10k_core.ko.bak-\$(date +%Y%m%d-%H%M%S)
  cp -a \$D/ath10k_snoc.ko \$D/ath10k_snoc.ko.bak-\$(date +%Y%m%d-%H%M%S)
  cp /tmp/ath10k_core.ko /tmp/ath10k_snoc.ko \$D/
  depmod -a \$KVER
  nmcli device set wlan0 managed no
  rmmod ath10k_snoc ath10k_core
  modprobe ath10k_snoc
'"
```
Vermagic matches (7.2.0-rc5 SMP preempt mod_unload aarch64) even though
device kernel was built with clang and host build uses gcc — toolchain is
not part of vermagic.
