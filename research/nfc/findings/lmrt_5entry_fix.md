# LMRT 5-entry fix for 0113 (S3FWRN5 / rhodep NFC port)

## Summary

The post-mi-fix `nci_rf_set_listen_mode_routing_req()` built only 3 entries
(NFC-A tech -> eSE, T2T -> host, MIFARE -> eSE; LMRT plen 17). On this
controller that table is accepted with status 0x0 but never lights the listen
front-end (matches `ese_dmesg.log:211`, plen17 -> 0 activations).

The corrected builder emits the SAME five entries the vendor NFA programs
(LMRT plen 27, `reader_dmesg.log:51`) -- the table that actually wakes the RF.

## The corrected builder (5 entries)

Order and semantics (from `hal_ese_coexist.md` §1b / §7):

| # | type  | value        | NFCEE (emulate_host=0) | NFCEE (emulate_host=1) | power |
|---|-------|--------------|------------------------|------------------------|-------|
| 1 | TECH  | NFC-A (0x00) | eSE 0x83               | host 0x00              | 0x3B  |
| 2 | PROTO | MIFARE(0x80) | eSE 0x83               | host 0x00              | 0x3B  |
| 3 | PROTO | ISO-DEP(0x04)| eSE 0x83               | host 0x00              | 0x3B  |
| 4 | TECH  | NFC-F (0x02) | host 0x00              | host 0x00              | 0x3F  |
| 5 | PROTO | NFC-DEP(0x05)| host 0x00              | host 0x00              | 0x3F  |

- Entries 1-3 use `nci_emu_ese_id(ndev) ? : NCI_NFCEE_ID_HOST`. When
  `emulate_host=1`, `nci_emu_ese_id()` returns 0, so they fall back to the host
  automatically (requirement 2 satisfied without a separate branch).
- Entries 4-5 are hard-wired to the host (0x00) at power 0x3F: they are RF
  primers, not offhost routes. LF_PROTOCOL_TYPE=0 (set on both paths in
  `nci_start_poll`) keeps the P2P peer suppressed so NFC-F only powers the analog
  front-end.
- ISO-DEP (entry 3) added (`DEFAULT_ISODEP_ROUTE=0x83`).
- `LF_T3T_FLAGS` is NOT sent (S3FWRN5 rejects it with status 0x9 when there are
  no matching LF_T3T_IDENTIFIERS -- documented in the 0113 SET_CONFIG comment).

## Field / byte order (the one thing that had to be verified, NOT assumed)

FACT -- `struct nci_lmrt_tech_entry` / `nci_lmrt_proto_entry`
(`include/net/nfc/nci.h`, added by patch 0111) are:

```c
struct nci_lmrt_tech_entry  { __u8 type, len, nfcee_id, power_state, technology; };
struct nci_lmrt_proto_entry { __u8 type, len, nfcee_id, power_state, protocol;   };
```

i.e. wire order = `type len nfcee_id power_state value`. This matches NCI 2.0
(routing-entry Value = Route(NFCEE-id), Power-State, RF-Technology/Protocol).

The RE doc (`hal_ese_coexist.md:59,76-88`) tabulates the two middle octets
power-state-FIRST (`00 03 3B 83 00`). That is a documentation transcription
convention, NOT the wire order -- it is explicitly marked INFERENCE there, while
the struct is spec-conformant real code. The *semantics* the doc asserts
(eSE = 0x83, power = 0x3B / 0x3F) are authoritative and are preserved exactly.

### Resulting on-air bytes

