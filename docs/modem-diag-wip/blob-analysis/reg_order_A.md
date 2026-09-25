# reg_order_A — Registration order & numeric command_id of RFTEST commands

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Base:** `0xd8000000` (`/tmp/modemre/clade_dec_full.bin`, tool `dis.sh <va> <len>`).
**Rodata/data:** `rd.py <va> <n>` (segment map inside rd.py; b21 @0xc3553000, b23 @0xc8b6a000,
b26 @0xcc000000).
**Full code disasm:** `/tmp/_full_dis.txt` (covers 0xd8000000–0xd89ffff4).
**Cross-refs:** `command_id_map_B.md`, `slot_correlation.md`, `cmd_capability_map.md`,
`radio_config_unpack.md`.

**Legend:** **FACT** = read directly from disasm/bytes at a cited VA ·
**INFERENCE** = deduced with cited evidence · **UNKNOWN** = only resolvable on a live device (RAM).

---

## 0. TL;DR — DIRECT ANSWER

**There is NO static registration-order counter that assigns the numeric `command_id`
(byte 10, 0..0x31) to each named RFTEST command.** The `command_id` is a **runtime-assigned
counter** written into the heap master table `@0xca79a850`. This was the working hypothesis in
the task brief, and it is **CONFIRMED** here by 5 independent static checks (§2). Consequently:

- **The exact numeric `command_id` of COMMAND_CAPABILITY / RADIO_CONFIG / IQ_CAPTURE / RX_MEASURE
  is UNKNOWN from static analysis alone.** It is only observable live (read COMMAND_CAPABILITY's
  CMD_MASK; §6).
- **The strongest static proxy for the registration order is the FTM-RFTEST source-declaration
  order, recovered byte-exact from the F3 (diag) format-string table `@0xc3555b80` (§3):**

  ```
  decl 0 : RADIO_CONFIG        (a.k.a. RADIO_CFG)   unpacker 0xd8183f30 (wrap 0xd8182ec0→0xd818327c)
  decl 1 : COMMAND_CAPABILITY                        unpacker 0xd81849ec
  decl 2 : RX_MEASURE                                unpacker 0xd8185b1c
  decl 3 : WAIT_TRIGGER
  decl 4 : MSIM_CFG
  decl 5 : TX_CONTROL
  decl 6 : IQ_CAPTURE                                dispatcher 0xd8189818
  decl 7 : TX_MEASURE
  decl 8 : IRAT_CONFIG
  ```

- **If (and only if) the runtime registration counter follows source-declaration order** — the
  usual case for the Qualcomm FTM RF-test stack, but **NOT verifiable in this image** — the
  numeric `command_id` would be:

  | command             | unpacker VA | command_id (INFERENCE, decl-order) |
  |---------------------|-------------|------------------------------------|
  | RADIO_CONFIG        | 0xd8183f30  | **0** |
  | COMMAND_CAPABILITY  | 0xd81849ec  | **1** |
  | RX_MEASURE          | 0xd8185b1c  | **2** |
  | IQ_CAPTURE          | 0xd8189818  | **6** |

  This is an **INFERENCE**, not a FACT. It is the single best static estimate; the registrar that
  would prove/refute it is not present as static data (§2, §4). Contrast with the task "Pista":
  COMMAND_CAPABILITY is NOT first — RADIO_CONFIG is declared first; CC is second.

---

## 1. THE TWO INDEX AXES (recap, FACT)

Do not confuse them:
- `sub_command` = wire bytes [4..5] (LE), `0x10xx` → static handler-wrapper jump-table
  `@0xc37c649c` (24 slots), dispatcher 0xd82714b4. **FACT.**
- `command_id` = wire **byte 10** (`memub(req+0xa)`, ≤0x31) → per-command struct
  `memw(memw(##0xca79a850)+id*4+0x34)`, resolver **0xd8272684**. **FACT.**

Resolver (FACT, `dis.sh 0xd8272684 0x20`):
```
p0=cmph.gtu(id,#0x31); r3=memw(##0xca79a850); r3=addasl(r3,id,#2); r3=memw(r3+#0x34); return r3
```

The command_id is what the client puts on the wire; the registration counter is what decides which
integer each command *answers to*.

---

## 2. WHY THERE IS NO STATIC REGISTRATION-ORDER COUNTER — 5 PROOFS (FACT)

### 2.1 The master table is alloc + memset + generic-defaults ONLY (no per-command binding)
- Sole writer of `memw(##0xca79a850)` = **0xd8272368** (`r0=malloc; r2=##0x954c; memw(##0xca79a850)=r0;
  memset0`). **FACT** (`dis.sh 0xd8272340 0x60`).
