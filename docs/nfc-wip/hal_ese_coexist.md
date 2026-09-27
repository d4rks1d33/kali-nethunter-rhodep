# S3FWRN5 / S3NRN4V HAL RE — eSE MIFARE card emulation coexisting with the NFC-F/DEP RF-priming peer

Target: Moto G82 (rhodep), Samsung S3NRN4V CLF, `nfc_nci_sec.so` (ARM64, 117 KB,
stripped, BuildID f4972906…). Goal: replicate Android's card emulation so that a
reader's **NFC-A field is routed to the eSE (NFCEE 0x83, protocol MIFARE 0x80)**
while the listen front-end stays powered — which on this chip only happens when
the **NFC-F/NFC-DEP peer is also armed**.

This report answers each question with the evidence found in the `.so`, the
vendor conf, and the two live NCI captures, and marks every claim
**FACT / INFERENCE / UNKNOWN**.

---

## 0. The single most important RE result (read this first)

**FACT — The Samsung HAL does NOT build the LMRT, RF_DISCOVER_MAP or
RF_DISCOVER. It is a pure transport plus a config provider.** Confirmed three
ways from the disassembly:

- `nfc_hal_pre_discover()` (0xe740): sends **no** NCI command. It only logs and
  returns a one-byte global flag at `0x1cffc`. (asm read in full.)
- `hal_nci_send_clearLmrt()` (0x10028): the *only* place the HAL touches the
  listen routing table, and it emits a **fixed 5-byte constant** loaded from
  rodata `0x5188 = `**`21 01 02 00 00`** = RF_SET_LISTEN_MODE_ROUTING, plen 2,
  more=0, **num_entries=0** → a *clear*. It never appends a single routing entry.
- `device_set_mode(eNFC_DEV_MODE)` (0xd250): an `ioctl(fd, 0x40045301, mode)` to
  `/dev/sec-nfc` — a **driver power/mode** call, not an NCI RF command.

**INFERENCE (strong) — the actual LMRT / DISCOVER_MAP / DISCOVER are built by the
AOSP NFA stack (`libnfc-nci.so`, userspace) above the HAL.** The HAL's job in
that flow is to answer the HIDL `getConfig()` callback with a struct populated
from the vendor `.conf`. That function is `nfc_hal_getVendorConfig` (V1.1) and
`nfc_hal_getVendorConfig_1_2` (V1.2), disassembled below. Those config values —
`DEFAULT_ROUTE`, `DEFAULT_OFFHOST_ROUTE`, `OFFHOST_ROUTE_ESE/UICC`,
`DEFAULT_ISODEP_ROUTE`, `DEFAULT_NFCF_ROUTE`, `*_PWR_STATE`,
`NFA_PROPRIETARY_CFG`, `DEVICE_HOST_WHITE_LIST` — are what *determine* the bytes
NFA then programs. So "the correct LMRT" is defined by these conf keys plus the
NFA algorithm, not by anything inside the `.so`.

This matches the port note exactly and is now proven at the instruction level.

---

## 1. RF_SET_LISTEN_MODE_ROUTING (LMRT) — byte-by-byte

### 1a. What the HAL itself sends
```
21 01 02 00 00      RF_SET_LISTEN_MODE_ROUTING  more=0  num_entries=0   (a CLEAR)
```
**FACT** (rodata const @0x5188, sent by `hal_nci_send_clearLmrt` via
`__send_to_device`). This is the same "clear" the mainline port already sends;
there is no hidden Samsung LMRT.

### 1b. What NFA builds on Android (reconstructed from the conf + the live plen)

NCI LMRT entry format (NCI 2.0, 5 bytes each for tech/proto):
```
type  len=03  [power_state]  [nfcee_id]  [value]
  type: 0x00 = TECHNOLOGY-based, 0x01 = PROTOCOL-based, 0x02 = AID
  value: for TECHNOLOGY -> RF tech (A=0x00,B=0x01,F=0x02)
         for PROTOCOL   -> RF proto (T2T? see note; ISO_DEP=0x04, NFC_DEP=0x05,
                                      MIFARE(Samsung/NXP prop)=0x80)
  power_state: bitmask, bit0 switched-on, bit1 switched-off, bit2 battery-off,
               higher bits = screen-off / screen-off-lock variants
  nfcee_id: 0x00 = host (DH), 0x83 = eSE, 0x15/0x82 = UICC (see conf)
```

