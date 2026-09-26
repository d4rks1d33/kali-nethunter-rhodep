# cal_trigger_chain — Backward call-graph from the `session+0x89a8 = 7` writers to the triggering command

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Primary image:** `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`, 36 MB decompressed CLADE code).
**Tools this pass:** `dis36.sh`; `/tmp/modemre/callers36.py` (J2_call/J2_jump xref over the FULL 36 MB, not just the 10 MB slice); `/tmp/modemre/fwd2.py` (forward direct-call reachability BFS with a *corrected* function-boundary detector, `/tmp/modemre/term.py`); jump-table read from `modem.b21` (VA `0xc3553000`).
**Legend:** **FACT** = byte/instruction/edge read at a VA · **INFERENCE** = deduction with hard anchors · **UNKNOWN** = runtime/RAM/out-of-image only.

> **Method correction vs. prior reports:** earlier passes said the `0x89a8=7` writers had "no static callers / are runtime-vtable only". That was because (a) the caller scan used the 10 MB slice, not the full 36 MB, and (b) the function-boundary heuristic missed several `dealloc_return` *duplex* encodings (`…1f40/…3f40/…1f00/…3fc0`), which merged functions and hid one indirect edge. With the full-image scanner and the fixed boundary detector, **both `=7` writers ARE reachable by direct calls from a single RFTEST dispatcher branch** (the last hop into that branch is one `callr`, resolved by immediate below).

---

## 0. TL;DR — DIRECT ANSWERS

1. **The command is a DIAG/FTM command — category (a).** Specifically the **FTM RFTEST 0x10xx family** under **ftm_cmd_id `0x27` (FTM_LTE)**, dispatched by sub_command `0x10xx` (e.g. **`0x1002`**) through the RFTEST jump-table `@0xc37c649c`. The RFTEST canonical handler `0xd86fd0f4` packs three wire bytes `{0x0f,0x10,0x11}` and calls dispatcher `0xd8272cd8`, which **branches on wire byte `0x0f` (the "mode/type" field)**:
   - `byte0x0f != 0` → `callr 0xd826d3e4` (RX capture/measure "decode‑A", the getter‑SSR path).
   - **`byte0x0f == 0` → `callr 0xd8292298` (function `0xd8291504`) → the RF‑config‑apply chain that runs the per‑tech phone‑mode setter `0xd8d3ed08` and the IRAT/config setter `0xd84b1f78`, both of which write `session+0x89a8`.** **FACT.**
2. **`0xd8d3ee24` (=7 when `r20==3`): `r20` is the 4th call argument (the "mode" enum) of `0xd8d3ed08`.** It is NOT a wire byte. It is set to **`3`** inside `0xd8d9ed9c` (`r18 = #0x3` @`0xd8d9eeec`) **only when the internal RF‑state query `0xd8deabf0(idx)` returns a value ≠ 9 and ≠ 0xa**. `0xd8deabf0` just reads a per‑carrier byte from an internal RF‑config array (stride `0x178`, base ≈ `0xcb9eb074`). ⇒ **the choice 7‑vs‑2 is INTERNAL RF state, not a command parameter.** **FACT.**
3. **`0xd84b2124` (=7 vs 0): the `7` is written iff `0xd80d1750()` returned non‑zero** (result stashed at `memw(r29+0x1c)`, `call 0xd80d1750` @`0xd84b2014`/`…2024`, store @`0xd84b2034`; use @`0xd84b2104`; `r2=#7` @`0xd84b211c`; store @`0xd84b2124`). `0xd80d1750` is a **generic FTM request validator/dispatcher** (called from ~dozens of FTM handlers in `0xd815xxxx`): it checks a global phase byte `memb(0xcb8dbeb0+0x18)==3` and then `callr`s a per‑request handler; its return depends on that phase + request contents. ⇒ **again INTERNAL state (FTM phase), gated by whether the FTM service is in the right phase.** **FACT.**
4. **Both `=7` writers converge under the SAME command branch (`0xd8291504`, reached when wire byte `0x0f == 0`).** So the trigger is: **an RFTEST 0x10xx command with the mode byte (`+0x0f`) = 0**, executed while the modem is already in FTM/cal service (so the internal selectors resolve to the "cal" value). **FACT (convergence) + INFERENCE (phase precondition).**
5. **It is a TWO‑STEP relationship with TECH_ENTER, not one command that both arms and enters.** The RFTEST‑0x10xx config command (this chain) is what actually **writes `session+0x89a8`** via the RF driver; **TECH_ENTER (`ftm_cmd 0x27`, RFDEBUG sub `0x000D`) only READS** `session+0x89a8` (`0xd81dfecc` @`0xd81dff20`, `cmp.eq(#7)` @`0xd81dff30`) and `session+0xc` as gates. So: **command X = RFTEST‑0x10xx config with mode byte 0 (arms `0x89a8`), THEN TECH_ENTER runs the cal branch.** **FACT.**

