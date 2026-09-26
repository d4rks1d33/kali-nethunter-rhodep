# tech_enter_callback — full disassembly & analysis of the LTE per-tech TECH_ENTER callback `0xd8247540`

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Primary image:** `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`, valid code `0xd8000000..~0xda800000`).
**Rodata/data:** `modem.b21` (VA `0xc3553000`), `modem.b23` (`.data`, VA `0xc8b6a000`), `seg27_dec.bin` (VA `0xce480000`, R-- rodata).
**Tools:** `dis36.sh`, `find_refs.py`, `_fns.pkl`, `reach_gate.py` (this pass).

**Legend:** **FACT** = byte/instruction read at a VA · **INFERENCE** = deduction with hard anchors · **UNKNOWN** = runtime/RAM/out-of-ELF only.

---

## 0. TL;DR — DIRECT ANSWERS

1. **`0xd8247540` is the LTE per-tech "enter/config" callback (record+4).** It is **in-image** (not resident):
   it is a mid-function entry point of the LTE FTM enter function whose proper prologue is at **`0xd82473e4`**,
   and whose main configuration worker is the sibling function at **`0xd8247574`**. When TECH_ENTER invokes it,
   it validates the LTE FTM object `@0xca79a820`, checks the session **mode byte `session+0x89a8`**, and — only
   when that byte `== 7` — runs a linear chain of LTE RF-config builder calls
   (`0xd84d389c`, `0xd84ddb88`, `0xd84d3a6c`, `0xd85afbc8`, `0xd823c430`, `0xd824d150`, `0xd825ecf4`,
   `0xd824163c`, `0xd8260e38`). **FACT (disasm) + INFERENCE (RF-config role).**
2. **It does NOT set cal mode itself.** Neither `0xd8247540`/`0xd82473e4`/`0xd8247574` nor any of their direct
   (function-bounded) callees references `0xcbf4f740`, `0xcbf56000`, or `0xca79c494`, and the whole callback body
   contains **zero `callr`** and **zero calls/jumps outside the code image** (only `0xc0913xxx` return thunks).
   The store of literal `2` to `0xcbf4f740` does not exist anywhere in the 36 MB image (re-confirmed: only writers
   are `mux(0/1)` at `0xd81bd494`/`0xd81bd5fc`). **FACT.**
3. **The branch that gates the RF-config work is a PARAMETER, not a hardcoded path: the session mode byte
   `session+0x89a8`.** The TECH_ENTER dispatcher (`0xd81dfecc`) and the callback both read this byte; the values
   used across the enter path are **{0, 2, 5, 7}**. `== 7` opens the RF-config/cal branch in the LTE callback
   (`0xd8247b24`) and in tech3 (`0xd826c21c`); `== 5` selects a single-arg invocation form in the dispatcher
   (`0xd81e00cc`). Additionally the enter path requires **`session+0xc == 2`** (`0xd81e5d20`). These two fields
   are the "mode/scenario" state that must already be armed. **FACT.**
4. **No separate in-image FTM command writes `session+0x89a8`/`session+0xc` via those offsets** (all 32 references
   in the image are READS; zero stores by that offset form). They are set on the runtime-allocated FTM session by
   the phone-mode / set-mode path (FTM_SET_MODE canonical / QMI-DMS FTM), which is not statically resolvable to a
   sub-cmd byte in this build (matches `ftm_set_mode_verified.md`). **FACT (absence of in-image store) + INFERENCE.**
5. **Conclusion:** As-is, the LTE TECH_ENTER callback **runs LTE RF bring-up config but never writes the cal gate**.
   The `2` (=`FTM_RF_MODE_CAL`) is written by resident/paged RF-cal code that the callback's RF-config calls reach
   only through deeper (out-of-image) driver code — the trigger is the whole FTM bring-up executed while the modem
   is in FTM/cal service AND with `session+0x89a8 == 7` / `session+0xc == 2` already set by a prior set-mode.
   **FACT (in-image edges/gates) + INFERENCE (resident writer).**

---

## 1. WHAT `0xd8247540` IS — record layout & entry-point structure (FACT)

