# S3FWRN5 / S3NRN4V listen-mode (card emulation) activation analysis

Target: Moto G82 (rhodep), Samsung **S3NRN4V** NFCC (NCI 2.0), mainline `s3fwrn5`
with port patches 0101-0113 (0x3B power-byte fix present).
Trigger: `rhodep-nfc listen 04:35:3d:6a:f7:54:80 08`.
Scope: NCI protocol + vendor-HAL/conf comparison. No hardware needed.

---

## 0. What the captured flow actually is (baseline fact)

The flow in `nci_flow.txt` is **NOT** patch 0111 alone. It contains `NFCEE_MODE_SET`
(opcode 0x201) and `NFCEE_DISCOVER_NTF` handling, which only **0112 + 0113** emit,
and `MODE_SET` is only issued by 0113 **when `ndev->ese_nfcee_id` is non-zero**.

**FACT:** the running kernel is 0111+0112+0113 with the eSE branch active
(`ese_nfcee_id = 0x83`). The header note in `nci_flow.txt` ("SET_CONFIG order sent by
patch 0111 ... BIT_FRAME_SDD, PLATFORM_CONFIG, NFCID1, SEL_INFO, TOTAL_DURATION") is
**stale** — that host-path order produces **five** SET_CONFIG (plens 4,4,10,4,5), but
only **three** (plens 5,4,5) appear on the wire. In the eSE branch 0113 **skips all
LA_\*** and instead sends three tags:

| # | tag (0113 eSE branch) | id | val | plen | captured status |
|---|-----------------------|------|-----|------|-----------------|
| 1 | LF_PROTOCOL_TYPE      | 0x50 | 00        | 4 | (rsp, ok) |
| 2 | LF_T3T_FLAGS          | 0x3F | 00 00     | 5 | **0x09 FAIL** |
| 3 | TOTAL_DURATION        | 0x00 | E8 03     | 5 | 0x0 |

(The log's plen sequence "5,4,5" is the dynamic-debug RSP print order, not TX order;
TX order per 0113 source is `LF_PROTOCOL_TYPE(4) → LF_T3T_FLAGS(5) → TOTAL_DURATION(5)`.
plen-4 = the single len-1 tag (LF_PROTOCOL_TYPE, ok); the two plen-5 are the two
len-2 tags; the failing one is **LF_T3T_FLAGS**, since TOTAL_DURATION `E8 03` is a
universally supported tag.)

The question's framing of the plen-5 candidates as `LA_BIT_FRAME_SDD (0x0330)` or
`LA_SEL_INFO (0x0332)` is a **red herring**: those are NFA/AOSP *internal* 2-byte
PMIDs, not NCI wire tags (wire tags are 1 byte: 0x30 / 0x32), and in the eSE branch
**no LA_\* is sent at all**. The hex `01 03 30 01 04` in the prompt does not parse as
a valid single-tag NCI SET_CONFIG payload for tag 0x30 (that would be `01 30 01 04`,
plen 4). So the failing tag is not an LA_* tag.

---

## 1. CORE_SET_CONFIG status 0x09 — which tag, why, and the fix

### status 0x09 meaning
- **FACT:** in the NCI core status table, `0x09 = STATUS_MESSAGE_SIZE_EXCEEDED`.
- **INFERENCE:** a 5-byte payload cannot exceed any message-size limit, so the NFCC
  is not using the literal "message too big" meaning here. In a `CORE_SET_CONFIG_RSP`
  the status is the *aggregate* result and the response body then lists the offending
  Parameter_ID(s): `Status(1) | Num_Params(1) | Param_ID(s)[n]`. A non-zero status
  with `plen 3` = `status + num(1) + one failing tag id`. Samsung/Broadcom-lineage
  firmwares reuse `0x09` in this position to mean **"this configuration parameter /
  value is not acceptable in the current state"** (out-of-range / semantically
  inconsistent value), returning the rejected tag id in the body.