emulate_host=0 (eSE path):
```
21 01 1B 00 05  00 03 83 3B 00  01 03 83 3B 80  01 03 83 3B 04  00 03 00 3F 02  01 03 00 3F 05
```
emulate_host=1 (host path):
```
21 01 1B 00 05  00 03 00 3B 00  01 03 00 3B 80  01 03 00 3B 04  00 03 00 3F 02  01 03 00 3F 05
```
plen = 0x1B = 27, num_entries = 5 -- matches the live `reader_dmesg.log` plen27
capture. Byte-for-byte these differ from the RE string only in the nfcee/power
octet order (`83 3B` vs the doc's `3B 83`), for the reason above; the values are
identical.

## New macro

`include/net/nfc/nci.h`:
```c
#define NCI_LMRT_POWER_STATE_ALL  0x3b   /* eSE/offhost routes */
#define NCI_LMRT_POWER_STATE_RF   0x3f   /* NFC-F/NFC-DEP host RF primer */
```

## Patch validation

Reconstructed tree: `linux-motorola-rhodep-7.2_rc5.tar.gz` + patches 0001..0112
(source= order from the pmaports APKBUILD). Reconstructed `net/nfc/nci/core.c`
is byte-identical to the provided `core.c.with0112` (verified with diff).

`patch -p1 --dry-run < 0113-...patch` on that tree:
```
checking file include/net/nfc/nci.h
checking file include/net/nfc/nci_core.h
checking file net/nfc/nci/ntf.c
checking file net/nfc/nci/core.c
```
All hunks apply with NO fuzz and NO offset. All 9 hunk headers verified: for each
`@@ -X,Y +A,B @@`, computed (context+del)=Y and (context+add)=B. The core.c LMRT
hunk is `@@ -907,60 +928,94 @@` (old 60 / new 94, confirmed).

The full series (0001..0125, with the corrected 0113) applies with only 4
pre-existing fuzz warnings on UNRELATED patches (0062 drm-panel, 0076 remoteproc,
0108 dts, 0115 ath10k) -- none touch NFC; 0113 itself is clean.

## FACT / INFERENCE / UNKNOWN

FACT
- Struct wire order is `type len nfcee_id power_state value` (nci.h, patch 0111).
- eSE NFCEE id = 0x83, MIFARE protocol = 0x80 (live NFCEE_DISCOVER_NTF).
- Vendor HAL sends only a CLEAR LMRT (`21 01 02 00 00`); NFA builds the table.
- 3-entry table (plen17) -> 0 activations; 5-entry table (plen27) -> chip
  activates NFC-F listen. (ese_dmesg.log / reader_dmesg.log.)
- OFFHOST_AID_ROUTE_PWR_STATE = 0x3B (libnfc-nci.conf).
- Corrected 0113 applies with no fuzz/offset; hunk headers arithmetically exact.

INFERENCE
- The exact five-entry content/order (which tech/proto to which NFCEE at which
  power) reproduces the vendor plen27 table; reconstructed from conf + live plen,
  not from a raw payload hexdump (the capture logs plen only, not the bytes).
- 0x3F for the F/DEP primer entries (RE doc infers NFA reuses all-power-states).
- Routing NFC-A technology (not just MIFARE proto) to the eSE is the decisive
  entry that lets the eSE own ATQA/SAK/UID.

UNKNOWN
- Whether this table alone finally makes the eSE MIFARE card answer a reader on
  Linux, or whether the remaining vendor piece (NFCEE_POWER_AND_LINK_CTRL
  `21 03 02 83 03` and/or an NFC-A-listen RF register profile carried in the
  firmware `.bin` blobs) is still required. Not resolvable from the .so; needs a
  live SUBE capture/replay (hal_ese_coexist.md §7).
- The exact wire order of the nfcee/power octets is taken from the struct + NCI
  2.0 spec; it was NOT directly observed on the wire (capture logs no payload).
  If a live byte capture ever shows `... 3B 83 ...` instead of `... 83 3B ...`,
  the two fields in the struct (and both entry structs) would need swapping --
  but that would contradict the NCI 2.0 routing-entry layout.

## Files
- Corrected patch: `/tmp/nfcre5/0113-nfc-route-mifare-listen-to-ese.patch`
- This summary:     `/tmp/nfcre5/lmrt_5entry_fix.md`
- The git repo and pmbootstrap caches were NOT modified.