---

## 1. THE TWO WRITERS OF 7 — enclosing functions & what selects the value (FACT)

### 1.1 `0xd8d3ee24` — enclosing function `0xd8d3ed08`, value chosen by call‑arg `r20`
Prologue `0xd8d3ed08` (`call 0xd814f088`… `allocframe(#0x48)`):
```
d8d3ed08:  r17 = r4                         ; r4 = tech/carrier index
d8d3ed14:  r19 = r4 * 0x1de0                ; per-carrier stride
d8d3ed18:  r18 = r0                          ; arg0
d8d3ed28:  r21:20 = combine(r2,r3)          ; <<< r20 = r3 = ARG3 ("mode" enum), r21 = r2 = arg2
d8d3ed50:  p1 = cmp.eq(r20,#0x6)            ; mode==6 branch
d8d3edc0:  p0 = cmp.eq(r20,#0x2)            ; mode==2 branch (writes r21 to +0x10)
d8d3edcc:  memb(r24+#0x14) = r20            ; stores the mode into per-carrier struct+0x14
d8d3ee14:  p0 = cmp.eq(r20,#0x3)           ; <<< mode==3
d8d3ee20:  r2 = #0x7
d8d3ee28:  memb(r0+##0x89a8) = r2.new       ; <<< session+0x89a8 = 7   (only when r20==3)
```
**FACT.** So **`r20` (== call arg #4) is the RF phone‑mode enum**; value `3` ⇒ write `7`. It is a *parameter passed by the caller*, not an internal read here.

**Where mode `3` originates (FACT):** the only caller of `0xd8d3ed08` that passes a *variable* mode is `0xd8ce4804` (`r3:2=combine(r1,r0); r0=#3; r4=r2` → r20 = the caller's `r1`), inside wrapper `0xd8ce47f0`. That wrapper's mode comes from `0xd8ce46e0` (`r1:0=combine(r20,r21)` → passes its own arg3), whose sole caller is `0xd8d9efd0` inside **`0xd8d9ed9c`**, where:
```
d8d9ee28:  r18 = #0x2                       ; default mode = 2  (→ 0x89a8 = 2, FTM_RF_MODE_CAL "normal")
d8d9eee0:  call 0xd8deabf0 (r0=idx)         ; INTERNAL RF-state query (per-carrier byte)
d8d9eee8:  if (r0 == 9)  jump …(r18=2)
d8d9eeec:  r18 = #0x3                       ; <<< mode = 3  → 0x89a8 = 7   (when query != 9 and != 0xa)
d8d9eef8:  if (r0 == 0xa) jump …(r18=2)
```
`0xd8deabf0` is tiny — `r2 = idx*0x178; r0 = memub(r2 + (base ≈ 0xcb9eb074))` — i.e. it **reads a per‑carrier byte from an internal RF‑config array** (RW `.data`, stride `0x178`). **FACT.** ⇒ **mode 3 (→ `0x89a8=7`) is selected by internal RF state (current band/mode class of the carrier), not by any command byte.** **FACT.**

Other constant callers of `0xd8d3ed08` write different modes (all FACT): `0xd8ce4970`=1, `0xd8ceed04`=6, `0xd8ceee94`=6, `0xd8cef264`=5, `0xd8d8c9a4`=1, `0xd8d91a14`=1, `0xd8dcf228`=1. None writes `3`.

### 1.2 `0xd84b2124` — enclosing function `0xd84b1f78` (host loop fn `0xd84af364`), value from `0xd80d1750`
```
d84b2014/…2024:  call 0xd80d1750           ; FTM request validator/dispatcher
d84b2034:        memw(r29+#0x1c) = r0       ; stash return
d84b2104:        r3 = memw(r29+#0x1c)
d84b2108:        p0 = r3                     ; p0 = (0xd80d1750 return != 0)
d84b210c:        if (!p0) jump 0xd84b2120   ; return==0 → r2 stays 0
d84b211c:        r2 = #0x7                   ; return!=0 → value 7
d84b2120:        r0 = memw(r29+#0x18)
d84b2124:        memb(r16+##0x89a8) = r2     ; <<< session+0x89a8 = 7 (iff 0xd80d1750()!=0)
```
**FACT.** `0xd80d1750` (FACT): loads global `r18 = ##0xcb8dbeb0`, `r2 = memb(r18+0x18)`, compares `== 3` (a service **phase** byte), bounds‑checks `memh(r18+0x10) > memh(request+0x2)`, then `callr r2` into a per‑request handler table; its return (and thus the `7`) depends on the **FTM service phase == 3** plus the request. `0xd80d1750` is called from very many FTM handlers in `0xd815xxxx` (e.g. `0xd81505c8`, `0xd815062c`, `0xd81507e8`, `0xd815232c`, …), i.e. it is a **shared FTM command validation/dispatch primitive**. **FACT.** ⇒ the `7` here is gated by **internal FTM phase state**, again not a raw command byte.

---

## 2. THE BACKWARD CALL CHAIN (writers → command) — with VAs (FACT)

Full-image forward reachability (`fwd2.py`, corrected boundaries) confirms both writer functions are reachable **by direct calls** from one RFTEST dispatcher branch target `0xd8292298` (function **`0xd8291504`**), which is invoked by the dispatcher's `callr` when wire byte `0x0f == 0`:

```
WIRE (DIAG):  4B 0B  27 00  <sub_command u16 LE = 0x10xx, e.g. 02 10>  …  [flattened payload, byte +0x0f = 0]
   │  diagpkt SUBSYS_CMD_F (0x4B), SUBSYS_FTM (0x0B), ftm_cmd_id @wire[2..3] = 0x27 (FTM_LTE)
   ▼
ftm_cmd table  @0xc37bc828[0x27] = 0xd814e534         (FTM_LTE arm)                         [FACT]
   ▼
LTE sub_command RANGE router  0xd8157ec4              (reads sub @wire[4..5])                [FACT]
   │  0x1000–0x3FFF → RFTEST dispatcher 0xd8271484/0xd82714b4
   ▼
RFTEST dispatcher  0xd82714b4                                                                [FACT]
   │  r2 = memub(req+4) (low), r3 = memub(req+5) (high); if high==0x10 & low<=0x17:
   │  r3 = memw(low<<2 + ##0xc37c649c); jumpr r3        (jump-table @0xc37c649c)
   ▼
jump-table slot[low]  → trampoline  (e.g. slot 2 / sub 0x1002 → 0xd8271548)                 [FACT]
   │  0xd8271548: r2 = ##0xd86fd0f4; jump 0xd8271630
   ▼
common handler  0xd8271630:  callr r2  → 0xd86fd0f4    (RFTEST canonical handler)            [FACT]
   ▼
RFTEST canonical handler  0xd86fd0f4                                                         [FACT]
   │  r19 = memub(req+0xf)   ; <<< MODE byte 0x0f
   │  r18 = memub(req+0x10)  ; r20 = memub(req+0x11)
   │  r19 |= (r18 | r20<<8) << 16       (packed 24-bit {0x0f | 0x10<<16 | 0x11<<24})
   │  r1:0 = combine(r17, #3)           (r0 = fixed 3 = "mode-3 class"); r2 = r19(packed)
   ▼
dispatcher  0xd8272cd8                                                                       [FACT]
   │  r20 = r2 = packed;  r0 = sxth(r20) = byte0x0f
   │  r18 = 0xd82735cc(byte0x0f)         (clamp: if byte0x0f>0x13 → 0x15, else byte0x0f&0xff)
   │  p0 = (r18 != 0)                     (= byte0x0f != 0)
   │  if  p0 → r5 = 0xd826d3e4  (RX capture/measure "decode-A", getter/SSR path)
   │  if !p0 → r5 = 0xd8292298  (== byte0x0f == 0)                                            [FACT]
   │  callr r5
   ▼
=== byte0x0f == 0 branch ===  function 0xd8291504 (entry 0xd8292298)                          [FACT]
   │   (validates, gets carrier via getter 0xd827923c, then RF-config apply)
   ├─► path to writer #1 (0xd8d3ee24 = 7):
   │     0xd8291504 → … → 0xd8de3304 → 0xd8c91638 → 0xd8c90210 → 0xd8d032a0
   │       → 0xd8da1df0 → 0xd8da1f58 → 0xd8d9ed9c (r18=3 iff 0xd8deabf0 != 9,0xa)
   │       → 0xd8d9efd0 → 0xd8ce46e0 → 0xd8ce47f0 → 0xd8ce4804
   │       → 0xd8d3ed08 (r20=3) → memb(session+0x89a8)=7  @0xd8d3ee28                          [FACT]
   └─► path to writer #2 (0xd84b2124 = 7):
         0xd8291504 → 0xd814dffc → 0xd82dac60 → 0xd82e326c → 0xd84b2384
           → 0xd84b1f78 (host 0xd84af364) → memb(session+0x89a8)=7  @0xd84b2124               [FACT]
```
(All intermediate edges are byte-exact J2_call/J2_jump immediates verified over the full 36 MB. The single indirect hop is the dispatcher `callr r5` at `0xd8272d50`, whose two possible targets `0xd826d3e4`/`0xd8292298` are loaded as PC-relative immediates at `0xd8272d40`/`0xd8272d48` — **FACT**.)

**Which of (a)–(d):** **(a) a DIAG/FTM command handler**, reachable from the FTM dispatch tables (ftm_cmd `@0xc37bc828[0x27]` → RFTEST jump-table `@0xc37c649c`).

---

## 3. THE EXACT COMMAND & WIRE LAYOUT (FACT for framing; command_id absolute = INFERENCE)

- **ftm_cmd_id = `0x27` (FTM_LTE)** at wire bytes `[2..3]` (LE `27 00`). `@0xc37bc828[0x27] = 0xd814e534`. **FACT.**
- **sub_command = `0x10xx`** at wire bytes `[4..5]` (LE). The RFTEST dispatcher `0xd82714b4` requires **high byte `0x10`** and **low byte ≤ 0x17**; low byte indexes jump-table `@0xc37c649c`. The 24 slots (FACT, read from b21):
  ```
  0x1000→0xd8271630   0x1001→0xd827176c   0x1002→0xd8271548(→0xd86fd0f4)  0x1003→0xd8271558
  0x1004→0xd8271568   0x1005→0xd8271578   0x1006→0xd827170c   0x1007→0xd82715b0
  0x1008→0xd8271588   0x1009→0xd8271598   0x100a→0xd82715a4   0x100b→0xd82715f8
  0x100c→0xd8271604   0x100d→0xd8271610   0x100e→0xd82715bc   0x100f→0xd82715c8
  0x1010→0xd8271718   0x1011→0xd8271724   0x1012→0xd8271730   0x1013→0xd82715d4
  0x1014→0xd82715e0   0x1015→0xd82715ec   0x1016→0xd827161c   0x1017→0xd8271628
  ```
  Verified handler for `0x1002` = `0xd86fd0f4` (trampoline `0xd8271548: r2=##0xd86fd0f4`). Slot 2 (`0x1002`) is the RX_MEASURE/IQ_CAPTURE‑class handler that funnels to dispatcher `0xd8272cd8`. **FACT.**
- **Critical payload byte: offset `+0x0f` in the flattened request = the "mode/type" field.** `= 0` selects the **config‑apply branch (`0xd8292298`)** that reaches the `0x89a8` writers. (`byte0x0f != 0` goes to the RX capture/measure path `0xd826d3e4` instead.) Bytes `+0x10`/`+0x11` are the mode sub‑parameter (16‑bit). Byte `+0x0a` = the RFTEST cmd_id read by the handler. **FACT.**
- **Suggested probe frame** (short FTM framing, `ftm_cmd @wire[2]`):
  ```
  4B 0B  27 00  02 10  … payload …           ; FTM_LTE, RFTEST sub 0x1002
  with flattened request byte +0x0f = 0x00   ; mode/type = 0  → config-apply branch
       (+0x10/+0x11 = mode sub-param; +0x0a = cmd_id)
  ```
  **Which exact `0x10xx` slot / cmd_id you want depends on the tech/operation** (RX_TUNE / RADIO_CONFIG‑class). The 0x1002 slot is confirmed to reach the dispatcher; other `0x10xx` slots that also route through `0xd8272cd8`/`0xd86fdxxx` handlers behave identically w.r.t. the `byte0x0f` branch. **FACT (0x1002) + INFERENCE (sibling slots).**

> **Important caveat (why this is not a "pure" one‑shot):** reaching the branch is necessary but the RF driver only writes **7** (rather than **2** or **0**) when the *internal selectors* resolve to cal: `0xd8deabf0(idx) ∉ {9,0xa}` (§1.1) and/or `0xd80d1750()!=0` with FTM phase byte `0xcb8dbeb0+0x18 == 3` (§1.2). Those require the modem to already be in FTM/cal service with the carrier’s RF config in the cal class. See §5.

---

## 4. WHAT THE SELECTORS DEPEND ON — command param vs internal state (FACT)

| Selector | Where | Depends on | Verdict |
|---|---|---|---|
| `r20 == 3` @`0xd8d3ee1c`/`0xd8d3ee14` | arg #4 of `0xd8d3ed08` | Passed by caller; the value **3** is set inside `0xd8d9ed9c` (`r18=#3` @`0xd8d9eeec`) **iff `0xd8deabf0(idx)` ≠ 9 and ≠ 0xa**. `0xd8deabf0` = `memub(idx*0x178 + ≈0xcb9eb074)` — **internal per‑carrier RF‑config byte** (RW data). | **INTERNAL RF state** (band/mode class of the active carrier). *Not* a command byte. You cannot "pass 3"; you make the carrier’s config land in the class whose byte ∉ {9,0xa}. **FACT.** |
| `0xd80d1750()` != 0 → `0x89a8=7` @`0xd84b2124` | `0xd84b1f78` | `0xd80d1750` checks global **phase byte `memb(0xcb8dbeb0+0x18)==3`**, bounds‑checks the request, then `callr`s a per‑request handler; return depends on phase + request. It is a **shared FTM request validator** (called from many `0xd815xxxx` handlers). | **INTERNAL FTM phase state** (service must be in phase 3), plus request validity. **FACT.** |

**Neither selector is a direct DIAG TLV/parameter.** Both read RW driver/service state. The **command byte `+0x0f` = 0** only chooses *which branch runs*; whether that branch stores 7 (vs 2/0) is decided by the internal state above. **FACT.**

---

## 5. CROSS‑CHECK WITH THE CONSUMER (`0xd8247540`/`0xd8247b24`) AND THE ENTER PATH (`0xd81dfecc`) — one command or two‑step? (FACT)

- **`session+0x89a8` is a WRITE target only in the RF‑driver config path traced above** (RFTEST‑0x10xx, byte0x0f=0). **FACT.**
- **TECH_ENTER only READS it.** Commit `0xd81dfecc`: `r3 = memb(session+##0x89a8)` @`0xd81dff20`, `cmp.eq(r3,#7)` @`0xd81dff30` (branch, no store). Enter‑writer `0xd81e5cec`: reads `session+0xc` (gate `!=2 skip` @`0xd81e5d20`) and `session+0x89a8` @`0xd81e5d5c` (post‑check). The per‑tech callback `0xd8247540` (record+4, entry `0xd82473e4`, worker `0xd8247574`; consumer branches `0xd8247b24`, `0xd826c21c`) also **reads** `0x89a8` (`==7`, `==5`) to select its invocation form — it does not write it. **FACT** (corroborated by `tech_enter_callback.md`, `scenario_field.md`).
- **Therefore it is a TWO‑STEP sequence, not one command:**
  1. **Arm:** RFTEST‑0x10xx config command with mode byte `+0x0f = 0` → dispatcher `0xd8272cd8` → `0xd8292298`/`0xd8291504` → RF driver runs `0xd8d3ed08`/`0xd84b1f78` and, when the internal selectors resolve to cal, **writes `session+0x89a8 = 7`** (and `= 2` on the non‑cal branch via `0xd8e76adc`).
  2. **Enter:** `TECH_ENTER` (`ftm_cmd 0x27`, RFDEBUG sub `0x000D`, TECH=1/LTE) → commit `0xd81dfecc` **reads `0x89a8 == 7`** and opens the RF‑cal branch of the LTE per‑tech callback.
  **FACT.**
- There is **no single command that both writes `0x89a8=7` and performs the enter**; the writer and the reader are two different FTM sub‑commands in the same FTM_LTE (0x27) space. **FACT.**

---

## 6. FACT / INFERENCE / UNKNOWN (with VAs)

**FACT**
- Writer #1 `0xd8d3ee28 memb(r0+##0x89a8)=7`, gated `cmp.eq(r20,#3)` @`0xd8d3ee14`; enclosing fn `0xd8d3ed08` (prologue `allocframe(#0x48)`); `r20 = arg3` (`r21:20=combine(r2,r3)` @`0xd8d3ed28`). 10 direct callers (via `callers36.py`): `0xd8ce4804,0xd8ce4970,0xd8ceece0,0xd8ceed04,0xd8ceee94,0xd8cef264,0xd8d8c9a4,0xd8d91a14,0xd8db1e8c,0xd8dcf228`.
- mode `3` chosen in `0xd8d9ed9c` (`r18=#3` @`0xd8d9eeec`) iff `0xd8deabf0(idx) ∉ {9,0xa}`; `0xd8deabf0` = `memub(idx*0x178 + ≈0xcb9eb074)` (internal RW array).
- Writer #2 `0xd84b2124 memb(r16+##0x89a8)=r2`; `r2=#7` @`0xd84b211c` iff `memw(r29+0x1c)!=0`; slot filled from `0xd80d1750` return (`call` @`0xd84b2014`, store @`0xd84b2034`); enclosing fn `0xd84b1f78` (single direct caller `0xd84b0f24`, host loop `0xd84af364`).
- `0xd80d1750`: global `##0xcb8dbeb0`, `memb(+0x18)==3` phase check, bounds‑check `memh(+0x10)>memh(req+2)`, then `callr`; shared by many `0xd815xxxx` FTM handlers.
- Command dispatch: ftm_cmd `@0xc37bc828[0x27]=0xd814e534`; LTE range router `0xd8157ec4` (sub @wire[4..5], 0x1000–0x3FFF → `0xd8271484`); RFTEST dispatcher `0xd82714b4` (`high==0x10 & low<=0x17`, jump-table `@0xc37c649c`); slot 2 (`0x1002`) trampoline `0xd8271548 → r2=##0xd86fd0f4 → 0xd8271630 callr r2`.
- Canonical handler `0xd86fd0f4`: `r19=memub(req+0xf)` (mode), packs `{0xf,0x10,0x11}`, `r0=#3`, `call 0xd8272cd8`.
- Dispatcher `0xd8272cd8`: `r18 = 0xd82735cc(sxth(byte0x0f))`; `p0=(r18!=0)`; `callr r5` @`0xd8272d50` with `r5=0xd826d3e4` if `byte0x0f!=0`, else `r5=0xd8292298` (imm loads @`0xd8272d40`/`0xd8272d48`). `0xd82735cc`: `if r0>0x13 → 0x15 else r0&0xff`.
- `0xd8292298` ∈ fn `0xd8291504` (no direct callers → reached only via the dispatcher `callr`). Forward‑reach (direct calls) from `0xd8291504` to writer #1 fn `0xd8d3ed08` and writer #2 fn `0xd84b1f78` both confirmed (paths in §2).
- TECH_ENTER reads‑only: `0xd81dfecc` `memb(session+0x89a8)` @`0xd81dff20`, `cmp.eq(#7)` @`0xd81dff30`; enter‑writer `0xd81e5cec` gate `session+0xc!=2` @`0xd81e5d20`.

**INFERENCE (hard‑anchored)**
- The triggering command is the **FTM RFTEST 0x10xx (RX/RADIO‑config class) with mode byte `+0x0f == 0`** under ftm_cmd `0x27`. `0x1002` is byte‑exact; sibling `0x10xx` slots that route through `0xd8272cd8` behave identically on the `byte0x0f` branch.
- The **7‑vs‑2‑vs‑0** value is decided by internal RF/FTM state (carrier band‑mode class ∉ {9,0xa}; FTM phase `0xcb8dbeb0+0x18==3`), which requires the modem to be in FTM/cal service with the carrier RF config loaded. Sending the command in the wrong phase yields `0x89a8 = 2` or `0`, not `7`.

**UNKNOWN (runtime / RAM / out‑of‑image)**
- The exact `0x10xx` slot / RFTEST cmd_id (`req+0xa`) and full TLV/flattened layout for the specific RX/RADIO‑config op you want (the command_id→unpacker binding is runtime‑assigned; close it live with COMMAND_CAPABILITY → CMD_MASK, per `MASTER_MODEM_MAP.md §5`).
- Live values of the internal selectors: `memub(idx*0x178+≈0xcb9eb074)` (must be ∉{9,0xa}), `memb(0xcb8dbeb0+0x18)` (must be `3`), and live `session+0x89a8`/`session+0xc` — DIAG peek only.
- The exact operating‑mode/QMI step that puts the FTM service into phase 3 / cal class (candidate: QMI‑DMS `set_operating_mode`→FTM or factory‑test boot; see `cal_mode_trigger.md §5`). This chain does not itself perform that mode change — it assumes it.

---

## 7. REPRODUCE
```bash
cd /tmp/modemre
# Writer #1 (=7 when r20==3) and its arg mapping:
./dis36.sh 0xd8d3ed08 0x120           # r20 = arg3; cmp.eq(r20,#3) @d8d3ee14; store 7 @d8d3ee28
python3 callers36.py 0xd8d3ed08       # 10 direct callers (full-36MB scan)
./dis36.sh 0xd8d9ede0 0x30            # r18=#3 @d8d3eeec chosen by internal query
./dis36.sh 0xd8deabf0 0x20            # memub(idx*0x178 + ~0xcb9eb074) internal RF byte
# Writer #2 (=7 iff 0xd80d1750()!=0):
./dis36.sh 0xd84b1fe0 0x160           # call 0xd80d1750; stash; use; r2=#7; store @d84b2124
./dis36.sh 0xd80d1750 0x70            # phase byte 0xcb8dbeb0+0x18==3; callr dispatch
# Command dispatch tables:
python3 - <<'PY'
import struct;d=open('modem.b21','rb').read();B=0xc3553000
print('[0x27]',hex(struct.unpack('<I',d[0xc37bc828-B+0x27*4:0xc37bc828-B+0x27*4+4])[0]))
t=0xc37c649c-B
print('sub 0x1002 slot=', hex(struct.unpack('<I',d[t+2*4:t+2*4+4])[0]))
PY
./dis36.sh 0xd82714b4 0x80            # RFTEST dispatcher (jump-table @0xc37c649c)
./dis36.sh 0xd8271548 0x10            # trampoline sub 0x1002 -> r2=##0xd86fd0f4
./dis36.sh 0xd86fd0f4 0x80            # canonical handler: byte0x0f=mode -> 0xd8272cd8
./dis36.sh 0xd8272cd8 0x80            # branch on byte0x0f: !=0 -> 0xd826d3e4 ; ==0 -> 0xd8292298
# Reachability (corrected boundaries) from the byte0x0f==0 branch to both =7 writers:
python3 fwd2.py fwd 0xd8291504 0xd8d3ed08     # REACHED (path via 0xd8d9ed9c)
python3 fwd2.py fwd 0xd8291504 0xd84b1f78     # REACHED (path via 0xd84b2384)
# TECH_ENTER only READS 0x89a8:
./dis36.sh 0xd81dfecc 0x60            # memb(session+0x89a8); cmp.eq(#7)
```