- Populate loop **0xd82723c0**: iterates `r16 = 0 → 0x32` (50 slots), per-slot allocates a 0x28-byte
  struct and fills **identical generic defaults**:
  `+0x1=0x15, +0x2(h)=0xffff, +0x4(h)=0x33, +0x8(w)=0xffff, +0xc(w)=0xffff, +0x1e(h)=0x7fff,
   +0x25=3, +0x26=3`, name/handler fields left 0. Loop end `cmp.eq(r16,#0x32)`. **FACT**
  (`dis.sh 0xd82723c0 0x1a0`, esp. 0xd82724f0 `cmp.eq(r16,#0x32)`).
  → No slot gets a command-specific unpacker/name here. There is no `counter++ ; slot[counter]=CMD`
  code path in the image.

### 2.2 The unpacker VAs never appear as aligned data pointers anywhere
Searched **every** loadable segment (b02..b29 via rd.py, plus clade_dec_full.bin, clade_all.bin,
clade_dec.bin) for LE words == unpacker VA:
```
0xd81849ec (COMMAND_CAPABILITY): 0 aligned hits
0xd8183f30 (RADIO_CONFIG unpk) : 0 aligned hits
0xd8182ec0 (RADIO_CONFIG wrap) : 0 aligned hits
0xd8189818 (IQ_CAPTURE disp)   : 0 aligned hits
0xd8185b1c (RX_MEASURE)        : 0 aligned hits   (1 UNALIGNED byte-coincidence @0xcc806f4e in the
                                                   compressed b26 blob — offset%4==2, not a pointer)
```
**FACT.** → There is no static `{command_id → unpacker}` or `{name → unpacker}` descriptor array.

### 2.3 The unpacker VAs are also absent as static immediates outside their own bodies
`grep ##0xd81849ec / ##0xd8189818 / ##0xd8183f30` in `/tmp/_full_dis.txt` → 0 hits outside each
function. RADIO_CONFIG's wrapper 0xd8182ec0 is the only one referenced at all, and only as an
`immext` **stored into a module-context object** (0xd8182e90: `memw(r16+0)=##0xd8182ec0`,
`memw(r16+4)=##0xd8182f40`), i.e. installed in a *module ctx* @0xca65d640, never into a
`command_id` slot by literal. **FACT** (`dis.sh 0xd8182e50 0xc0`; grep §7).

### 2.4 The RFTEST commands are NOT dispatched through the master table at all
The named unpackers are reached by `callr entry[0]` on **self-contained module state machines**,
keyed by a per-module context object, not by the `command_id` index:
- RADIO_CONFIG: ctx getter 0xd8182484 returns `##0xca65d640`; wrapper 0xd8182ec0 is a state machine
  (`r17==0 → jump 0xd818327c → …0xd81833d0: call 0xd8183f30`; `r17==1 → 0xd8183d54`). **FACT.**
- The master table `@0xca79a850` (0x28-byte structs) is a per-command **property/state database**
  written at *request time* by setters 0xd827263c / 0xd8272608 / 0xd8272600 (see §2.5), consumed by
  query handlers (0xd86fd328 etc.). It carries `+0x1,+0x2,+0x4,+0x8..` scalar attributes, **not**
  unpacker pointers.

### 2.5 The setters are called at REQUEST time with a runtime id, never from a central registrar
Callers of 0xd827263c (`struct+0x1=r1; struct+0x2(h)=r2`, id in r0) and 0xd8272608
(`struct+0x4(h)=r1`, id in r0):
```
0xd82893c8, 0xd828c604, 0xd828ca70          (request-time property setters; e.g. 0xd828ca70 takes
0xd86fd434, 0xd86fd550, 0xd86fd784           id=memuh(r29+0x48) / memub(r29+0x91) from the LIVE request)
```
None is a fixed sequence of `set(0,…); set(1,…); set(2,…)` with an incrementing constant. **FACT**
(`grep "call 0xd827263c\|call 0xd8272608" /tmp/_full_dis.txt`; `dis.sh 0xd828ca40 0xd0`,
`dis.sh 0xd86fd328 0x150`).

> **Conclusion:** the numeric `command_id ↔ command` map is assigned by a **runtime registration
> counter** and is not encoded anywhere in the static image. The registrar that increments it runs
> against the heap table at boot; its ordering is only observable live (COMMAND_CAPABILITY CMD_MASK,
> §6). Every static artifact (unpackers, field-tables, name-tables, wire layout, F3 declaration
> order) is fully pinned — the integer is not.

---

## 3. REGISTRATION-ORDER PROXY — F3 declaration order (FACT, byte-exact)