### 1.1 The record is built at init by `0xd82472e8` (registrar); callback stored at record+4
`register_tech_record(tech=1, record=obj+0x10)` is issued from `0xd824739c`:
```
d8247398:  r0 = #0x1 ; r2 = memw(r17=&0xca79a820)     ; tech=1(LTE), r2 = LTE obj ptr
d824739c:  call 0xd81dfcd0                             ; register_tech_record
d82473a0:  r1 = add(r2,#0x10)                          ; record = obj+0x10
```
The obj fields were written just above (r0 = freshly-allocated obj at `0xd824730c`):
```
d824730c: memw(obj+#0x10) = ##0xd82473c0     ; record+0
d8247314: memw(obj+#0x14) = ##0xd8247540     ; record+4   <<< THE TECH_ENTER CALLBACK
d824732c: memw(obj+#0x1c) = ##0xd8247e00
d8247334: memw(obj+#0x20) = ##0xd8248800
d8247340: memw(obj+#0x28) = ##0xd82496c0
d8247348: memh(obj+#0x34) = ##0x302 ; memb(obj+#0x36) = #0x4
```
The obj ptr is stored to `memw(##0xca79a820)` in the registrar prologue:
```
d82472e8: call 0xd8829688 ; allocframe(#0x10)
d82472f0: call 0xd824a8f8                       ; alloc LTE obj -> r0
d82472fc: memw(r17=##0xca79a820) = r0           ; obj stored
```
**FACT.** The registrar `0xd82472e8` runs at subsystem init (single caller `0xd84d4e64` in the linear init
sequence `0xd84d4d88`; see `cal_entry_sequence.md`). So `0xca79a820`→obj and record+4=`0xd8247540` are populated
before any DIAG command. **FACT.**

### 1.2 How TECH_ENTER reaches and invokes the callback (FACT)
```
DIAG 4B 0B  27 00  0D 00 ...   ; SUBSYS_CMD_F, SUBSYS_FTM, ftm_cmd_id=0x27 (FTM_LTE), RFDEBUG sub=0x000D (TECH_ENTER)
  ftm_cmd table @0xc37bc828[0x27] = 0xd814e534 (FTM_LTE)      [0x00]=0xd814e1ec (FTM_COMMON)
  -> RFDEBUG slot 13 handler 0xd8174758 -> callr @0xd817480c
  -> enter path 0xd81e5cec / commit 0xd81dfecc
```
Commit `0xd81dfecc` register flow (FACT):
```
d81dfeec:  r19 = r0(ctx) ; r2 = memw(ctx)                     ; r2 = session
d81dff14:  r16 = memw(session+#0xc) ; r20 = memw(session+#0x0); r16=session mode word, r20=tech id
d81dff20:  r3  = memb(session+##0x89a8)                       ; MODE BYTE
d81dff2c:  r21 = add(##0xca733d10, asl(r21,#0x2))             ; r21 = &table[tech]  (slot address)
d81dffc4:  r1  = memw(r20<<#0x2 + ##0xca733d10)               ; record ptr = table[tech] = obj+0x10 (READ)
...
d81e00c0:  r2 = memw(r19+#0x0) ; r3 = memw(r21+#0x0)          ; r2=session, r3 = table[tech] = record ptr(obj+0x10)
d81e00c8:  r2 = memb(r2+##0x89a8)                             ; MODE BYTE again
d81e00cc:  p0 = cmp.eq(r2,#0x5); if(!p0) jump 0xd81e0100      ; <<< branch on mode==5
d81e00d0:  r2 = memw(r3+#0x4)                                 ; fn = memw(record+4) = memw(obj+0x14) = 0xd8247540
d81e00d8:  r21=#1 ; r1=#0
d81e00dc:  r0 = r19(session/ctx)
d81e00e0:  callr r2                                           ; <<< INVOKE 0xd8247540 (mode==5: r0=ctx, r1=0)
  ; mode!=5 path:
d81e0104:  r1:0 = combine(#0,r19) ; callr r2  @0xd81e0108     ; 0xd8247540 with arg1=0
d81e0134:  r1:0 = combine(#1,r19) ; callr r2  @0xd81e0138     ; 0xd8247540 with arg1=1
```
**All three `callr` sites invoke `memw(record+4) = 0xd8247540`.** The mode byte `session+0x89a8` decides which
form (single call with r1=0, vs two calls r1=0 then r1=1). **FACT (byte-exact).**

