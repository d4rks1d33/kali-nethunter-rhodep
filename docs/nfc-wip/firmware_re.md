# RE of the Samsung S3NRN4V NFC controller firmware image & HAL service binary

Reverse engineering of `sec_s3nrn4v_firmware.bin` (the NFC MCU's resident
executable firmware) and `android.hardware.nfc-service.sec` / `nfc_nci_sec.so`
(the Android vendor NFC HAL), to determine whether **NFC-A card-emulation
(listen) is a firmware capability of this part** and whether the mainline
bring-up problem ("chip accepts the NCI listen setup with `status 0x0` but never
raises `RF_INTF_ACTIVATED_NTF` on NFC-A") is a firmware-level gate or purely an
NCI/HAL trigger.

Legend: **FACT** = directly observed in the supplied binaries or measured;
**INFERENCE** = derived from ARM/NCI knowledge applied to what is observed;
**UNKNOWN** = not determinable from the material.

Scope note: this report covers the two artifacts prior reports did **not**
disassemble — the **MCU firmware image** and the **vendor HAL binaries**. The RF
register blobs (`rfreg`/`swreg`), the vendor conf, and the byte-level NCI listen
sequence are covered in `swreg_rfreg_analysis.md`, `vendor_listen_sequence.md`
and `nfc.md`; this report cross-checks and, where relevant, corrects them.

---

## 1. Firmware format, architecture, version

### 1.1 Format — FACT

`sec_s3nrn4v_firmware.bin` is **184620 bytes (0x2d12c)**. It is **a loadable,
signed code+data image with a small header**, not a TLV/opcode update package.
Layout:

| region (file) | content |
|---|---|
| 0x0000 – 0x000b | build-date ASCII `202112140956` (2021-12-14 09:56) |
| 0x000c – 0x000f | `0x001735f6` — image-length / CRC field (algorithm unconfirmed) |
| 0x0010 – 0x002b | **header record**, magic/version `65 00 03 02` (ver **0x0365**) + 6 length/offset dwords (0x2c, 0x80, 0x12c, 0x2d, 0xac, 0x80) |
| 0x002c – 0x012b | **256 bytes high-entropy** (entropy 7.21) = **RSA signature / hash block** (two 0x80-byte fields per the header offsets 0x2c and 0xac) |
| 0x0130 – 0x014b | **code-section header**: magic `65 00 03 02`, entry `0x00002421`, load base `0x00002400`, `ff` padding |
| 0x014c – 0x2a7ff | **Thumb-2 code + literal pools + data tables** (entropy 6.9 — plaintext compiled code, NOT encrypted) |
| 0x2a800 – 0x2a90a | **built-in NCI config default table** (section id `ee`, ver `03 03`) — see §2 |
| ~0x2a760 area | config-handler **dispatch table** (function pointers `0x15e45`, `0x170f7`, …) + vendor record `SLSI` + capability list |
| 0x2b… – 0x2c31f | tail **DEF register images** (two `44 45 46 00` = `"DEF\0"` trailers at 0x2c1c8, 0x2c318) — same "DEF" family as the external rfreg/swreg blobs |
| … – 0x2d12b | `0x00`/`0xff` padding |

Key format facts:
- Version marker `65 00 03 02` → **version 0x0365 = "3.65" family**, the *same*
  version tag as the external `rfreg.bin`/`swreg.bin` blobs (`65 03 …`), and the
  same `"DEF\0"` register-image family appears embedded in the tail. So the FW
  image, the rfreg and the swreg are one coherent release (v3.65, 2021-12-14).
- The 256-byte high-entropy block at 0x2c is a **code signature** (secure boot),
  consistent with `FW_UPDATE_MODE=0` — Android trusts the resident signed image
  and does not reflash by default.

### 1.2 Architecture — FACT (definitive)

The NFC MCU is an **ARM Cortex-M (ARMv7-M, Thumb-2)** core. Proof:
- The code at file 0x14c disassembles cleanly as Thumb-2 with real prologues/
  epilogues (`push {r4,r5,r6,lr}` / `pop {…,pc}`, `bx lr`), a byte-copy loop, and
  PC-relative literal pools.
- Literal pools contain **`0xE000E000`** (the ARMv7-M **System Control Space /
  NVIC** base on the private peripheral bus) and **`0x20002ab8`** (a pointer into
  the Cortex-M **SRAM region 0x20000000**). These two constants are unique to the
  Cortex-M memory map and are conclusive.
- Load base **0x2400** (from the section header) yields self-consistent absolute
  call targets and data pointers; the on-chip RAM data pointers embedded in the
  config area (`0x2ca30`, `0x2ca3c`, `0x2ca54`, …) map with a fixed VA=file+0x22b4
  delta, i.e. the image is position-fixed at VA 0x2400.

Note on `FW_BASE_ADDRESS=0x2000` (conf) vs load base `0x2400` (header): the 0x400
gap is the region below the code that holds the Cortex-M **vector table /
header** kept in RAM; the executable text starts at 0x2400. (INFERENCE.)

### 1.3 Version string — FACT

- **Build stamp:** `202112140956` (ASCII, offset 0) = 2021-12-14 09:56.
- **Image version tag:** `0x0365` (v3.65 family), matching rfreg/swreg.
- **Vendor tag:** the only meaningful ASCII string in the whole image is
  **`SLSI`** (Samsung System LSI) at 0x2a7bb, followed by a 14-byte capability
  record `64 09 | 04 05 11 13 21 23 24 30 41 00 00 01` (see §2.3).
- The HAL prints the running version via `%s: Complete: NFC FW Version:
  %02x.%02x.%02x` and compares two 3-byte version triples (on-chip vs .bin) in
  `nfc_fw_force_update_check` (`nfc_nci_sec.so` @0xf9b4). The exact decimal
  triple is not stored as ASCII in the .bin (it is read from the FW/GET_VERSION
  response at runtime), so the human-readable "x.y.z" is **UNKNOWN** from the
  file alone, but the internal version tag is 0x0365.

---

## 2. Is NFC-A LISTEN / card-emulation present as a firmware capability?

**YES. The firmware contains both the NFC-A listen configuration slots and the
NFC-A listen (card-side) frame/anticollision code path.** This is the central
new finding of this report, and it is FACT, from the image itself.

### 2.1 Built-in NCI config default table includes the LA_* (NFC-A listen) group — FACT

At file 0x2a800 there is a self-contained **NCI config default parameter table**
(section id `0xee`, version `03 03`, length 0xe4). Decoded TLVs (`<tag><len><val>`):

```
tag  len val            NCI name
30   01  04             LA_BIT_FRAME_SDD      <- NFC-A listen
31   01  03             LA_PLATFORM_CONFIG    <- NFC-A listen
32   01  00             LA_SEL_INFO           <- NFC-A listen
33   04  08 00 00 00    LA_NFCID1             <- NFC-A listen (default UID slot)
38   01  00             LB_SENSB_INFO         <- NFC-B listen
39   04  00 00 00 00    LB_NFCID0
3a   04  00 00 00 00    LB_APPLICATION_DATA
3b   01  00             LB_SFGI
3c   01  45             LB_ADC_FO
3e   01  00             LB_*
50   01  02             LF_PROTOCOL_TYPE      <- NFC-F listen
51   08  ff*8           LF_T3T_IDENTIFIERS
53   02  00 00          LF_T3T_PMM
54   01  06             LF_T3T_MAX
55   01  01             LF_T3T_FLAGS
… plus PA/PB/PF/PI poll groups, and prop tags 80/81/82/83/85/a2/a3/a4
```

Interpretation:
- The firmware's config manager **defines, stores and defaults the full
  LA_/LB_/LF_ listen parameter set**, including all four NFC-A listen tags
  (0x30–0x33). NFC-A listen config is therefore a *recognized, non-gated*
  capability of this firmware version — it is not stubbed out or absent.
- This is exactly why the on-device `CORE_SET_CONFIG` of the LA_* tags returns
  `status 0x0` (measured in `nfc.md`): they hit real, handled config slots.

### 2.2 NFC-A listen (card-side) frame handler / anticollision code — FACT

The firmware code contains a receive-path classifier for **reader-originated
NFC-A frames**, i.e. the tag/CE side of NFC-A. At VA 0x1111a–0x1112c:

```
cmp r0,#0xe0   ; RATS (ISO-DEP layer-4 request to a listening tag)
cmp r0,#0x50   ; HLTA / DESELECT
cmp r0,#0x93   ; SEL_REQ cascade level 1
cmp r0,#0x95   ; SEL_REQ cascade level 2
cmp r0,#0x97   ; SEL_REQ cascade level 3
cmp r1,#0xd4   ; NFC-DEP (ATR_REQ)
```

and a lower, RF-register-driven receiver at VA 0x0d2f0–0x0d316 that reads a HW
register, calls a HW callback (`blx`), and branches on `0x52` (WUPA) and `0xe0`
(RATS). There is also cascade-tag `0x88` handling at 0x0b506–0x0b54c and REQA
`0x26` at 0x1a54e. Together these are the **NFC-A listen anticollision / SELECT /
RATS responder** — the state machine a card runs when a reader interrogates it.

A multi-technology **listen dispatcher** exists at VA 0x1e75c, switching on RF
tech-and-mode bytes `0x81/0x82/0x83/0x85` (the passive/active listen and
NFC-DEP variants).

Conclusion: **NFC-A listen/card-emulation is a present, compiled firmware
feature in this v3.65 image — both the config surface and the RF/protocol code
exist.** It is not version-gated or absent. (FACT.)

### 2.3 The `SLSI` capability record — INFERENCE

The vendor record `04 "SLSI" 0e | 64 09 04 05 11 13 21 23 24 30 41 00 00 01`
lists config-group prefixes. `0x30` (LA/NFC-A listen group) and `0x41` (LI /
Listen ISO-DEP group) are **present** in this list, corroborating that NFC-A
listen and listen-side ISO-DEP are advertised capabilities of the part.
(INFERENCE — the exact schema of this record is not documented.)

---

## 3. Is there a firmware-level gate on listen/CE?

**No positive evidence of a firmware gate that disables NFC-A listen, and
strong evidence against one** (the config slots and the A-listen code path both
exist and are exercised — the chip *does* answer NFC-DEP and NFC-F listen using
this same dispatcher). (FACT for the presence of the code; INFERENCE that there
is no explicit A-disable.)

What this report can and cannot say about the observed symptom:
- The firmware being *capable* of NFC-A listen (this report) and the chip
  *actually lighting its NFC-A analog front-end from the plain NCI sequence*
  (the on-device symptom) are different layers. The firmware capability is
  present; whether it is *armed* depends on (a) the exact NCI/prop trigger the
  host sends and (b) the analog RF-listen profile loaded into the RF register
  file.
- Per `swreg_rfreg_analysis.md`, the mainline-loaded `rfreg`/`swreg` images are a
  **poll/reader RF profile with only a minimal receiver block and no
  per-technology NFC-A listen matrix**. So even though the firmware *code* can
  run NFC-A listen, the *analog front-end* may not be configured to answer an
  NFC-A reader at 106 kbit/s unless the vendor's listen RF profile is loaded.
  That is an RF-register/HAL issue, **not** a firmware-code gate.
- **UNKNOWN:** whether the firmware refuses to arm the A front-end unless a
  specific proprietary trigger or RF profile is present. Nothing in the image
  disassembly proves such a hard gate; the more parsimonious explanation
  (consistent with all evidence) is that the front-end simply isn't *configured*
  for A listen by the register set mainline loads, not that firmware forbids it.

---

## 4. Service binary & vendor HAL findings

### 4.1 `android.hardware.nfc-service.sec` — FACT

AArch64 PIE, the **AIDL NFC HAL service** (`android.hardware.nfc-V1-ndk`). It is
a **thin wrapper**: it implements `INfc` (open/close/write/coreInitialized/
preDiscover/…) and forwards everything to `nfc_nci_sec.so` via the symbols
`nfc_hal_open`, `nfc_hal_close`, `nfc_hal_write`, `nfc_hal_pre_discover`,
`nfc_hal_core_initialized`, `nfc_hal_power_cycle`, `nfc_hal_factory_reset`,
`nfc_hal_enableAidl`, `nfc_hal_getVendorConfig_aidl`,
`nfc_hal_closeForPowerOffCase`. It contains **no NCI opcode logic, no listen/CE
enable, no proprietary command** — only config-key marshalling
(`NFA_PROPRIETARY_CFG`, `DEFAULT_ROUTE`, `OFFHOST_ROUTE_ESE`, …) for the AIDL
`getVendorConfig`. The real vendor logic lives in `nfc_nci_sec.so`.

### 4.2 `nfc_nci_sec.so` — the complete inventory of what the HAL sends — FACT

Every NCI-emitting function in the vendor HAL was enumerated and disassembled.
There are exactly these command senders:

| function | what it emits |
|---|---|
| `hal_nci_send_reset` | CORE_RESET |
| `hal_nci_send_init` | CORE_INIT |
| `hal_nci_send_core_set_conf` | CORE_SET_CONFIG (init-time, from conf) |
| `hal_nci_send_clearLmrt` | **`21 01 02 00 00`** — RF_SET_LISTEN_MODE_ROUTING *clear* (verified from the literal at .rodata 0x5188) |
| `hal_nci_send_prop_fw_cfg` | **`2F 28 …`** — proprietary FW clock/config (uses `FW_CFG_CLK_SPEED`, `get_clock_info`) |
| `hal_nci_send_setSWAPITrace` | proprietary trace |
| `hal_vs_nci_send(oid,buf,len)` | generic **`2F <oid> <len> <payload>`** prop sender |

Callers of `hal_vs_nci_send` use only these OIDs (from the disassembly):
- **`2F 2A`** (OID 0x2a, len 1) — the **dual-rfreg transfer** sub-commands
  (start/chunk/stop), i.e. the RF register push (patch 0104). Called 4×.
- **`2F 25`** (OID 0x25, len 8) — RF-register **version set** (`hal_vs_set_rfreg_version`).

`nfc_hal_pre_discover` (@0xe740) **sends no NCI command** — it only logs and
returns a status byte. `hal_nci_send_clearLmrt` builds a clear-LMRT identical in
format to mainline's.

**There is NO "enable card emulation", "enable NFC-A listen", or listen-profile
opcode anywhere in the vendor HAL.** Every proprietary `2F xx` command
(`25/28/2A` and trace) is **init-time firmware/clock/RF-register/version**
plumbing. This confirms and completes the partial finding in `nfc.md`.

Corollary (FACT): in the Android stack the entire NFC-A listen / card-emulation
NCI state machine (SET_CONFIG LA_*, RF_DISCOVER_MAP, RF_SET_LISTEN_MODE_ROUTING,
RF_DISCOVER, activation) is produced by **`libnfc-nci.so` in the system
partition (AOSP NFA stack)** — *above* this vendor HAL. The vendor `.so` is only
the firmware/rfreg/clock/config transport, exactly like the kernel `sec-nfc`
driver is only a byte transport.

---

## 5. Conclusion

### Is the NFC-A listen front-end a firmware feature that just needs the right NCI trigger, or is there a firmware-level gate?

**It is a firmware feature that is present and not gated at the firmware-code
level.** The v3.65 resident firmware:
- defines and defaults the full NFC-A listen config group (LA_BIT_FRAME_SDD,
  LA_PLATFORM_CONFIG, LA_SEL_INFO, LA_NFCID1) — §2.1, FACT;
- contains the NFC-A listen (card-side) receive/anticollision/SELECT/RATS code
  and a multi-technology listen dispatcher — §2.2, FACT;
- shows no explicit "NFC-A listen disabled" gate — §3.

Therefore the firmware image is **not** the blocker in the sense of "this FW
version can't do NFC-A CE". The chip's silicon + firmware can be an NFC-A tag
(independently proven: Android does HCE on this exact part).

### Where the actual block sits, and whether mainline can fix it by matching the HAL's NCI sequence

Two layers sit between "firmware capable" and "tag activates", and the block is
**not** in the vendor HAL's NCI command stream:

1. **NCI/HAL trigger layer.** The vendor HAL sends *no* magic CE-enable command
   (§4.2, FACT). So there is no single proprietary opcode mainline is missing.
   The listen/CE NCI sequence is generated by AOSP `libnfc-nci` (userspace), and
   mainline's in-kernel `net/nfc` has never implemented host CE for T2T/ISO-DEP.
   Matching that *sequence* (SET_CONFIG → DISCOVER_MAP → LMRT → RF_DISCOVER,
   correct order/power-byte) is necessary but, per on-device measurement in
   `nfc.md`, has **not** been sufficient — the chip accepts it all with
   `status 0x0` yet the NFC-A analog front-end never engages.

2. **Analog RF-listen profile layer (the most likely real blocker).** The
   `rfreg`/`swreg` images mainline loads are a **poll/TX profile with no
   dedicated NFC-A listen RF matrix** (`swreg_rfreg_analysis.md`, corroborated
   here by the fact that the *firmware code* can do A-listen but the *front-end*
   never answers). NFC-F listen works because it is firmware-autonomous and does
   not need a host-supplied NFC-A listen profile. On Android the vendor HAL loads
   the RF register set via `2F 2A` (dual-rfreg) — the *same* blobs mainline uses
   — so the difference is **not** a second listen-specific `2F xx` push in the
   HAL (there is none, §4.2). That leaves two candidates for why Android's A
   front-end engages and mainline's does not:
   - the **exact NCI/prop trigger + ordering** `libnfc-nci` uses at CE-arm that
     the firmware needs to *commit* the A-listen front-end (a host-side sequence
     issue, in principle replicable in Linux), or
   - a **runtime RF-register override** the Android stack applies for listen that
     is not in the static blobs (would need a live capture to confirm).

**Bottom line (mainline-actionability):**
- The fix is **achievable in principle from the host** (it is not a permanent
  silicon/firmware lock): the firmware supports NFC-A listen and expects only
  standard NCI + the RF-register set that is already loaded.
- But it is **not** achievable merely by adding a missing vendor opcode from the
  HAL — because the HAL has none. The decisive experiment is to **capture the
  live NCI (+ any `2F xx`) stream Android emits at the moment CE is armed and a
  reader field appears** (stock boot + `rhodep-nfc raw`, or an HAL/kernel trace),
  and replay/replicate that exact sequence and ordering in the kernel. If that
  capture shows only standard NCI (no extra prop command), then the fix is host
  sequence/ordering + the userspace CE data path; if it shows an extra
  proprietary RF/register write at CE-arm, that write is the trigger to add to
  the driver.

The single strongest concrete lead remains the **command ordering + power-state
byte** in patch 0111 (LMRT sent before DISCOVER_MAP; `0x3f` vs vendor `0x3B`) —
see `vendor_listen_sequence.md` §4 — tried against the now-confirmed fact that
the firmware itself will run NFC-A listen once its front-end is actually armed.

---

## 6. FACT / INFERENCE / UNKNOWN

FACT
- Firmware format: signed loadable image, header + 256-byte RSA/hash signature +
  Thumb-2 code (load base 0x2400) + built-in NCI config default table + tail
  `DEF\0` register images. Build stamp `202112140956`, version tag 0x0365
  (matches rfreg/swreg v3.65 family). `FW_UPDATE_MODE=0` → resident signed image
  is run, not reflashed.
- Architecture: **ARM Cortex-M / ARMv7-M Thumb-2** (proven by `0xE000E000`
  SCS/NVIC and `0x20000000`-region SRAM pointers in the literal pools).
- The firmware contains the **NFC-A listen config group (LA_ tags 0x30–0x33)**
  with defaults, plus LB_ and LF_ listen groups, in a built-in config table.
- The firmware contains **NFC-A listen (card-side) code**: REQA/WUPA (0x26/0x52),
  SEL_REQ CL1/2/3 (0x93/0x95/0x97), cascade tag 0x88, RATS 0xe0 handling, and a
  listen-mode RF dispatcher (0x81/0x82/0x83/0x85).
- Vendor tag `SLSI`; capability record lists group 0x30 (LA) and 0x41 (LI).
- Service binary is a thin AIDL wrapper delegating to `nfc_nci_sec.so`.
- The vendor HAL's *only* proprietary opcodes are `2F 25` (RF version),
  `2F 28` (FW clock/config), `2F 2A` (dual-rfreg push) + trace; `clearLmrt` =
  `21 01 02 00 00`; `pre_discover` sends nothing. **No CE/listen-enable command.**

INFERENCE
- 0x400 gap between `FW_BASE_ADDRESS=0x2000` and code base 0x2400 = RAM vector
  table/header region.
- No firmware-level gate specifically disables NFC-A listen; the on-device
  "A never activates" symptom is at the analog RF-listen-profile / host-trigger
  layer, not the firmware code.
- Because the HAL carries no CE-enable opcode, matching mainline to the HAL's
  proprietary command set alone cannot unblock A-listen; the trigger (if any) is
  in the AOSP `libnfc-nci` NCI stream or a runtime RF override.

UNKNOWN
- Human-readable decimal FW version triple (read at runtime, not stored as ASCII
  in the .bin); internal tag is 0x0365.
- Exact algorithm/coverage of the 0x0c length/CRC field.
- Whether the firmware requires a specific NCI trigger/ordering (host-replicable)
  or a runtime RF-register override (needs live capture) to arm the NFC-A analog
  listen front-end. This is the one decisive open item; resolve it by capturing
  the live NCI + `2F xx` stream Android emits at CE-arm on the stock ROM.