**The correct table for "NFC-A/MIFARE → eSE, RF primed by NFC-F/DEP" (INFERENCE,
built to match the vendor conf routes + power states):**

```
more=0  num_entries=5
# 1) NFC-A technology  -> eSE 0x83  (so the eSE owns the whole NFC-A layer:
#    ATQA/SAK/UID/anticollision + Crypto1). This is the decisive entry.
  00 03 3B 83 00
# 2) MIFARE protocol (0x80) -> eSE 0x83  (MIFARE Classic is presented as proto
#    0x80, not plain T2T; belt-and-suspenders with the tech entry)
  01 03 3B 83 80
# 3) ISO-DEP protocol (0x04) -> eSE 0x83  (DEFAULT_ISODEP_ROUTE=0x83; harmless
#    here, keeps EMV/T4 offhost as Android has it)
  01 03 3B 83 04
# 4) NFC-F technology -> host 0x00  (present but with the F/DEP peer NEUTRALISED,
#    see §5 — this entry + LF_* config is only there to power the analog front-end)
  00 03 3F 00 02
# 5) NFC-DEP protocol (0x05) -> host 0x00  (kept ONLY so the chip arms the F
#    listener that lights the RF; the peer is suppressed via LF_PROTOCOL_TYPE)
  01 03 3F 00 05
```
Full command bytes:
```
21 01 1B 00 05  00 03 3B 83 00  01 03 3B 83 80  01 03 3B 83 04  00 03 3F 00 02  01 03 3F 00 05
```
(plen 0x1B = 27, matching the **reader_dmesg** capture which shows LMRT `plen=27`,
5 entries — see cross-check below.)

Power states used:
- **0x3B** for the offhost/eSE entries = the vendor's `OFFHOST_AID_ROUTE_PWR_STATE`
  (`libnfc-nci.conf:87`) — on + switched-off + battery-off + screen-off variants,
  i.e. the card answers with the screen off / powered down. **FACT** the conf
  value is 0x3B; **INFERENCE** that NFA reuses it for the tech/proto offhost
  entries.
- **0x3F** (all power states) for the host NFC-F/DEP RF-priming entries — max
  availability so the F listener is always up to keep the analog front-end alive.

> Note on the current port's 3-entry table (ese_dmesg `plen=17`): it sends only
> `A-tech→eSE`, `MIFARE→eSE`, and one more, with **no NFC-F/NFC-DEP entry** — that
> is exactly the configuration in which the note says "the chip activates
> NOTHING." The reader-log 5-entry table (`plen=27`) is the one that keeps the F
> peer and *does* light the front-end. The fix is to always include entries 4+5
> (RF priming) alongside 1–3 (eSE routing).

### 1c. Live cross-check (FACT, from the captures)
- `ese_dmesg.log:211` — `LMRT GID=0x1 OID=0x1 plen=17` → more+num+**3 entries**
  (3×5+2=17). This is the "eSE-only, no F/DEP" table → **0 activations**.
- `reader_dmesg.log:51` — `LMRT plen=27` → more+num+**5 entries** (5×5+2=27). This
  table keeps NFC-F + NFC-DEP → the chip **repeatedly activates as NFC-F LISTEN**
  (`rf_intf_activated_ntf`, `tech_and_mode 0x82`, `rf_protocol 0x5` NFC-DEP,
  `rf_interface 0x1` FRAME — `reader_dmesg.log:66-77`).

The asymmetry in the two live tables **is** the phenomenon: the 5-entry table
that keeps NFC-F/DEP is the one that lights the RF.

---

## 2. RF_DISCOVER_MAP (GID 0x1 OID 0x0)

### What the captures show (FACT)
- `ese_dmesg.log:164` — `RF_DISCOVER_MAP plen=19` (3 mapping triplets of 3 bytes
  = 9, but plen 19 implies ~6 triplets; the port maps poll+listen for several
  protocols). Status 0x0.
- `reader_dmesg.log:43` — `RF_DISCOVER_MAP plen=4` (a single triplet, listen
  path). Status 0x0.