### 1.3 `0xd8247540` is a valid in-image entry inside the LTE enter function (FACT)
`_fns.pkl` function boundaries:
- `0xd82472e8 .. 0xd82473e4` = registrar (record+0 = `0xd82473c0` is its err/return tail).
- **`0xd82473e4 .. 0xd8247574`** = LTE enter function; **record+4 = `0xd8247540` is a jump-target inside it.**
- **`0xd8247574 .. 0xd8247e28`** = LTE enter main worker (also exposes `obj+0x1c=0xd8247e00` = its log/return tail).

These record fields are **multiple entry points / continuation labels of the compiler-fused LTE FTM state
functions** (same pattern for tech2/tech3, but tech2's record+4 `0xd8213e7c` happens to align on a clean
prologue `call 0xd814e6ac; allocframe(#0x50)`). **FACT.**

---

## 2. FULL DISASSEMBLY — what the callback does (FACT)

### 2.1 LTE enter function `0xd82473e4` (contains the record+4 entry `0xd8247540`)
```
d82473e4:  call 0xd8829688 ; allocframe(#0x10)                ; prologue
d82473f0:  r2 = memw(##0xca79a820)                            ; LTE obj
d82473f4:  if (r2!=0) jump 0xd8247408                         ; else -> assert 0xd8242f98 (fatal)
d8247408:  r16 = r0 ; if(r0==0) ... (0xd80d8998/0xd824a148 = log/return)
d8247420:  r17 = add(r16,#0x7f24) ; r18=#0xc8 ; r19 = add(r16,#0x7f10)
d824742c:  r1  = memw(r16+#0x0)                               ; state word
d8247430:  cmp.eq(r1,#1) ...                                   ; state machine on r16->0 in {1,2,8,...}
   ... configures memw(r19+0/4/8/c), memh(memw(r17)+8/a/c) with #0x1d0/#0x9a/#0x108/#0xd9/#0x23f ...
d8247540:  immext(#0xc0); memh(r3+#0x8)=##0xd9 ; memh(r3+#0x4)=##0x23f     ; <<< record+4 ENTRY (continuation)
d8247550:  r17=#0x1 ; cmp.eq(r2,#0); if(!=0) jump 0xd824756c
d8247558:  r1:0=combine(0,##0xf802a550) ; r2=memw(r16) ; r3=memw(r16+#0xc)
d8247564:  call 0xd80dec28 (log) ; r17=#0
d824756c:  r0 = r17 ; jump 0xc0913b74                          ; return r0 (0/1 status)
```
Semantics (INFERENCE with FACT anchors): this arm sets small config half-words (`0xd9`, `0x23f`, `0x1d0`, `0x9a`,
`0x108`) into the LTE object's sub-structs (`obj+0x7f10`/`obj+0x7f24` regions) based on the object's current state
word, then returns a 0/1 status. It is a **parameter/state-record setter**, heavily log-instrumented
(`0xd80d8998`, `0xd824a148`, `0xd8242f98` = QSHRINK log / err handlers). **No gate, no callr, no resident call.**

### 2.2 LTE enter main worker `0xd8247574` (the real bring-up body)
Prologue + call graph (FACT — full listing in `/tmp/cbw.txt`):
```
d8247574:  call 0xd814e6ac ; allocframe(#0xc0)                ; prologue (r17:16 = combine(r1,r0) = args)
...
d8247b18:  r25 = memw(r29+#0x44)                              ; r25 = session (saved)
d8247b1c:  immext(#0x8980)
d8247b20:  r2  = memb(r25+##0x89a8)                           ; MODE BYTE
d8247b24:  if (cmp.eq(r2,#0x7)) jump 0xd8247b38                ; <<< mode==7 -> RF-CONFIG BLOCK
   (mode!=7 -> jump 0xd8247ba8, skips the RF-config calls)
;---- RF-CONFIG BLOCK (mode==7) ----
d8247b48:  call 0xd84d389c        ; band/BW packer (extractu/asl on r16..r4 -> RF cfg word)
d8247b60:  call 0xd84ddb88        ; RF global @0xcaad5944 update
d8247b74:  call 0xd84d3a6c        ; RF cfg apply (via 0xd814f088 helper)
d8247b94:  call 0xd85afbc8        ; RF instance op (via 0xd85aeec0)
d8247bbc:  call 0xd823c430        ; band/earfcn cfg (extractu/asl pack, big frame 0xe0)
d8247bd4:  call 0xd824d150        ; RF session op (0xd8829a00/0xd824d24c/0xd824d23c)
d8247bf4:  call 0xd825ecf4        ; RF cfg (0xd82430d8)
d8247c14:  call 0xd824163c        ; RF cfg apply/notify
   (earlier: d8247b0c call 0xd8260e38 = mode/state query returning 0/1)
d8247c3c:  cmp.eq(r22,#1) -> success/return path (log 0xd8242f98/0xd8157900/0xd824a75c)
d8247e0c:  r0 = r23 ; jump 0xc0913ad8                          ; return
```
**FACT (disasm) + INFERENCE (each callee is an LTE RF band/channel/bandwidth config builder; `extractu`/`asl`
packing + RF globals confirm RF-config, not cal-gate).**

