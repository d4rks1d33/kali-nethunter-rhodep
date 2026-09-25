# canonical_wrapper — per-carrier deref (0xca79c494) reachability of the 24 RFTEST 0x10xx wrappers,
# the RADIO_CONFIG carrier-apply path (setter 0xd8279264), and the query-pure wrapper

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Code image:** `/tmp/modemre/clade_dec_full.bin` (VA base 0xd8000000). Disasm: `dis.sh <va> <len>`
(llvm-objdump hexagon v66). Rodata: `rd.py`. Stub table `@0xc37c649c` (24), dispatcher `0xd82714b4`.
**Cross-refs:** `base_and_safe_B.md`, `gate_resolution.md`, `reg_order_B.md`, `command_id_map_B.md`.

**Legend:** **FACT** = read directly from disasm/bytes at a cited VA · **INFERENCE** = deduced with
hard evidence · **UNKNOWN** = only resolvable on a live device (RAM/runtime).

**Method (sound static reachability, no false edges):**
- Correct Hexagon call/jump decode (`find_refs.py`: J2_call op7=0b0101101, J2_jump op7=0b0101100).
- Function boundaries built from prologue detection (`build_fns.py`, 7155 prologues), each function
  scanned over its FULL extent [start, next_start) so early-return-then-continue bodies are complete
  (`reach.py`). Return trampolines (`0xd8829698/88`, `0xc0913a00..bff`) are treated as epilogues, NOT
  call edges (this was the key to avoiding the "envelope reaches everything" false positive).
- Indirect `callr` resolved explicitly where the target register immediate is statically known
  (`0xd8272cd8`: r5 ∈ {`0xd826d3e4`,`0xd8292298`}). Remaining callr are runtime vtable dispatch and
  were bounded by intersecting each wrapper's reachable set with the COMPLETE set of 49 functions
  that call the getter (`find_refs 0xd827923c`) — an empty intersection proves getter-unreachability.

---

## 0. THE TWO ACCESSORS OF 0xca79c494 — EXACT, AND THEIR ONLY REFERENCES (FACT)

There are exactly **two** inline sites that touch `0xca79c494` in the whole 10 MB image (verified by
`scan_ptr_access.py`, every other byte-pattern match is a false positive to a *different* pointer,
disassembled and rejected):

**GETTER `0xd827923c`** (LOAD + NULL-check, returns the active-carrier ctx pointer):
```
d827923c: immext(#0xca79c480)
d8279240: r0 = memw(##0xca79c494)              ; LOAD active-carrier ctx
d8279244: if (!cmp.eq(r0,#0x0)) jump 0xd8279260 (return r0)
d8279248: call 0xd80d8998                      ; NULL -> log error
d8279258: r0 = memw(##0xca79c494)              ; re-load (still NULL) and return NULL anyway
d8279260: jumpr r31
```
It has **49 distinct calling functions** (all in the exec cluster 0xd826xxxx–0xd829xxxx). It is called
by `call` (op 0x5a…), never by data pointer. **FACT.**

**SETTER `0xd8279264`** (STORE — the ONLY writer of 0xca79c494):
```
d8279264: { jumpr r31 ; immext(#0xca79c480) ; memw(##0xca79c494) = r0 }   ; leaf, store-then-return
```
Static references to `0xd8279264`: **exactly ONE `call`, from `0xd8273b40`** (`find_refs.py`). **FACT.**
`0xd8273b40` is inside the **carrier-apply function `0xd8273b2c`**:
```
d8273b2c: call 0xd814e6ac ; allocframe(#0x30)    ; prologue
d8273b34: call 0xd81bd7ec                         ; compute carrier ctx -> r0..r3
d8273b40: call 0xd8279264                         ; STORE ctx into 0xca79c494  <<<
```
`0xd8273b2c` has ONE static caller: `0xd81bd2a8`, inside function **`0xd81bd01c`** (FTM-LTE module,
gp-relative state). `0xd81bd01c` has **NO static call/jump refs and NO data-pointer refs** → it is a
**runtime-registered callback** (FTM-LTE carrier-apply, `callr` from a RAM function-pointer table).
**FACT (0 static refs) + INFERENCE (FTM-LTE callback).**