### The offending tag: **LF_T3T_FLAGS (0x3F)**, value `00 00`
- **INFERENCE (high confidence):** 0113 sets `LF_T3T_FLAGS = 0x0000` to disable all
  Type-3 listen slots. On this firmware that value is rejected:
  - `LF_T3T_FLAGS` is a 16-bit bitmask selecting which `LF_T3T_IDENTIFIERS_1..16`
    slots are active. Setting it to `0x0000` while NFC-F LISTEN is about to be offered
    (RF_DISCOVER includes `82`) is internally inconsistent — the firmware expects the
    T3T listen parameters to be coherent (`LF_T3T_FLAGS` non-zero *and* the matching
    `LF_T3T_IDENTIFIERS`/`LF_T3T_PMM`/`LF_T3T_MAX` present, or the whole T3T config
    left at defaults). A lone `LF_T3T_FLAGS=0` is neither, so it is refused with 0x09.
  - This is exactly the kind of "accepted-in-isolation-elsewhere, rejected-here" tag
    that a per-parameter 0x09 flags.

### Why it matters even though 0113 "works around" it
The reject is **silent to the driver's success path** but it means the T3T listen
front-end is left at firmware defaults — i.e. 0113's *intended neutralisation of the
parasitic NFC-F/Type-3 peer never takes effect for T3T*. Combined with the fact that
the F peer suppression was the whole reason for this tag, the config is a no-op and
the chip may still present a Type-3/NFC-DEP peer or, worse, sit in an inconsistent
listen state.

### Fix
Two acceptable fixes; prefer the first:

1. **Do not send `LF_T3T_FLAGS` at all.** To suppress the NFC-F peer, clearing
   `LF_PROTOCOL_TYPE = 0x00` (no NFC-DEP over F) is sufficient and is accepted
   (status 0x0). Leave the T3T listen config at firmware defaults. This removes the
   0x09 entirely.
2. If T3T slots must be explicitly disabled, send `LF_T3T_FLAGS` **together with** a
   consistent T3T identity block in the *same* batched SET_CONFIG
   (`LF_T3T_IDENTIFIERS_1`, `LF_T3T_PMM`, `LF_T3T_MAX`) so the firmware sees a
   coherent T3T configuration. This is more fragile; option 1 is recommended.

Also: **batch the SET_CONFIG into one command** (num_params = N) instead of one
command per tag. Batching is what the vendor HAL and AOSP do (`raw.log` shows the
vendor batches: `20 02 14 01 29 11 …` is one command carrying one 17-byte tag), it
halves the round-trips, and — critically — it lets the firmware validate the LF/LA
set *as a set*, which is how it wants T3T-related tags validated.

---

## 2. The eSE NFCEE id — 0x82 vs 0x83, and the MODE_SET target

### Ground truth from the chip (NFCEE_DISCOVER_NTF decode)
NCI 2.0 `NFCEE_DISCOVER_NTF` layout: `NFCEE_ID | Status | Num_Protocols | Protocols[] |
Num_TLVs | TLVs[]`.

```
NTF #1: 83 01 01 80 01 a0 01 02 00
  83            NFCEE_ID   = 0x83
  01            Status     = connected/enabled
  01            Num_Proto  = 1
  80            Protocol   = 0x80  (MIFARE, Samsung/NXP proprietary)
  01            Num_TLVs   = 1
  a0 01 02      TLV        = type 0xA0 len 1 val 0x02  (Samsung vendor TLV)

NTF #2: 15 01 01 00 01 04 06 00 00 02 00 3b 01 00
  15            NFCEE_ID   = 0x15
  01            Status     = connected
  01            Num_Proto  = 1
  00            Protocol   = 0x00 (undetermined)
  ...           TLV carrying an ATR-like 0x3B  → SIM/UICC
```

- **FACT:** the NFCEE whose **only** supported protocol is **0x80 (MIFARE)** is
  **NFCEE id 0x83**. Its lone-MIFARE protocol set is the signature of the transit
  applet — **this is the eSE holding the SUBE card.**
- **FACT:** the UICC/SIM enumerates as **NFCEE id 0x15** (protocol undetermined,
  ATR-like TLV).