### 2.3 Call-graph facts (FACT)
- Direct callback body (`0xd82473e4..0xd8247e28`): **0 `callr`**, **0 out-of-image call/jump** except return thunks
  `0xc0913ad8`/`0xc0913b74`.
- Its unique (non-log) callees `0xd84d389c / 0xd84ddb88 / 0xd84d3a6c / 0xd85afbc8 / 0xd823c430 / 0xd824d150 /
  0xd825ecf4 / 0xd824163c / 0xd8260e38`: none reference `0xcbf4f740`, `0xcbf56000`, `0xca79c494`, `0xd81bd018`
  (carrier-activate), `0xd8273b2c` (APPLY), or `0xd8279264` (carrier setter) in their own bodies (grep = 0 each).
  They reach the paged RF driver only through deeper code. **FACT.**

---

## 3. DOES IT SET CAL MODE / REFERENCE THE GATES? (FACT)

- **`0xcbf4f740` (gate a):** not referenced by the callback or its direct callees. Image-wide the ONLY writers are
  `memb(gp+#0x740)=mux(pred,#1,#0)` at **`0xd81bd494`** and **`0xd81bd5fc`** (values 0/1), reset `=0` at
  `0xd81bd7c8`. The gate READER at `0xd81bf2d8` uses only `memub/memb(gp+#0x740)` (loads). **Zero stores of the
  literal 2 in the whole 36 MB image.** **FACT.**
- **`0xcbf56000` (gate b):** not referenced by the callback. Created lazily by `0xd8284cf4`/`0xd8284eac`
  (runtime/event-bound; see `rf_cal_mode_gates.md`). **FACT.**
- **`0xca79c494` (carrier ptr):** not referenced by the callback. Set only via APPLY `0xd8273b2c`→`0xd8279264` in
  the carrier-activate `0xd81bd018` (a DIFFERENT command path — RADIO_CONFIG). **FACT.**
⇒ **The LTE TECH_ENTER callback does not, by itself, enter cal mode or open either gate.** **FACT.**

---

## 4. THE MODE PARAMETER — `session+0x89a8` and `session+0xc` (FACT)

The callback's cal/RF-config branch is **parameter-gated**, not hardcoded:

| Field | Where read | Values compared | Effect |
|---|---|---|---|
| `session+0x89a8` (byte) | `0xd81dff30`, `0xd81e00cc`, `0xd81e5d60`, `0xd8247b24`, `0xd826c21c`, `0xd828a0e8`, `0xd81e0fa8`, … | **7, 5, 2, 0** | `==7` opens the RF-config block in the LTE callback worker (`0xd8247b24`) and tech3 (`0xd826c21c`); `==5` selects single-arg invocation form (`0xd81e00cc`); `==7` also sets `r0=0x10` in enter path (`0xd81e5d60`). |
| `session+0xc` (word) | `0xd81dff14`+downstream, `0xd81e5d20` | **==2 required** | Enter path proceeds only if `session+0xc == 2` (`if(!=2) jump skip`, `0xd81e5d20`); this is the session-mode gate noted in `tech_state_gate.md`. |