### Correct listen-mode map (INFERENCE)
Each triplet: `protocol  mode(bit0=poll,bit1=listen)  rf_interface`.
```
04 02 02     ISO-DEP , LISTEN , ISO-DEP interface (0x02)   -> for T4/APDU offhost
05 01 03     NFC-DEP , POLL?  ...   (NFC-DEP mapped to FRAME 0x03)
05 02 03     NFC-DEP , LISTEN , FRAME interface (0x03)      -> arms the F/DEP peer
02 02 01     T2T     , LISTEN , FRAME interface (0x01)      -> only if host T2T
```
Key facts learned during the port and confirmed by the HAL being transport-only:
- **Do NOT put MIFARE 0x80 in RF_DISCOVER_MAP.** The controller rejects
  DISCOVER_MAP with `status 0x1` if 0x80 is listed. MIFARE is handled purely via
  the **LMRT** (routed to the eSE), never via a DISCOVER_MAP interface. **FACT**
  (documented and reproduced in `nfc.md`).
- **NFC-DEP → FRAME (0x03) listen** is what arms the peer that powers the analog
  front-end. It must be mapped for the RF to come up (that is why NFC-F listen
  works today). **INFERENCE.**
- The eSE MIFARE path needs **no** DISCOVER_MAP entry — routing an offhost
  technology/protocol to an NFCEE is a pure LMRT operation; the CLF forwards the
  RF to the NFCEE without a host RF interface. **INFERENCE (strong).**

---

## 3. RF_DISCOVER (GID 0x1 OID 0x3)

### Live (FACT)
- Both captures: `RF_DISCOVER plen=5` → `num=2` + two `(tech_and_mode, freq)`
  pairs. Status 0x0.
- The two offered listen techs are **NFC-A listen 0x80** and **NFC-F listen
  0x82** (confirmed in `findings_so_far.txt:12` and reproduced live). The chip
  then activates on **0x82** (NFC-F/DEP), never on 0x80 alone.