⇒ The write to `0xca79c494` happens **only** through the FTM-LTE carrier-apply callback `0xd81bd01c`
→ `0xd8273b2c` → `0xd8279264`, which is triggered downstream of a **carrier configuration** (RADIO_
CONFIG applying BAND+EARFCN). It is **NOT reachable by any static call from the 24 DIAG wrappers**;
none of them can set the pointer directly. **FACT.**

---

## 1. Q1 — PER-WRAPPER BFS: DOES THE PATH DEREF 0xca79c494? (FACT)

Sound reachability of all 24 handlers (`reach.py`). "getter" = reaches the deref `0xd827923c`
(load of `0xca79c494`); "resolver" = reaches `0xd8272684`; "setter" = reaches the store `0xd8279264`;
"region" = touches `0xca6e3228`.

| sub    | handler     | resolver | **derefs 0xca79c494 (getter)** | setter | region | verdict |
|--------|-------------|:--------:|:------------------------------:|:------:|:------:|---------|
| 0x1000 | 0xd86fcf84  | no  | **NO**  | no  | no  | bail (cmp.eq r2,0x2a) → status err. SAFE, no-op |
| 0x1001 | 0xd827176c  | no  | **NO**  | no  | no  | error handler → 0x14. SAFE, no-op |
| 0x1002 | 0xd86fd0f4  | yes | **YES** | no  | yes | **CRASH**: 0xd8272cd8 →(callr)→ 0xd826d3e4 → 0xd827923c |
| 0x1003 | 0xd86fd230  | yes | **YES** | no  | yes | **CRASH**: 0xd826d67c → 0xd827923c |
| 0x1004 | 0xd86fd328  | yes | **YES** | no  | yes | **CRASH**: 0xd826d7b0 → 0xd827923c |
| 0x1005 | 0xd86fd474  | yes | **YES** | no  | yes | **CRASH**: 0xd826db54 → 0xd827923c |
| 0x1006 | 0xd8271c94  | no  | **NO**  | no  | no  | **SAFE**: small state-setter (byte0xa slot≤2), no resolver, no carrier ctx |
| 0x1007 | 0xd86fdb88  | yes | **YES** | no  | yes | **CRASH**: 0xd826e090 → 0xd827923c |
| 0x1008 | 0xd86fd5c8  | yes | **YES** | **yes** | yes | **CRASH**: 0xd826e204 → 0xd827923c (also reaches setter, but derefs getter too) |
| 0x1009 | 0xd86fd80c  | yes | **YES** | no  | yes | **CRASH**: 0xd826e458 → 0xd827923c |
| 0x100a | 0xd86fda68  | yes | **YES** | no  | yes | **CRASH**: (tail) 0xd826e6bc → 0xd827923c |
| 0x100b | 0xd86fe120  | no  | **NO**  | no  | no  | **SAFE**: config-apply cluster (0xd82805f0/0xd8280608), table 0xca79a8e0, NOT 0xca79c494 |
| 0x100c | 0xd86fe1e4  | no  | **NO**  | no  | no  | **SAFE**: 0xd8264f08 config; no resolver, no getter |
| 0x100d | 0xd86fde80  | **yes** | **NO** | no  | no  | **SAFE + calls resolver**: reads cmd_id@0xa, calls 0xd8272684, then per-tech work (0xd828067c→0xd82798f8, 0xd829bf84). NO carrier deref |
| 0x100e | 0xd86fe0a4  | no  | **NO**  | no  | no  | **SAFE**: 0xd8263f98 |
| 0x100f | 0xd86fdf94  | no  | **NO**  | no  | no  | **SAFE**: bails early |
| 0x1010 | 0xd82722c4  | no  | **NO**  | no  | no  | **SAFE**: shared 0x30xx (0xd829c404) |
| 0x1011 | 0xd8271b64  | no  | **NO**  | no  | no  | **SAFE**: shared 0x30xx (0xd828054c) |
| 0x1012 | 0xd8271bcc  | no  | **NO**  | no  | no  | **SAFE**: shared 0x30xx (0xd82805f0) |
| 0x1013 | 0xd8271774  | no  | **NO**  | no  | no  | invalid slot → 0x14. SAFE, no-op |
| 0x1014 | 0xd86fe300  | no  | **NO**  | no  | no  | **SAFE**: 0xd8272bb0(parse)+0xd84b5184, getter-clean |
| 0x1015 | 0xd86fe558  | no  | **NO**  | no  | no  | **SAFE**: 0xd8272bb0+0xd81bf0e8, getter-clean |
| 0x1016 | 0xd86fe60c  | no  | **YES** | no  | yes | **CRASH**: 0xd826db54 → 0xd827923c |
| 0x1017 | 0xd86fe7e0  | no  | **NO**  | no  | no  | **SAFE**: 0xd8263f98 |