### Resolving the contradiction with `libnfc-sec-vendor.conf`
The conf says `OFFHOST_ROUTE_ESE={82}` and `OFFHOST_ROUTE_UICC={83}`. These are **not**
raw NCI wire NFCEE IDs:
- **INFERENCE:** the `OFFHOST_ROUTE_*` tokens in `libnfc-nci` are **AOSP/NFA route
  handles / logical IDs** that the HAL maps to the actual enumerated NCI NFCEE ids at
  runtime; they are configuration hints for the NFA routing layer, not the ids the
  controller reports in NFCEE_DISCOVER_NTF. The vendor HAL string table
  (`nfc_nci_sec.so`) carries `OFFHOST_ROUTE_ESE` / `OFFHOST_ROUTE_UICC` /
  `OFF_HOST_ESE_PIPE_ID` as conf keys it consumes and remaps — it does not treat 0x82
  as a wire id.
- The chip's own enumeration is the authority. **The eSE is 0x83 (MIFARE), the UICC
  is 0x15.** The conf's `{82}`/`{83}` do **not** match the wire ids and must not be
  used as MODE_SET / routing targets directly.

> Note the conf's `DEFAULT_ROUTE=0x83`, `DEFAULT_NFCF_ROUTE=0x83`,
> `DEFAULT_OFFHOST_ROUTE=0x83` — several of these do land on 0x83, consistent with the
> eSE being 0x83 on this hardware, which strengthens the reading that `{82}` is a
> stale/logical token, not the eSE wire id.

### Was the NFCEE_MODE_SET correct?
- **FACT:** `NFCEE_MODE_SET` (opcode 0x201) returned status **0x0 (accepted)**.
- 0113 targets `ndev->ese_nfcee_id`, which its NTF handler set to **0x83** (the id that
  listed MIFARE 0x80). So the MODE_SET was `NFCEE_MODE_SET(0x83, ENABLE)` — **the
  correct NFCEE.** The wire frame is `22 01 02 83 01`.
- **Caveat:** the "unknown ntf 0x202" before the RSP is the **NFCEE_MODE_SET_NTF**
  (GID 0x2, OID 0x2 → opcode 0x202), which mainline does not parse but which the chip
  legitimately sends after enabling an NFCEE. That is benign; MODE_SET succeeded.

**Verdict:** eSE = **0x83** (protocol MIFARE 0x80); MODE_SET targeted 0x83 and was
accepted — **correct**. The conf's `{82}`/`{83}` are logical route handles, not wire
ids, and are misleading; ignore them for NCI-level targeting.

---

## 3. Why it still does NOT activate (RF_DISCOVER status 0x0, no RF_INTF_ACTIVATED_NTF)

Several distinct issues, in order of impact:

### (a) Command ordering is wrong vs canonical
Captured order: `RF_DISCOVER_MAP → NFCEE_DISCOVER → SET_CONFIG → NFCEE_MODE_SET →
LMRT → RF_DISCOVER`.
Canonical (AOSP/NFA + vendor HAL): **`SET_CONFIG → RF_DISCOVER_MAP → (NFCEE enable) →
LMRT → RF_DISCOVER`**.
- **INFERENCE:** sending `RF_DISCOVER_MAP` *before* the listen `SET_CONFIG` means the
  interface mapping is programmed before the LA_/LF_ listen identity exists. On this
  firmware the discover-map and the listen config should be coherent when discovery
  starts. The order should be fixed regardless of other causes.

### (b) The 0x09-rejected LF_T3T_FLAGS leaves listen config inconsistent
As in §1: the failed SET_CONFIG means the T3T listen state is at firmware defaults,
not the intended "neutralised" state. So the parasitic F/Type-3 peer suppression is
only half-applied (`LF_PROTOCOL_TYPE=0` took, `LF_T3T_FLAGS` did not). A partially
configured listen profile is a plausible reason the A listener never arms.

