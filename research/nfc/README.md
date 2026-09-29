# NFC on rhodep (Moto G82 5G) — card emulation research

Everything for making NFC **card emulation (listen mode)** work under mainline
Linux on rhodep. Reading tags (poll mode) already works; emulation is the open
item. The target use case is emulating a transit card (SUBE) provisioned in the
phone's embedded secure element (eSE).

Kept separate from the modem research (`../modem-blob/`).

## The chip (confirmed)

Samsung **S3FWRN5 / S3NRN4V** (chip code 0x86), on i2c 0x27 (`2-0027`), driver
`s3fwrn5_i2c`. Confirmed in the vendor DT (`blair-rhodep-common-overlay.dtsi`:
`sec-nfc@27`) and live. The `st,st21nfc` seen in some Motorola DTs is the
**blair-milanf** variant, NOT rhodep. GPIOs: VEN=48, FIRM=8, IRQ=9, clk_req=7.

## The diagnosis (triangulated by 3 sources)

Card emulation accepts the full NCI listen setup (RF_SET_LISTEN_MODE_ROUTING and
RF_DISCOVER return status 0x0) but historically never raised
RF_INTF_ACTIVATED_NTF on NFC-A. The cause is **NOT** a missing RF listen profile:
- The chip firmware (`sec_s3nrn4v_firmware.bin`, Cortex-M/Thumb-2, v3.65, SLSI)
  HAS the full NFC-A listen card path built in (LA_ tags, REQA/WUPA, SEL_REQ,
  RATS). Not gated in firmware. (findings/firmware_re.md)
- The RF blobs (hwreg/swreg) are byte-identical to ours (md5); nobody flashes the
  chip firmware (FW_UPDATE_MODE=0). (findings/swreg_rfreg_analysis.md)
- The Samsung HAL sends no proprietary listen-enable command; the listen NCI
  stream comes from AOSP libnfc-nci. (findings/hal_listen_re.md)

The concrete deltas vs the vendor (findings/vendor_listen_sequence.md):
1. **listen-mode routing power byte 0x3B, not 0x3f** — the chip accepts 0x3f
   (status 0) but never activates for it. **This fix is applied** (patch 0111,
   NCI_LMRT_POWER_STATE_ALL 0x3f->0x3b) and built into the flashed kernel.
2. command order should be SET_CONFIG -> RF_DISCOVER_MAP -> LMRT -> RF_DISCOVER
   (still to try if 0x3B alone is insufficient).
3. eSE routing: route NFC-A technology + MIFARE protocol to NFCEE 0x83, power
   0x3B, after NFCEE_MODE_SET(0x83, ENABLE).

## State at last session (2026-09-27) — RF listen front-end CONFIRMED to activate

Major progress. The listen front-end **does light up** — the historical blocker
was a **missing RF_DISCOVER_MAP for listen mode** (the MAP was only ever sent in
nci_open_device, never inside the listen window). With the MAP added + the accumulated
fixes, the chip raised **RF_INTF_ACTIVATED_NTF repeatedly** as NFC-F LISTEN
(activation_rf_tech_and_mode 0x82, rf_protocol 0x5 NFC-DEP, rf_interface 0x1 FRAME).
So it is **NOT** an RF/vendor wall.

Fixes resolved this session:
- SET_CONFIG status 0x9 = the `LF_T3T_FLAGS` tag (T3T incoherent without
  LF_T3T_IDENTIFIERS). **Removed** (patch 0113). Now both SET_CONFIGs return 0x0.
- NFCEE_MODE_SET status 0x3 = re-enabling an already-enabled eSE. Made
  **conditional** (skip if ese_enabled==0x01) (patch 0113).
- power byte 0x3B applied (patch 0111).

The remaining wall — the DEP-vs-eSE conflict:
- The S3FWRN5 only powers the listen front-end when the **NFC-F/NFC-DEP peer is
  armed**. Suppressing DEP (LF_PROTOCOL_TYPE=0 + LMRT without NFC-F/DEP routes) ⇒
  **0 activations** (both host and eSE paths).
- RE of the vendor HAL (findings/hal_ese_coexist.md) said the working LMRT has
  **5 entries**: NFC-A/MIFARE/ISO-DEP → eSE 0x83 @0x3B, and NFC-F tech + NFC-DEP
  proto → host 0x00 @0x3F (the last two prime the analog front-end). An earlier
  fix had wrongly dropped the F/DEP entries (plen 17). **Restored to 5 entries**
  (plen 27, confirmed on the wire) — patch 0113 + findings/lmrt_5entry_fix.md.
