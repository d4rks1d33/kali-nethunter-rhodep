# Vendor S3FWRN5 listen / card-emulation NCI sequence — reverse-engineering report

Target: Moto G82 (rhodep), Samsung **S3FWRN5 / S3NRN4V** NFC controller (NCI 2.0,
`nci_ver 0x20`). Goal: replicate Android libnfc-nci's listen/CE bring-up in Linux
mainline so the chip activates as an NFC-A tag (`RF_INTF_ACTIVATED_NTF`) and routes
the field to the eSE (NFCEE 0x83, MIFARE 0x80) for the SUBE transit card.

Legend for every claim: **FACT** = directly observed in the supplied material or
measured on the device per `nfc.md`; **INFERENCE** = derived from NCI 2.0 / ETSI /
libnfc-nci spec knowledge applied to this part; **UNKNOWN** = not determinable from
the supplied material.

---

## 0. What the supplied vendor material actually contains (important caveat)

**FACT.** Both `/tmp/nfcre/vendor_libnfc.conf` (20 KB) and
`/tmp/nfcre/vendor_nfc_big.txt` (393 KB) contain **exactly one, identical, and
TRUNCATED** copy of the vendor `libnfc-nci.conf`. The conf fragment begins at
`NFC_DEBUG_ENABLED` and ends at `NFA_EE_ROUTE_DEBOUNCE_TIMER=0x01`, after which the
raw dd runs straight into an unrelated Android linker/`ld.config`/`libs` block
(`libaptX_encoder.so`, `libEGL.so`, …) and, in the big file, into
`privapp-permissions` XML. There is **no second conf fragment** and no continuation.

The complete set of `KEY=VALUE` lines recoverable from the vendor conf is:

```
NFC_DEBUG_ENABLED=0
NFA_STORAGE="/data/nfc"
PRESERVE_STORAGE=0x01
#NFA_DM_DISC_DURATION_POLL=333               (commented out)
POLLING_TECH_MASK=0x0F
UICC_LISTEN_TECH_MASK=0x07
NFA_MAX_EE_SUPPORTED=0x02
AID_FOR_EMPTY_SELECT={08:A0:00:00:01:51:00:00:00}
AID_MATCHING_MODE=0x01
NCI_RESET_TYPE=0x00
PRESENCE_CHECK_RETRY_COUNT=0
#NFA_DM_LISTEN_ACTIVE_DEACT_NTF_TIMEOUT=3    (commented out)
EUICC_MEP_MODE=0x03
NFCEE_EVENT_RF_DISCOVERY_OPTION=0x00
OPTIMIZE_ROUTING_TABLE_UPDATE=0x00
NFA_EE_ROUTE_DEBOUNCE_TIMER=0x01
```

**Verified negative results** (`grep -a` over both raw blocks, string and
case-insensitive substring):

| Key searched | Result |
|---|---|
| HOST_LISTEN_TECH_MASK | **NOT PRESENT** |
| DEFAULT_ROUTE | **NOT PRESENT** |
| DEFAULT_OFFHOST_ROUTE | **NOT PRESENT** |
| DEFAULT_NFCF_ROUTE | **NOT PRESENT** |
| DEFAULT_ISODEP_ROUTE | **NOT PRESENT** |
| DEVICE_HOST_WHITE_LIST | **NOT PRESENT** |
| OFF_HOST_ROUTE_UICC / OFF_HOST_ROUTE_ESE | **NOT PRESENT** |
| DEFAULT_SYS_CODE_ROUTE / _PWR_STATE | **NOT PRESENT** |
| OFFHOST_AID_ROUTE_PWR_STATE | **NOT PRESENT** |
| NFA_DM_CFG | **NOT PRESENT** |
| TOTAL_DURATION | **NOT PRESENT** |
| LEGACY_MIFARE_READER | **NOT PRESENT** |
| P2P_LISTEN_TECH_MASK | **NOT PRESENT** |
| any `LA_*` / `LF_*` config | **NOT PRESENT** |
| SCREEN_STATE / screen routing | **NOT PRESENT** |
| CE_ROUTE_STRICT_DISABLE | **NOT PRESENT** |

The only `*LISTEN_TECH*` key in the vendor conf is `UICC_LISTEN_TECH_MASK=0x07`.

