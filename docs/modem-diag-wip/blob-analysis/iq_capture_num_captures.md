# IQ_CAPTURE num_captures — why it returns 0, the full field-id table, and the RRA question

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Snapdragon 695 / Moto G82 5G)
Base disasm: `clade_dec_36m.bin` (VA base 0xd8000000, valid 0xd8000000..0xdb400000).
Tooling (preserved): `dis36.sh <va> <len>` (llvm-objdump-18 hexagon v66) · `rd.py <va> <n>` (rodata by VA,
auto-selects modem.bNN). rodata segments used: **b21 base 0xc3553000**, **b23 base 0xc8b6a000**.

Legend: **OBSERVED** = instruction/byte/pointer read directly from this image (disasm or rodata) ·
**INFERRED** = deduction from observed facts · **UNKNOWN** = body lives in dlpager-paged pool / runtime NV.

Companion reports (prior sessions, re-verified here): `map_iq_capture.md`, `iq_delivery_channel.md`,
`radio_config_unpack.md`, `map_ftm_framework.md`, `iq_final_values.md`.

---

## 0. EXECUTIVE SUMMARY — DIRECT ANSWERS

| # | Question | Answer | Confidence |
|---|----------|--------|-----------|
| a | Full 75-entry field-id name table @0xc906c720 | **Recovered in full (§1).** NUM_OF_CAPTURES is field_id **0x2c (44)**; NUM_OF_SAMPLES is **0x0e (14)**; IQ_CAPTURE_TYPE is **0x27 (39)**. | **OBSERVED** |
| b | What the mode-105 IQ_CAPTURE (cmd=6) unpacker requires for num_captures>0 | **NUM_OF_CAPTURES (0x2c) is NOT consumed by this build's IQ_CAPTURE unpacker — it is silently dropped (handler 0xd818a210, log-only, no store).** The capture count is driven by **NUM_OF_SAMPLES (0x0e)** + **ACTION_ACQUIRE** + a valid active carrier. num_captures=0 with error_code=0 ⇒ the ACQUIRE produced zero samples: NUM_OF_SAMPLES default/0, or the capture never armed because the RF carrier was not activated. (§2, §3) | **OBSERVED (drop) / INFERRED (root cause)** |
| c | Is ASSIGN_RRA a hard prerequisite? | **No separate "ASSIGN_RADIO_ALLOCATION" is required by the mode-105 IQ_CAPTURE path.** The hard prerequisite is that the **active-carrier pointer 0xca79c494 be non-NULL**, which is written only by the carrier-activate path (0xd81bd018 → 0xd8273b2c → setter 0xd8279264), gated by RF-cal-mode (gp+0x740==2) and RF-ctx (gp+0x7000!=0). TRM_RRA is a **separate FTM command** (module slot 3, its own name table @0xc906cce0) used in the mode-30 LTE-NS resource-manager flow, not on the mode-105 RF-TEST IQ path. (§4) | **OBSERVED (gates/setter) / INFERRED (RRA not required)** |

---

## 1. FULL 75-ENTRY FIELD-ID NAME TABLE @0xc906c720  [OBSERVED]

Pointer array in **b23** @0xc906c720 (index = field_id). Strings resolve into **b21** (0xc414bxxx /
0xc37bexxx). All 75 entries recovered byte-exact. This is the shared RX_MEASURE/IQ_CAPTURE wire name table.