- **Enum meaning (INFERENCE):** `session+0x89a8` is the FTM **phone-mode / scenario** selector; value **7** is the
  RF-config/cal scenario that makes the enter callback build+apply the RF config (the branch that ultimately drives
  the resident cal driver). Value 5 is a lighter single-call form. `session+0xc == 2` is the session-active mode.
- **Who sets these fields (FACT + INFERENCE):** all **32** in-image references to `+0x89a8` are **READS**
  (`memb`/`memub`); there is **no store to `+0x89a8` by that offset form anywhere in the image** (grep for stores = 0).
  The FTM session object is runtime-allocated (via `memw(ctx)`), and the mode/scenario byte is written by the
  **phone-mode / set-mode path** on that object (FTM_SET_MODE canonical or QMI-DMS FTM), whose store is not
  statically at `+0x89a8` in this MBN. So a **prior set-mode command arms the byte**; TECH_ENTER only reads it.
  **FACT (all reads, no in-image store) + INFERENCE (set-mode writes it).**

---

## 5. IS THERE A SEPARATE "ARM CAL MODE" COMMAND BEFORE TECH_ENTER? (FACT + INFERENCE)

- **FTM_COMMON static dispatch `0xd8271290`** recognizes only sub-cmds `0x10F / 0x4F7 / 0x4F5` — none arm cal
  (`0x10F`=`0xd8263e2c` sets `gp+0x1ed5/0x1ed6=1`, not the cal gate or the mode byte). **FACT.**
- **FTM_COMMON alt handler `0xd8271400`** dispatches sub-cmds `0/1/2/4` via `callr` to handler tables at
  `0xd86f82xx..0xd86f84xx`, and a jump-table `@0xc37c649c` for the `0x10/0x20/0x30` group
  (`r3 = memw(r2<<2+##0xc37c649c); jumpr r3` @`0xd8271510`). This is the family that includes the classic
  **FTM_SET_MODE (cmd 0x0001, mode=2/cal)**; its exact sub-cmd byte for "enter cal phone-mode" is **UNKNOWN
  statically** (binding lives in the runtime FTM dispatch tables / resident code — matches
  `ftm_set_mode_verified.md §4`). **FACT (handler shape) + UNKNOWN (exact byte).**
- **No in-image FTM command writes `0xcbf4f740=2` directly** (§3). The set-mode command's effect on the gate is a
  side-effect executed by the resident RF-cal driver, not an in-image store. **FACT + INFERENCE.**

⇒ **A prior set-mode is required to arm `session+0x89a8 == 7` (and `session+0xc == 2`)** so the TECH_ENTER
callback takes the RF-config branch. The set-mode's exact DIAG byte is not resolvable from the MBN. **INFERENCE.**

---

## 6. BYTE-EXACT SEQUENCE (as far as the MBN allows) / evidence of out-of-MBN

The **in-image** callback is fully mapped and takes its RF-config branch on a parameter (`session+0x89a8==7`).
The **cal-gate write itself is out-of-MBN.** Best-supported live sequence (verify by DIAG peek):

