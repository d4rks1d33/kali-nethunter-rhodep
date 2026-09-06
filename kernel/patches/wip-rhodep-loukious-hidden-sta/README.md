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

## Still open (next session)

1. **`aireplay-ng -9` broadcast probe test gets 0 answers.** The frames go
   out with `status=0` and no oops, but no AP replies. Directed deauth
   works, so radiation is happening — but broadcast probe requests may be
   dropped by fw before hitting the air (WCN3990 has stricter opmode
   filtering than WCN3998 for broadcast SA). Deprioritized because deauth
   is enough for the immediate use case.
2. **Cosmetic bug**: `rhodep inject: helper vdev N ready (mac=(null))` when
   the fallback path is taken (invalid monitor MAC). `mon_mac` variable is
   never assigned in the `goto have_mac` path. Trivial fix: set
   `mon_mac = inj_mac;` before the goto.
3. **DKMS/patch export**: current form is snapshots; needs to be diffed
   against upstream 7.2-rc5 and split into ordered `01xx-*.patch` files
   before it can go in `kernel/patches/` proper.
4. **fw-crash recovery**: `ath10k_core_restart` should call
   `ath10k_rhodep_inject_teardown()` so the flag is cleared before fw
   reboots and the old vdev id becomes stale. Not implemented yet.

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