| fid | 0x | name | | fid | 0x | name |
|----:|----|------|-|----:|----|------|
| 0 | 0x00 | UNASSIGNED                     | | 38 | 0x26 | NO_TONE_SENSITIVITY_STDDEV |
| 1 | 0x01 | RX_CARRIER                     | | 39 | 0x27 | **IQ_CAPTURE_TYPE** |
| 2 | 0x02 | RFM_DEVICE                     | | 40 | 0x28 | MEAS_FREQUENCY_ERROR |
| 3 | 0x03 | EXPECTED_AGC                   | | 41 | 0x29 | INPUT_CW_TONE_FREQUENCY |
| 4 | 0x04 | RX_AGC                         | | 42 | 0x2a | RX_FREQUENCY_ERROR |
| 5 | 0x05 | LNA_GS                         | | 43 | 0x2b | APPLY_FREQUENCY_ERROR_TX |
| 6 | 0x06 | SIG_PATH                       | | 44 | 0x2c | **NUM_OF_CAPTURES** |
| 7 | 0x07 | RX_MODE                        | | 45 | 0x2d | IQ_SAMPLE_DEBUG_INFO_0 |
| 8 | 0x08 | RX_SLOT                        | | 46 | 0x2e | IQ_SAMPLE_DEBUG_INFO_1 |
| 9 | 0x09 | NUM_OF_BURST                   | | 47 | 0x2f | IQ_SAMPLE_DEBUG_INFO_2 |
| 10 | 0x0a | SENSITIVITY                   | | 48 | 0x30 | SUB_TECH |
| 11 | 0x0b | CTON                         | | 49 | 0x31 | RSB_FREQ |
| 12 | 0x0c | PEAK FREQ                     | | 50 | 0x32 | RSB_PWR_DBC |
| 13 | 0x0d | **FETCH_IQ**                 | | 51 | 0x33 | RSB_MEAS_INT |
| 14 | 0x0e | **NUM_OF_SAMPLES**           | | 52 | 0x34 | MEAS_TYPE |
| 15 | 0x0f | **IQ_DATA_FORMAT**           | | 53 | 0x35 | NUM_OF_TONES |
| 16 | 0x10 | **SAMP_FREQ**                | | 54 | 0x36 | MULTI_TONES_SENSITIVITY |
| 17 | 0x11 | OVERRIDE_LNA                 | | 55 | 0x37 | MULTI_TONES_PEAK_FREQ |
| 18 | 0x12 | RX_GAIN_CTL_TYPE             | | 56 | 0x38 | MULTI_TONES_CTON |
| 19 | 0x13 | NO_TONE_SENSITIVITY          | | 57 | 0x39 | WAVE_TYPE |
| 20 | 0x14 | NO_TONE_SENSITIVITY_SPUR_NUM | | 58 | 0x3a | RB_OFFSET |
| 21 | 0x15 | NO_TONE_SENSITIVITY_SPUR_FERQ_OFFSET | | 59 | 0x3b | MEAS_BER |
| 22 | 0x16 | NO_TONE_SENSITIVITY_SPUR_LEVEL | | 60 | 0x3c | XPORT_BLKS_ERR |
| 23 | 0x17 | BEAM_ID                      | | 61 | 0x3d | XPORT_BLKS_TOT |
| 24 | 0x18 | TIME_US                      | | 62 | 0x3e | WAVE_ACTION |
| 25 | 0x19 | SUB_FRAME_CONFIG             | | 63 | 0x3f | AGC_DBFS |
| 26 | 0x1a | NUM_AVERAGES                 | | 64 | 0x40 | WVFM_IMMED_TRIGGER |
| 27 | 0x1b | RX_AGC_MIN                   | | 65 | 0x41 | DROOP_COMPENSATION |
| 28 | 0x1c | RX_AGC_MAX                   | | 66 | 0x42 | RX_IF_BACKOFF |
| 29 | 0x1d | RX_AGC_STDDEV                | | 67 | 0x43 | RX_AGC_OUTER_LOOP |
| 30 | 0x1e | CTON_MIN                     | | 68 | 0x44 | DL_WAVEFORM_TYPE |
| 31 | 0x1f | CTON_MAX                     | | 69 | 0x45 | WBEE_DBFS10 |
| 32 | 0x20 | CTON_STDDEV                  | | 70 | 0x46 | FFTEE_DBFS10 |
| 33 | 0x21 | SENSITIVITY_MIN             | | 71 | 0x47 | POWER_MODE_TYPE |
| 34 | 0x22 | SENSITIVITY_MAX             | | 72 | 0x48 | PATH_SWITCH |
| 35 | 0x23 | SENSITIVITY_STDDEV         | | 73 | 0x49 | PATH_STATE |
| 36 | 0x24 | NO_TONE_SENSITIVITY_MIN     | | 74 | 0x4a | GAIN_DELTA |
| 37 | 0x25 | NO_TONE_SENSITIVITY_MAX     | |    |      | |

Repro: `python3 rd.py 0xc906c720 75` then resolve each pointer in b21/b23.
(Note: the brief's "known IQ field-ids" are confirmed exactly — 1=RX_CARRIER, 2=RFM_DEVICE, 4=RX_AGC,
13=FETCH_IQ, 14=NUM_OF_SAMPLES, 15=IQ_DATA_FORMAT, 16=SAMP_FREQ, 17=OVERRIDE_LNA, 39=IQ_CAPTURE_TYPE,
**44=NUM_OF_CAPTURES**.)

---

## 2. WHAT THE IQ_CAPTURE (cmd=6) UNPACKER ACTUALLY CONSUMES  [OBSERVED]

### 2.1 Dispatch mechanics (unpacker body function, entry 0xd81893a4 / per-field dispatch 0xd8189818)
The per-TLV field dispatch reconstructs field_id and routes in two groups:

```
PRIMARY (0xd81897cc..0xd818981c):
  d81897cc: r4 = and(r26,#0x7f)              ; r26 = field_id
  d81897e8: r2 = add(r4,#-0x1)               ; r2 = field_id-1
  d81897f0: p0 = cmp.gtu(r2,#0x19)           ; (field_id-1) > 0x19 ?
  d818980c: if (p0) jump 0xd8189b38          ;   yes -> group-2 feeder / log
  d8189818: r2 = memw(r2<<#0x2 + ##0xc37c0828) ; else jump-table[field_id-1]
  d818981c: jumpr r2

GROUP-2 dispatch (0xd818a0ac..0xd818a0e0, reached via 0xd8189b38):
  d818a0c8: r2 = add(r1,#-0x1)               ; r1 = field_id ; r2 = field_id-1
  d818a0cc: if (cmp.gtu(r2,#0x18)) jump 0xd818a210  ; (field_id-1) > 0x18 -> DROP
  d818a0dc: r5 = memw(r2<<#0x2 + ##0xc37c0890) ; else jump-table2[field_id-1]
  d818a0e0: jumpr r5
```
- Jump-table1 @**0xc37c0828**, jump-table2 @**0xc37c0890** (= 0xc37c0828 + 0x68). **OBSERVED.**
- **0xd818a210 = the "default2" handler: log-only, NO STORE** (§2.2).
- **0xd8189b38 = the group-2 feeder**: it logs the field via the internal 28-name struct @0xc906c8f8
  and advances to the group-2 dispatch. **OBSERVED.**

### 2.2 The drop handler 0xd818a210 does not store the value  [OBSERVED, byte-exact]
```
d818a210: call 0xd80d77a8                    ; F3 debug log ("unknown/unhandled field")
d818a21c: r23 = add(r23,#0xc)                ; advance TLV cursor (stride 0xc)
d818a220: r22 = add(r22,#0x1)                ; next index
d818a224: if (!cmp.eq(r22,r18)) jump 0xd818a04c ; loop; (no write to any ctx/descriptor slot)
```
There is **no `memw(ctx+..)=value`** anywhere in this handler. The TLV value is discarded.

### 2.3 Which field_ids actually store vs drop  [OBSERVED]
Storing handlers read a per-field config default from `memw(r25+0)` (= config-struct base) at a fixed
offset, then write the descriptor slot. Verified bodies:

| field | name | handler | store target / cfg_default | consumed? |
|------:|------|---------|-----------------------------|-----------|
| 0x01 | RX_CARRIER      | 0xd8189864 | descriptor; selects carrier | **yes** |
| 0x02 | RFM_DEVICE      | 0xd81898bc | cfg+0x3c | yes |
| 0x04 | RX_AGC          | 0xd81898ec | cfg+0x28 (memuh,16b) | yes |
| 0x0d | FETCH_IQ        | 0xd8189964 | cfg+0x64 → common store 0xd81898a0 (writes WIRE value) | **yes** |
| 0x0e | NUM_OF_SAMPLES  | 0xd8189994 | cfg+0x6c (memub, 8b default) | **yes** (see §3.2 caveat) |
| 0x0f | IQ_DATA_FORMAT  | 0xd81899d8 | cfg+0x68 | **yes** |
| 0x10 | SAMP_FREQ       | 0xd8189934 | cfg+0x60 | **yes** |
| 0x11 | OVERRIDE_LNA    | 0xd8189aa8 | cfg+0x74 | yes |
| 0x27 | IQ_CAPTURE_TYPE | 0xd818a1a8 | memw(ctx+0x48) | **yes** |
| **0x2c** | **NUM_OF_CAPTURES** | **0xd818a210** | **— (drop/log-only)** | **NO** |

**OBSERVED result: field 0x2c (NUM_OF_CAPTURES=44) → 0xd818a210.** Walk: field_id-1 = 0x2b > 0x18 at the
group-2 gate 0xd818a0cc ⇒ jump 0xd818a210 ⇒ logged and discarded. Sending NUM_OF_CAPTURES has **no effect
whatsoever** on this build's IQ_CAPTURE command.

### 2.4 The internal normalized 28-field struct @0xc906c8f8 has no NUM_OF_CAPTURES  [OBSERVED]
The capture/repack logic operates on a normalized struct (names @0xc906c8f8), fully recovered:
```
0 UNASSIGNED  1 SUBSCRIBER  2 TECHNOLOGY  3 TX_CARRIER  4 RX_CARRIER  5 RFM_DEVICE  6 SIG_PATH
7 ACTION_GETCFG  8 ACTION_ACQUIRE  9 ACTION_FETCH  10 ACTION_EST_SENS  11 ACTION_CTON
12 ACTION_PEAK_FREQ  13 IQ_SOURCE  14 SAMP_SIZE  15 DATA_FORMAT  16 SAMP_FREQ  17 MAX_DIAG_SIZE
18 SAMP_OFFSET  19 NUM_SAMP_BYTES  20 EST_SENS  21 CTON  22 PEAK_FREQ  23 EXPECTED_AGC
24 LNA_GAIN_STATE  25 OVERRIDE_LNA_GAIN_STATE  26 GAIN_CONTRL  27 SUB_TECH
```
There is **no "num_captures" / "NUM_OF_CAPTURES" field** in the internal model. The sample count is carried
by **NUM_OF_SAMPLES (wire 0x0e)** → acquire count; ACTION_ACQUIRE arms the buffer; NUM_SAMP_BYTES/SAMP_OFFSET
page the fetch. **OBSERVED (names) / INFERRED (NUM_OF_CAPTURES is a RX_MEASURE-family statistics field, not an
IQ acquire control in this build).**

---

## 3. WHY num_captures=0 WITH error_code=0  [OBSERVED gates / INFERRED root cause]

"error_code=0, num_captures=0" means the command parsed and returned success but the ACQUIRE produced zero
samples. Two observed mechanisms explain this:

### 3.1 No active carrier → the acquire has nothing to capture  [OBSERVED chain]
The IQ_CAPTURE deferred executor (decode-A, mode==5) calls the active-carrier getter:
```
0xd826d3e4 (executor) ... 0xd826d40c: call 0xd827923c   ; getter(active-carrier ptr @0xca79c494)
GETTER 0xd827923c:
  d8279240: r0 = memw(##0xca79c494)
  d8279244: if (r0 != 0) return r0
  d8279248: call 0xd80d8998  -> err_fatal (SSR)          ; NULL -> hard crash
```
- If the carrier ptr is NULL the command **SSRs** (not a clean num_captures=0). So an error_code=0 result
  implies the ptr was **non-NULL** (carrier context existed) — but the carrier may still not have been
  *actively tuned/streaming*, so the sample buffer fill returns 0. **OBSERVED (getter) / INFERRED (empty fill).**

### 3.2 NUM_OF_SAMPLES handler writes the config default byte, not necessarily the wire u32  [OBSERVED, needs live confirm]
```
NUM_OF_SAMPLES handler 0xd8189994:
  d81899ac: r3 = memub(r3+#0x6c)             ; r3 = CONFIG DEFAULT (8-bit) from cfg+0x6c
  d81899d4: memb(r2+#0x4) = r3               ; store the default byte into the descriptor
  d81899c0..cc: memb(slot+0x5..0xb) = 0      ; zero the rest of the slot
```
Unlike FETCH_IQ/SAMP_FREQ (which jump to the common store 0xd81898a0 that writes the **wire** value r20),
the NUM_OF_SAMPLES handler writes `memub(cfg+0x6c)` — a **single-byte config default** — into the slot and
zeroes the high bytes. **OBSERVED.** If `cfg+0x6c` defaults to 0 (runtime NV/config), the effective sample
count is 0 ⇒ ACQUIRE fills 0 samples ⇒ **num_captures=0**. **INFERRED (cfg default = UNKNOWN static; it is
read from `memw(r25+0)+0x6c` which is a runtime config-struct).**