### (c) The field never reaches the eSE — no RF_NFCEE_ACTION_NTF
- **FACT (from nfc.md):** across all variants tested, the host sees **no
  `RF_NFCEE_ACTION_NTF` (GID 0x1 OID 0x9)** and no activation — 40 s silence. That NTF
  is the signal that the CLF delivered the RF field to the eSE. Its absence means the
  **NFC-A listen front-end never engaged at RF**, so nothing was ever routed.
- `RF_DISCOVER` returning status 0x0 only means the *command* was accepted; it says
  nothing about the analog front-end actually arming the A listener.

### (d) The root cause is a vendor RF/listen profile, not a missing NCI command
- **INFERENCE (high confidence, corroborated by nfc.md's HAL disassembly):** on this
  S3NRN4V the NFC-A **listen** front-end only comes up when the vendor RF-register
  profile for listen is loaded. Mainline `s3fwrn5` loads the `hwreg`/`swreg` blobs via
  the proprietary `2F 2A` dual-rfreg opcode (patch 0104) — which is why **reader mode
  and NFC-F listen work** — but those blobs are the reader/poll + F analog profile.
  The Android HAL additionally programs a listen-specific RF configuration that plain
  NCI cannot reproduce. Without it, every NCI command is accepted (status 0x0) yet the
  A listener never physically arms, and no reader (SUBE app *or* Flipper Zero) sees the
  phone, and the field never reaches the eSE.
- Two lesser candidates worth ruling in/out first (cheap, NCI-only):
  - **NFCEE_POWER_AND_LINK_CTRL** (NCI 2.0, GID 0x2 OID 0x3), which mainline lacks and
    Android sends to keep the eSE powered/linked during listen. Try `22 03 02 83 03`.
    If it returns `UNKNOWN_OID (0x08)` the firmware wants another mechanism.
  - Fixing the ordering + removing the 0x09 tag (below) so the listen profile is at
    least internally coherent.

**Bottom line for §3:** the *missing* things are (1) correct ordering, (2) a valid
(not 0x09-rejected) listen SET_CONFIG, and possibly (3) `NFCEE_POWER_AND_LINK_CTRL`;
but the *dominant* blocker is (4) the vendor listen RF profile the plain NCI path does
not load. Items 1-3 are necessary and cheap; item 4 is the ceiling and likely the
real wall.

---

## 4. Corrected byte-exact NCI sequence

Header: `[MT|GID] [OID] [PLEN] payload`. CMD MT=0x2n. GID: CORE=0, RF=1, NFCEE=2.
UID = `04 35 3d 6a f7 54 80`, SAK (SEL_INFO) = `08` (MIFARE). Power states = `3b`.

Two variants: **A** = MIFARE-via-eSE (the real goal), **B** = host NFC-A tag (fallback
/ diagnostic). Both drop the 0x09 offender and use canonical ordering.

### Common pre-step (already done at bring-up)
```
CORE_RESET / CORE_INIT / proprietary rfreg (2F 2A) — unchanged (patches 0101-0104)
NFCEE_DISCOVER (NCI 2.0, empty)      22 00 00      → RSP status0 + NTFs (eSE=0x83)
```

### Variant A — route MIFARE listen to the eSE (0x83)

```
# 1) SET_CONFIG — batched, valid values only. NO LA_* (eSE owns NFC-A identity).
#    NO LF_T3T_FLAGS (that was the 0x09 reject). Suppress F/DEP peer with
#    LF_PROTOCOL_TYPE=0 only. Give discovery a real dwell (TOTAL_DURATION=1000ms).
#    num_params=2: [0x50 len1 00][0x00 len2 E8 03]  -> count = 1+3+4 = 8 = 0x08
20 02 08 02 50 01 00 00 02 E8 03

# 2) RF_DISCOVER_MAP — T2T -> FRAME, LISTEN mode (before discovery, after config).
#    num=1: [proto=02(T2T) mode=02(listen) iface=01(frame)]
21 00 04 01 02 02 01

# 3) NFCEE_MODE_SET(0x83, ENABLE) — enable the eSE that holds the MIFARE card.
22 01 02 83 01
#    expect: RSP status0, then NFCEE_MODE_SET_NTF (opcode 0x202) — benign.

# 4) (try) NFCEE_POWER_AND_LINK_CTRL(0x83, keep powered+linked) — NCI2.0, not in
#    mainline. If RSP is UNKNOWN_OID (0x08), skip it.
22 03 02 83 03

# 5) LMRT — route NFC-A *technology* AND MIFARE *protocol* to eSE 0x83; T2T to host
#    as fallback. power_state = 3b. more=00, num=03.
#    tech  : type00 len03 nfcee83 pwr3b tech00
#    proto : type01 len03 nfcee83 pwr3b proto80  (MIFARE)
#    proto : type01 len03 nfcee00 pwr3b proto02  (T2T -> host, optional)
#    count = 2 + 5+5+5 = 17 = 0x11
21 01 11 00 03 00 03 83 3b 00 01 03 83 3b 80 01 03 00 3b 02

# 6) RF_DISCOVER — NFC-A + NFC-F passive listen (F needed to wake the front-end).
#    num=2: [80 freq1][82 freq1]   (0x80 = NFC-A passive listen, 0x82 = NFC-F PL)
21 03 05 02 80 01 82 01
#    expect (if the RF profile allows it): RF_NFCEE_ACTION_NTF (21 09 ..) then
#    RF_INTF_ACTIVATED_NTF routed to the eSE.
```

### Variant B — host NFC-A tag (diagnostic; MIFARE Classic NOT emulable from host)

```
# 1) SET_CONFIG — batched host NFC-A identity, valid values, NO LF_T3T_FLAGS.
#    LA_BIT_FRAME_SDD=04, LA_PLATFORM_CONFIG=00, LA_NFCID1=<7B>, LA_SEL_INFO=08,
#    LF_PROTOCOL_TYPE=00 (kill NFC-DEP over F), TOTAL_DURATION=E8 03.
#    num_params=6:
#      30 01 04 | 31 01 00 | 33 07 04 35 3d 6a f7 54 80 | 32 01 08 | 50 01 00 | 00 02 E8 03
#    count = 1 +3+3+9+3+3+4 = 26 = 0x1a
20 02 1a 06 30 01 04 31 01 00 33 07 04 35 3d 6a f7 54 80 32 01 08 50 01 00 00 02 E8 03

# 2) RF_DISCOVER_MAP — T2T -> FRAME, LISTEN.
21 00 04 01 02 02 01

# 3) LMRT — NFC-A tech + T2T proto + MIFARE proto to HOST (0x00), pwr 3b.
#    more=00 num=03
#    tech A -> host   : 00 03 00 3b 00
#    proto T2T -> host: 01 03 00 3b 02
#    proto MIFARE->host:01 03 00 3b 80
21 01 11 00 03 00 03 00 3b 00 01 03 00 3b 02 01 03 00 3b 80

# 4) RF_DISCOVER — NFC-A + NFC-F passive listen.
21 03 05 02 80 01 82 01
```

Key deltas from the current (failing) flow:
- **Removed `LF_T3T_FLAGS=00 00`** → eliminates the `status 0x09`.
- **Batched** SET_CONFIG (1 command) instead of 3-5 separate commands.
- **Reordered** to `SET_CONFIG → DISCOVER_MAP → (MODE_SET/PWR_CTRL) → LMRT → DISCOVER`.
- **Added** `NFCEE_POWER_AND_LINK_CTRL(0x83)` attempt (variant A).
- eSE MODE_SET/route target confirmed as **0x83** (not the conf's `{82}`).

---

## 5. Summary table

| Item | Result |
|------|--------|
| Tag giving status 0x09 | **LF_T3T_FLAGS (0x3F)** = `00 00` in the 0113 eSE branch |
| Meaning of 0x09 | core = MESSAGE_SIZE_EXCEEDED; here used per-parameter as "value not acceptable in this state" (inconsistent T3T config: flags=0 without matching LF_T3T_IDENTIFIERS/PMM/MAX) |
| Fix | drop `LF_T3T_FLAGS` (suppress F peer with `LF_PROTOCOL_TYPE=0` only) or send a coherent T3T block; batch SET_CONFIG |
| Real eSE NFCEE id | **0x83** (only protocol = 0x80 MIFARE) — holds the SUBE card |
| UICC/SIM NFCEE id | **0x15** (protocol undetermined, ATR-like TLV) |
| conf `{82}`/`{83}` | AOSP/NFA logical route handles, **not** wire ids; ignore for NCI targeting |
| NFCEE_MODE_SET target | 0x83, status 0x0 → **correct** (the 0x202 "unknown ntf" is the benign MODE_SET_NTF) |
| Why no activation | wrong order + 0x09-rejected LF config (config incoherent) + no NFCEE_POWER_AND_LINK_CTRL + **dominant: vendor listen RF profile not loaded → NFC-A front-end never arms → no RF_NFCEE_ACTION_NTF, no RF_INTF_ACTIVATED_NTF** |
| Corrected sequence | §4 variant A (eSE) / B (host) |

---

## 6. FACT / INFERENCE / UNKNOWN

**FACT**
- Running kernel = 0111+0112+0113, eSE branch (`MODE_SET` only emitted there).
- The three captured SET_CONFIG are LF_PROTOCOL_TYPE(0x50), LF_T3T_FLAGS(0x3F),
  TOTAL_DURATION(0x00) — the 0113 eSE branch — not the LA_* set.
- eSE = NFCEE 0x83, sole protocol 0x80 (MIFARE); UICC = 0x15 (from the NTF byte
  decode in nfc.md).
- MODE_SET targeted 0x83 and returned status 0x0.
- 0x202 = NFCEE_MODE_SET_NTF; 0x201 (as an unknown *ntf* after DISCOVER) =
  NFCEE_DISCOVER_NTF — both unparsed by mainline but legitimate.
- No RF_NFCEE_ACTION_NTF and no RF_INTF_ACTIVATED_NTF ever arrive (nfc.md).
- Vendor HAL disassembly (nfc.md) found no "card-emulation enable" NCI command; the
  proprietary opcodes are rfreg/clock/trace only.
- `LMRT plen 27` ⇒ 5 entries; `RF_DISCOVER_MAP plen 19` ⇒ multi-entry map.

**INFERENCE**
- The failing plen-5 tag is **LF_T3T_FLAGS** (not TOTAL_DURATION, which is universally
  supported); 0x09 = per-parameter "value not acceptable" for an inconsistent T3T
  config with no matching identifiers.
- conf `{82}`/`{83}` are NFA logical handles, not wire ids (HAL consumes them as conf
  keys and remaps; chip enumeration is authoritative).
- Dominant activation blocker is a vendor listen RF-register profile the plain NCI
  path does not load (same wall for host and eSE routes), corroborated by: reader +
  NFC-F listen work (their analog profile is loaded) but NFC-A listen never arms
  despite every command returning status 0x0.

**UNKNOWN**
- The exact body of the failing CORE_SET_CONFIG_RSP (the returned failing tag id) is
  not in the captures — it is inferred from plen/value analysis, not read byte-for-
  byte. Capture with `nci` dynamic debug the full RSP bytes to confirm the tag id
  (expect `40 02 03 09 01 3F`).
- Whether `NFCEE_POWER_AND_LINK_CTRL (22 03 02 83 03)` is accepted (status 0x0) or
  UNKNOWN_OID (0x08) on this firmware — untested.
- The precise contents / opcode stream of the Android listen RF profile (would require
  disassembling the HAL's RF-register update path for a listen-specific profile, or
  capturing the live Android NCI+proprietary stream during a SUBE read). This is the
  decisive unknown for actually lighting the NFC-A listen front-end.
- Whether, once the front-end arms, the eSE would answer end-to-end (Crypto1 is in the
  eSE, so expected to work, but unverified because activation never occurs).
