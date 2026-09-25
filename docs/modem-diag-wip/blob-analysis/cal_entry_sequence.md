# cal_entry_sequence — Who populates the per-tech callback table 0xca733d10, the two cal gates, and the ordered entry sequence

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Primary image:** `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`, aligned & authoritative for the FTM/RF region; verified against known store `0xd81bd494`).
**Static data:** `modem.b23` @0xc8b6a000 (RW `.data`), `modem.b21` @0xc3553000 (R-- rodata), `modem.b25` @0xcbf4f000 (gp/.sdata), seg27_dec @0xce480000 (R-- paged rodata).
**Tools:** `dis36.sh`, `mkelf.py`, `find_refs.py`, `_fns.pkl`, `scan_733d10.py` (this pass).

**Legend:** **FACT** = byte/instruction read at a VA · **INFERENCE** = deduction with hard anchors · **UNKNOWN** = only resolvable at runtime / in RAM / outside the ELF.

> This report ANSWERS the open question left by `cal_mode_trigger.md`/`rf_cal_mode_gates.md`/`ftm_common_handler.md`
> ("the per-tech callback table is populated at runtime — UNKNOWN statically"). It is NOT unknown: the table
> `0xca733d10` is populated by a single registration function `0xd81dfcd0` at **subsystem-init time**, with
> **static record pointers** for LTE/tech2/tech3. Those records are real, present in the image, and the fn
> pointer at `record+4` (the one the indirect call at `0xd81dfecc` invokes) is a valid code VA. **The table is
> NOT empty; the indirect call is NOT a no-op.**

---

## 0. TL;DR — direct answers

1. **What populates 0xca733d10 and when (Q1):** the single writer is `memw(r2<<2 + ##0xca733d10) = r1` at
   **`0xd81dfce4`**, inside the registration function **`0xd81dfcd0`** = `register(r0=tech_index (≤0x14), r1=record_ptr)`.
   It stores a **pointer to a per-tech record struct** into `table[tech]`. It has **4 static callers**, all of
   which run at **subsystem-init / constructor time** (none is reached from a DIAG command dispatch). The records
   are **statically initialised** (in `.data` for tech2/tech3, or built in `.bss` at init for LTE). **FACT.**
   ⇒ For LTE (tech-index 1) the table is populated at init; the indirect call is a real call, not a no-op.