> Practical consequence: on this build the IQ sample count is bounded/overwritten by the **config-struct
> default** at cfg+0x6c, and the num-captures concept (field 0x2c) is not wired. To get num_captures>0 you
> must (a) ensure the carrier is actually activated/streaming (§3.1 / §4) and (b) exercise ACTION_ACQUIRE
> with a non-zero effective NUM_OF_SAMPLES. If cfg+0x6c default is 0 and the wire value is being clamped to
> the default, the sample count must be set via the path the config-struct expects — verify the live value
> of `memw(cfg+0x6c)` and whether a larger NUM_OF_SAMPLES actually lands, since the handler's byte-store
> suggests the wire value may not propagate as a full u32.

### 3.3 The required properties for a non-empty IQ_CAPTURE ACQUIRE  [OBSERVED fields / INFERRED minimum set]
From the storing handlers (§2.3) and the internal model (§2.4), the properties that matter:
- **RX_CARRIER (0x01)** — selects the carrier descriptor. **Required.**
- **NUM_OF_SAMPLES (0x0e)** — sample count (subject to §3.2 caveat). **Required, must be non-zero/effective.**
- **SAMP_FREQ (0x10)** — sample rate (WB log2 enum). **Required.**
- **IQ_DATA_FORMAT (0x0f)** — 8-bit vs 16-bit buffer. **Required.**
- **IQ_CAPTURE_TYPE (0x27)** — IQ source (raw vs post-decimation). Recommended.
- **FETCH_IQ (0x0d)** — 0 for ACQUIRE phase, 1 for FETCH phase.
- **NUM_OF_CAPTURES (0x2c)** — **IGNORED (dropped). Do not rely on it.**

---

## 4. IS ASSIGN_RRA A HARD PREREQUISITE FOR MODE-105 IQ_CAPTURE?  [OBSERVED]

**No.** There are two distinct things that the brief's wording ("ASSIGN_RADIO_ALLOCATION cmd 1004, mode 30
FTM_MODE_LTE_NS") can refer to; neither is a required step on the mode-105 (FTM_RF_TEST) IQ path:

### 4.1 The real prerequisite is the active-carrier pointer, set by carrier-activate  [OBSERVED]
`map_ftm_framework.md` §3 (re-verified): IQ_CAPTURE/RX_MEASURE require `0xca79c494 != NULL`. The **only**
writer is the setter 0xd8279264, reached solely from APPLY 0xd8273b2c inside the carrier-activate handler
**0xd81bd018**, which is gated by:
- **G-mode** (0xd81bd160): `memb(gp+0x740) == 2`  (RF cal mode active).
- **G-ctx** (0xd81bd28c→0xd8286240): `memw(gp+0x7000) != 0` (RF instance/ctx present).

The chain that satisfies these is: **FTM set RF-cal-mode → TECH_ENTER (LTE, sub 0x0d, TECH=1) → RADIO_CONFIG
(BAND + EARFCN/CHANNEL + BANDWIDTH + RX_CARRIER + RFM_DEVICE)**, whose downstream FTM-LTE activate
(`rflte_mc_carrier_activate` / `rflte_ftm_mc_wakeup`) makes 0xd81bd018 pass its gates and write the ptr.
**OBSERVED (gates, setter, single-writer reachability per prior reach.py) / INFERRED (activate is the trigger).**

### 4.2 TRM_RRA is a separate FTM command, not on the mode-105 IQ path  [OBSERVED]
The firmware does have an FTM "Transceiver Resource Manager / Radio Resource Assignment" command:
- **Module slot 3 = TRM_RRA**, unpacker **0xd86c0fe4**, repack 0xd86c240c, bufsz 0x4d740,
  F3 fmt `[FTM.RFTEST][TRM_RRA][UNPACK]` @**0xc4168270** and REPACK @0xc41682b2. **OBSERVED.**
- Its field-name table is @**0xc906cce0** (38 entries): `SUB_IDX, TECH, BAND, CHAN, BANDWIDTH, RXTX, CHAIN,
  CARRIER_IDX, RESOURCE, REASON, PATH_FUNCTIONALITY, DEVICE, SIGNAL_PATH, ANTENNA_PATH, ANTENNA_NUM, PLL_ID,
  TIME_US, SUB_TECH, RFM_CONTEXT, FILTER_TYPE, BWP_START_LOC, SCS_HZ, MSIM_TUNE_AWAY, NB_ID, LANE_ID,
  RESERVED1, RESERVED2, CARRIER_CONTROL, SRS_SOURCE_CARRIER, SRS_CS_STYLE, SRS_CS_IMPACTED,
  SRS_ANTENNA_NUMBER, ASDIV_CONFIG_MASK, FETCH_REGROUP_TABLE, REGROUP_COMBO_NUMBER,
  ANT_NUMBER_PER_REGROUP_COMBO, TX_SWITCH_TYPE`. **OBSERVED.**
- This is the resource-manager assignment used by the **non-signaling LTE (mode 30 / LTE-NS)** style flow.
  Its unpacker body (like all unpackers) is a pure TLV parser; it does **not** call the active-carrier
  setter/getter directly. **OBSERVED** (scan of 0xd86c0fe4 body: no `call 0xd8279264/0xd827923c`).

**Conclusion:** the mode-105 IQ_CAPTURE path does **not** require a prior TRM_RRA / ASSIGN_RRA command. It
requires the **active-carrier pointer** to be populated, which comes from the RF-cal-mode + TECH_ENTER +
RADIO_CONFIG activate chain (§4.1). TRM_RRA is a parallel/independent FTM resource command. If a live
sequence that worked used a QUERY_RRA (cmd 4) safely, that is consistent with the QUERY-safe list
(map_ftm_framework.md §3.7) — queries don't reach the getter with num_tlv=0.

---

## 5. ACTIONABLE RECOMMENDATION

