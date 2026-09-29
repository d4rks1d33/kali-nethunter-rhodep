# OUT_rx_measure — Which FTM RX diagnostic handlers are reachable WITHOUT the special mode (0xcbf4f740==2)

**Target:** SM6375 / Moto G82 5G (rhodep) · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Image:** `/tmp/mre/clade_dec_36m.bin` (VA base `0xd8000000`, 36 MB, decompressed CLADE) + `clade_exc_high.bin` (`0xd0000000`).
**Tools:** `re-scripts/dis.sh <va> <len>`, `find_refs.py`, `reach_ptr.py` (this pass, sound forward BFS with a
660-entry infra-trampoline blacklist), fan-in scan over all `call`/`jump` opcodes.

**Legend:** **FACT** = byte/instruction read at a cited VA, or an exact xref count · **INFERENCE** = deduction
with hard anchors · **UNKNOWN** = only resolvable on a live device (RAM/runtime).

---

## 0. VERDICT (direct answers)

1. **Is any receiver data reachable WITHOUT mode byte 0xcbf4f740==2?**
   **Negative for real sample/power *values*; positive for the *transport/parse* path only.** **FACT-backed.**
   - The data pointer `0xca79c494` has **exactly one writer** (`memw(##0xca79c494)=r0` @`0xd8279264`), reachable
     through **exactly one** call chain, and that chain is **dominated by the `0xcbf4f740==2` gate**. There is **no
     other writer** and **no ungated path** to it (verified by exact single-caller xrefs, §4). ⇒ In the normal state
     (mode≠2) the pointer stays NULL. **FACT.**
   - Every receiver *executor* that actually returns measured values (RX_AGC/CTON/sensitivity/IQ samples) dereferences
     `0xca79c494` through the abort-on-null getter `0xd827923c` (96 call-sites, all in the RF-measure driver region
     0xd826c000–0xd8294000). With a NULL pointer the getter jumps to `err_fatal` (`0xd80d8998 → 0xc0d60074`). **FACT.**
   - What *is* reachable in the normal state: the command **unpackers/repackers** (parse TLVs, marshal a reply frame,
     do bound-checks and clean bails). They do **not** themselves touch `0xca79c494` or `0xcbf4f740`. But they contain
     no receiver *data* — that only exists behind the executor deref. So a "clean" reply is possible, but it carries
     no samples/power. **FACT + INFERENCE.**

2. **Measurement handler command index 0x01 = RX_MEASURE (unpacker `0xd8185654`).**
   - Returns **signal-power / spectral measurements** (RX_AGC, EXPECTED_AGC, SENSITIVITY, CTON, PEAK FREQ,
     RX_AGC_MIN/MAX/STDDEV, CTON_*, SENSITIVITY_*, MEAS_FREQUENCY_ERROR) **and optionally IQ sample buffers**
     (FETCH_IQ, NUM_OF_SAMPLES, IQ_DATA_FORMAT, SAMP_FREQ, IQ_CAPTURE_TYPE). **FACT** (field table @`0xc906c720`, §5).
   - Its own static call graph (unpacker `0xd8185654` + measure-exec `0xd8185368` + repacker `0xd81865f4`) reads
     **NEITHER** `0xca79c494` **NOR** `0xcbf4f740`. **FACT** (§6). It obtains the measured values through a runtime
     vtable dispatch `callr r3` (`r3 = memw(ctx+4)`) @`0xd81853b8`, which lands in the RF-measure executors that DO
     deref `0xca79c494`. So the *parser* is reachable in the normal state, but the *values* still come from the gated
     pointer. **FACT (parser) + INFERENCE (executor deref via callr).**

3. **AP-reachable power/spectrum sweep?** No dedicated per-frequency power sweep command exists in the RF-TEST table.
   RX_MEASURE is the power/spectrum reader (per-tune power + statistics + PEAK FREQ + CTON); a "sweep" is done by the
   host issuing RADIO_CONFIG(tune) then RX_MEASURE repeatedly per frequency. The only `*sweep*` strings in the image
   are TX cal (`ftm_calv3_xpt_seq_*sweep*`), not an RX power-vs-frequency scan. **FACT** (§7).