F3 (diag debug) format-string table `@0xc3555b80`, 16 B/entry `{msg_id:u32, argc:u32, fmt:ptr,
file:ptr}`, ssid 0x17 = RF. Decoded in table order (the C-source declaration order of the FTM
RFTEST command files). **FACT** (`rd.py 0xc3555b80 …`, strings dereferenced):

| decl | command            | UNPACK fmt   | REPACK fmt   | unpacker (FACT) |
|------|--------------------|--------------|--------------|-----------------|
| 0    | RADIO_CONFIG/CFG   | (LTE 0xc37cc190) | 0xc37c0374 | **0xd8183f30** (wrap 0xd8182ec0→0xd818327c) |
| 1    | COMMAND_CAPABILITY | 0xc37c03cc   | 0xc37c042c   | **0xd81849ec** |
| 2    | RX_MEASURE         | 0xc37c0590   | 0xc37c05e0   | **0xd8185b1c** |
| 3    | WAIT_TRIGGER       | 0xc37c0664   | 0xc37c06bd   | — |
| 4    | MSIM_CFG           | 0xc37c073c   | —            | — |
| 5    | TX_CONTROL         | 0xc37c07d8   | —            | — |
| 6    | IQ_CAPTURE         | 0xc37c08f4   | 0xc37c0949   | **0xd8189818** (dispatcher, field-tbl @0xc37c0828) |
| 7    | TX_MEASURE         | 0xc37c0dc4   | 0xc37c0e14   | — |
| 8    | IRAT_CONFIG        | 0xc37ca3f8(IRAT) | —        | — |

Exact strings (FACT):
```
'[FTM.RFTEST][RADIO_CFG][REPACK]: [%3d][ %12s ][ %lld ][ 0x%8x ]'
'[FTM.RFTEST][COMMAND_CAPABILITY][UNPACK]:[%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][RX_MEASURE][UNPACK]:[%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][WAIT_TRIGGER][UNPACK]:[%2d][%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][MSIM_CFG][UNPACK]:[%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][TX_CONTROL][UNPACK]:[%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][IQ_CAPTURE][UNPACK]:[%2d][%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][TX_MEASURE][UNPACK]:[%3d][ %12s ][ %12d ]'
'[FTM.RFTEST][IRAT_CONFIG]:[%3d][ %12s ][ %12d ]'
```

**Note on the `[%3d]` / `[%2d][%3d]` first args:** these are **runtime** values (field index /
carrier index / logged id), not a hard-coded command_id constant. Verified inside COMMAND_CAPABILITY
(0xd8184fe0: `r3` is a runtime field index feeding jump-tbl @0xc37c03b4, then logged). So the F3
lines do NOT bake the command_id either.

> **Caveat (why decl-order ≠ proven counter):** the F3 table is the *logging* declaration order.
> The runtime registrar walks a boot-time init list; nothing in the static image proves that list is
> in the same order as the F3 table. Empirically the two coincide in most Qualcomm FTM builds, hence
> the INFERENCE — but it is not a FACT for this image.

---

## 4. THE INIT/REGISTRATION FUNCTION & THE COUNTER — what was found (FACT) and not (UNKNOWN)

### 4.1 Master-table lifecycle (FACT)
- **Allocator/creator:** **0xd8272368** (inside 0xd8272344) — `malloc(0x954c)` → `##0xca79a850`,
  then `memset 0`. **FACT.**
- **Generic populator (50 slots):** **0xd82723c0** — the loop with counter **r16 = 0 → 0x32**
  (this *is* an incrementing counter, but it only writes **identical defaults**, not per-command
  bindings; §2.1). Called from 0xd82723a4, 0xd8272574, 0xd82727b4, 0xd8272a90. **FACT.**
- **Per-command property setters (request-time, id in r0):** 0xd827263c, 0xd8272608, 0xd8272600.
  **FACT.**

### 4.2 The module init that DOES register RADIO_CONFIG's ops (FACT) — but not into a command_id slot
- **0xd8182640** (RADIO_CONFIG module init) — builds module ctx @0xca65d640, installs field
  sub-handlers (BAND/CHANNEL/…). Installs unpacker/repack via 0xd8182e50 into the *ctx*
  (`entry[0]=0xd8182ec0`, `entry[4]=0xd8182f40`), NOT into `@0xca79a850+id*4+0x34`. **FACT.**
- Reached via thunk **0xd84aa03c** = `{ call 0xd8182640; call 0xd81793b0; call 0xd86c3ee8;
  jump 0xd816cf60 }` — a sequential init chain. 0xd84aa03c is itself invoked from a boot init table
  that is **not present as static data** in the extracted segments (its VA has 0 aligned pointer
  hits, §2.2 method) → the ordering authority is not statically recoverable. **FACT (chain) /
  UNKNOWN (the table that orders all command inits).**