1. **Stop sending NUM_OF_CAPTURES (0x2c).** It is dropped by handler 0xd818a210 — no effect.
2. **Guarantee the active carrier before IQ_CAPTURE.** Verify `memw(0xca79c494) != 0` (or per-tech entered
   flag `memb(0xca7897b0 + tech*8) == 1`) after the RF-cal-mode → TECH_ENTER(LTE) → RADIO_CONFIG(BAND+EARFCN+
   BW+RX_CARRIER) sequence. Without it you get SSR, not num_captures=0 — so error_code=0/num_captures=0 means
   the carrier context existed but was not streaming.
3. **Drive the count via NUM_OF_SAMPLES (0x0e), with ACTION_ACQUIRE (FETCH_IQ=0 first).** Confirm live that
   a non-zero NUM_OF_SAMPLES actually lands — the handler writes `memub(cfg+0x6c)` (default byte), so if the
   config default is 0 the effective count may be 0. Read `memw(cfg+0x6c)` live to confirm the default and
   whether the wire u32 propagates.
4. **Also supply SAMP_FREQ (0x10), IQ_DATA_FORMAT (0x0f), IQ_CAPTURE_TYPE (0x27).** These are consumed.
5. **TRM_RRA is optional** for mode-105 IQ; don't treat ASSIGN_RRA as the missing step.

---

## 6. FACT / INFERENCE / UNKNOWN — with VAs

**OBSERVED (this image):**
- Full 75-entry name table @0xc906c720 (§1). NUM_OF_CAPTURES = field 0x2c; NUM_OF_SAMPLES = 0x0e.
- IQ_CAPTURE field dispatch: primary jump-table @0xc37c0828 (gate `(field_id-1)>0x19` @0xd81897f0 →
  0xd8189b38), group-2 jump-table @0xc37c0890 (gate `(field_id-1)>0x18` @0xd818a0cc → 0xd818a210).
- field 0x2c → 0xd818a210 = **log-only, no store** (byte-exact, §2.2).
- Storing handler offsets: FETCH_IQ cfg+0x64, NUM_OF_SAMPLES cfg+0x6c (memub/8b), IQ_DATA_FORMAT cfg+0x68,
  SAMP_FREQ cfg+0x60, IQ_CAPTURE_TYPE memw(ctx+0x48) (§2.3).
- Internal 28-field model @0xc906c8f8 has no num_captures field (§2.4).
- IQ executor 0xd826d3e4 calls getter 0xd827923c; getter err_fatals on NULL 0xca79c494 (§3.1).
- Active-carrier setter 0xd8279264 single-writer = APPLY 0xd8273b2c in carrier-activate 0xd81bd018, gated by
  gp+0x740==2 and gp+0x7000!=0 (§4.1).
- TRM_RRA = module slot 3, unpacker 0xd86c0fe4, name table @0xc906cce0, F3 @0xc4168270 (§4.2).

**INFERRED:**
- NUM_OF_CAPTURES is a RX_MEASURE-family statistics field; in this build it is simply not an IQ acquire
  control (dropped) — so num_captures>0 is governed by NUM_OF_SAMPLES + a streaming carrier, not by 0x2c.
- error_code=0/num_captures=0 ⇒ ACQUIRE fill returned 0: either cfg+0x6c default is 0 / wire count not
  propagating, or the carrier is not actively streaming at the configured BAND/EARFCN/BW.
- Mode-105 IQ does not require a prior TRM_RRA/ASSIGN_RRA.

**UNKNOWN (runtime / dlpager-paged):**
- Live value of config-struct `cfg+0x6c` (NUM_OF_SAMPLES default) and whether the wire u32 lands — read live.
- The RFLTE prop_action bodies (`rflte_ftm_iq_capture_prop_action_{8,16}bit`) and the actual sample-fill /
  num_captured computation (seg27 dlpager pool; only strings/contract observed, body not disassembled).
- Exact enum values for IQ_DATA_FORMAT / IQ_CAPTURE_TYPE / SAMP_FREQ (RFC/NV).

---

## 7. REPRODUCE
```bash
python3 rd.py 0xc906c720 75                 # full field-id name table (§1) — resolve ptrs in b21/b23
python3 rd.py 0xc906c8f8 28                 # internal 28-field model (§2.4)
./dis36.sh 0xd81897c0 0x60                  # primary field dispatch + gate (§2.1)
./dis36.sh 0xd818a0ac 0x40                  # group-2 dispatch + gate -> 0xd818a210 (§2.1)
./dis36.sh 0xd818a210 0x20                  # drop handler: log-only, no store (§2.2)
./dis36.sh 0xd8189994 0x44                  # NUM_OF_SAMPLES handler: memub(cfg+0x6c) store (§3.2)
./dis36.sh 0xd826d3e4 0x40                  # IQ executor -> getter 0xd827923c (§3.1)
./dis36.sh 0xd86c0fe4 0x200                 # TRM_RRA unpacker (§4.2)
python3 rd.py 0xc906cce0 38                 # TRM_RRA field-name table (§4.2)
```

---

## 8. ACQUIRE trigger & num_captures computation (live-driven)