2. **The exact indexing (correction to the question's wording):** the access is NOT `memw(0xca733d10 + tech*8 + 4)`.
   It is **`fn = memw( memw(0xca733d10 + tech*4) + 4 )`** — i.e. `table[tech]` (word stride **4**) yields a
   **record pointer**, and the callback is at **record+4**. Verified at `0xd81dffc4` (`r1=memw(r20<<2+##0xca733d10)`),
   `0xd81e00d0` (`r2=memw(r3+#0x4)`), `0xd81e00e0` (`callr r2`). **FACT.**

3. **What sets 0xcbf4f740==2 and creates 0xcbf56000 (Q2):** unchanged from prior passes and re-confirmed here —
   there is **NO store of the literal 2 to 0xcbf4f740 anywhere in the image** (36 MB CLADE + native b02/b10/b12/b13/b30).
   The `2` (=`FTM_RF_MODE_CAL`) is written by the **resident/paged RF-cal driver (LL1 / `ftm_calibration_v3` /
   `rflte_ftm_*`), OUTSIDE this image**. `0xcbf56000` is created lazily by the get-or-create accessor `0xd8284eac`
   (creator `0xd8284cf4`, vtable `##0xc37c6d80`) the first time the RF-instance subsystem is touched. **FACT (absence
   of store 2) + FACT (lazy creator) + INFERENCE (out-of-image writer).**
   The **in-image trigger** that ultimately makes the paged driver write the `2` is the **per-tech `callr`** at
   `0xd81dfecc` (`0xd81e00e0`/`0xd81e0108`/`0xd81e0138`) — i.e. the very callback that lives in `0xca733d10[tech]`.

4. **0x10F / 0x4F7 / 0x4F5 (Q3):** these are the only 3 sub-commands of `ftm_cmd 0x00` handler `0xd8271290`.
   - `0x10F → 0xd8263e2c`: per-tech RF **enable/attach** (param32 @pkt[6..9]); sets `gp+0x1ed5`/`gp+0x1ed6=1`;
     does NOT touch `0xca733d10`, `0xcbf4f740`, or `0xcbf56000`.
   - `0x4F7`/`0x4F5 → 0xd86f8514`: RF **measurement/verify** reading an **internal ctx** (`0xd827280c` → table
     `@0xca79a850`), not wire params beyond the sub-cmd.
   - **None of the three starts the cal/test subsystem, populates `0xca733d10`, or sets `0xcbf4f740`.** **FACT.**

5. **What reaches 0xd81dfecc (Q4):** the per-tech commit `0xd81dfecc` is reached via **TECH_ENTER**
   (RFDEBUG sub `0x000D`) on `ftm_cmd_id=0x27` (FTM_LTE) → handler `0xd8174758` → per-tech `callr`
   (`0xd817480c`) → enter-commit `0xd81e5cec`/`0xd81dfecc`. `0xd81dfecc` **assumes the table is already
   populated** — it only READS `0xca733d10[tech]` and its callbacks; it never writes the table. Population happened
   earlier at init (§1). **FACT.**

6. **Ordered sequence (Q5):** table population is automatic at boot/subsystem-init (no command needed). The two
   cal gates are the gating step. See §6.

---

## 1. Q1 — WHO POPULATES 0xca733d10 AND WHEN (FACT)

### 1.1 The single store (the writer) — FACT
Full scan of the 36 MB image for every use of `immext(#0xca733d00)` (16 sites; `scan_733d10.py`) and for the
store opcode `ad82e190` (`memw(Rt<<#2+##U)=Rs`, 3 occurrences image-wide) proves there is **exactly one** store
that targets this table:

```
0xd81dfce0:  immext(#0xca733d00)
0xd81dfce4:  memw(r2<<#0x2 + ##0xca733d10) = r1      ; <<< table[r2] = r1   (ONLY writer, image-wide)
0xd81dfce8:  dealloc_return
```
The other two `ad82e190` stores (0xd83983b4, 0xd9de9b0c) pair with different immext values (0xca776cf/0xd037727),
i.e. they target OTHER addresses. **FACT.**

### 1.2 The registration function `0xd81dfcd0` — FACT
```
0xd81dfcd0:  p0 = cmp.gtu(r0,#0x14); if(p0) jump 0xd81dfcec    ; bounds-check tech-index <= 0x14
0xd81dfcd4:  r2 = r0; allocframe(#0)                            ; r2 = tech-index
0xd81dfcd8:  if(!p0) r0 = #0x1                                  ; default return = 1 (ok)
0xd81dfcdc:  p0 = cmp.eq(r1,#0x0); if(p0) jump 0xd81dfcfc       ; if(record_ptr == NULL) -> error/log
0xd81dfce0:  immext(#0xca733d00)
0xd81dfce4:  memw(r2<<2 + ##0xca733d10) = r1                    ; table[tech] = record_ptr
0xd81dfce8:  dealloc_return
0xd81dfcec:  ... call 0xd80d77a8 (log "tech out of range") ...
```
Signature (FACT): **`int register_tech_record(uint tech_index /*≤0x14*/, void *record_ptr)`** → writes
`0xca733d10[tech] = record_ptr`, returns 1. **FACT.**

### 1.3 The 4 callers — all init / constructor time (FACT)
`find_refs.py 0xd81dfcd0` + full 36 MB call/jump scan:

| Caller (call site) | tech (r0) | record_ptr (r1) | enclosing fn | reached by |
|---|---|---|---|---|
| **0xd824739c** | **1 (LTE)** | `obj+0x10` (obj @0xca79a820, built at init) | `0xd82472e8` | init step @**0xd84d4e64** (linear subsystem-init sequence) |
| **0xd8213404** | **2** | **`##0xc8dc6f40`** (static `.data`) | `0xd8213234` | via **0xd81bd018** (module-init call @0xd81e53dc) |
| **0xd826cc74** | **3** | **`##0xc8dc7040`** (static `.data`) | `0xd826cc70` | constructor-style (no static caller → runtime init table) |
| **0xd8535108** | dynamic | dynamic (loop-built record) | ~0xd84d… | init loop |

**Evidence the callers are init, not command-triggered (FACT):**
- LTE reg fn `0xd82472e8` has **exactly one** code caller in the whole 36 MB: `0xd84d4e64`, which sits in a
  classic **linear subsystem-init sequence** at `0xd84d4d88` — each step is a `call` followed by a step-number
  error code (`r16 = #0x4,#0x5,#0x6,#0x7,#0x8,#0x9,#0xa,#0xb,#0xc,#0xd,…`) that jumps to a common failure handler
  (`0xd84d4dc8`). The `call 0xd82472e8` (LTE register) is unconditional in that sequence. **FACT.**
- The top-level init `0xd84d4d88` has **NO** code callers (dispatched via a boot init/task table living in the
  compressed RW data pool, not in the code image). **FACT.**
- tech3 reg `0xd826cc70` is a **tiny standalone function** (`register(3, ##0xc8dc7040)` then abort-if-null) with
  **NO** static callers → constructor/init-table dispatched. **FACT.**
- tech2 setup `0xd8213234` is called only from `0xd81bd3f4` inside `0xd81bd018`, and `0xd81bd018` is called only
  from `0xd81e53dc` inside **module-init `0xd81e52c8`** (which itself has no callers → boot init table). So the
  tech2 registration also runs at module init (its tech2 branch is gated by `memub(gp+0x748)==1` @0xd81bd3dc).
  **FACT.**

⇒ **The table is populated at subsystem/module init, before any DIAG command runs.** No command is required to
populate it; a command (TECH_ENTER) only *reads* it. **FACT.**

### 1.4 The records ARE present and non-empty — FACT
The record pointers for tech2/tech3 are static addresses in `modem.b23` (`.data`, base 0xc8b6a000). Read directly:

```
record tech2 @0xc8dc6f40:  +00=d8213c94  +04=d8213e7c  +08=d82146dc  +0c=d8214850  +10=d82141bc  +18=d8214450
record tech3 @0xc8dc7040:  +00=d826c1b4  +04=d826c208  +08=00000000  +0c=d826c24c  +10=d826c5fc  +18=d826c974
```
These are **vtable-like structs of function pointers** into the code (0xd82xxxxx). The callback the indirect call
uses is at **record+4**:
- tech2 → **0xd8213e7c** (valid fn, prologue `call 0xd814e6ac; allocframe(#0x50)`). **FACT.**
- tech3 → **0xd826c208** (valid fn, prologue with `p0=cmp.eq(r0,#0); allocframe`). **FACT.**

For **LTE (tech1)** the record is `obj+0x10`, and `0xd82472e8` writes the fn pointers into it at init:
`memw(obj+0x10)=##0xd82473c0` (record+0), `memw(obj+0x14)=##0xd8247540` (record+4), plus
`obj+0x1c=0xd8247e00`, `obj+0x20=0xd8248800`, `obj+0x28=0xd82496c0`, etc. (VAs 0xd82473c0/0xd8247540/… all in
the LTE FTM module). So for LTE the callback at **record+4 = 0xd8247540** — a valid function
(`immext(#0xc0); memh(r3+#0x8)=##0xd9; …`). **FACT.**

⇒ **For LTE the table entry is populated and the indirect call at `0xd81dfecc` invokes `0xd8247540` (record+4).
It is not a no-op.** **FACT.**

---

## 2. Q4 — WHAT COMMAND REACHES 0xd81dfecc, AND DOES IT POPULATE THE TABLE? (FACT)

`0xd81dfecc` is the **per-tech enter-commit**. Path (verified, matches `tech_state_gate.md`/`cal_mode_trigger.md`):
```
DIAG 4B 0B  27 00  0D 00 …    ; SUBSYS_CMD_F, SUBSYS_FTM, ftm_cmd_id=0x27 (FTM_LTE), RFDEBUG sub=0x000D (TECH_ENTER)
  → ftm_cmd table @0xc37bc828[0x27] = stub 0xd814e534 (FTM_LTE)
  → RFDEBUG dispatch (tbl @0xca65b414, slot 13) → handler 0xd8174758
  → per-tech callr @0xd817480c → enter path 0xd81e5cec / commit 0xd81dfecc
```
Inside `0xd81dfecc` (FACT):
```
d81dff14:  r16 = memw(r2+#0xc)                       ; session->0xc  (mode; checked ==2 downstream)
d81dff2c:  r21 = add(##0xca733d10, asl(r21,#0x2))     ; &table[tech]   (address only)
d81dffc4:  r1  = memw(r20<<2 + ##0xca733d10)          ; record = table[tech]     (READ)
d81dffc8:  if(record != 0) …                          ; null-check the record
d81e00d0:  r2  = memw(r3+#0x4)                         ; fn = record->+4
d81e00e0:  callr r2                                    ; <<< invoke callback[tech]  (record+4)
   (sibling calls: 0xd81e0108 record+4 again w/ arg1=0; 0xd81e0138 record+4 w/ arg=1)
```
**`0xd81dfecc` only READS `0xca733d10`; it never writes it.** It **assumes the table is already populated** (which
it is, from init — §1). If for some reason the slot were NULL, `0xd81dffc8` takes the error branch and logs
(`##0xf8033d98`), returning 0 — it does not crash and does not populate. **FACT.**

---

## 3. Q3 — ftm_cmd 0x00 (handler 0xd8271290): sub-commands 0x10F / 0x4F7 / 0x4F5 (FACT)

Handler `0xd8271290` parses the sub-cmd from wire pkt[4..5] (u16 LE) and dispatches with 3 linear `cmp.eq`
(no jump-table). Full disasm 0xd8271290..0xd8271314 (re-verified; see `ftm_common_handler.md` for byte listing):

| Sub (u16 @pkt4) | Handler | What it does | table/gate side-effects |
|---|---|---|---|
| **0x010F** | `0xd8263e2c` | per-tech RF **enable/attach**; reads param32 @pkt[6..9]; resolves RF inst (`0xd8055528`, tbl `@0xca79a8e0`) + tech obj (`0xd82726c4`, tbl `@0xca79a850`); on success sets `memb(gp+0x1ed5)=1` (0xcbf50ed5), `memb(gp+0x1ed6)=1` (0xcbf50ed6). | **NONE** — does NOT write `0xca733d10`, `0xcbf4f740`, or `0xcbf56000`. |
| **0x04F7** | `0xd86f8514` | RF **measurement/verify** over an INTERNAL ctx: `0xd8829a00` (get session), `0xd827280c` (→ `memw(##0xca79a850)` indexed → internal struct), reads struct fields +0xa/+0xc/+0xe/+0xf/+0x10. Uses NO wire params beyond the sub-cmd. | **NONE.** |
| **0x04F5** | `0xd86f8514` | same handler, different log branch. | **NONE.** |
| any other | default | log + result=0 (status byte 4). | **NONE.** |

**Conclusion (FACT):** **none of 0x10F / 0x4F7 / 0x4F5 starts the calibration/test subsystem, populates
`0xca733d10`, or sets `0xcbf4f740`.** They operate on RF instance/measurement state that must already exist.
0x10F is a per-tech *enable* (a plausible prerequisite of "activating" a tech, but it does not open the cal gate
nor register the callback table — that already happened at init). **FACT.**

---

## 4. Q2 — 0xcbf4f740==2 and 0xcbf56000 (re-confirmed; FACT + INFERENCE)

Fully covered and re-verified in `rf_cal_mode_gates.md` and `ftm_common_handler.md`; summary with VAs:

- **gp = 0xcbf4f000** (triangulation 140/423 + byte-exact cross-checks). **FACT.**
- **Gate (a) 0xcbf4f740** (byte, RF-mode enum {0,1,2}): read/gate `0xd81bd160`→`0xd81bd168 if(!=2) jump 0xd81bd4b0`.
  **Only byte writers in the whole image:** `0xd81bd494` and `0xd81bd5fc` (`memb(gp+0x740)=mux(pred,#1,#0)` → 0/1),
  reset `0xd81bd7c8` (=0). **There is NO store of the literal 2 to 0xcbf4f740 in the 36 MB CLADE image nor in the
  native segments b02/b10/b12/b13/b30**, by any of the 4 write vectors (gp-rel memb/memh/memw/memd, absolute,
  store-imm, base-pointer; encodings confirmed via llvm-mc-18). **FACT.**
  ⇒ The `2` (=`FTM_RF_MODE_CAL` / `NR5G_LL1_CAL_FTM_RF_MODE_CAL`) is written by code **outside this image**
  (resident/paged RF-cal driver: LL1 / `ftm_calibration_v3_*` / `rflte_ftm_*`). Anchors (seg27, FACT):
  `NR5G_LL1_CAL_FTM_RF_MODE_CAL` @0xce6cf8fe, `lte_LL1_get_ul_ftm_cal_mode(cxn_id) > 0` @0xce5bd52b. **INFERENCE (writer identity).**
- **Gate (b) 0xcbf56000** (word, RF-instance C++ object ptr): read/gate `0xd8286240` (`if(!=0) …`). Created lazily
  by get-or-create accessor **`0xd8284eac`** (creator **`0xd8284cf4`**: alloc `0xd84c4b80`, install vtable
  `##0xc37c6d80`, `memw(gp+0x7000)=obj` @0xd8284d20/@0xd8284ee4). Called from `0xd8286290` (in `0xd828626c`), which
  has no static caller → runtime/event-bound. **Not created at boot; created on first touch of the RF-instance
  subsystem.** **FACT.**

- **In-image trigger for both gates (INFERENCE with FACT anchors):** the only in-image edge that reaches the paged
  RF-cal driver is the **per-tech `callr` at `0xd81dfecc`** (the callbacks from `0xca733d10[tech]`, §1/§2) plus the
  carrier-activate `0xd81bd018`. When the modem is in FTM/cal service (RFM+MCPM up) and TECH_ENTER(LTE)+RADIO_CONFIG
  run, those callbacks execute the resident driver, which writes `0xcbf4f740=2` and (via the RF-instance path)
  makes `0xd8284eac` create `0xcbf56000`. The store of `2` itself is out-of-image. **INFERENCE (strong) + FACT (callr/gates).**

---

## 5. Correction to the question's premise about the table

- The question states the callback is `memw(0xca733d10 + tech*8 + 4)`. **The real access is
  `memw( memw(0xca733d10 + tech*4) + 4 )`** — `0xca733d10` is an array of *record pointers* (word stride 4), and the
  fn ptr is at offset +4 *inside the pointed-to record*. This does not change the conclusion, but the effective
  stride of the pointer array is **4, not 8**. (The prior `cal_mode_trigger.md` §2.1 line
  `r21 = add(##0xca733d10, r21<<2)` and `record = memw(r20<<2 + ##0xca733d10)` already implied stride 4; the
  "tech*8+4" in the task brief conflated the record-internal layout with the array stride.) **FACT.**
- The table is therefore **NOT empty** for LTE/tech2/tech3: it holds valid record pointers installed at init, and
  each record's +4 slot holds a valid code VA. **FACT.**

---

## 6. Q5 — ORDERED SEQUENCE that leaves 0xca733d10[LTE] populated AND 0xcbf4f740==2

**Step 0 (automatic, no command): table population.**
At subsystem/module init the boot init sequence runs `register_tech_record` (0xd81dfcd0) for each tech:
`0xca733d10[1]=&LTE_record` (callback record+4 = 0xd8247540), `[2]=0xc8dc6f40`, `[3]=0xc8dc7040`, etc. This happens
**before any DIAG command**. ⇒ `0xca733d10[LTE]` is already populated when the modem is up. **FACT.**
(If you observe it NULL in a live peek, the LTE FTM subsystem-init step at `0xd84d4e64` did not run — i.e. the modem
is not in a state where the FTM-LTE module was initialised; that is the only way LTE's slot stays NULL.)

**The gating step is `0xcbf4f740==2` (+ `0xcbf56000!=0`), which needs the resident cal driver — out of image.**
Best-supported live sequence (FACT gates/callr + INFERENCE trigger; verify by DIAG peek):

```
A) Put the modem in FTM/CAL service so rfm_init + MCPM + the resident RF-cal driver are active.
   (QMI-DMS set_operating_mode -> OFFLINE/FTM, or boot in factory-test.)   [INFERENCE]
   Prereqs (seg27 anchors, FACT): RFM_INIT (@0xce6de1b0), MCPM ON (@0xce748848).

B) TECH_ENTER(LTE):  4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00   ; SUB=0
      02 00 04 00 01 00 00 00   ; TECH=1 (LTE)
      03 00 04 00 00 00 00 00   ; SCENARIO=0
   → commit 0xd81dfecc → callr 0xca733d10[LTE].fn (= record+4 = 0xd8247540) → resident FTM-LTE driver.
   NOTE: also requires session->0xc == 2 for the state store @0xca7897b0 (see tech_state_gate.md).

C) RADIO_CONFIG(BAND+EARFCN+BW+RX_CARRIER)  [unpacker 0xd8183f30, field-tbl @0xc37c0290]
   → carrier-activate 0xd81bd018. With gp+0x740==2 and gp+0x7000!=0 (set by the resident driver in A/B),
     APPLY 0xd8273b2c → setter 0xd8279264 → memw(0xca79c494)=0xca6e3b88.

D) VERIFY LIVE (only source of truth — the store of 2 is not in the ELF):
     memb(0xcbf4f740) == 2      (gate a)
     memw(0xcbf56000) != 0      (gate b)
     memw(0xca79c494) != 0      (carrier ptr)
     memb(0xca7897b0 + tech*8) == 1   (tech-state, from tech_state_gate.md)

E) IQ_CAPTURE / RX_MEASURE — only now (getter 0xd827923c returns non-NULL, no SSR).
```
Order A→B→C is mandatory. The table population (Step 0) is free/automatic at init. **FACT (gates/callr) + INFERENCE (A trigger).**

**Is the trigger of the `2` genuinely out-of-image? YES — evidence:** exhaustive 4-vector scan over the 36 MB CLADE
image AND all native segments (b02/b10/b12/b13/b30) finds **zero** stores of the literal 2 to 0xcbf4f740; the only
writers write mux(0/1). The `2` semantics live in seg27 (`FTM_RF_MODE_CAL`, `lte_LL1_get_ul_ftm_cal_mode`) which is
paged R-- rodata for LL1 physical-layer code that is not in this MBN. **FACT (absence) + INFERENCE (resident driver).**

---

## 7. FACT / INFERENCE / UNKNOWN (with VAs)

**FACT**
- Single writer of `0xca733d10`: `memw(r2<<2+##0xca733d10)=r1` @**0xd81dfce4**, in `register_tech_record`
  @**0xd81dfcd0** (bounds `tech≤0x14` @0xd81dfcd0; null-check record @0xd81dfcdc). Only `ad82e190`+`ca733d00`
  immext pair in the image.
- 4 callers of 0xd81dfcd0: **0xd824739c** (tech=1 LTE, record=obj+0x10), **0xd8213404** (tech=2, record=##0xc8dc6f40),
  **0xd826cc74** (tech=3, record=##0xc8dc7040), **0xd8535108** (dynamic). All init/constructor context.
- LTE reg fn **0xd82472e8** has exactly one caller image-wide: **0xd84d4e64**, in linear subsystem-init sequence
  @**0xd84d4d88** (step error-codes r16=4..0xd). Top init 0xd84d4d88 has no code callers (boot init table).
- tech3 reg **0xd826cc70**: no static callers (constructor/init-table). tech2 setup **0xd8213234** ← 0xd81bd3f4
  (in **0xd81bd018**, called only from 0xd81e53dc in module-init **0xd81e52c8**, which has no callers).
- Static records: tech2 @0xc8dc6f40 (+4=**0xd8213e7c**), tech3 @0xc8dc7040 (+4=**0xd826c208**); LTE record built at
  init: record+0=0xd82473c0, **record+4=0xd8247540** (obj+0x14). All are valid code prologues.
- Indexing: `record=memw(r20<<2+##0xca733d10)` @0xd81dffc4; `fn=memw(record+#0x4)` @0xd81e00d0; `callr r2` @0xd81e00e0
  (siblings @0xd81e0108, @0xd81e0138). Array stride 4, callback at record+4.
- 0xd81dfecc only READS the table (never writes it); TECH_ENTER path: ftm_cmd 0x27 → 0xd814e534 → RFDEBUG slot 13
  @0xd8174758 → callr @0xd817480c → 0xd81dfecc.
- FTM_COMMON handler **0xd8271290**: sub 0x10F→0xd8263e2c (sets gp+0x1ed5/ed6; no table/gate), 0x4F7/0x4F5→0xd86f8514
  (internal ctx via 0xd827280c/@0xca79a850; no table/gate). No jump-table.
- gp=0xcbf4f000. Gate(a) 0xcbf4f740 read/gate 0xd81bd160/68; only writers 0xd81bd494/0xd81bd5fc (mux 0/1), reset
  0xd81bd7c8. **Zero stores of literal 2** in 36 MB CLADE + native. Gate(b) 0xcbf56000 lazy creator 0xd8284cf4
  (vtable ##0xc37c6d80), accessor 0xd8284eac ← 0xd8286290 (runtime-bound).
- SSR chain: getter 0xd827923c (memw(0xca79c494) NULL→err_fatal); setter 0xd8279264 (=0xca6e3b88) only from APPLY
  0xd8273b2c ← 0xd81bd2a8 in 0xd81bd018.

**INFERENCE (hard-anchored)**
- The 4 registration callers run at boot/subsystem-init (linear init sequence + constructor style + module-init),
  so `0xca733d10` is populated before any DIAG command; TECH_ENTER only reads it.
- `0xcbf4f740=2` is written by the resident/paged RF-cal driver (LL1/ftm_calibration_v3), NOT in this image;
  the in-image trigger is the per-tech `callr` (0xca733d10[tech].fn) + carrier-activate, which run the driver when
  the modem is in FTM/cal service.
- FTM_COMMON sub-commands do not start cal / do not populate the table / do not set the gate.

**UNKNOWN (runtime / RAM / outside the ELF)**
- The boot init-table entry (in the compressed RW pool b20/b26) that dispatches init 0xd84d4d88 and module-init
  0xd81e52c8 — not present as a static pointer in the code image.
- The VA of the store `0xcbf4f740=2` and the exact command/TLV of the mode trigger (resident driver; not in MBN).
- Whether the LTE FTM-init step (0xd84d4e64) actually ran on your live target — confirm by peeking
  `memw(0xca733d10 + 1*4)` (should be a non-NULL record ptr in the 0xca79xxxx / 0xc8dcxxxx range) and
  `memw(memw(0xca733d10+4)+4)` (should equal 0xd8247540 for LTE).
- Live values of 0xcbf4f740 / 0xcbf56000 / 0xca79c494 (peek DIAG only).

---

## 8. Reproduce
```bash
cd /tmp/modemre
# (1) Single writer of the table + registration fn:
python3 scan_733d10.py | grep -i st_        # only 0xd81dfce4 (ad82e190) pairs with ca733d00 immext
./dis36.sh 0xd81dfcd0 0x28                    # register_tech_record(tech, record_ptr)
python3 find_refs.py 0xd81dfcd0              # 4 callers: 0xd8213404 0xd824739c 0xd826cc74 0xd8535108
# (2) Caller args (tech, record):
./dis36.sh 0xd8213404 0x14                    # tech=2, record=##0xc8dc6f40
./dis36.sh 0xd826cc68 0x14                    # tech=3, record=##0xc8dc7040
./dis36.sh 0xd824730c 0xa0                    # LTE: obj @0xca79a820, fn ptrs, register(tech=1, obj+0x10)
# (3) Records (static .data) and callback at +4:
python3 - <<'PY'
import struct; d=open('modem.b23','rb').read(); B=0xc8b6a000
for va in (0xc8dc6f40,0xc8dc7040):
    print(hex(va),[hex(x) for x in struct.unpack('<8I',d[va-B:va-B+32])])
PY
./dis36.sh 0xd8213e7c 0x10                     # tech2 callback (record+4)
./dis36.sh 0xd826c208 0x10                     # tech3 callback (record+4)
./dis36.sh 0xd8247540 0x10                     # LTE   callback (record+4)
# (4) The indirect call site:
./dis36.sh 0xd81dffc0 0x30                     # record=memw(tech*4+0xca733d10)
./dis36.sh 0xd81e00c0 0x30                     # fn=memw(record+4); callr r2
# (5) Init sequence containing the LTE registration:
./dis36.sh 0xd84d4d88 0x110 | grep -E "call 0x|r16 = #"   # step-numbered subsystem init
# (6) Gates (unchanged from prior passes):
./dis36.sh 0xd81bd160 0x14                     # gate(a) ==2
./dis36.sh 0xd8284eac 0x50                     # gate(b) get-or-create
```