Crashers (deref NULL 0xca79c494): **0x1002, 0x1003, 0x1004, 0x1005, 0x1007, 0x1008, 0x1009, 0x100a,
0x1016** (9 wrappers). Safe (no deref): **0x1000, 0x1001, 0x1006, 0x100b, 0x100c, 0x100d, 0x100e,
0x100f, 0x1010, 0x1011, 0x1012, 0x1013, 0x1014, 0x1015, 0x1017** (15 wrappers). **FACT** (each getter
reach verified by an explicit call chain into `0xd827923c`; each "NO" verified by empty intersection of
the wrapper's reachable set with the 49 getter-calling functions — see §7).

> CORRECTION vs the crash list in the prompt/base_and_safe_B: the boundary-correct BFS shows
> **0x1014 and 0x1015 are getter-clean (SAFE)**, not crashers. Only these 9 wrappers actually reach the
> NULL deref. (0x1005/0x1007/0x1008/0x1009/0x100a were confirmed crashers here too, extending the
> prompt's 0x1002/0x1003/0x1004 set.)

### 1.1 Why the deref crashes with only TECH_ENTER (FACT)
Every crashing wrapper reaches an exec core (`0xd826d3e4`/`0xd826d7b0`/`0xd826d9b4`/`0xd826db54`/
`0xd826e090`/`0xd826e204`/`0xd826e458`/`0xd826e6bc`) that does `call 0xd827923c` and stores the
returned pointer as the active carrier context, e.g. in `0xd826d3e4`:
```
d826d40c: call 0xd827923c        ; r0 = memw(0xca79c494)  (NULL after TECH_ENTER only)
d826d414: r19 = r0               ; r19 = NULL carrier ctx
d826d42c: r2 = memw(##0xca6e3228); per-carrier region
d826d450: memw(r29+#0x4)=r19     ; spill NULL ctx as arg
d826d4e4: r3:2 = combine(r18,r19); pass NULL ctx to downstream deref -> SSR
```
The getter NULL-checks and logs but **returns NULL anyway** (`0xd8279258`); the caller does not
re-check and dereferences it. **FACT.**

---

## 2. Q2 — CANONICAL WRAPPER FOR RADIO_CONFIG (command_id 0)

**There is NO 0x10xx wrapper that reaches the setter `0xd8279264` without first deref-ing the getter.**
- Only **0x1008** reaches the setter statically, but its path `0xd86fd5c8 → 0xd826e204 → 0xd827923c`
  **derefs the getter first** → it crashes with a NULL pointer, so it cannot be used to *initialize*
  the pointer. **FACT.**
- The setter is reachable cleanly ONLY through the FTM-LTE runtime callback `0xd81bd01c → 0xd8273b2c
  → 0xd8279264` (§0), which is **not statically reachable from any DIAG wrapper**. **FACT.**
- The DIAG RADIO_CONFIG unpacker `0xd8183f30` itself does NOT statically reach the setter/getter
  (`reach.py 0xd8183f30`: all False) — it hands off to the FTM-LTE module via runtime `callr`, and
  that module's carrier-apply (`0xd81bd01c`) performs the store. **FACT + INFERENCE (FTM handoff).**

**Consequence (INFERENCE, strong):** RADIO_CONFIG is **not carried by the crashing action wrappers**.
The 24 0x10xx wrappers are hardcoded query/action handlers; the resolver `0xd8272684` result is used
only for response status, **not** to `callr` a per-command unpacker (verified: 0x1002 calls
`0xd8272cd8` which `callr`s a *hardcoded* `0xd826d3e4/0xd8292298`, ignoring the resolver struct;
0x100d calls the resolver and **discards** its return, doing its own per-tech work). So the
`command_id → unpacker (0xd8183f30/0xd81849ec)` dispatch used by RADIO_CONFIG/COMMAND_CAPABILITY is a
**different entry** than these action wrappers (the generic FTM command path via the resolver
descriptor's `callr`, populated in RAM). **FACT of the wrapper mechanics; the command-path binding is
UNKNOWN statically (RAM).**

**Practical answer for RADIO_CONFIG:** you cannot pick a 0x10xx sub_command that "runs RADIO_CONFIG
and sets 0xca79c494" from this table without a NULL deref. RADIO_CONFIG must be issued on the
**command_id-driven path** (command_id = 0 at wire byte 10) through a wrapper whose OWN body does not
deref the getter. Among all 24, the wrappers that (a) read command_id at byte 0xa AND (b) never deref
0xca79c494 are: **0x100d only** (it is the sole resolver-caller that is getter-clean). If the live
build routes command_id-0 (RADIO_CONFIG) through the resolver descriptor invoked on a getter-clean
wrapper, **0x100d is the safe carrier for command_id 0**. Whether 0x100d's post-resolver code actually
`callr`s the RADIO_CONFIG descriptor is **UNKNOWN statically** (it discards the resolver return in the
disassembled path). **INFERENCE + UNKNOWN.**

⇒ **Recommended RADIO_CONFIG sub_command = 0x100d, command_id (byte 10) = 0x00.** This is the ONLY
non-crashing wrapper that both validates and passes the command_id to the resolver. If in-vivo it does
not actually apply the carrier (pointer stays NULL), then the carrier is applied via the FTM path and
must be confirmed by re-reading `memw(0xca79c494)` after the command (see §4). **INFERENCE.**

---

## 3. Q3 — QUERY-PURE WRAPPER (for COMMAND_CAPABILITY, command_id 0x0b)

**Query-pure = calls the resolver `0xd8272684` but NEVER derefs `0xca79c494`.**
Exactly **ONE** wrapper qualifies: **sub_command 0x100d (`0xd86fde80`).** **FACT.**
```
d86fde9c: r17 = memub(r19+#0xa)      ; command_id = wire byte 10
d86fdea0: cmp.gtu(r17,#0x31)         ; bound id <= 0x31
d86fdec4: call 0xd8272684            ; RESOLVER (validates command_id)  <<<
d86fdecc: call 0xd82735e8            ; getter-clean
d86fded4: call 0xd828067c            ; -> 0xd82798f8/0xd8279a30 (per-tech, getter-clean)  FACT
d86fdee4: call 0xd829bf84            ; getter-clean
```
Reachable set of 0x100d ∩ {49 getter-calling functions} = **∅** (verified). It reaches the resolver,
never the deref. All other resolver-callers (0x1002–0x100a) deref the getter. **FACT.**

⇒ **COMMAND_CAPABILITY sub_command = 0x100d, command_id (byte 10) = 0x0b** (B=0 assumption from
reg_order_B.md; confirm CMD_MASK live). This runs the resolver safely with `0xca79c494` = NULL,
because 0x100d does not enter the per-carrier exec. **FACT (no deref) + INFERENCE (cmd_id 0x0b).**

> NOTE — this SUPERSEDES `base_and_safe_B.md §3.2`, which claimed "no purely-query wrapper exists" and
> recommended 0x1002 after establishing a carrier. That earlier conclusion missed 0x100d because it
> only disassembled 9 resolver-callers by hand and did not run a boundary-correct BFS. **0x100d is a
> resolver-caller with NO carrier deref**, so COMMAND_CAPABILITY can be queried on 0x100d **without**
> first establishing a carrier. (0x1002 remains valid *only after* a carrier is applied.)

---

## 4. Q4 — DOES ANY OTHER PATH SET 0xca79c494 (init / carrier alloc)?

- **Only `0xd8279264` writes `0xca79c494`** (§0, single store site in the image). No "init"/"prep"
  command inlines a second write. **FACT.**
- **`0xd8279264` is reached only via the FTM-LTE runtime callback `0xd81bd01c → 0xd8273b2c`** (§0),
  triggered by **carrier configuration** (RADIO_CONFIG applying BAND+EARFCN, whose unpacker `0xd8183f30`
  hands to the FTM-LTE module). **FACT (store site & call chain) + INFERENCE (RADIO_CONFIG trigger,
  consistent with gate_resolution.md §4.4).**
- **sub 0x1006 is NOT carrier-alloc.** `0xd8271c94` is a tiny state-setter: reads bytes 0xa..0xd,
  bounds byte0xa ≤ 2, calls `0xd8271b58`/`0xd829c008`, zeroes a small struct (`memb(r2+0xa..0xd)=0`).
  It never calls the resolver and never touches `0xca79c494`. Safe, but does **not** init the carrier
  pointer. **FACT.**
- **sub 0x100b/0x100c are config-apply, but on a different table.** They call `0xd82805f0/0xd8280608`
  → `0xd8279154` which indexes `0xca79a8e0` (per-command-id table, bound ≤0x31), **not** `0xca79c494`.
  Safe, but do not init the carrier pointer. **FACT.**

⇒ **No DIAG wrapper initializes `0xca79c494` directly.** It is initialized only as a *side effect* of
RADIO_CONFIG's carrier apply flowing into the FTM-LTE module (`0xd81bd01c`). To make any of the
crashing action wrappers (0x1002/0x1003/0x1004/0x1007/0x1008/0x1009/0x100a/0x1014/0x1015/0x1016)
safe, **a carrier must first be applied** (RADIO_CONFIG with BAND+EARFCN via the command_id path), so
that the FTM callback runs and `memw(0xca79c494) != 0`. **FACT + INFERENCE.**

---

## 5. FINAL SEQUENCE (byte-accurate)

Wire header (FTM/LTE): `4B 0B 27 00` then `[sub:LE @4..5] [num_tlv:LE @6..7]`, **command_id @ byte 10**.

```
STEP 1  TECH_ENTER (LTE)  [RFDEBUG sub 0x000D — from gate_resolution.md/enter_mode_path.md]
        4B 0B 27 00 0D 00 03 00 \
           01 00 04 00 00 00 00 00 \    ; SUB      = 0
           02 00 04 00 01 00 00 00 \    ; TECH     = 1 (LTE)
           03 00 04 00 00 00 00 00      ; SCENARIO = 0

STEP 2  COMMAND_CAPABILITY (query, SAFE with 0xca79c494 = NULL)  [sub 0x100D, command_id 0x0B]
        Query-pure wrapper 0x100d calls the resolver but never derefs the carrier ctx.
        4B 0B 27 00 0D 10 01 00  00 00 0B 00 00 00 00 00 00 00  01 00 04 00 FF FF FF FF
        |           |     |         |        (num_tlv=1, TLV field1=QUERY_COMMAND=0xFFFFFFFF)
        |           |     |         +-- byte 10 = command_id = 0x0B (COMMAND_CAPABILITY)
        |           |     +------------ byte 6..7 = num_tlv = 1
        |           +------------------ byte 4..5 = sub_command = 0x100D (LE: 0D 10)
        +------------------------------ 4B 0B 27 00 = DIAG FTM LTE header
        Response field3 = CMD_MASK: bit N set ⇒ command_id N registered.
        bit 0 ⇒ RADIO_CONFIG, bit 5 ⇒ IQ_CAPTURE, bit 0x0b ⇒ COMMAND_CAPABILITY (confirms B=0).
        (num_tlv=0 also OK to just probe: 4B 0B 27 00 0D 10 00 00 00 00 0B 00 00 00 00 00 00 00)

STEP 3  RADIO_CONFIG (applies carrier -> sets 0xca79c494 via FTM callback)  [sub 0x100D, command_id 0x00]
        Use the getter-clean wrapper 0x100d to carry command_id 0 to the resolver/unpacker path.
        4B 0B 27 00 0D 10 05 00  00 00 00 00 00 00 00 00 00 00 \
           01 00 04 00 00 00 00 00 \    ; RX_CARRIER = 1   val 0   (fix carrier index FIRST)
           19 00 04 00 01 00 00 00 \    ; TECH_MODE  = 25  val 1 (LTE)
           05 00 04 00 03 00 00 00 \    ; BAND       = 5   val 3 (B3)
           06 00 04 00 27 06 00 00 \    ; CHANNEL    = 6   EARFCN 1575
           07 00 04 00 20 4E 00 00      ; BANDWIDTH  = 7   20000 kHz
        byte 10 = command_id = 0x00 (RADIO_CONFIG). BAND+EARFCN present -> FTM carrier-apply runs
        -> 0xd81bd01c -> 0xd8273b2c -> 0xd8279264 writes 0xca79c494. (field_ids FACT @0xc37c0268;
        LTE values INFERENCE.) VERIFY LIVE: memw(0xca79c494) != 0 after this step.

STEP 4  IQ_CAPTURE / RX_MEASURE (now SAFE: 0xca79c494 populated)  [command_id 0x05 / 0x01]
        Once the pointer is non-NULL, the action wrappers no longer NULL-deref. Simplest action
        wrapper = 0x1002 (single resolver call + single exec):
        4B 0B 27 00 02 10 04 00  00 00 05 00 00 00 00 00 00 00 \    ; command_id 0x05 = IQ_CAPTURE
           0E 00 04 00 00 40 00 00 \    ; NUM_OF_SAMPLES = 14  16384
           10 00 04 00 00 C0 D4 01 \    ; SAMP_FREQ      = 16  30720000
           0F 00 04 00 00 00 00 00 \    ; IQ_DATA_FORMAT = 15  0
           0D 00 04 00 01 00 00 00      ; FETCH_IQ       = 13  1
        (field-tbl @0xc37c0828, iq_final_values.md. Without STEP 3 this crashes.)
```

**Ordering rationale:** STEP 2 (query) needs no carrier (0x100d never derefs 0xca79c494), so it can run
right after TECH_ENTER to resolve the live command_ids. STEP 3 (RADIO_CONFIG) applies the carrier and,
via the FTM callback, is the ONLY thing that sets 0xca79c494. STEP 4 (any crashing action wrapper) is
safe only after STEP 3.

---

## 6. FACT / INFERENCE / UNKNOWN (with VAs)

**FACT**
- Only store of 0xca79c494: `0xd827926c` inside setter `0xd8279264` (leaf). Only loads: `0xd8279240/
  0xd8279258` inside getter `0xd827923c`. All other byte-pattern matches are to different pointers
  (disassembled & rejected: 0xd0020694, 0xca645454, 0xca78dfd4, 0xca7b1814, 0xca65b414, 0xca789794,
  0xca7bff14). `scan_ptr_access.py`.
- Setter `0xd8279264`: exactly ONE static caller `0xd8273b40` (in carrier-apply `0xd8273b2c`).
- `0xd8273b2c` ← `0xd81bd2a8` (in `0xd81bd01c`); `0xd81bd01c` has 0 static refs (runtime callback).
- Getter `0xd827923c`: 49 distinct calling functions, all in exec cluster 0xd826xxxx–0xd829xxxx.
- Crashing wrappers reach the getter: 0x1002,0x1003,0x1004,0x1005,0x1007,0x1008,0x1009,0x100a,0x1014,
  0x1015,0x1016 (each with an explicit call chain into `0xd827923c`).
- Safe wrappers (getter-unreachable, verified by empty intersection with the 49 getter-callers):
  0x1000,0x1001,0x1006,0x100b,0x100c,0x100d,0x100e,0x100f,0x1010,0x1011,0x1012,0x1013,0x1017.
- 0x100d `0xd86fde80` reads command_id @ byte 0xa, calls resolver `0xd8272684`, then getter-clean
  `0xd82735e8`/`0xd828067c`(→`0xd82798f8`/`0xd8279a30`)/`0xd829bf84`. NO carrier deref.
- 0x1002 `0xd86fd0f4` calls resolver then `0xd8272cd8`, which `callr`s HARDCODED `0xd826d3e4`(type 3/5)
  or `0xd8292298` (else), both → getter. Resolver return used only for status.
- 0x1006 `0xd8271c94`: state-setter (byte0xa≤2, zeros struct), no resolver, no 0xca79c494.
- RADIO_CONFIG unpacker `0xd8183f30` and COMMAND_CAPABILITY unpacker `0xd81849ec` have 0 static
  callers → runtime `callr` from the resolver descriptor / FTM path.

**INFERENCE**
- B=0 ⇒ command_id RADIO_CONFIG=0x00, RX_MEASURE=0x01, IQ_CAPTURE=0x05, COMMAND_CAPABILITY=0x0b
  (reg_order_B.md; confirm via CMD_MASK).
- `0xca79c494` is set only as a side effect of RADIO_CONFIG's carrier apply through the FTM-LTE
  callback `0xd81bd01c`. No DIAG wrapper sets it directly.
- 0x100d is the safe carrier for both command_id 0x0b (query) and command_id 0x00 (RADIO_CONFIG),
  being the only getter-clean resolver-caller.

**UNKNOWN (live only)**
- Whether 0x100d's post-resolver code actually `callr`s the RADIO_CONFIG descriptor (it discards the
  resolver return in the static path) — i.e., whether command_id-0 on 0x100d truly applies the
  carrier. Verify by re-reading `memw(0xca79c494)` after STEP 3.
- Absolute base B and exact command_id integers (RAM tables `@0xca79a850`/`@0xca9ef490`). CMD_MASK.
- Targets of the 90 unresolved runtime `callr` sites in the config paths (RAM function pointers).

---

## 7. REPRODUCE
```
python3 scan_ptr_access.py                 # only store=0xd827926c, only loads=0xd8279240/58   §0
python3 find_refs.py 0xd8279264            # setter callers = [0xd8273b40] only               §0
python3 find_refs.py 0xd827923c            # 49-way getter caller set                         §0/§1
python3 find_refs.py 0xd8273b2c            # [0xd81bd2a8]                                      §0
python3 find_refs.py 0xd81bd01c            # [] -> runtime callback                           §0
dis.sh 0xd827923c 0x30                     # getter: load+NULLcheck, returns NULL anyway       §1
dis.sh 0xd8279264 0x10                     # setter: memw(0xca79c494)=r0 (leaf)                §0
dis.sh 0xd8273b2c 0x20                     # carrier-apply: compute ctx then call setter       §0
dis.sh 0xd826d3e4 0x60                     # exec core: call getter, r19=NULL, deref downstream §1.1
dis.sh 0xd8272cd8 0x90                     # callr r5 = 0xd826d3e4 / 0xd8292298 (hardcoded)     §2
dis.sh 0xd86fde80 0xa0                     # 0x100d: resolver + getter-clean per-tech (QUERY)   §3
dis.sh 0xd8271c94 0x80                     # 0x1006: state-setter, no resolver/carrier          §4
python3 build_fns.py                       # 7155 prologues -> _fns.pkl
python3 reach.py 0xd86fd0f4 -v             # 0x1002 reaches getter (CRASH)                      §1
python3 reach.py 0xd86fde80 -v             # 0x100d: resolver=True getter=False (QUERY-PURE)    §3
python3 reach.py 0xd8183f30                # RADIO_CONFIG unpacker: no static getter/setter     §2
```
