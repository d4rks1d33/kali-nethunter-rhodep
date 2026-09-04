# Firmware RE session — 2026-09-04

Session where we did the first real reverse-engineering + on-device patch test
of `wlanmdsp.mbn`, trying to open monitor-vdev TX so pwnagotchi can channel-hop
AND inject deauth from the same interface on the internal WCN3990. The blob is
git-ignored; only this note is tracked.

## What we did

1. **Multi-agent static analysis** of the 779k-instruction Hexagon disasm
   (`/tmp/opencode/fw/disasm.txt`) with three independent agents. All three
   converged on the same finding: a `switch(vdev->opmode)` gate in
   `_wlan_mgmt_tx_send` that only allows opmodes `{0=IBSS, 1=STA, 2=?, 6=AP}`
   to reach frame submission. MONITOR (=4) falls into a reject block at
   `0xb001392c` that logs `_wlan_mgmt_tx_send:691` and returns without ever
   queueing the frame to the WAL.

2. **Byte-level patch** to that gate at file offset `0x53660`:
   - Before: `04 e2 02 10 64 c6 52 10`  
     (`if (cmp.eq(r2,#0x2)) jump 0xb0013668` + `if (!cmp.eq(r2,#0x6)) jump 0xb001392c`)
   - After: `00 c0 00 7f 00 c0 00 7f`  
     (two single-instruction NOP packets; parse bits `11` in each word)
   - Effect: after the earlier `cmp.gt(r2,#1) → jump 0xb0013660`, control now
     falls straight through the two NOPs into the ALLOW block at
     `0xb0013668: r2 = add(r18,#0xc)`.

3. **Flashed patched firmware** to `/readonly/firmware/image/wlanmdsp.mbn`
   (remount rw), rebooted. Confirmed secure boot is still not enforced:
   patched md5 `dc7d5eed…` loaded, `ath10k_snoc` initialised without any
   auth/PIL/CRC failure, `wcn3990 hw1.0 target 0x00000008` OK, wlan0 back up
   and associated.

4. **On-device test** with external TP-Link witness in monitor on ch11,
   monitors-only config on phy0 (wlan0 STA deleted, mon0 created), 30
   deauth-broadcast injections with spoofed SA via `nl80211-mgmt-tx.py`.

## Result of test 1

| observation | before patch | after patch (this session) |
|---|---|---|
| kernel accepts NL80211_CMD_FRAME on mon0 | 0/40 (no wlan0 to source SA from) | **30/30** |
| firmware `mgmt-tx-compl` event returned | 16 × status=1 (discard) | **NONE returned at all** |
| frames heard by external TP-Link witness | 0/16 (fw dropped) | **0/30** |
| wlan0 stability post-flash | — | **stable, no crash, reassociates fine** |

**Interpretation.** We patched a gate — but not the *right* gate for this
path.

- The gate we found (`_wlan_mgmt_tx_send` at `0xb0013544`) sits behind the
  **WMI `MGMT_TX_SEND_CMD` (0x7008)** handler. That's the path our earlier
  successful deauth (from the STA vdev offchannel + `AUTH_AND_DEAUTH_RANDOM_TA`,
  patch 0118) went through: `iw`/`nl80211-mgmt-tx.py` → mac80211 offchannel →
  WMI `MGMT_TX_SEND` → firmware WMI handler → `_wlan_mgmt_tx_send` → …
  On a **STA** vdev, opmode=1 passes the allow-list and the frame radiates.
- A frame injected on a **monitor vif via netlink `NL80211_CMD_FRAME`** does
  NOT go through the WMI `MGMT_TX_SEND` handler in the driver at all — it
  goes through `ath10k_mac_op_tx` → `ath10k_mac_tx_h_get_txmode()` which
  unconditionally returns `ATH10K_HW_TXRX_RAW` for monitor vif → **HTT raw
  TX**, not WMI mgmt-tx.
- HTT raw TX from monitor was already known to crash the firmware
  (`cmnos_thread.c:4005`, README item 13). In this test the kernel-side
  offchannel wrapping avoided the crash (the frame reached firmware wrapped as
  an offchannel HTT TX, which just gets silently dropped when the vdev has no
  peer — no completion, no radiation).

So the patched gate is real and lifting it *would* let mgmt-tx succeed from a
monitor vdev **if the frame reached the WMI path**. But mac80211+ath10k routes
monitor-vif injection through the HTT raw path, which is a separate discard
that we haven't identified yet.

## What's needed next session

1. **Instrument or re-read `ath10k_mac_tx_h_get_txmode()` to route monitor-vif
   mgmt frames through `ATH10K_HW_TXRX_MGMT`** (already tried once — see
   `backups/wip-injection-session/README.md` — but with an unpatched firmware
   that then discarded with `status=1`). NOW with the fw patch, that combination
   might radiate.

2. **Alternative:** find the HTT TX discard gate in the firmware. Symbols in
   `backups/firmware-re-tools/fw-symbol-strings.txt` include
   `ar_wal_tx_q_enque_discard` (`0xb016c1d0`), `tx_cong_ctrl_discard_desc`
   (`0xb0183470`), `monitor_htt_tgt_rx_ll` (`0xb0137fbc`) — these are candidates
   for the HTT-side gate. Also `wal_local_frame_mgmt_tx_completion`
   (`0xb01524ec`) is the completion emitter — the WMI dispatcher must be
   ultimately calling it, and if it's not called it means the frame died even
   earlier.

3. **Third combined patch:** apply BOTH firmware patches AND the driver-side
   mgmt-tx reroute (from the WIP session on 2026-09-04). That combination
   wasn't tested — driver reroute alone gave status=1 (fw rejected because
   opmode≠allowed), firmware patch alone kept HTT raw discard silent. Together
   might work.

## Files

- `wlanmdsp.mbn.attempt1-nop-gate.mbn` — the patched image
  (md5 `dc7d5eed2a8f88274ddc3dd7c5b30fde`). Kept as reference so we don't
  redo the analysis next time.
- pristine still at `backups/firmware/wlanmdsp.mbn.rhodep-orig`
  (md5 `f377d18cae70e74610ce386713ecde5c`).

## Restore recipe (device side)

```sh
sudo mount -o remount,rw /readonly/firmware
sudo cp /root/wlanmdsp.mbn.rhodepbak /readonly/firmware/image/wlanmdsp.mbn
sudo sync && sudo reboot
# verify md5 == f377d18cae70e74610ce386713ecde5c
```

Done at end of session; wlan0 is back on pristine firmware.

## Big picture

We are **very close**:

- Passive mgmt capture on wlan0: ✅ (patch 0117)
- Deauth injection with spoofed AP addr2 from STA vdev: ✅ (patch 0118 +
  rhodep-inject-lab)
- Channel-hopping capture on mon0 alone: ✅ (rhodep-pwn-monstart-internal)
- Firmware IS patchable: ✅ (proven twice now)
- Firmware gate at `_wlan_mgmt_tx_send:691` mapped exactly: ✅
- Monitor-vdev raw-TX HTT gate: ❌ still unidentified
- Combined driver+firmware patch to route monitor-inject through WMI: ⏳ next
  session
