# WIP: RAW HTT tx for monitor mgmt frames — DOES NOT WORK

Seventh session (2026-09-05). Quick test after parking Loukious port.

## Approach

Simplest possible: route ALL monitor-vif frames (data + mgmt) through the
HTT RAW tx path (`ATH10K_HW_TXRX_RAW` → `ATH10K_MAC_TX_HTT` →
`ath10k_htt_tx()`). Same path aireplay-ng data injection uses via
`kernel/patches/0115-ath10k-allow-raw-tx-on-monitor-vdev-for-WCN3990.patch`.

The theory (Agent C, seventh session): fw treats the RAW frame as opaque
802.11 blob, radiates via WAL data-tx classifier. Bypasses the WMI mgmt-tx
opmode allow-list entirely.

## Change

Single 3-line change in `ath10k_mac_tx_h_get_txmode()` (mac.c around 3775):
remove the `if (ath10k_mon_mgmt && ieee80211_is_mgmt(fc)) return
ATH10K_HW_TXRX_MGMT;` clause. Just return `ATH10K_HW_TXRX_RAW` for any
monitor vif regardless of frame type.

## Result

**Firmware crashed** on mgmt-frame injection within ~2 seconds:
```
ath10k_snoc c800000.wifi: failed to transmit packet, dropping: -108
ath10k_snoc c800000.wifi: failed to enable wcn3990: -22
ath10k_snoc c800000.wifi: firmware crashed! (guid ...)
```

The RAW HTT path is fine for **data** frames on monitor (aireplay-ng
works) but the fw does not accept mgmt frame_control on the RAW data
tx classifier — it either asserts on it directly, or the peerless
mgmt-frame path down in the WAL data classifier has a NULL-deref.

## Files

- `mac.c.snapshot` — full mac.c with only the get_txmode change

## Reverted

Removed the change; module reverted to `.prev-bak` (685938dc) which has
patch 0117/0118 shipped features (mgmt-rx capture + spoofed addr2)
plus the previous rhodep_inject reroute-to-0x7006 hook. Fw stays pristine.