- But live: **still 0 activations** with the 5-entry LMRT + `LF_PROTOCOL_TYPE=0`.
  Strong hypothesis: **LF_PROTOCOL_TYPE=0 itself kills the activation** — the chip
  needs the NFC-DEP peer actually armed (not just routed) to light the front-end.
  Conflict: can't "prime the RF via NFC-F without an active DEP peer".

### Next session (decided with user) — test 5-entry LMRT WITHOUT LF_PROTOCOL_TYPE=0
Keep the DEP peer armed (do NOT set LF_PROTOCOL_TYPE=0), keep the 5-entry LMRT that
routes NFC-A → eSE, and with a reader present check whether: (a) the chip activates
(expected yes, as NFC-F/DEP), and (b) the reader's NFC-A field routes to the eSE so
it sees the SUBE MIFARE — i.e. does the eSE win the NFC-A layer while the DEP peer
is up, or does the DEP peer always win? This is the decisive experiment. If DEP
always wins, the fallback is an agent RE of whether the chip can activate NFC-A
(→eSE) and NFC-DEP simultaneously at all, or if they're mutually exclusive.

Also queued: NFCEE_POWER_AND_LINK_CTRL (`21 03 02 83 03`) — flagged UNKNOWN in the
HAL RE as a possible missing piece for the eSE to answer.

### Phone state (left ready to power off)
- `nci.ko` with the 5-entry LMRT is installed **immutable** (chattr +i) at
  `/lib/modules/7.2.0-rc5/kernel/net/nfc/nci/nci.ko` (md5 `86128efa1e461694e1abadd925a21596`,
  = kernel-modules/nci-lmrt5-emulate_host.ko). It persists across reboot. Loaded
  in default (poll) mode, neard running, NFC reads tags OK.
- To reinstall after a kernel reflash: chattr -i, cp the saved .ko, chattr +i,
  depmod -a 7.2.0-rc5. To test eSE listen: `modprobe nci emulate_host=0`.
- Latest boot image: `out/rhodep-nfc-lmrt5.img` (only needed if reflashing kernel;
  the nci.ko is a module, install it separately per above).

### Earlier this session (superseded, for context)
Flashed with the 0x3B fix; the listen initially hit SET_CONFIG 0x9 and
NFCEE_MODE_SET 0x3 (both now resolved above). SSH/WiFi kept dropping during listen
(the LISTEN mode cuts WiFi) — solved by running everything detached with logging to
/tmp/nfctest/ and reading logs after reconnect.

## Layout

- `vendor-lineage/` — the Android NFC HAL extracted byte-exact from the LineageOS
  rhodep OTA (payload.bin -> vendor.img ext4): chip firmware, RF blobs, HAL .so,
  service binary, configs, init.rc. `super-extract-raw/` = the earlier raw dd
  fragments from the on-device super partition (superseded by the clean LineageOS
  extraction) + lp_extract.py.
- `findings/` — the RE reports (also mirrored in the repo `docs/nfc-wip/`).
- `scripts/` — `rhodep-nfc` (userspace NCI tool: read/watch/raw/listen/info) and
  `rhodep-nfc-listen-test.sh` (on-device listen test with logging).
- `live-logs/` — on-device NCI captures (nfc_raw.log has the frame dump; nfc.pcap
  for Wireshark). This session's runs: ese_dmesg.log, reader_dmesg.log,
  lmrt5_dmesg.log (the 5-entry LMRT run, plen 27).
- `kernel-modules/` — `nci-lmrt5-emulate_host.ko`: the built nci.ko with the
  5-entry LMRT + eSE routing + emulate_host param (md5 86128efa…), matches the
  immutable copy installed on the phone. Reinstall via chattr -i/cp/chattr +i/depmod.

## How to reproduce the extraction (LineageOS vendor)

    unzip -o lineage-...-rhodep-signed.zip payload.bin
    python3 dump_payload.py payload.bin vendor vendor.img   # A/B OTA extractor
    sudo mount -o ro,loop vendor.img /mnt
    # HAL: /mnt/lib64/nfc_nci_sec.so, /mnt/bin/hw/android.hardware.nfc-service.sec
    # fw:  /mnt/firmware/sec_s3nrn4v_firmware.bin
    # cfg: /mnt/etc/libnfc-nci.conf, /mnt/etc/libnfc-sec-vendor.conf