### 4.3 The registration-order counter itself
- **NOT FOUND as a static construct.** No global/local `counter` is incremented per named-command
  registration with a per-command binding. The only incrementing loop over 0..0x31 is the generic
  default-filler 0xd82723c0 (§2.1), which does not bind names/unpackers. **FACT (absence within the
  0xd8000000–0xd89ffff4 window and all rodata/data segments) / UNKNOWN (runtime).**

---

## 5. ORDERED command_id → command (BEST INFERENCE) + the four targets

Because the counter is runtime-assigned (§2, §4), the table below is the **best static inference**,
built on the F3 declaration order (§3) under the assumption `counter order == declaration order`
(unproven for this image; see §3 caveat):

| command_id (INFERENCE) | command            | unpacker VA (FACT) | field-tbl (FACT) | evidence class |
|------------------------|--------------------|--------------------|------------------|----------------|
| **0**                  | RADIO_CONFIG       | 0xd8183f30         | @0xc37c0290      | decl 0; tune (BAND/CHANNEL/BW); faults w/ tlv=0 |
| **1**                  | COMMAND_CAPABILITY | 0xd81849ec         | @0xc37c03b4      | decl 1; query, empty-request-safe, emits CMD_MASK |
| **2**                  | RX_MEASURE         | 0xd8185b1c         | @0xc37c0478      | decl 2; measurement query |
| 3                      | WAIT_TRIGGER       | —                  | —                | decl 3 |
| 4                      | MSIM_CFG           | —                  | —                | decl 4 |
| 5                      | TX_CONTROL         | —                  | —                | decl 5 |
| **6**                  | IQ_CAPTURE         | 0xd8189818         | @0xc37c0828      | decl 6; sample capture (measure/capture path) |
| 7                      | TX_MEASURE         | —                  | —                | decl 7 |
| 8                      | IRAT_CONFIG        | —                  | —                | decl 8 |

Each field-table is referenced **only** inside its own unpacker (0 static cross-refs; grep §7),
which re-confirms per-command runtime dispatch — consistent with §2. Unpacker VAs and field-table
VAs are **FACT**; the integer command_id column is **INFERENCE**.

### The four targets — exact answer form
- **COMMAND_CAPABILITY = 0xd81849ec** — command_id = **1** *(INFERENCE, decl-order)* · **UNKNOWN
  (exact) statically**; close live via §6 (its reply carries CMD_MASK).
- **RADIO_CONFIG = 0xd8183f30** — command_id = **0** *(INFERENCE, decl-order)* · **UNKNOWN (exact)**.
- **RX_MEASURE = 0xd8185b1c** — command_id = **2** *(INFERENCE, decl-order)* · **UNKNOWN (exact)**.
- **IQ_CAPTURE = 0xd8189818** — command_id = **6** *(INFERENCE, decl-order)* · **UNKNOWN (exact)**.

---

## 6. HOW TO CLOSE THE EXACT NUMBERS LIVE (mechanism = FACT)

COMMAND_CAPABILITY returns a **CMD_MASK** bitmap (grp-22 field 3) where **bit N = 1 ⇒ command_id N
is registered**. Iterating set bits and re-querying `QUERY_COMMAND=N` yields each command's
PROPERTY_MASK; cross-ref names via F3 (§3). This is the only way to pin the integers.

Steps (details/packets in `command_id_map_B.md` §6 and `slot_correlation.md` §5):
```
STEP 0  TECH_ENTER LTE  (clears tech-state gate 0xd81e5d60 → status 0x14 otherwise)     FACT
STEP 1  COMMAND_CAPABILITY with QUERY_COMMAND(field 1)=0xFFFFFFFF  →  read CMD_MASK(field 3)
STEP 2  for each set bit N: QUERY_COMMAND=N, read PROPERTY_MASK 0_63..192_255 (fields 4..7)
STEP 3  match returned name/properties to F3 names (§3) → command_id N ↔ command
```
CC field IDs (grp-22 name-table @0xc906ce18, byte-exact): QUERY_COMMAND=1, QUERY_PROPERTY=2,
CMD_MASK=3, PROPERTY_MASK 4..7. **FACT.**

---

## 7. FACT / INFERENCE / UNKNOWN — with VAs

**FACT**
- Master table: creator 0xd8272368 (`malloc 0x954c`→`##0xca79a850`, memset0); generic populator
  0xd82723c0 (counter r16=0→0x32, identical defaults, no per-command binding).
