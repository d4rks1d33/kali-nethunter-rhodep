# WIP: WCN3990 monitor-vif mgmt-tx (0x7006 legacy path)

Sixth session (2026-09-04..05). Not yet a finished patch — parked here to preserve state.

## Goal
Make mgmt-tx frames from a monitor vdev actually radiate on WCN3990 by
routing them through WMI cmd `0x7006` (legacy `MGMT_TX_CMDID`) which we've
patched in the firmware to skip its opmode allow-list. The default 0x7008
`MGMT_TX_SEND` path unconditionally writes status=1 for non-STA/AP opmodes
and never submits the frame.

## Files
- `wmi-tlv.c.snapshot` — kernel source with new `ath10k_wmi_tlv_op_gen_mgmt_tx`
  (TLV-wrapped) + hook in `wmi_tlv_ops` table.
- `mac.c.snapshot` — kernel source with `ath10k_mgmt_over_wmi_tx_work` reroute
  that calls `ath10k_wmi_mgmt_tx()` (which goes through `gen_mgmt_tx` → 0x7006)
  when the vif is monitor and `ath10k_mon_mgmt` is set.

## Prerequisite
Firmware `wlanmdsp.mbn` must be patched: file offset `0x53660` NOP'd
(`04 e2 02 10 64 c6 52 10` → `00 c0 00 7f 00 c0 00 7f`). See item 13 in main
README + backups/firmware-re-session-20260904/.

## Fifth-session state (2026-09-04)
- First attempt: inline `wmi_mgmt_tx_cmd` layout (like non-TLV 10.x fw path)
- Result: fw crashes after 2 frames, driver reports `EAGAIN` then `ESHUTDOWN`,
  mac80211 triggers `ieee80211_restart_work` → hw restart
- Root cause per 3 agents' converging analysis:
  WMI-TLV firmware requires TLV-wrapped payload for 0x7006 even though the
  cmd id is "legacy" — the wmitlv parser rejects inline structs and corrupts
  fw internals, leading to a delayed assert.

## Sixth-session state (2026-09-05)
- Fixed to TLV-wrap: `[wmi_tlv{STRUCT_MGMT_TX_HDR}][hdr][wmi_tlv{ARRAY_BYTE}][frame]`
- Module compiled: md5 `13b957bccc73fdc4b102e7b0898b0c41`
- Deployed to device at `/lib/modules/7.2.0-rc5/kernel/drivers/net/wireless/ath/ath10k/ath10k_core.ko`
- TEST NOT COMPLETED — device USB dropped, likely because previous
  test-round left fw in bad state; need physical replug / `otg` reset
  before we can verify.

## Next attempt after replug
1. `md5sum` module on device (should be `13b957bccc73fdc4b102e7b0898b0c41`)
2. `md5sum` fw (should be `dc7d5eed2a8f88274ddc3dd7c5b30fde`)
3. Bring wlan0 down, create `mon0` on phy0 (monitors-only)
4. Deauth sustained for 60s against test AP; user observes their other phone
5. Expected outcomes:
   - Success: phone drops from AP; witness capture shows OTA deauths with
     spoofed AP MAC as SA
   - Fw still crashes: means post-NOP path in fw still dereferences a
     null/invalid peer for monitor opmode → need to also NOP the peer-load
     branch at `b001366c` (see Agent A findings in item 13 fifth-session)
   - Fw does not crash but no OTA: means fw accepted the cmd but silently
     dropped the frame → escalate to force `bss_peer=NULL` path or
     alternative wire format experiments

## Known-open concerns
- No known driver, upstream or vendor, has ever exercised the `.gen_mgmt_tx`
  slot on WMI-TLV. This is genuinely virgin territory.
- Even with TLV-wrapping correct, the fw's `_wlan_mgmt_tx_send` post-gate
  code path may not handle monitor opmode's uninitialized peer table
  cleanly — Agent A identified two additional NULL-deref candidates at
  `b0013850` (`memw(r19+0x20)` on `bss_peer`) and `b0013868`
  (`memw(r6+0x24)` on `peer_desc[idx]+0x328`). If TLV-wrap alone doesn't fix
  the crash, next step is to patch fw to force the `bss_peer==NULL`
  fallthrough branch at `b0013870`.