```
A) Put modem in FTM/CAL service (rfm_init + MCPM + resident RF-cal driver up) AND arm the FTM phone-mode
   so the session mode byte session+0x89a8 becomes 7 and session+0xc becomes 2.
   -> FTM_SET_MODE (FTM_COMMON, mode=cal) or QMI-DMS FTM.  Exact sub-cmd byte = UNKNOWN (out-of-MBN binding).
   Prereqs (seg27 anchors): RFM_INIT (@0xce6de1b0), MCPM ON (@0xce748848).

B) TECH_ENTER(LTE)   [ftm_cmd 0x27, RFDEBUG sub 0x000D]
   4B 0B 27 00 0D 00 03 00
      01 00 04 00 00 00 00 00     ; SUB=0
      02 00 04 00 01 00 00 00     ; TECH=1 (LTE)
      03 00 04 00 00 00 00 00     ; SCENARIO=0
   -> commit 0xd81dfecc -> callr memw(0xca733d10[1]+4)=0xd8247540 (in-image LTE enter callback).
      IF session+0x89a8==7: worker 0xd8247574 runs the RF-config chain (0xd84d389c..0xd824163c) which drives
      the resident RF driver that (with A done) writes 0xcbf4f740=2 and creates 0xcbf56000.
      IF session+0x89a8!=7: the RF-config block is skipped (branch @0xd8247b24) -> gate stays closed.

C) RADIO_CONFIG(BAND+EARFCN+BW+RX_CARRIER)  -> carrier-activate 0xd81bd018 -> APPLY 0xd8273b2c ->
   setter 0xd8279264 -> memw(0xca79c494)=0xca6e3b88 (only if gate a==2 && gate b!=0).

D) VERIFY LIVE (only source of truth; the store of 2 is not in the ELF):
   memb(0xcbf4f740)==2 ; memw(0xcbf56000)!=0 ; memw(0xca79c494)!=0 ;
   ALSO peek memb(session+0x89a8)==7 and memw(session+0xc)==2 to confirm A armed the mode.

E) IQ_CAPTURE / RX_MEASURE (only now; getter 0xd827923c non-NULL, no SSR).
```
**Out-of-MBN evidence (FACT):** exhaustive 4-vector scan finds zero stores of literal 2 to `0xcbf4f740`; the
callback body has zero `callr` and zero out-of-image edges except return thunks; the `2` semantics
(`FTM_RF_MODE_CAL` @0xce6cf902, `NR5G_LL1_CAL_FTM_RF_MODE_CAL` @0xce6cf8e3, `lte_LL1_get_ul_ftm_cal_mode` @0xce5bd52b)
live in seg27 R-- rodata for LL1 code not present in this MBN.

---

## 7. FACT / INFERENCE / UNKNOWN (with VAs)

**FACT**
- Registrar `0xd82472e8`: alloc obj (`call 0xd824a8f8`), store `memw(##0xca79a820)=obj`; writes obj+0x10=0xd82473c0,
  **obj+0x14 (record+4) = 0xd8247540**, obj+0x1c=0xd8247e00, obj+0x20=0xd8248800, obj+0x28=0xd82496c0,
  memh(obj+0x34)=0x302, memb(obj+0x36)=4; `register(tech=1, record=obj+0x10)` @0xd824739c (call 0xd81dfcd0, r1=obj+0x10).
- Dispatcher `0xd81dfecc`: session=memw(ctx) @0xd81dfeec; session+0xc @0xd81dff14; **mode byte session+0x89a8**
  @0xd81dff20/@0xd81e00c8; record ptr = memw(tech*4+0xca733d10) @0xd81dffc4; fn = memw(record+4) @0xd81e00d0;
  branch `cmp.eq(mode,#5)` @0xd81e00cc; `callr r2`(=0xd8247540) @0xd81e00e0 (r0=ctx,r1=0), siblings @0xd81e0108
  (r1=0), @0xd81e0138 (r1=1).
- `_fns.pkl` boundaries: registrar 0xd82472e8..0xd82473e4; LTE enter fn 0xd82473e4..0xd8247574 (record+4=0xd8247540
  is a jump-target inside it); LTE enter worker 0xd8247574..0xd8247e28 (obj+0x1c=0xd8247e00 inside it).