- Resolver 0xd8272684 → `memw(memw(##0xca79a850)+id*4+0x34)`.
- Property setters (request-time, id in r0): 0xd827263c (+0x1,+0x2), 0xd8272608 (+0x4), 0xd8272600.
  Callers (all request-time): 0xd82893c8, 0xd828c604, 0xd828ca70, 0xd86fd434, 0xd86fd550, 0xd86fd784.
- Unpackers (each field-tbl referenced only in its own body):
  RADIO_CONFIG 0xd8183f30 (wrap 0xd8182ec0→0xd818327c; module init 0xd8182640; ctx @0xca65d640;
  installs ops via 0xd8182e50 as ctx entry[0]/[4], NOT into a command_id slot);
  COMMAND_CAPABILITY 0xd81849ec (field-tbl @0xc37c03b4, names @0xc906ce18);
  RX_MEASURE 0xd8185b1c (field-tbl @0xc37c0478);
  IQ_CAPTURE 0xd8189818 (dispatcher, field-tbl @0xc37c0828).
- Unpacker VAs: 0 aligned data-pointer hits in all segments; 0 static immediates outside own bodies.
- F3 declaration order @0xc3555b80 (§3), decoded byte-exact.
- Init chain thunk 0xd84aa03c = {call 0xd8182640; call 0xd81793b0; call 0xd86c3ee8; jump 0xd816cf60}.

**INFERENCE**
- Registration-counter order == F3 source-declaration order (usual for Qualcomm FTM RFTEST, unproven
  here). Under it: command_id RADIO_CONFIG=0, COMMAND_CAPABILITY=1, RX_MEASURE=2, IQ_CAPTURE=6
  (§0, §5). The task "Pista" (CAPABILITY first) does NOT hold: RADIO_CONFIG is declared first.

**UNKNOWN (live only)**
- The exact integer command_id (byte 10) of each named command — assigned by a runtime registration
  counter; the master table @0xca79a850 is populated with generic defaults at boot and per-command
  bindings live in module ctxs; unpacker VAs never appear as static pointers. Close with §6.
- The boot init table that orders all command-module inits (its pointer is not static data).
- The concrete CMD_MASK value and the command_id ↔ sub_command(0x10xx) map (runtime descriptor).

---

## 8. REPRODUCE
```
dis.sh 0xd8272340 0x60        # master table creator 0xd8272368 (malloc 0x954c, memset0)     FACT §2.1
dis.sh 0xd82723c0 0x1a0       # generic populator: counter r16=0->0x32, defaults only         FACT §2.1
dis.sh 0xd8272684 0x20        # resolver command_id -> struct                                  FACT §1
dis.sh 0xd8182640 0x120       # RADIO_CONFIG module init (ctx @0xca65d640)                     FACT §4.2
dis.sh 0xd8182e50 0xc0        # installs unpacker 0xd8182ec0 / repack 0xd8182f40 as ctx entries FACT §2.3
dis.sh 0xd84aa03c 0x20        # init chain thunk (RADIO_CONFIG init first in chain)            FACT §4.2
dis.sh 0xd828ca40 0xd0        # request-time property setter (id from live request)            FACT §2.5
dis.sh 0xd86fd328 0x150       # query handler using resolver+setters (not a registrar)         FACT §2.5
rd.py  0xc3555b80 0x800       # F3 table -> declaration order (§3)                             FACT §3
rd.py  0xc906ce18 160         # grp-22 CC field names (QUERY_COMMAND=1, CMD_MASK=3)            FACT §6
grep -n "call 0xd827263c\|call 0xd8272608" /tmp/_full_dis.txt   # all request-time             FACT §2.5
grep -n "0xd8182ec0\|0xd818327c\|0xd8183f30" /tmp/_full_dis.txt # RADIO_CONFIG refs (§2.3)      FACT
# pointer-search (0 aligned hits for every unpacker VA across all segments):
python3 - <<'PY'
import struct,importlib.util
s=importlib.util.spec_from_file_location('rd','/tmp/modemre/rd.py'); rd=importlib.util.module_from_spec(s)
try: s.loader.exec_module(rd)
except SystemExit: pass
for va in (0xd81849ec,0xd8183f30,0xd8182ec0,0xd8189818,0xd8185b1c):
  hits=[]
  for base,sz,f in rd.SEGS:
    d=open('/tmp/modemre/'+f,'rb').read(); p=struct.pack('<I',va); i=0
    while True:
      i=d.find(p,i)
      if i<0: break
      if (base+i)%4==0: hits.append(hex(base+i))
      i+=1
  print(hex(va),'aligned hits:',hits)
PY
```
