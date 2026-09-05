# WIP: Loukious "hidden STA vdev" port to ath10k mainline — NOT WORKING

Seventh session (2026-09-05). Parked snapshot; approach did not close.

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

## Port progress

1. Added `struct ath10k::rhodep_inject { created, vdev_id, mac_addr,
   chanfreq, mutex }` in core.h.
2. Added `ath10k_rhodep_ensure_inject_vdev()`, `ath10k_rhodep_inject_vdev_for_mon()`,
   `ath10k_rhodep_inject_teardown()` in mac.c.
3. Hook in `ath10k_mgmt_over_wmi_tx_work()`: for monitor-vif frames, call
   ensure_inject_vdev with current chanctx freq, use `ath10k_mac_get_any_chandef_iter`
   to find channel (userspace-mon0 vif has NULL chanctx; mac80211 assigns to
   internal hidden monitor sdata).
4. Rewrite `cmd->vdev_id` in `ath10k_wmi_tlv_op_gen_mgmt_tx_send()`.
5. Replaced msleep(150/150/100) with real `ath10k_vdev_setup_sync()` and
   `ath10k_wait_for_peer_created()` waits.

## What works
- Hidden vdev created OK on first boot (msleep or waits both fine).
- Channel detection via chanctx iterator works (freq=2462 correctly).
- Frames sent to fw with rewritten vdev_id.
- First test run right after boot: `status=0` for all frames (fw said OK).
  User's monitor phone was not observed at the time to confirm OTA.

## What broke

Second and subsequent tests: `status=1 (DISCARD)` on all frames. Phone did
NOT drop. Extended `wmi_tlv_mgmt_tx_cmd` with `tx_params_valid/tx_flags/peer_rssi`
fields and appended trailing `wmi_tlv_tx_send_params` TLV (both present in
qcacld send_mgmt_cmd_tlv) — this **crashes the fw immediately on any mgmt-tx**
(from any vdev, not just monitor), leading to firmware crash loop with
"firmware crashed" events every ~8s. Reverted the TLV extension.

## Unresolved theories (for next attempt)

1. **First-run status=0 was luck / stale state**, not proof of radiation.
   Would need TP-Link witness to verify OTA.
2. **DISCARD on subsequent runs**: fw may need VDEV_UP with a real BSSID to
   fully "activate" the vdev for tx scheduling, despite Loukious's comment
   claiming otherwise on WCN3998.
3. **WCN3990 firmware differs from WCN3998**: Loukious's Ghidra findings
   might not match our fw exactly. He targeted `FUN_b000fc10 _wlan_send_mgmt_to_host`;
   our RE identified `_wlan_mgmt_tx_send @ 0xb0013544` and
   `wlan_mgmt_tx_send_wmi_cmd_handler @ 0xb0013b78` as different cmd handlers
   (0x7006 vs 0x7008). The opmode check exists but downstream code path
   diverges.
4. **Peer type**: Loukious uses `WMI_PEER_TYPE_DEFAULT`; maybe WCN3990 wants
   `WMI_PEER_TYPE_BSS` for a self-peer on STA vdev to be routable for TX.
5. **The `tx_send_params` TLV IS required by qcacld's `send_mgmt_cmd_tlv`**
   but the WCN3990 fw likely uses a stricter TLV format that rejects the
   extra fields we appended. Perhaps only the trailing TLV (12 bytes) is
   needed, without extending the fixed_param struct.

## Files

- `mac.c.snapshot` — has rhodep_inject helpers (ath10k_rhodep_ensure_inject_vdev
  etc) around line 4348 + hook in ath10k_mgmt_over_wmi_tx_work.
- `mac.h.snapshot` — exports for ath10k_rhodep_inject_vdev_for_mon +
  ath10k_rhodep_inject_teardown.
- `core.h.snapshot` — struct ath10k rhodep_inject field.
- `core.c.snapshot` — mutex_init on init.
- `wmi-tlv.c.snapshot` — vdev_id rewrite in gen_mgmt_tx_send + extended TLV
  fields (the "extended" part is what crashes fw — revert if reusing).
- `wmi-tlv.h.snapshot` — extended struct wmi_tlv_mgmt_tx_cmd (fields
  tx_params_valid/tx_flags/peer_rssi) + new struct wmi_tlv_tx_send_params.
  **These extensions crash fw — do NOT reuse verbatim.**

## Next attempt (out of scope for this session)

Try approach without extending wmi_tlv_mgmt_tx_cmd:
1. Keep the original 6-field struct unchanged.
2. Only append the trailing `wmi_tlv_tx_send_params` TLV (12 bytes).
3. See if fw accepts that intermediate form.
4. If still DISCARD, try adding VDEV_UP to the hidden STA vdev.
5. If still DISCARD, try `WMI_PEER_TYPE_BSS` instead of DEFAULT.
6. If still DISCARD, more Ghidra on WCN3990 fw to find exactly what the
   0x7008 handler dereferences after the opmode gate for STA vdevs.

## What we're moving to (next session)

Approach 3 (from Agent C's analysis in item 13 fifth-session): route mgmt
frames from monitor vif through the RAW HTT tx path (`ATH10K_HW_TXRX_RAW`
→ `ATH10K_MAC_TX_HTT` → `ath10k_htt_tx()`). Same path aireplay-ng data
injection uses successfully via patch 0115. No fw patch, no vdev tricks;
just re-route the txmode selector in `ath10k_mac_tx_h_get_txmode()`.