4. **Does the tune routine populate `0xca79c494` by itself?** **Only when 0xcbf4f740==2.** RADIO_CONFIG (the tune
   command, slot 0) reaches carrier-activate `0xd81bd018`, whose body is gated at `0xd81bd160`
   (`if (memb(gp+0x740) != 2) jump 0xd81bd4b0`). The pointer write (`0xd8273b2c` APPLY → `0xd8279264` setter,
   value `##0xca6e3b88` from `0xd81bd7ec`) sits at `0xd81bd2a8`, inside the mode==2 branch. With complete tune params
   but mode≠2, the tune bails before the write. **FACT** (§4/§8).

5. **What could populate the pointer under some conditions (the one run that didn't abort)?** The write is performed by
   APPLY `0xd8273b2c` during RADIO_CONFIG carrier-activate, but **only after** the resident/paged RF-cal driver has set
   `0xcbf4f740=2` (a store that is **not present anywhere in this image** — out-of-image, LL1/ftm_calibration_v3). The
   mode-cycling sequence in the prior "no-abort" run put the modem into CAL service so the resident driver set the mode
   to 2; then TECH_ENTER(LTE)+RADIO_CONFIG ran the gated carrier-activate, which populated `0xca79c494=0xca6e3b88`, and
   the subsequent RX_MEASURE/IQ_CAPTURE executor found a non-NULL pointer and did not abort. **INFERENCE (strong) +
   FACT (gate/write chain).**

---

## 1. THE TWO POINTERS AND THE ABORT (FACT)

**Getter (deref, abort-on-null) `0xd827923c`:**
```
d827923c immext(#0xca79c480)
d8279240 r0 = memw(##0xca79c494)
d8279244 if (r0 != 0) jump 0xd8279260         ; return pointer
d8279248 call 0xd80d8998                       ; else err_fatal
d827924c r0 = ##0xf803b090                     ; assertion string arg
...
d8279260 jumpr r31
```
`0xd80d8998` tail-jumps to `0xc0d60074` (OS err_fatal / assertion → SSR). **FACT.**
Getter has **96 call-sites**, ALL in the RF-measure/capture driver region (0xd826c000–0xd8294000), none in the
command-unpacker region (0xd8182000–0xd818a000). **FACT** (opcode fan-in scan):
```
0xd8155000:2 0xd826c000:4 0xd826d000:13 0xd826e000:9 0xd826f000:6 0xd8274000:2 0xd827a000:4
0xd827e000:4 0xd827f000:8 0xd8280000:6 0xd8291000:6 0xd8292000:14 0xd8293000:8 0xd8294000:10
```

**Setter (the ONLY writer) `0xd8279264`:**
```
d8279268 immext(#0xca79c480)
d827926c memw(##0xca79c494) = r0            ; <<< only store to 0xca79c494 image-wide
```
Value written comes from `0xd81bd7ec` which returns `##0xca6e3b88`:
```
d81bd7ec immext(#0xca6e3b80)
d81bd7f0 r0 = ##0xca6e3b88 ; jumpr r31
```
**FACT.**

---

## 2. THE COMMAND TABLE @0xca79a850 — INDEX → HANDLER (FACT)

`command_id` (wire byte 10, ≤0x31) resolves the per-command struct via `0xd8272684`:
`r3=memw(##0xca79a850); r3=addasl(r3,id,#2); r3=memw(r3+#0x34)`. **FACT.**
The RF-TEST commands are registered by `0xd8182640` (the first module init from master registrar `0xd84aa03c`);
slot_index = registration ordinal = command index within the module (per `reg_order_B.md`, byte-exact via F3 names).

| idx | command (F3 name)     | unpacker VA | repacker VA | class |
|----:|-----------------------|-------------|-------------|-------|
| **0** | **RADIO_CONFIG** (tune) | 0xd8182ec0→0xd8183f30 | 0xd8182f48 | tune/config |
| **1** | **RX_MEASURE** (measure) | 0xd8185654 (disp 0xd8185b1c) | 0xd81865f4 | RX power/samples |
| 2 | TX_CONTROL            | 0xd8188428  | — | tx control |
| 3 | TRM_RRA               | 0xd86c0fe4  | — | resource |
| (4)| *(empty in mod0)*     | —           | — | — |
| 5 | IQ_CAPTURE            | 0xd81893a4 (disp 0xd8189818) | — | sample capture |
| 6 | TX_MEASURE            | 0xd818ab6c  | — | tx measure |
| 7 | MSIM_CFG              | 0xd8187fe4  | — | config |
| 8 | IRAT_CONFIG           | 0xd84aff68  | — | config |
| 9 | WAIT_TRIGGER          | 0xd81870fc  | — | trigger |
| 10| tx_measure (RFDEBUG)  | 0xd84ad0b8  | — | tx measure |
| 11| COMMAND_CAPABILITY    | 0xd81849ec  | 0xd81851a4 | query (CMD_MASK) |

**FACT.** (Absolute integer command_id = module_base + slot; module base is runtime-assigned via byte table
@`0xca9ef490`, so the absolute integer is **UNKNOWN** statically — the relative order above is FACT.)

---

## 3. PER-INDEX POINTER/MODE REACHABILITY (FACT — sound BFS)

Sound forward reachability from each **unpacker** (call+jump edges only; 660 infra-trampoline hubs — functions that
tail-jump/call into the OS/exception segment 0xc0xxxxxx — blacklisted to eliminate false positives that route through
shared logging/marshal/scheduler thunks). Results:

| idx | command            | root       | DEREF 0xca79c494 | reads 0xcbf4f740 | notes |
|----:|--------------------|------------|:----------------:|:----------------:|-------|
| 0 | RADIO_CONFIG (tune) | 0xd8182ec0 | via gated carrier only | **YES** | reads mode==2 gate @0xd81bd160; writes ptr only if ==2 |
| **1** | **RX_MEASURE** | 0xd8185654 | **NO (own graph)** | **NO** | scanned 29 fns incl. measure-exec 0xd8185368 & repacker; data via `callr r3` executor |
| 2 | TX_CONTROL          | 0xd8188428 | **NO** | **NO** | |
| 3 | TRM_RRA             | 0xd86c0fe4 | **NO** | **NO** | leaf |
| 5 | IQ_CAPTURE          | 0xd81893a4 | **NO (parser)** | **NO** | dispatcher parses TLVs; capture-exec via runtime callr (see 0x1002 wrapper below) |
| 6 | TX_MEASURE          | 0xd818ab6c | **NO** | **NO** | |
| 7 | MSIM_CFG            | 0xd8187fe4 | **NO** | **NO** | |
| 8 | IRAT_CONFIG         | 0xd84aff68 | **NO** | **NO** | |
| 9 | WAIT_TRIGGER        | 0xd81870fc | **NO** | **NO** | |
| 10| tx_measure(RFDEBUG) | 0xd84ad0b8 | **NO** | **NO** | |
| 11| COMMAND_CAPABILITY  | 0xd81849ec | **NO** | **NO** | pure query/REPACK |

**Touch NEITHER pointer nor mode byte (in their own static graph): indices 1,2,3,5,6,7,8,9,10,11.**
Only **index 0 (RADIO_CONFIG)** reads the mode byte (it is the tune command that gates the pointer write). **FACT.**

### sub_command 0x10xx handler wrappers (the wire dispatch layer, tbl @0xc37c649c) — spot checks
| sub    | wrapper     | DEREF 0xca79c494 | reads mode | path (FACT) |
|--------|-------------|:---------------:|:----------:|------|
| 0x1002 | 0xd86fd0f4  | **YES** | NO | `0xd86fd0f4 → 0xd8272cd8 →(callr r5)→ 0xd826d3e4 → 0xd827923c` (short, resolved-callr; the IQ/measure-capture exec deref) |
| 0x1008 | 0xd86fd5c8  | via exec | NO | RX_MEAS-family query wrapper; its measurement exec derefs (executor region) |
| **0x100D** | **0xd86fde80** | **NO** | **NO** | **confirmed clean** — parses cmd_id (memub(req+0xa)), resolves struct (0xd8272684), runs list/config ops (0xd82735e8, 0xd828067c, 0xd829bf84); never reads 0xca79c494 or 0xcbf4f740 |

**0x100D (`0xd86fde80`) confirmed:** its call graph (scanned 561 fns) never reads `0xca79c494` or `0xcbf4f740`. It is a
config/list wrapper (command_id at wire byte +0x0a and a second index at +0x0c; calls the id→struct resolver, a
list-setter `0xd82735e8`, and `0xd828067c`/`0xd829bf84`). It does **not** return receiver measurement data. **FACT.**

> Caveat (honesty): the "DEREF via exec" rows are through a runtime **`callr`** (context vtable) that the sound static
> BFS cannot follow except for the one verified resolved edge (0xd8272cd8→{0xd826d3e4,0xd8292298}). The unpackers'
> *own* code is clean; the deref lives one indirect hop away in the RF-measure executors that all call the abort-getter.

---

## 4. THE POINTER IS WRITTEN ONLY BEHIND mode==2 (FACT — exact xrefs)

Unique-caller chain (each step verified with `find_refs.py`):
```
setter  0xd8279264   ← called ONLY by 0xd8273b40   (find_refs: ['0xd8273b40'])
APPLY   0xd8273b2c   (contains 0xd8273b40)  ← called ONLY by 0xd81bd2a8   (find_refs: ['0xd81bd2a8'])
0xd81bd2a8 is inside carrier-activate 0xd81bd018, AFTER the gate:
   d81bd160 r2 = memb(gp+#0x740)             ; gp=0xcbf4f000 → 0xcbf4f740
   d81bd168 if (r2 != 2) jump 0xd81bd4b0     ; skip the whole activate/apply if mode != 2
   ...
   d81bd2a8 call 0xd8273b2c                  ; APPLY → 0xd8273b40 → 0xd8279264 (writes 0xca79c494)
```
The gate at `0xd81bd160` dominates the APPLY call at `0xd81bd2a8` (the not-equal-2 branch jumps to `0xd81bd4b0`, past
the APPLY). **⇒ No value of tune parameters can populate `0xca79c494` unless `0xcbf4f740==2`.** **FACT.**

The store of the literal `2` into `0xcbf4f740` is **absent from the entire image** (36 MB CLADE + native segments);
the only in-image writers of that byte write mux(0/1) at `0xd81bd494`/`0xd81bd5fc` (reset `0xd81bd7c8`). The `2`
(=`FTM_RF_MODE_CAL`) is set by the resident/paged RF-cal driver (LL1/ftm_calibration_v3), **out of this image**
(anchors: `NR5G_LL1_CAL_FTM_RF_MODE_CAL`, `lte_LL1_get_ul_ftm_cal_mode`). **FACT (absence) + INFERENCE (writer).**
(Consistent with `cal_entry_sequence.md` §4/§6.)

---

## 5. WHAT RX_MEASURE (index 0x01) RETURNS (FACT — field table @0xc906c720)

RX_MEASURE result/param field names (index = field_id):
```
1 RX_CARRIER  2 RFM_DEVICE  3 EXPECTED_AGC  4 RX_AGC  5 LNA_GS  6 SIG_PATH  7 RX_MODE  8 RX_SLOT
9 NUM_OF_BURST  10 SENSITIVITY  11 CTON  12 PEAK FREQ  13 FETCH_IQ  14 NUM_OF_SAMPLES
15 IQ_DATA_FORMAT  16 SAMP_FREQ  17 OVERRIDE_LNA  18 RX_GAIN_CTL_TYPE  19 NO_TONE_SENSITIVITY
20..22 NO_TONE_SENSITIVITY_SPUR_*  23 BEAM_ID  24 TIME_US  25 SUB_FRAME_CONFIG  26 NUM_AVERAGES
27 RX_AGC_MIN  28 RX_AGC_MAX  29 RX_AGC_STDDEV  30 CTON_MIN  31 CTON_MAX  32 CTON_STDDEV
33 SENSITIVITY_MIN  34 SENSITIVITY_MAX  35 SENSITIVITY_STDDEV  36..38 NO_TONE_SENSITIVITY_*_{MIN,MAX,STDDEV}
39 IQ_CAPTURE_TYPE  40 MEAS_FREQUENCY_ERROR
```
So RX_MEASURE returns **both**:
- **Signal power / spectral metrics:** RX_AGC (received power), EXPECTED_AGC, SENSITIVITY, CTON (carrier-to-noise),
  PEAK FREQ, MEAS_FREQUENCY_ERROR, plus MIN/MAX/STDDEV statistics over NUM_OF_BURST/NUM_AVERAGES.
- **Sample buffers:** FETCH_IQ + NUM_OF_SAMPLES + IQ_DATA_FORMAT + SAMP_FREQ + IQ_CAPTURE_TYPE (an in-line IQ fetch).
**FACT.**

RX_MEASURE reply frame format `[FTM.RFTEST][RX_MEASURE][REPACK]: [%3d][ %12s ][ %4d ][ 0x%8x ]` @0xc37c05e0
= `field_id, name, size_bytes, address`. The `0x%8x` address is where the sample/result buffer lives (physical, in
the memshare region for IQ). **FACT.**

---

## 6. RX_MEASURE OWN GRAPH IS CLEAN; DATA COMES VIA callr (FACT)

RX_MEASURE unpacker `0xd8185654` → measure-exec `0xd8185368` → repacker `0xd81865f4`.
- **`0xd8185368` (measure-exec)** static callees: `0xd8051b58` (infra hub, 1869 callers, tail-call into 0xc0989068)
  and a `callr r3` @`0xd81853b8` where `r3 = memw(r0+#0x4)` — a **runtime vtable method** off the RX_MEASURE context.
  After the callr it copies the result bytes into the reply struct (`memb(r0+..)` block). **FACT.**
- **`0xd81865f4` (repacker)** calls only marshaling helpers (`0xd8186720`, `0xd81855dc`, `0xd8185648`) + infra. No
  getter, no mode read. **FACT.**
- Sound BFS from `0xd8185654` (29 fns, infra-blacklisted): **DEREF_494=False, MODE_740=False.** **FACT.**

The earlier "RX_MEASURE reaches the getter" result was a **false positive** routed through the ubiquitous infra hub
`0xd8051b58` (1869 callers) — a tail-call trampoline into the exception segment. With that (and 659 similar thunks)
correctly excluded, RX_MEASURE's own graph does not deref the pointer; the deref is one **indirect** hop away in the
RF-measure executor selected by the context vtable, which is exactly the code that populates only when mode==2 tuned.

---

## 7. NO DEDICATED RX POWER/SPECTRUM SWEEP COMMAND (FACT)

- The RF-TEST command table (§2) has no "sweep" / "scan" command. RX power/spectrum is obtained per-tune via RX_MEASURE
  (RX_AGC / CTON / PEAK FREQ / SENSITIVITY + statistics). A host performs a sweep by looping
  `RADIO_CONFIG(BAND/CHANNEL/BW) → RX_MEASURE`. **FACT/INFERENCE.**
- The only `*sweep*` strings are **TX** calibration: `ftm_calv3_xpt_seq_pre_sweep_class` (@0xc4177937),
  `…sweep0/2/3_class`, `…phase_delta_pre_sweep_class` — XPT/EPT PA sweeps, not RX power-vs-frequency. **FACT.**
- Parameter TLVs to drive an RX power read (RX_MEASURE): tune first via **RADIO_CONFIG** field-ids
  (`BAND=5, CHANNEL=6, BANDWIDTH=7, RX_CARRIER=1, TECH_MODE=25`, table @0xc906c630 / `rftest_tlv_RX_TUNE_ids.txt`),
  then **RX_MEASURE** request TLVs `RX_CARRIER=1, RFM_DEVICE=2, SIG_PATH=6, RX_MODE=7, NUM_OF_BURST=9,
  NUM_AVERAGES=26, RX_GAIN_CTL_TYPE=18` and read back `RX_AGC=4, CTON=11, SENSITIVITY=10, PEAK FREQ=12,
  RX_AGC_MIN/MAX/STDDEV=27/28/29`. For samples: `FETCH_IQ=13, NUM_OF_SAMPLES=14, IQ_DATA_FORMAT=15, SAMP_FREQ=16,
  IQ_CAPTURE_TYPE=39`. **FACT (field ids).**

---

## 8. DOES TUNE POPULATE THE POINTER? (FACT)

RADIO_CONFIG (index 0, unpacker `0xd8182ec0→0xd8183f30`) is the tune command (BAND/CHANNEL/BANDWIDTH). Its
carrier-activate leg `0xd81bd018` **would** write `0xca79c494 = 0xca6e3b88` (via APPLY `0xd8273b2c → 0xd8279264`),
**but only inside the `mode==2` branch** (gate `0xd81bd160`, §4). Therefore:
- **Complete tune params, mode≠2:** tune parses and applies RF settings but **does NOT** populate `0xca79c494`
  (bails at `0xd81bd160 → 0xd81bd4b0`). A subsequent RX_MEASURE/IQ_CAPTURE executor then hits the NULL pointer and
  err_fatals in the getter. **FACT.**
- **mode==2 (set by out-of-image resident driver):** tune populates the pointer, and RX_MEASURE/IQ_CAPTURE succeed.
  This matches the prior "no-abort" run (§0.5). **FACT (chain) + INFERENCE (mode source).**

**⇒ The tune routine does NOT populate the pointer by itself in the normal state; it needs mode==2 first.**

---

## 9. FINAL VERDICT

- **Per-index pointer/mode touch (own graph):** only **index 0 (RADIO_CONFIG)** reads `0xcbf4f740`; **none** of the
  unpackers write/read `0xca79c494` in their own code. Indices **1,2,3,5,6,7,8,9,10,11 touch NEITHER**. **FACT.**
- **RX_MEASURE (index 0x01):** returns signal power/spectral metrics (RX_AGC, CTON, SENSITIVITY, PEAK FREQ, stats)
  **and** IQ samples (FETCH_IQ …). Its *parser/repacker* is reachable in the normal state and does not deref the
  pointer, but the *measured values* are produced by a runtime `callr` executor that dereferences `0xca79c494` (the
  gated pointer). **So RX_MEASURE is NOT usable to obtain real data without mode==2.** **FACT + INFERENCE.**
- **AP-reachable power/spectrum scan:** no dedicated sweep command; RX_MEASURE is the power/spectrum reader, driven
  per-frequency by RADIO_CONFIG tunes. Same mode==2 dependency. **FACT.**
- **Does tune populate the pointer:** only under `0xcbf4f740==2`; not by itself in the normal state. **FACT.**
- **Is any receiver DATA reachable without the special mode?** **NO.** Every path that yields real
  samples/power/spectrum passes through the abort-on-null getter `0xd827923c`, and the sole writer of the pointer it
  reads is uniquely gated by `mode==2`. The command *transport and parsing* are reachable (you get a well-formed
  reply, bound-checks, clean bails), but they carry **no receiver measurement values**. This is a **valid negative
  result**: without the resident cal driver setting `0xcbf4f740=2` (out-of-image), no FTM RX command returns real
  data over the existing diagnostic transport. **FACT-backed.**

> Practical corollary (matches `cal_entry_sequence.md` §6 / `os_iq_path.md`): to actually read receiver data you must
> first drive the modem into CAL/FTM service so the resident LL1 driver sets `0xcbf4f740=2`, then TECH_ENTER(LTE) +
> RADIO_CONFIG(tune) to populate `0xca79c494`, then RX_MEASURE/IQ_CAPTURE. There is no in-image shortcut.

---

## 10. REPRODUCE
```bash
cd /tmp/mre
# Getter (abort-on-null) and setter (only writer):
bash re-scripts/dis.sh 0xd827923c 0x30      # r0=memw(0xca79c494); if null -> 0xd80d8998 err_fatal
bash re-scripts/dis.sh 0xd8279264 0x10      # memw(0xca79c494)=r0  (unique writer)
(cd /tmp/modemre && python3 find_refs.py 0xd8279264)   # -> ['0xd8273b40']  (single caller)
(cd /tmp/modemre && python3 find_refs.py 0xd8273b2c)   # -> ['0xd81bd2a8']  (single caller)
bash re-scripts/dis.sh 0xd81bd160 0x160     # mode==2 gate dominating the APPLY @0xd81bd2a8
bash re-scripts/dis.sh 0xd81bd7ec 0x10      # value written = ##0xca6e3b88
# Command table / indices:
bash re-scripts/dis.sh 0xd8272684 0x30      # id -> struct resolver (@0xca79a850)
bash re-scripts/dis.sh 0xd8182640 0x2a0     # RF-TEST module init (slot registrations)
# RX_MEASURE (index 0x01):
bash re-scripts/dis.sh 0xd8185654 0x40      # unpacker head
bash re-scripts/dis.sh 0xd8185368 0x60      # measure-exec: callr r3 @0xd81853b8 (context vtable)
bash re-scripts/dis.sh 0xd8185b1c 0x08      # field dispatcher (tbl @0xc37c0478)
(cd /tmp/modemre && python3 rd.py 0xc906c720 40)   # RX_MEASURE field names (RX_AGC,CTON,FETCH_IQ,...)
# Sound reachability (infra-hub blacklist in /tmp/mre/_infra.txt):
(cd /tmp/modemre && python3 /tmp/mre/re-scripts/reach_ptr.py \
   0xd8182ec0 0xd8185654 0xd8188428 0xd86c0fe4 0xd81893a4 0xd818ab6c \
   0xd8187fe4 0xd84aff68 0xd81870fc 0xd84ad0b8 0xd81849ec 0xd86fde80 0xd86fd0f4)
# Getter fan-in (96 sites, all in RF-measure region):
# (opcode scan over clade_dec_36m.bin — see §1)
```