- Callback body 0xd82473e4/0xd8247574: references `memw(##0xca79a820)` @0xd82473f0; **mode byte
  memb(r25+##0x89a8)** @0xd8247b20 with `cmp.eq(#7)` @0xd8247b24 gating RF-config block; RF-config calls
  0xd84d389c @0xd8247b48, 0xd84ddb88 @0xd8247b60, 0xd84d3a6c @0xd8247b74, 0xd85afbc8 @0xd8247b94, 0xd823c430
  @0xd8247bbc, 0xd824d150 @0xd8247bd4, 0xd825ecf4 @0xd8247bf4, 0xd824163c @0xd8247c14, 0xd8260e38 @0xd8247b0c;
  return thunks 0xc0913ad8/0xc0913b74. **Zero callr; zero out-of-image edges except thunks.**
- Gate a `0xcbf4f740`: reader/gate 0xd81bd160/0xd81bd168; ONLY writers 0xd81bd494 (`memb(gp+0x740)=mux(p0,1,0)`)
  and 0xd81bd5fc (`=mux(p2,1,0)`), reset 0xd81bd7c8 (=0); reader 0xd81bf2d8 (memub/memb loads @0xd81bf300/34c/39c/40c).
  **Zero stores of literal 2.**
- ftm_cmd table @0xc37bc828[0x00]=0xd814e1ec, [0x27]=0xd814e534. Enter path also 0xd81e5cec:
  `if(session+0xc!=2) skip` @0xd81e5d20; `memb(0xca7897b0+tech<<3)=1` @0xd81e5d54; `memb(session+0x89a8)` `cmp.eq(#7)`
  @0xd81e5d60.
- FTM_COMMON dispatch 0xd8271290 (subs 0x10F/0x4F7/0x4F5 only); alt 0xd8271400 (subs 0/1/2/4 via callr to
  0xd86f82xx..0xd86f84xx; jump-table @0xc37c649c @0xd8271510 for 0x10/0x20/0x30 group).
- All 32 image references to `+0x89a8` are READS; zero stores to `+0x89a8` by that offset form.

**INFERENCE (hard-anchored)**
- `0xd8247540` is the LTE per-tech TECH_ENTER "enter/config" callback (record+4); it runs LTE RF band/channel/BW
  config builders when the session mode byte `session+0x89a8 == 7`, and returns a 0/1 status. It does not itself
  write the cal gate.
- `session+0x89a8` is the FTM phone-mode/scenario selector (7 = RF-config/cal scenario; 5 = lighter form);
  `session+0xc == 2` = session-active. Both must be armed by a prior set-mode command (FTM_SET_MODE cal /
  QMI-DMS FTM) before TECH_ENTER's callback takes the RF-config branch.
- The `0xcbf4f740=2` write is performed by resident/paged RF-cal driver code reached only through the deeper
  callees of the callback's RF-config chain (not in-image).

**UNKNOWN (runtime / RAM / out-of-ELF)**
- The exact DIAG sub-cmd byte + payload of the set-mode command that writes `session+0x89a8=7` / `session+0xc=2`
  (runtime FTM dispatch binding; not statically at those offsets in the MBN).
- The VA of the store `0xcbf4f740=2` (resident driver, absent from the MBN).
- Live values of `session+0x89a8`, `session+0xc`, `0xcbf4f740`, `0xcbf56000`, `0xca79c494` (peek DIAG only).
- Runtime value stored at obj+0x14 on the live target (should equal 0xd8247540; confirm via
  `memw(memw(0xca733d10+4)+4)`).

---

## 8. REPRODUCE
```bash
cd /tmp/modemre
# Registrar & record fields (record+4 = 0xd8247540):
./dis36.sh 0xd82472e8 0x28            # alloc obj, store to 0xca79a820
./dis36.sh 0xd824730c 0x40            # memw(obj+0x14)=##0xd8247540
./dis36.sh 0xd8247398 0x18            # register(tech=1, record=obj+0x10)
# Callback function & entry:
./dis36.sh 0xd82473e4 0x190           # LTE enter fn; record+4=0xd8247540 is inside
./dis36.sh 0xd8247574 0x8b4           # LTE enter worker; mode byte @0xd8247b20 cmp #7 @0xd8247b24
# Invocation & mode-byte branch:
./dis36.sh 0xd81dfecc 0x60            # session, session+0xc, session+0x89a8
./dis36.sh 0xd81e00c0 0x30            # fn=memw(record+4); cmp.eq(mode,#5); callr 0xd8247540
./dis36.sh 0xd81e5cec 0x80            # enter path: session+0xc==2, session+0x89a8==7
# No gate write in callback / image:
python3 - <<'PY'
import re
n=0
for ln in open('_big1.txt'):
    if re.search(r'gp\+#0x740\) = ',ln): print(ln.rstrip()); n+=1
print('gate-a writers:',n)   # only 0xd81bd494 / 0xd81bd5fc (mux 0/1)
PY
# Callback subtree callees don't touch gate/activate:
for f in 0xd824d150 0xd825ecf4 0xd824163c 0xd823c430 0xd8260e38 0xd85afbc8 0xd84d389c 0xd84d3a6c 0xd84ddb88; do
  echo -n "$f "; ./dis36.sh $f 0x400 | grep -cE '0xd81bd018|0xd8273b2c|0xd8279264|memb\(gp\+#0x740\) = '; done
```
