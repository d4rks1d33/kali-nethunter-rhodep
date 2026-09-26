# Next steps — NFC card emulation (rhodep)

State 2026-09-26. The 0x3B power-byte fix is built and flashed. Card emulation
still not confirmed activating in LISTEN mode; two new NCI errors surfaced.

## Where it stands
- Chip: Samsung S3FWRN5/S3NRN4V @ i2c 0x27, driver s3fwrn5. Reading works.
- Diagnosis (triangulated, see research/nfc/ and docs/nfc-wip/): emulation is a
  pure-NCI problem, not a missing RF profile. Firmware has NFC-A listen; blobs are
  identical to the vendor's.
- Applied: patch 0111 NCI_LMRT_POWER_STATE_ALL 0x3f -> 0x3b (built, flashed).
- Live test (live-logs/): with the 0x3B kernel, `rhodep-nfc listen`:
  - CORE_SET_CONFIG (3rd one, opcode 0x2 plen 5) -> **status 0x9** (rsp plen 3 =
    reports the failing tag). A rejected config tag.
  - NFCEE_MODE_SET (0x201) -> **status 0x3** (rejected) — the eSE enable from
    patch 0113.
  - RF_SET_LISTEN_MODE_ROUTING and RF_DISCOVER -> status 0x0.
  - No LISTEN-mode RF_INTF_ACTIVATED_NTF confirmed (runs kept getting cut by SSH
    drops; a poll-mode activation was seen reading the external reader).

## Next steps (in order)
1. **SSH-drop-proof test.** The on-device script died at `systemctl stop neard`
   (systemctl can hang). Rewrite to `pkill -9 neard` (no systemctl), flush per
   line, run under nohup/setsid detached from the SSH session; read the log after.
   Or run over USB (172.16.42.1) which is steadier when WiFi flaps.
2. **Resolve CORE_SET_CONFIG status 0x9.** Decode the rsp (plen 3) to see WHICH
   LA_* / TOTAL_DURATION tag the chip rejects, and drop/fix it. status 0x9 =
   NCI_STATUS_MESSAGE_SIZE_EXCEEDED or invalid-param depending on part; the rsp's
   3rd byte names the failing tag id. A rejected config tag can block activation.
3. **Resolve NFCEE_MODE_SET status 0x3 (rejected).** The eSE (NFCEE 0x83) enable
   is rejected. Check: is 0x83 the right eSE id here? The LineageOS vendor conf has
   OFFHOST_ROUTE_ESE={82} and OFFHOST_ROUTE_UICC={83} — i.e. the eSE may be **0x82**,
   not 0x83. Re-read the NFCEE_DISCOVER_NTF to confirm the eSE id and its supported
   protocols, and MODE_SET the correct one. (patch 0112/0113.)
4. **If still no listen activation: the command-order fix.** Reorder to
   SET_CONFIG -> RF_DISCOVER_MAP -> LMRT -> RF_DISCOVER (0111 sends LMRT before
   MAP). More invasive in nci_start_poll.
5. Verify with the physical reader (Flipper/phone): a LISTEN activation shows
   activation_rf_tech_and_mode with bit 0x80 set (0x80..0x83), vs 0x00 for poll.

## Key data
- eSE id ambiguity: vendor conf says OFFHOST_ROUTE_ESE={82}. Our 0112 found 0x83
  with protocol MIFARE. Reconcile live from NFCEE_DISCOVER_NTF.
- Vendor listen power byte 0x3B (applied). Vendor routing all to 0x83 by default.
- Full HAL + firmware + configs preserved in research/nfc/vendor-lineage/.