**Context (OBSERVED, this session's live run):** the sequence
`DIAG_CONTROL_F(3) → SET_CALIBRATION_STATE(mode20,cmd137,state1)=OK →
RADIO_CONFIGURE(mode105,cmd1, BAND3 CH1575 BW5 RX_CARRIER0)=OK →
IQ_CAPTURE ACQUIRE(mode105,cmd6, RX_CARRIER0 NUM_OF_SAMPLES1024 SAMP_FREQ30720000
IQ_DATA_FORMAT1 IQ_CAPTURE_TYPE0 FETCH_IQ0)` returned (LE) `4b0b 6900 0600 5400
1600 00000000 00008000` → **error_code=0, num_captures=0, no SSR** (so the
active-carrier ptr `0xca79c494` was non-NULL — carrier context existed).

This section answers the four brief questions by disassembling the unpacker→
executor→action chain on `clade_dec_36m.bin`. It **corrects one claim in §2.3/§3.2**
(see §8.2) about where the wire value lands.

### 8.0 DIRECT ANSWERS

| # | Question | Answer | Conf. |
|---|----------|--------|-------|
| 1 | Which wire field makes action=ACQUIRE? | **NONE. There is no wire field_id that maps to ACTION_ACQUIRE (model idx 8).** Both IQ_CAPTURE field jump-tables (51 slots @0xc37c0828 + group-2 @0xc37c0890) were fully enumerated: every slot is a value-store handler or the reject/drop handler — **no slot sets any ACTION_* flag.** The three action flags (GETCFG=7 / ACQUIRE=8 / FETCH=9) are produced **programmatically** by the deferred executor from a command-type selector (`r0 = mux(sel>2, #0x8, #0x0)` @**0xd82793ac**), not from a TLV. **FETCH_IQ (wire 0x0d) is the only wire action input; it maps to ACTION_FETCH (9), never to ACQUIRE.** | **OBSERVED** |
| 2 | cfg+0x6c default & does wire NUM_OF_SAMPLES override it? | cfg+0x6c is an **8-bit field in a heap-allocated runtime config-struct** (alloc'd @0xd8182654 via 0xd8062414; value from NV/RFC, **not a static constant** — UNKNOWN statically, must be read live). The NUM_OF_SAMPLES handler **0xd8189994** copies `memub(cfg+0x6c)` into the normalized-model value slot and **does NOT use the wire u32** (confirmed: handler reads only r22/r25, never r0=wire value, §8.2). Wire NUM_OF_SAMPLES (0x0e) maps to internal model **SAMP_SIZE (14)**, not a free "num_samples" u32. | **OBSERVED (mechanism) / UNKNOWN (default value)** |
| 3 | num_captures computation / gate | The ACQUIRE itself is driven by the LTE-ML1 async capture state machine (`LTE_ML1_SM_STM_ASYNC_SAMP_CAPTURE_CNF`, b21@11540407). The executor **0xd826d3e4** → worker **0xd82682b8** sets the command action-state byte (`memb(0xca79a8e0[cmd]+0x1fe)=action` @**0xd8279300/0xd827931c**) and invokes the ACQUIRE path (selector 5, §8.3) **unconditionally** when the command runs — there is no acquire-vs-nothing branch keyed on a wire flag. num_captured (`num_samples_captured <= num_samples_to_capture`, assert in seg27) is written by the paged RFLTE prop-action body (dlpager pool, body not in this image). **The gate that produces 0 is upstream: num_samples_to_capture = the effective SAMP_SIZE value = cfg+0x6c default (§8.2), and/or the RX chain not actively streaming (§8.4).** | **OBSERVED (dispatch/state) / INFERRED (fill=0 cause)** |
| 4 | Does ACQUIRE need an active RX stream that RADIO_CONFIGURE doesn't establish? | **Yes, strongly indicated.** ACQUIRE is serviced by LTE-ML1 `ASYNC_SAMP_CAPTURE` which requires the RxLM/DTR-RX chain to be activated and running (`rflm_dtr_rx_activate_chain`, map_iq_capture.md §9). A non-NULL `0xca79c494` (what we have) proves carrier *context* exists but **not** that the RxFE is streaming. No separate "start rx" RFTEST command exists; the streaming state is a product of the full cal-mode + tune activate, and may not be armed by RADIO_CONFIGURE alone in this invocation. | **INFERRED (strong, from ML1 contract + getter semantics)** |

### 8.1 The full field dispatch has NO ACTION setter  [OBSERVED, byte-exact]

Primary dispatch (0xd81897cc..0xd818981c): `r2 = field_id-1`; `if (r2>0x19) → group-2`;
else `jumpr memw(r2<<2 + 0xc37c0828)`. **Full 51-entry jump-table1 @0xc37c0828 enumerated**
(`python3 rd.py 0xc37c0828 51`): the only non-reject handlers are
`0xd8189864(RX_CARRIER) 0xd81898bc(RFM_DEVICE) 0xd8189874(EXPECTED_AGC)
0xd81898ec(RX_AGC) 0xd8189964(FETCH_IQ) 0xd8189994(NUM_OF_SAMPLES)
0xd81899d8(IQ_DATA_FORMAT) 0xd8189934(SAMP_FREQ) 0xd8189aa8(OVERRIDE_LNA)
0xd8189a08(RX_GAIN_CTL) 0xd8189ad8(BEAM_ID) 0xd8189b08(SUB_FRAME_CONFIG)`; the rest are
reject `0xd8189b38`. **Group-2 jump-table2 @0xc37c0890 enumerated** (25 entries):
`0xd818a0e4(NUM_AVERAGES) 0xd818a0f4(RX_AGC_MIN) 0xd818a154/158(RX_AGC_MAX/STDDEV)
0xd818a104(CTON_MIN) 0xd818a178/190/198(CTON_STDDEV/SENS_MIN/MAX)
0xd818a1c8(NO_TONE_SENS_STDDEV) 0xd818a1a8(IQ_CAPTURE_TYPE) 0xd818a1c4(MEAS_FREQ_ERR)
0xd818a1d4(SUB_TECH) 0xd818a1e4(RSB_FREQ) 0xd818a1f0(RSB_PWR_DBC)`; all others reject
`0xd818a210`. **Not one entry in either table writes an ACTION_* flag.** ⇒ *You cannot
"send field 0x?? = ACQUIRE"; no such field exists on the wire for this build.* **OBSERVED.**

### 8.2 CORRECTION: the IQ_CAPTURE field handlers store a CONFIG value, not the wire u32  [OBSERVED]

The §2.3/§3.2 claim that FETCH_IQ/SAMP_FREQ store the *wire* value via the common store
0xd81898a0 is **wrong**; re-reading the handlers shows they all store a **config-struct
value** into the normalized-model entry's value slot (+4..+7). The wire value r20 is written
by the *main loop* only into the entry's **index/metadata** slot (+0..+3), which selects the
model name index (log reads +0..+2 as the 0xc906c8f8 name index @0xd8189ba8). Byte-exact:
```
SAMP_FREQ   0xd8189934: r3 = memw(cfg+0x60)  (0xd818994c) ; jump 0xd81898a0 stores r3
IQ_DATA_FMT 0xd81899d8: r3 = memw(cfg+0x68)  (0xd81899f0) ; jump 0xd81898a0 stores r3
FETCH_IQ    0xd8189964: r3 = memw(cfg+0x64)  (0xd818997c) ; jump 0xd81898a0 stores r3
NUM_SAMPLES 0xd8189994: r3 = memub(cfg+0x6c) (0xd81899ac) ; stores r3 (8b) to +4, zero +5..+b
common store 0xd81898a0: memb(dest+4)=r3; memb(+5)=r3>>8; memb(+6)=r3>>16; memb(+7)=r3>>24
```
None of these handlers reads `r0` (the wire TLV value passed by the apply-loop callr) — the
stored **value is unconditionally the config-struct field.** For NUM_OF_SAMPLES specifically
it is an **8-bit** read (`memub`), so even if cfg+0x6c held a value, the model sample-count is
bounded to ≤255 from that path. **OBSERVED.**

> Consequence: on this build the normalized-model sample count (what the ACQUIRE consumes) is
> **cfg+0x6c (an 8-bit config default), not the wire NUM_OF_SAMPLES=1024.** If cfg+0x6c is 0
> at runtime, num_samples_to_capture=0 ⇒ the prop-action fills 0 ⇒ **num_captures=0** with a
> clean error_code=0 — exactly the observed result. **OBSERVED (store path) / INFERRED (=root cause).**

The wire→config application is a *separate earlier pass* (apply-loop @0xd8189524): per-TLV it
reads `model_idx = memw(tlv+0x24)`, gates `(model_idx-1) > 0x13 → reject` (@0xd818954c, so only
model idx **1..0x14** are applicable), and `callr memw(ctx + model_idx*0xc + 0x8)` with the wire
value. Model idx 8 (ACTION_ACQUIRE) *is* inside 1..0x14, so **if** any wire field translated to
idx 8 it would be applied — **but none does** (§8.1); the actions are command-type-driven.

### 8.3 Where ACQUIRE (model idx 8) actually comes from  [OBSERVED]

Deferred executor **0xd826d3e4** (reached live via `sub_command 0x1002 → 0xd86fd0f4 →
0xd8272cd8 →(callr r5)→ 0xd826d3e4`, OUT_rx_measure.md §3). Inside, the IQ/measure action
index is computed by **0xd8279358** (selector→model-index): at **0xd82793a0-0xd82793b4**
```
r17 = sel-1
r0 = mux(cmp.gtu(r17,#1), #0x8, #0x0)   ; sel>2  → r0 = 8  (= ACTION_ACQUIRE)
if (cmp.eq(sel,#3)) r0 = #0x0
... (CTON=0x15, etc.)
```
and **0xd8279450**: `r0 = mux(sel==1, #0x3, #0x8)`. The ACQUIRE path is entered via
`0xd8283554 → 0xd827a12c → 0xd8279358 (r1=5)` ⇒ selector 5 ⇒ **index 8 = ACQUIRE**. The command
action-state byte is persisted by **0xd8279300** → `memb(0xca79a8e0[cmd_id]+0x1fe) = action`
(state table @**0xca79a8e0**, stride ≥0x204, indexed by command_id ≤0x31). **OBSERVED.**
⇒ *IQ_CAPTURE(cmd6) runs ACQUIRE by command type; FETCH_IQ=0 does not need to "select" it, and
FETCH_IQ=1 only flips to ACTION_FETCH (9). The ACQUIRE is already firing; it just captures 0.*

### 8.4 Why the fill is 0  [OBSERVED chain / INFERRED cause]

- Getter **0xd827923c** (`memw(0xca79c494)`, err_fatal on NULL) returned non-NULL in the live
  run (no SSR) ⇒ carrier context present. **OBSERVED.**
- The capture is serviced by **LTE-ML1 ASYNC_SAMP_CAPTURE** (`LTE_ML1_SM_STM_ASYNC_SAMP_CAPTURE_CNF`
  b21@11540407). This path requires the RxLM/DTR-RX chain to be *active/streaming*
  (`rflm_dtr_rx_activate_chain`), which is a state beyond "carrier ptr non-NULL." A configured-
  but-not-streaming RxFE yields `num_samples_captured = 0`. **OBSERVED (ML1 contract) / INFERRED
  (fill=0).**
- Combined with §8.2 (sample count sourced from the 8-bit cfg+0x6c, not the wire 1024), the two
  compatible root causes are: **(a) cfg+0x6c default = 0** (num_samples_to_capture=0), and/or
  **(b) RX not actively streaming.** Both produce error_code=0 / num_captures=0. **INFERRED.**

### 8.5 DELIVERABLE — what to change

1. **There is NO wire ACQUIRE field to add.** Stop looking for "set field 0x?? = ACQUIRE";
   IQ_CAPTURE(cmd6) already invokes ACTION_ACQUIRE by command type (§8.3). **OBSERVED.**
2. **NUM_OF_SAMPLES does not propagate as a wire u32 on this build** — the handler stores the
   8-bit `cfg+0x6c` config default into the model (§8.2). **Action:** read `memub(cfg+0x6c)` live
   (cfg = `memw(r25+0)` at unpack time; the heap struct from 0xd8182654). If it is 0 → that is the
   zero-sample cause; the count must be raised by the path the config-struct expects (NV/RFC
   default for this RF-cal config), not by the TLV. Also note SAMP_SIZE path is byte-wide
   (≤255), so 1024 cannot land through it regardless. **OBSERVED / UNKNOWN(value).**
3. **Ensure the RX is actually streaming before ACQUIRE** — a non-NULL `0xca79c494` is necessary
   but not sufficient; the RxLM/DTR-RX chain must be activated (full RF-cal-mode==2 + TECH_ENTER
   (LTE) + RADIO_CONFIG activate, per §4.1 / map_iq_capture.md §9). RADIO_CONFIGURE alone (as in
   the live run) can leave the chain configured-but-idle ⇒ 0 fill. Consider running an **RX_MEASURE
   (cmd/idx 1)** first (it arms the same executor family and forces the RX chain through an active
   measure) before the IQ_CAPTURE ACQUIRE. **INFERRED (strong).**
4. **cfg+0x6c real default/max:** not a static constant in this image (runtime NV/RFC; struct is
   heap-allocated @0xd8182654). The hard upper bound is the DDR buffer (`sample_capture_buffer_
   size_words`, map_iq_capture.md §7). The SAMP_SIZE wire path is 8-bit; larger counts, if
   supported, must come via the config default or the MAX_DIAG_SIZE/NUM_SAMP_BYTES fetch-window
   fields (model 17/19), not NUM_OF_SAMPLES. **UNKNOWN(value) / OBSERVED(width).**

### 8.6 OBSERVED vs INFERRED (VAs)

**OBSERVED (this image):**
- 51-entry jump-table1 @0xc37c0828 + 25-entry jump-table2 @0xc37c0890 fully enumerated; no
  ACTION_* setter in either (§8.1).
- Field handlers store `memw/memub(cfg+0x60/0x64/0x68/0x6c)` (config value), not the wire u32:
  SAMP_FREQ 0xd818994c, IQ_DATA_FORMAT 0xd81899f0, FETCH_IQ 0xd818997c, NUM_OF_SAMPLES 0xd81899ac
  (8-bit), common store 0xd81898a0 writes r3 to dest+4..7 (§8.2). **Corrects §2.3/§3.2.**
- Main loop writes wire r20 to entry+0..3 = model-name index (log @0xd8189ba8 reads +0..2 to
  index names @0xc906c8f8) (§8.2).
- Apply-loop @0xd8189524 gate `(model_idx-1)>0x13 → reject` @0xd818954c; `callr ctx[idx*0xc+8]`.
- Action-index generator 0xd8279358 `mux(sel>2,#8,#0)` @0xd82793ac; 0xd8279450 `mux(sel==1,#3,#8)`.
- Action-state store `memb(0xca79a8e0[cmd]+0x1fe)=action` @0xd8279300; state table @0xca79a8e0.
- ACQUIRE reached via 0xd8283554→0xd827a12c→0xd8279358(sel=5)→idx 8.
- Executor 0xd826d3e4 → getter 0xd827923c (err_fatal on NULL 0xca79c494); live run non-NULL (no SSR).
- ML1 async-capture anchor `LTE_ML1_SM_STM_ASYNC_SAMP_CAPTURE_CNF` b21@11540407.

**INFERRED:**
- num_captures=0/error_code=0 ⇒ ACQUIRE fired (by command type) but filled 0: because the model
  sample count = cfg+0x6c (8-bit config default, not wire 1024) and/or the RxLM/DTR-RX chain was
  configured but not streaming under RADIO_CONFIGURE-only.
- A prior RX_MEASURE (idx 1) or a full cal-mode==2 + TECH_ENTER + RADIO_CONFIG activate is likely
  required to put the RxFE into the streaming state ASYNC_SAMP_CAPTURE needs.

**UNKNOWN (runtime / dlpager pool):**
- Live value of cfg+0x6c (NUM_OF_SAMPLES default) — read `memub(memw(r25+0)+0x6c)` live.
- The seg27/dlpager prop-action body that writes num_samples_captured (not in this image).

### 8.7 REPRODUCE (this section)
```bash
python3 rd.py 0xc37c0828 51                 # jump-table1: no ACTION setter (§8.1)
python3 rd.py 0xc37c0890 25                 # jump-table2: no ACTION setter (§8.1)
./dis36.sh 0xd8189934 0x2c                  # SAMP_FREQ stores memw(cfg+0x60) (§8.2)
./dis36.sh 0xd8189994 0x44                  # NUM_OF_SAMPLES stores memub(cfg+0x6c) 8-bit (§8.2)
./dis36.sh 0xd81898a0 0x20                  # common store: writes r3 (cfg val) to dest+4..7 (§8.2)
./dis36.sh 0xd8189524 0x74                  # wire→model apply-loop + gate (model_idx 1..0x14) (§8.2)
./dis36.sh 0xd8279358 0x40                  # action-index generator mux(sel>2,#8,#0) → ACQUIRE (§8.3)
./dis36.sh 0xd8279450 0x30                  # mux(sel==1,#3,#8) (§8.3)
./dis36.sh 0xd8279300 0x30                  # action-state store memb(0xca79a8e0[cmd]+0x1fe) (§8.3)
./dis36.sh 0xd826d3e4 0x200                 # deferred executor (§8.3/§8.4)
./dis36.sh 0xd827923c 0x30                  # active-carrier getter (err_fatal on NULL) (§8.4)
```