### Correct RF_DISCOVER (INFERENCE)
```
23 03 05 02  80 01  82 01
             |  |    |  +-- discovery frequency 1
             |  |    +----- NFC-F PASSIVE LISTEN
             |  +---------- discovery frequency 1
             +------------- NFC-A PASSIVE LISTEN
num_configs = 2
```
Offering **both** A-listen (0x80) and F-listen (0x82) is mandatory on this
firmware: NFC-A listen alone never brings the front-end up (proven: "NFC-A only
offered: nothing"). The F entry is the RF primer; the A entry is what the eSE
answers on. **INFERENCE, backed by the live asymmetry.**

---

## 4. Full HAL/NFA sequence for eSE card emulation (order + values)

Combining the HAL init trace (`ese_dmesg.log`) with the NFA card-emulation order.
FACT for everything observed in the logs; INFERENCE for the NFA-built RF phase.

```
# ---- HAL init (FACT, ese_dmesg.log) ----
CORE_RESET_CMD            20 00 01 01                         -> RSP + NTF nci_ver 0x20
NCI_PROP 2F 28            2F 28 01 ..    (select/prep rf image)          -> RSP
NCI_PROP 2F 2A (rfreg)    2F 2A 01 ..    dual-rfreg update, many 254-byte chunks
   ... 16× 2F 2A plen 254 (hwreg+swreg blobs) ...                       -> each RSP 0x0
   2F 2A plen 42, 2F 2A plen 3  (tail)                                  -> "rfreg update success, checksum 0x5b9a"
CORE_INIT_CMD             20 01 02 ..                                    -> nfcc_features 0x80067e1a,
                                                                            RF ifaces 00/01/02/03,
                                                                            max_routing_table_size 1170
# ---- NFA card-emulation bring-up ----
CORE_SET_CONFIG (several) 20 02 ..   tags below                          -> status 0x0 (each)
NFCEE_DISCOVER            22 00 00    (empty, NCI 2.0)                    -> RSP status 0x0, num 0x2
   NFCEE_DISCOVER_NTF     83 01 01 80 01 a0 01 02 00   -> eSE 0x83, proto 0x80 (MIFARE)
   NFCEE_DISCOVER_NTF     15 01 01 00 ...              -> UICC 0x15
NFCEE_MODE_SET(0x83,EN)   22 01 02 83 01                                 -> RSP 0x0 + MODE_SET_NTF
   (skip if NFCEE_DISCOVER_NTF already reported status 0x01 = ENABLED)
RF_DISCOVER_MAP           21 00 ..   (listen maps: ISO-DEP/LISTEN,
                                      NFC-DEP/LISTEN→FRAME, T2T if host)  -> status 0x0
RF_SET_LISTEN_MODE_ROUTING 21 01 1B 00 05  <5 entries, §1b>              -> status 0x0
RF_DISCOVER               23 03 05 02 80 01 82 01                        -> status 0x0
```

### CORE_SET_CONFIG tags (the ones that matter here)

**Routing/identity tags (INFERENCE which NFA emits from the conf):**
- For the **eSE route**: **do NOT** set host `LA_*` identity tags
  (`LA_BIT_FRAME_SDD 0x30`, `LA_PLATFORM_CONFIG 0x31`, `LA_NFCID1 0x33`,
  `LA_SEL_INFO 0x34`). The eSE owns the NFC-A identity (real card's
  ATQA/SAK/UID). Forcing a host `LA_NFCID1`/`LA_SEL_INFO=0x08` would hide the
  card. **FACT** (established in `nfc.md` step 4).
- **LF_* tags** to neutralise the parasitic peer (see §5): `LF_PROTOCOL_TYPE
  (0x50) = 0`, and consider `LF_T3T_FLAGS (0x3F) = 0`.
- `TOTAL_DURATION (0x00)` — set a non-trivial listen window (e.g. `E8 03` =
  1000 ms) so the A-listen slot actually coincides with the reader field.
- `LB_*` (Type-B listen) — not used for a MIFARE/NFC-A card; leave default.

**Proprietary remap (FACT, from conf + HAL):**
`NFA_PROPRIETARY_CFG = {00, 81, 82, 80, 8A, 80, 70, 74, F4}` — read by the HAL in
`getVendorConfig` at struct bytes [1..10] and handed to NFA. **Index 5 = 0x80**
is the PROTOCOL value MIFARE Classic is presented under; that is the `0x80` used
in the LMRT MIFARE entry. **FACT.**

### Proprietary (0x2F…) commands (FACT, from the .so + log)
All 0x2F opcodes seen are **init-time RF/clock/trace**, never a "card-emulation
enable":
- `2F 28` — `hal_nci_send_prop_fw_cfg` / select rf image (`select_image_*`).
- `2F 2A` — dual-rfreg update (`hal_vs_rfreg_update_dual_option`), the analog
  register blobs `sec_s3fwrn5_rfreg.bin`/`swreg.bin`. This is why NFC-F listen &
  reader mode work.
- `2F 25/26/27` — clock/trace/SWAPI trace (`hal_nci_send_setSWAPITrace`,
  `get_config_propnci_get_oid`).
There is **no** vendor opcode that arms NFC-A tag listen. **FACT** (strings +
symbol survey + full disasm of the send helpers).

---

## 5. How the DEP peer (RF) and the eSE (NFC-A) coexist without the DEP answering

**FACT + INFERENCE.** The mechanism has two parts, and neither lives in the HAL:

1. **Keep the NFC-F listener to power the analog front-end.** This firmware will
   not light its listen RF for NFC-A alone; it only comes up when NFC-F listen is
   offered (proven both ways in the captures). So NFC-F stays in RF_DISCOVER
   (0x82) and NFC-DEP stays mapped to FRAME in DISCOVER_MAP + present as a host
   LMRT entry. That is the *primer*.

2. **Suppress the P2P peer so NFC-F only powers the analog, never answers.** With
   firmware defaults, offering NFC-F auto-arms an autonomous **NFC-DEP peer** that
   answers the reader itself (that is the `tech_and_mode 0x82 / proto 0x5`
   activation spamming `reader_dmesg.log`). To stop it while keeping the RF up:
   - `CORE_SET_CONFIG LF_PROTOCOL_TYPE (0x50) = 0x00` → no NFC-DEP over NFC-F.
   - `CORE_SET_CONFIG LF_T3T_FLAGS (0x3F) = 0x00` → no Type-3 listen slots.
   Effect (verified in the port, `0113` header): the `data_exch_rf_tech_and_mode
   0x82` NFC-DEP activation that used to fire on every tap **disappears**; NFC-F
   listen then only energises the front-end. **FACT** (port-verified).

   The routing then does the steering: **NFC-A technology → eSE 0x83** in the
   LMRT means when the reader raises an NFC-A field, the CLF hands the whole
   NFC-A layer (anticollision, SAK, Crypto1) to the eSE, not to the host DEP
   state machine. NFC-F/DEP entries point at the host but the peer is disarmed,
   so nothing on the host responds. **INFERENCE (this is the intended NCI
   behaviour and matches the conf).**

### The wall the port hit, and where the real answer is (UNKNOWN → likely vendor RF profile)
With the eSE route + LF neutralisation, the controller **accepts every command
(status 0x0)** but the **NFC-A listen front-end never engages at RF**:
`RF_NFCEE_ACTION_NTF` (GID 0x1 OID 0x9) never fires, no activation, tested with
the SUBE app *and* a Flipper. Two candidates remain, neither reproducible from
plain NCI:
- **`NFCEE_POWER_AND_LINK_CTRL` (NCI 2.0, GID 0x2 OID 0x3)** — Android sends it
  (implied by `OFFHOST_AID_ROUTE_PWR_STATE=0x3B`, i.e. low-power offhost states)
  to keep the eSE powered+linked during listen. Try `21 03 02 83 03` after
  MODE_SET. **UNKNOWN** whether this firmware needs it (not in the .so; NFA-side).
- **A vendor RF *listen* profile.** The `2F 2A` rfreg blobs loaded at init are
  reader/NFC-F-listen analog images; it is plausible the CLF only arms the NFC-A
  *listen* analog path when a *different* RF register set is loaded, which the
  plain NCI path never triggers. **UNKNOWN** — would need to capture the exact
  NCI + 0x2F stream Android emits while the SUBE card is being read and replay
  it, or diff the rfreg blobs for a listen-A profile. The HAL disasm shows the
  loader (`hal_vs_rfreg_update_dual_option`, `hal_vs_merge_rf_image`) but the
  *contents* that would carry an A-listen profile are in the `.bin` blobs, not
  the `.so`.

---

## 6. ALL routing / power keys the HAL reads from the vendor conf

**FACT** — extracted by disassembling `nfc_hal_getVendorConfig` (V1.1, 0xec00)
and `nfc_hal_getVendorConfig_1_2` (V1.2, 0xefe8) and resolving every
`GetNumValue`/`CNfcConfig::getValue` key string and its destination struct
offset:

### From `getVendorConfig` (V1.1 NfcConfig struct)
| conf key | dest offset | type | value in this vendor conf |
|---|---|---|---|
| `ISO_DEP_MAX_TRANSCEIVE`   | +20 (w) | u32  | 0xFEFF |
| `DEFAULT_OFFHOST_ROUTE`    | +11 (b) | u8   | **0x83 (eSE)** |
| `DEFAULT_NFCF_ROUTE`       | +12 (b) | u8   | **0x83** |
| `DEFAULT_SYS_CODE_ROUTE`   | +13 (b) | u8   | (unset → default) |
| `DEFAULT_SYS_CODE_PWR_STATE`| +14 (b)| u8   | (unset) |
| `DEFAULT_ROUTE`            | +15 (b) | u8   | **0x83 (eSE)** |
| `DEVICE_HOST_WHITE_LIST`   | +24 (vec)| bytes| (unset) |
| `OFF_HOST_ESE_PIPE_ID`     | +16 (b) | u8   | (unset; `OFF_HOST_SIM_PIPE_ID=0x06`) |
| `OFF_HOST_SIM_PIPE_ID`     | +17 (b) | u8   | 0x06 |
| `NFA_PROPRIETARY_CFG`      | +1..10  | bytes| {00,81,82,80,8A,80,70,74,F4} |
| `PRESENCE_CHECK_ALGORITHM` | +1 (b, ≤5)| u8 | 5 |

### From `getVendorConfig_1_2` (V1.2 additions)
| conf key | value |
|---|---|
| `OFFHOST_ROUTE_UICC` | **{83}** (note: conf comment vs value — see below) |
| `OFFHOST_ROUTE_ESE`  | **{82}** |
| `DEFAULT_ISODEP_ROUTE` | **0x83** |

### Other routing/power keys present in the vendor confs (FACT, from the files)
`libnfc-sec-vendor.conf`:
```
DEFAULT_OFFHOST_ROUTE=0x83
OFFHOST_ROUTE_ESE={82}
OFFHOST_ROUTE_UICC={83}
DEFAULT_NFCF_ROUTE=0x83
DEFAULT_SYS_CODE={FE:FF}
DEFAULT_ROUTE=0x83
OFF_HOST_SIM_PIPE_ID=0x06
ISO_DEP_MAX_TRANSCEIVE=0xFEFF
DEFAULT_ISODEP_ROUTE=0x83
NFA_PROPRIETARY_CFG={00,81,82,80,8A,80,70,74,F4}
PRESENCE_CHECK_ALGORITHM=5
NFA_POLL_BAIL_OUT_MODE=0
```
`libnfc-nci.conf`:
```
POLLING_TECH_MASK=0x6F
P2P_LISTEN_TECH_MASK=0x44        (A|ACTIVE)
UICC_LISTEN_TECH_MASK=0x07       (A|B|F)
SCREEN_OFF_POWER_STATE=1
NFA_MAX_EE_SUPPORTED=3
NFA_AID_BLOCK_ROUTE=1
AID_MATCHING_MODE=0
OFFHOST_AID_ROUTE_PWR_STATE=0x3B   <-- the offhost power-state bitmask
LEGACY_MIFARE_READER=1             (READER flag, NOT emulation)
```

**Important consistency notes (INFERENCE):**
- The conf labels are swapped relative to the observed NFCEE ids: the live
  NFCEE_DISCOVER shows **eSE = 0x83** (proto MIFARE) and **UICC = 0x15**. But the
  conf has `OFFHOST_ROUTE_ESE={82}` and `OFFHOST_ROUTE_UICC={83}`. On this board
  the eSE that actually holds the MIFARE applet is **0x83** (per the live
  enumeration — trust the hardware over the conf label). Use **0x83** as the eSE
  target in the LMRT. `DEFAULT_ROUTE=0x83` and `DEFAULT_OFFHOST_ROUTE=0x83`
  reinforce that the box's default sink is 0x83.
- `HOST_LISTEN_TECH_MASK` is **not** present in either conf (so NFA uses its
  default); there is no host A-listen forced by conf.

---

## 7. Bottom line for the Linux port

- **LMRT to use** (RF up via F/DEP, NFC-A/MIFARE → eSE): the 5-entry table in
  §1b — `A-tech→0x83@0x3B`, `MIFARE(0x80)→0x83@0x3B`, `ISO-DEP(0x04)→0x83@0x3B`,
  `F-tech→0x00@0x3F`, `NFC-DEP(0x05)→0x00@0x3F`. The current port's 3-entry table
  (no F/DEP) is exactly the "nothing activates" case.
- **RF_DISCOVER_MAP**: listen maps for ISO-DEP/ISO-DEP-iface and
  NFC-DEP/FRAME-iface; **never** map MIFARE 0x80 (chip rejects). eSE routing needs
  no map entry.
- **RF_DISCOVER**: offer **both** 0x80 (NFC-A listen) and 0x82 (NFC-F listen);
  A-alone never lights the RF.
- **Order**: SET_CONFIG (LF_* neutralise, TOTAL_DURATION, no host LA_*) →
  NFCEE_DISCOVER → NFCEE_MODE_SET(0x83) → RF_DISCOVER_MAP → LMRT → RF_DISCOVER.
- **Coexistence**: keep NFC-F listen for the analog primer, kill the P2P peer with
  `LF_PROTOCOL_TYPE=0` (+`LF_T3T_FLAGS=0`), route NFC-A tech to the eSE so the
  eSE — not the host DEP peer — answers.
- **The HAL adds nothing to this** beyond transport + the conf values in §6.
- **Remaining unknown / most likely missing piece**: `NFCEE_POWER_AND_LINK_CTRL`
  (`21 03 02 83 03`) and/or a **vendor NFC-A-listen RF register profile** carried
  in the `.bin` blobs, not in the `.so`. That is the next thing to capture from a
  live Android SUBE read and replay.

---

## Evidence index
- `nfc_hal_pre_discover` @0xe740 — no NCI TX (disasm).
- `hal_nci_send_clearLmrt` @0x10028 — sends const `21 01 02 00 00` @rodata 0x5188.
- `device_set_mode` @0xd250 — `ioctl 0x40045301` to /dev/sec-nfc (power/mode).
- `nfc_hal_getVendorConfig` @0xec00 / `_1_2` @0xefe8 — conf-key → struct map (§6).
- `ese_dmesg.log` — init 2F2A rfreg, CORE_INIT, NFCEE_DISCOVER (83/15),
  LMRT plen17 (3 entries, no F/DEP → 0 activations).
- `reader_dmesg.log` — LMRT plen27 (5 entries, keeps F/DEP → repeated NFC-F/DEP
  activations, `tech_and_mode 0x82`, proto 0x5, FRAME iface).
- `libnfc-sec-vendor.conf`, `libnfc-nci.conf` — routing/power keys (§6).