**Why they are absent (INFERENCE).** These are exactly the keys that on modern
AOSP/libnfc-nci moved **out** of `libnfc-nci.conf` and into either (a) compiled-in
defaults in `libnfc-nci`/`NfcService` (`RoutingOptionManager`,
`DEFAULT_ROUTE`/`DEFAULT_ISODEP_ROUTE`/`DEFAULT_OFFHOST_ROUTE` etc. now come from
`config.xml`/resource overlays and system properties like
`persist.nfc.*`/`ro.*`), or (b) a separate vendor conf
(`libnfc-nxp.conf`-style / `nfc_hal_ext`), or (c) `libnfc-nci-XXX.conf` product
overlay. The truncated dd simply did not capture that file. So the routing/listen
masks **cannot be read from the supplied material** — they must be obtained from a
second dump (see "What to capture next").

---

## 1. HOST_LISTEN_TECH_MASK

**UNKNOWN from the supplied material** — the key is not present in either blob (see
table above).

**INFERENCE (best reconstruction of the vendor's effective value):**
- The chip advertises host CE for A/B/F. Android's default and near-universal value
  is `HOST_LISTEN_TECH_MASK=0x07` (NFC-A | NFC-B | NFC-F). Given this device does
  real HCE (Google Pay etc.), `0x07` is the almost-certain effective value.
- Bit meaning (`tNFA_TECHNOLOGY_MASK`, confirmed by the comments in the vendor conf
  itself): `A=0x01, B=0x02, F=0x04`. NFC-A host listen is armed by bit 0x01.
- This is the key that (with a matching LMRT host route) arms NFC-A host listen.
  **It is the value patch 0111 is effectively trying to emulate in-kernel** by
  building the LA_* config + host LMRT route.

**Bottom line for Q1:** the exact value is not recoverable here, but functionally it
is `0x07` and NFC-A is enabled (bit 0). This is *not* the missing piece for your
problem — patch 0111 already programs the equivalent host route; the block is at the
RF/front-end layer, not this mask (see §4).

---

## 2. Routing config and eSE (NFCEE 0x83 / MIFARE 0x80)

**UNKNOWN from the conf** (DEFAULT_ROUTE / OFF_HOST_ROUTE_ESE not present).

**FACT (measured, from `nfc.md`):**
- `NFA_MAX_EE_SUPPORTED=0x02` (conf) — two EEs expected, matches the two discovered.
- eSE = **NFCEE id 0x83**, status 0x01 (connected), **one** protocol **0x80
  (MIFARE)**, one Samsung TLV `A0 01 02`.
  NFCEE_DISCOVER_NTF: `83 01 01 80 01 a0 01 02 00`.
- UICC (SIM) = **NFCEE id 0x15**, protocol 0x00, ATR-like `3B` TLV. Not the card.
- `UICC_LISTEN_TECH_MASK=0x07` (conf) — UICC listens A/B/F; irrelevant to the eSE
  card but shows the vendor listen masks are all `0x07`.
- CORE_INIT `nfcc_features = 0x80067e1a`: technology-based routing supported,
  switched-on **and** switched-off power states (screen-off CE works),
  `max_routing_table_size = 1170`. RF interfaces 0x00/0x01/0x02/0x03.

**INFERENCE — the vendor routing the eSE card needs (reconstructed):**
- `OFF_HOST_ROUTE_ESE = 0x83`, `DEFAULT_OFFHOST_ROUTE = 0x83` (eSE is the offhost
  target for the transit applet).
- `DEFAULT_ROUTE = 0x00` (host) is the AOSP default; the *MIFARE/transit* routing is
  a protocol+technology route to 0x83, not a change to DEFAULT_ROUTE.
- The vendor LMRT for the transit card routes, all with an offhost power-state mask
  that includes switched-off (`OFFHOST_AID_ROUTE_PWR_STATE`/`DEFAULT_SYS_CODE_PWR_STATE`
  typically `0x3B` on these parts):
  - **NFC-A technology → NFCEE 0x83** (so the eSE owns ATQA/SAK/UID selection), and
  - **protocol 0x80 (MIFARE) → NFCEE 0x83**.
  This is exactly what patch 0113 does (per `nfc.md`), and the controller accepts it
  (status 0x0). So the *routing values are already correct*; the wall is RF (§4).

The power-state byte matters: the note in `nfc.md` about
`OFFHOST_AID_ROUTE_PWR_STATE=0x3B` is the vendor's offhost power mask. Use `0x3B`
(switched-on + switched-off + battery-off-ish combos the part supports) rather than
`0x3f` for the eSE entries; `0x3f` sets reserved bits some firmwares reject silently.

---

## 3. Byte-level NFC-A listen bring-up (canonical libnfc-nci / NCI 2.0 order)

Target card: **ATQA 0x0044, SAK 0x08, 7-byte UID 04:35:3d:6a:f7:54:80** (MIFARE
Classic 1K emulation). All values below are **INFERENCE** from the NCI 2.0 spec and
libnfc-nci behavior, cross-checked against the tags patch 0111 already uses.

### Canonical order (this is what libnfc-nci/NFA does)

```
1. CORE_SET_CONFIG   (all listen params in ONE command, not many)
2. RF_DISCOVER_MAP   (with LISTEN-mode map entries)
3. RF_SET_LISTEN_MODE_ROUTING (LMRT)
4. RF_DISCOVER
```

Patch 0111 sends the LMRT **inside `nci_start_poll` via `nci_request`** and then
lets the existing flow send DISCOVER_MAP and RF_DISCOVER — i.e. its effective order
is **SET_CONFIG → LMRT → DISCOVER_MAP → RF_DISCOVER**, which is **wrong** (LMRT
before MAP). See §4.

### CORE_SET_CONFIG — the LA_* tags and exact bytes

NCI config tag IDs (FACT, NCI 2.0 spec; also in patch 0111):
- `LA_BIT_FRAME_SDD`  = **0x30**
- `LA_PLATFORM_CONFIG`= **0x31**
- `LA_SEL_INFO`       = **0x32**
- `LA_NFCID1`         = **0x33**
- `TOTAL_DURATION`    = **0x00**

Values for ATQA 0044 / SAK 08 / 7-byte UID (**INFERENCE**):

| Tag | ID | Len | Value | Meaning |
|---|---|---|---|---|
| LA_BIT_FRAME_SDD   | 0x30 | 1 | **0x04** | SDD pattern; low ATQA byte bits b4:b0 |
| LA_PLATFORM_CONFIG | 0x31 | 1 | **0x00** | ATQA high nibble / platform = T1T off, no config |
| LA_SEL_INFO        | 0x32 | 1 | **0x08** | SAK: b3=1 → MIFARE/T2T, ISO-DEP=0, NFC-DEP=0 |
| LA_NFCID1          | 0x33 | 7 | **04 35 3d 6a f7 54 80** | 7-byte UID → controller sets double-cascade + UID-size=01 in ATQA itself |

Key rules (INFERENCE, NCI 2.0 §Listen-A + Digital Protocol):
- The **SENS_RES (ATQA)** the chip emits is *built by the controller*, not sent as a
  config blob. Low byte = SDD bits (from LA_BIT_FRAME_SDD) OR'd with the UID-size
  bits **derived from the LENGTH of LA_NFCID1** (4→`00`, 7→`01`, 10→`10` in b7:b6).
  For a 7-byte UID the controller inserts b6=1, giving low byte `0x44` from an
  SDD of `0x04` → **ATQA 0x0044** exactly. So do **NOT** try to force the cascade
  bits into LA_PLATFORM_CONFIG (that corrupts the high byte). Patch 0111 gets this
  right.
- For the 7-byte UID the controller inserts the `0x88` cascade tag (CT) on the wire
  itself at cascade level 1; LA_NFCID1 carries the 7 real bytes only.
- SAK `0x08` in LA_SEL_INFO: bit3 = "T2T/MIFARE", bit5 (ISO-DEP) = 0, bit6 (NFC-DEP)
  = 0. This is what makes a reader see a Type-2/MIFARE tag, not a P2P peer.

The single vendor CORE_SET_CONFIG (`GID 0x2 OID 0x2`) frame would be (host CE case):
```
20 02  <len>  05                        ; SET_CONFIG, num_params = 5
        00 02 E8 03                     ; TOTAL_DURATION = 1000 ms (LE)
        30 01 04                        ; LA_BIT_FRAME_SDD = 0x04
        31 01 00                        ; LA_PLATFORM_CONFIG = 0x00
        32 01 08                        ; LA_SEL_INFO (SAK) = 0x08
        33 07 04 35 3d 6a f7 54 80      ; LA_NFCID1 (7-byte UID)
```
(For the **eSE** route case you send NO LA_* — the eSE owns the identity; see §2/§4.)

### RF_DISCOVER_MAP (`GID 0x1 OID 0x0`) — LISTEN entries

For host NFC-A T2T CE (INFERENCE): map **T2T ↔ FRAME interface, LISTEN mode**:
```
21 00 04  01   02 02 01
                └ 1 mapping: rf_protocol=T2T(0x02), mode=LISTEN(0x02), iface=FRAME(0x01)
```
Notes:
- Do **NOT** list MIFARE 0x80 in DISCOVER_MAP — the controller rejects it with
  status 0x1 (FACT, measured, patch 0111 comment). MIFARE is routed via LMRT only.
- Mode byte is a bitmask: POLL=0x01, LISTEN=0x02. libnfc-nci maps T2T for BOTH
  poll+listen in one entry (`0x03`) when reader+CE coexist; for pure CE, LISTEN
  (0x02) is enough.

### RF_SET_LISTEN_MODE_ROUTING (`GID 0x1 OID 0x1`) — LMRT

Host CE (Type-2) entries (INFERENCE), NCI 2.0 entry format
`[type][len][nfcee_id][power][value]`:
```
21 01  <len>  00  <n>
   tech  : 00 03 00 3B 00      ; TECHNOLOGY, nfcee=host(0x00), pwr=0x3B, tech=NFC-A(0x00)
   proto : 01 03 00 3B 02      ; PROTOCOL, host, T2T(0x02)
   proto : 01 03 00 3B 80      ; PROTOCOL, host, MIFARE(0x80)
```
eSE route (the transit card, INFERENCE — matches patch 0113):
```
   tech  : 00 03 83 3B 00      ; TECHNOLOGY NFC-A → eSE 0x83
   proto : 01 03 83 3B 80      ; PROTOCOL MIFARE → eSE 0x83
```
Power byte: use **0x3B** (vendor offhost mask). Patch 0111 uses 0x3f — see §4.

### RF_DISCOVER (`GID 0x1 OID 0x3`)

Offer **NFC-A passive listen (0x80) AND NFC-F passive listen (0x82)** together
(INFERENCE + FACT that F is the only path that currently activates):
```
21 03 05  02   80 01   82 01
              └ NFC-A passive listen, freq 1
                      └ NFC-F passive listen, freq 1
```

---

## 4. THE CONCRETE DIFFERENCE vs patch 0111 (the prize)

Ranked by likelihood of being the actual activation blocker. Note the overriding
**FACT** from `nfc.md`: the chip accepts *all* of 0111's SET_CONFIG/MAP/LMRT/DISCOVER
with `status 0x0`, is proven to do **NFC-F listen** and **NFC-DEP listen**, but never
lights the **NFC-A listen front-end** — neither an app nor a Flipper Zero sees it.
So the difference is not "a rejected command"; it is something that changes whether
the A front-end is armed at RF.

### (A) Command ORDER — LMRT is sent BEFORE RF_DISCOVER_MAP. **FIX FIRST.**
- **FACT:** patch 0111 issues the LMRT from inside `nci_start_poll`
  (`nci_request(nci_rf_set_listen_mode_routing_req…)`) *before* the code that emits
  RF_DISCOVER_MAP and RF_DISCOVER. libnfc-nci order is strictly
  **SET_CONFIG → DISCOVER_MAP → LMRT → RF_DISCOVER**.
- **INFERENCE:** some NCI firmwares validate/commit the routing table against the
  currently-mapped interfaces; programming the LMRT before the MAP exists can leave
  the A technology route inert even though the command returns status 0x0.
- **Action:** move the LMRT to run *after* DISCOVER_MAP and immediately *before*
  RF_DISCOVER.

### (B) Power-state byte 0x3f vs 0x3B in the LMRT.
- **FACT:** 0111 uses `NCI_LMRT_POWER_STATE_ALL = 0x3f`. The vendor offhost power
  mask on these parts is **0x3B** (bit2 reserved/unused on S3FWRN5).
- **INFERENCE:** S3FWRN5 firmware silently ignores routing entries whose power_state
  sets a bit it does not support, so a `0x3f` entry can be accepted (status 0x0) yet
  not applied. This exactly matches the "accepts everything, activates nothing"
  symptom.
- **Action:** use **0x3B** for every LMRT entry.

### (C) LA_NFCID1 without the CT / cascade handling — likely a non-issue.
- 0111 relies on the controller to insert the `0x88` CT from the 7-byte length; this
  is correct NCI behavior. Keep it. (Listed only to rule it out.)

### (D) DISCOVER_MAP has no LISTEN entry for every routed protocol.
- **FACT:** 0111 maps only T2T↔FRAME(LISTEN). Its LMRT also routes MIFARE(0x80),
  NFC-F and NFC-DEP.
- **INFERENCE:** NCI requires that a protocol routed in the LMRT with the LISTEN
  power/tech also have a corresponding activatable listen mapping; MIFARE can't be
  in MAP (rejected), but NFC-F/NFC-DEP routed to host in 0111's LMRT have no LISTEN
  MAP entry either. This mismatch can leave the discovery in an inconsistent listen
  state. **For the eSE path, drop the host NFC-F/NFC-DEP routes entirely** (they
  were only added in 0111 to observe the parasitic F activation).

### (E) TOTAL_DURATION — already added (EXP-3), not the blocker alone.
- 0111 sets `00 02 E8 03` (1000 ms). Correct; keep. Not sufficient by itself.

### (F) The real root cause per `nfc.md`: **vendor RF-listen profile, not an NCI
command.**
- **FACT:** disassembly of `nfc_nci_sec.so` found no "enable CE" NCI command; the
  hwreg/swreg blobs are **analog RF register images** (verified here: they end in
  the magic `44 45 46 00` = `"DEF\0"`, contain no LA_* TLVs, no UID/ATQA/SAK, no tag
  0x33). They are loaded via proprietary `2F 2A` (patch 0104), which is why F-listen
  and reader mode work.
- **INFERENCE (strongest overall):** the S3FWRN5 does not light its **NFC-A listen**
  analog front-end from the RF register set mainline loads. On Android the HAL loads
  a **listen-specific RF profile** (a different/extended rfreg push, or a proprietary
  RF-config opcode) when CE is armed. Reproducing that push — not a missing standard
  NCI tag — is what would make the A front-end engage. This is consistent with every
  standard NCI command returning status 0x0 while RF stays dark.

**Net recommendation for the prize:** apply (A)+(B) and remove the extraneous host
F/DEP LMRT routes (D); if the A front-end still does not engage, the remaining
difference is (F) — the vendor listen RF profile — which requires capturing the
proprietary NCI/`2F xx` stream Android emits at CE-arm (see next section).

---

## 5. Vendor-proprietary NCI commands (GID 0xF / 0x2F)

**FACT (from `nfc.md` HAL disassembly):**
- Proprietary opcodes seen in the HAL init: `2F 25`, `2F 26`, `2F 27`, `2F 28`,
  `2F 2A`. All are **rfreg / clock / trace during init** — never a "CE enable".
  `2F 2A` is the dual-rfreg push (patch 0104) that arms F-listen/reader RF.
- `hal_nci_send_clearLmrt` builds a *clear* LMRT `21 01 02 00 00` (same format as
  mainline's).
- `nfc_hal_pre_discover` sends nothing.

**The `2F 30 01 00` command mentioned in the task:**
- **UNKNOWN / NOT FOUND in the supplied material.** No `2F 30` occurs in the conf
  blobs, the rfreg/swreg blobs, or patch 0111, and `nfc.md` does not list it among
  the HAL's opcodes. It is plausibly a Samsung proprietary "PROP_SET_RF" /
  RF-profile-select opcode (`GID 0xF OID 0x30`, 1-byte arg `0x00`), which would fit
  hypothesis (F) — a proprietary RF/listen-mode select. **This cannot be confirmed
  from what was provided.** If it exists it is the single most interesting thing to
  test: send `2F 30 01 00` (and variants `2F 30 01 01`) right before RF_DISCOVER in
  the listen path and watch for the A front-end to engage / `RF_INTF_ACTIVATED_NTF`.

**NCI 2.0 command mainline lacks that Android sends for offhost CE (INFERENCE):**
- `NFCEE_POWER_AND_LINK_CTRL` — **GID 0x2 OID 0x3**: `21 03 02 83 03`
  (NFCEE 0x83, ctrl 0x03 = power+link kept during listen). Android sends this so the
  eSE stays powered/linked in switched-off states. Mainline has no opcode for it.
  If it returns UNKNOWN_OID (0x08) the firmware wants another mechanism; if it
  returns 0x0 it may be part of what keeps the eSE routable. Worth adding for the
  eSE path.

---

## 6. Reconstructed vendor listen/routing config (as complete as the fragments allow)

**Directly extracted (FACT):**
```
POLLING_TECH_MASK=0x0F            ; poll A|B|F|V
UICC_LISTEN_TECH_MASK=0x07        ; UICC listens A|B|F   (all vendor listen masks = 0x07)
NFA_MAX_EE_SUPPORTED=0x02         ; UICC(0x15) + eSE(0x83)
AID_MATCHING_MODE=0x01            ; exact-or-prefix
AID_FOR_EMPTY_SELECT={08:A0:00:00:01:51:00:00:00}
NCI_RESET_TYPE=0x00
PRESERVE_STORAGE=0x01
EUICC_MEP_MODE=0x03
NFCEE_EVENT_RF_DISCOVERY_OPTION=0x00
OPTIMIZE_ROUTING_TABLE_UPDATE=0x00
NFA_EE_ROUTE_DEBOUNCE_TIMER=0x01
```
**Reconstructed / effective (INFERENCE — NOT in the dump, needs confirmation):**
```
HOST_LISTEN_TECH_MASK=0x07        ; A|B|F host CE (NFC-A armed by bit0)
DEFAULT_ROUTE=0x00                ; host
DEFAULT_OFFHOST_ROUTE=0x83        ; eSE
OFF_HOST_ROUTE_ESE=0x83
OFF_HOST_ROUTE_UICC=0x15
DEFAULT_ISODEP_ROUTE=0x00 or 0x83 ; (transit applet is MIFARE, not ISO-DEP → 0x83)
DEFAULT_NFCF_ROUTE=<UNKNOWN>
DEFAULT_SYS_CODE_ROUTE=<UNKNOWN>
OFFHOST_AID_ROUTE_PWR_STATE=0x3B
DEFAULT_SYS_CODE_PWR_STATE=0x3B
```

---

## 7. eSE routing values (answer to Q2, summarized)

- eSE NFCEE id: **0x83** (FACT). Only protocol: **0x80 = MIFARE** (FACT).
- To route the SUBE card in listen, program the LMRT with:
  `TECHNOLOGY NFC-A(0x00) → 0x83, pwr 0x3B` **and** `PROTOCOL MIFARE(0x80) → 0x83,
  pwr 0x3B` (INFERENCE, matches patch 0113 which the chip accepts at status 0x0).
- Enable it first: `NFCEE_MODE_SET(0x83, ENABLE)` → status 0x0 + NTF (FACT, 0113).
- Do **not** set any host LA_* when routing to the eSE (the eSE owns ATQA/SAK/UID).
- Consider `NFCEE_POWER_AND_LINK_CTRL 21 03 02 83 03` after MODE_SET (INFERENCE).

---

## 8. Byte-level LA_* CORE_SET_CONFIG (answer to Q3, summarized)

For host NFC-A tag, ATQA 0044 / SAK 08 / 7-byte UID 04:35:3d:6a:f7:54:80:
```
20 02 <len> 05
   00 02 E8 03                    TOTAL_DURATION = 1000 ms
   30 01 04                       LA_BIT_FRAME_SDD  = 0x04
   31 01 00                       LA_PLATFORM_CONFIG= 0x00
   32 01 08                       LA_SEL_INFO (SAK) = 0x08
   33 07 04 35 3d 6a f7 54 80     LA_NFCID1 (UID, 7 bytes → double cascade auto)
```
The controller synthesizes SENS_RES=0x0044 from SDD 0x04 + UID-size bits derived
from the 7-byte NFCID1 length. Patch 0111 already emits these exact tags/values.

---

## 9. The concrete difference vs patch 0111 (answer to Q4, summarized)

1. **Order:** 0111 sends LMRT *before* RF_DISCOVER_MAP. Vendor/libnfc-nci order is
   SET_CONFIG → DISCOVER_MAP → **LMRT** → RF_DISCOVER. **Reorder.**  (highest-value,
   cheapest fix)
2. **Power byte:** 0111 uses `0x3f`; use the vendor's **`0x3B`** in every LMRT entry.
3. **Stray routes:** 0111 also routes host NFC-F + NFC-DEP in the LMRT with no
   matching LISTEN MAP entry; for the eSE/MIFARE goal, drop those.
4. **Deeper cause:** even with 1–3, the S3FWRN5 does not light its **NFC-A listen
   analog front-end** from the RF register set mainline loads. Android's HAL loads a
   **listen-specific vendor RF profile** at CE-arm (a proprietary RF push, candidate
   `2F 30 01 00` or an extended `2F 2A` rfreg). Reproducing that push — not a missing
   standard NCI tag — is the true remaining difference. This is consistent with all
   standard NCI returning status 0x0 while RF stays dark for both host and eSE paths.

---

## 10. Proprietary commands needed (answer to Q5, summarized)

- Confirmed vendor opcodes are init-time rfreg/clock/trace only:
  `2F 25/26/27/28/2A` (`2F 2A` = dual-rfreg, patch 0104). None is a CE-enable. (FACT)
- `2F 30 01 00`: **not present in the supplied material (UNKNOWN)**; plausible
  Samsung PROP RF/listen-select opcode — the top experiment to try before
  RF_DISCOVER in listen.
- `NFCEE_POWER_AND_LINK_CTRL` `21 03 02 83 03` (NCI 2.0, GID 0x2 OID 0x3): not in
  mainline; Android sends it for offhost CE. Add for the eSE path. (INFERENCE)

---

## 11. What to capture next to close the UNKNOWNs

1. **Re-dump the vendor NFC config completely** (untruncated): pull
   `/vendor/etc/libnfc-nci.conf`, `/vendor/etc/libnfc-nxp*.conf`, any
   `/vendor/etc/libnfc-*_rf*.conf`, and `/system/etc/libnfc-nci.conf`. The routing
   masks (HOST_LISTEN_TECH_MASK, DEFAULT_*ROUTE, OFF_HOST_ROUTE_ESE, *_PWR_STATE)
   live there or in resource overlays; the supplied dd stopped at
   `NFA_EE_ROUTE_DEBOUNCE_TIMER`.
2. **Capture the live NCI+proprietary stream** on Android when the SUBE card is read
   (kernel/HAL trace or `rhodep-nfc raw` after booting stock once), specifically the
   command(s) sent at "screen-on, CE armed" and at the moment the reader field
   appears — this is what reveals the vendor RF-listen profile / any `2F 30`.
3. On Linux, apply fixes (A)+(B)+(D) from §4, then try `2F 30 01 00` and
   `NFCEE_POWER_AND_LINK_CTRL` before RF_DISCOVER, watching for
   `RF_INTF_ACTIVATED_NTF` / `RF_NFCEE_ACTION_NTF`.

---

## Appendix: verification commands used

- `strings -n 4 vendor_libnfc.conf` → full recoverable conf (§0).
- `strings -n 3 vendor_nfc_big.txt | grep -E '^[A-Za-z_]…=' ` → same conf, rest binary.
- `grep -a -o -i <KEY>` over both blobs for every routing/listen key → all NOT FOUND
  except UICC_LISTEN_TECH_MASK (§0 table).
- rfreg/swreg tails end in `44 45 46 00` ("DEF\0"); no 0x33/UID/ATQA/SAK inside →
  confirms analog RF images, not NCI listen config (§4F).

---
## Convergence note (session 2026-09-26)

Three independent sources now agree on the SAME ceiling for NFC-A card emulation
(host AND eSE):
1. patch 0113 header (already tried eSE routing, LF_PROTOCOL_TYPE=0, NFC-A+NFC-F
   pairing, NFCEE_MODE_SET 0x83): "remaining block is the NFC-A listen front-end
   not engaging at RF, most likely a vendor RF profile the plain NCI path cannot
   reproduce."
2. swreg_rfreg_analysis.md: rfreg has poll/TX profiles but NO NFC-A listen RF
   profile.
3. vendor_listen_sequence.md: the true gap is the listen-specific vendor RF
   profile the Android HAL pushes, not a missing NCI tag.

So the concrete next step is to obtain the vendor's listen RF register stream:
capture what the Android NFC HAL (Samsung libsec-nfc / libnfc-nci vendor .so)
loads into the chip when card emulation starts (the delta vs rfreg||swreg), then
add a driver path to push it at CE start. This needs RE of the Android vendor NFC
HAL from the super/vendor partition, or an I2C/NCI capture of Android doing CE.
The cheap NCI deltas (order MAP->LMRT, power byte 0x3B, NFCEE_POWER_AND_LINK_CTRL)
are worth folding in regardless, but are unlikely to be sufficient alone.
