# reg_order_B — RF-test command registration order (slot index) → command → unpacker

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Base:** `0xd8000000` (`/tmp/modemre/clade_dec_full.bin`, tool `dis.sh <va> <len>`).
**Rodata:** b21 @0xc3553000, b23 @0xc8b6a000 (F3 msg tables). Full disasm: `/tmp/_full_dis.txt`.
**Cross-refs:** `command_id_map_B.md`, `slot_correlation.md`, `radio_config_unpack.md`, `cmd_capability_map.md`.

**Legend:** **FACT** = read from disasm/bytes at a cited VA · **INFERENCE** = deduced with evidence ·
**UNKNOWN** = only resolvable from a live device (RAM/runtime).

---

## 0. TL;DR — DIRECT ANSWERS

1. **The registration function is FOUND and the registration ORDER is STATICALLY RECOVERABLE.**
   The RF-test command module is registered by **`0xd8182640`** (the FIRST module init called by
   the master registrar **`0xd84aa03c`**). It installs every RF-test command's unpacker+repacker
   into a per-module command-context slot array (module ctx @`0xca65d640`, sub-ctx pointer at
   ctx+0x14). Each command occupies a fixed **12-byte (0xc) slot**; the **slot index = the command's
   ordinal within the module = the registration order**. **FACT** (0xd8182640 disasm §2).

2. **The four targets are IDENTIFIED by their F3 name strings at their exact registration slots**
   (all in module `0xd8182640`, all confirmed byte-exact via `[FTM.RFTEST][<NAME>]` in rodata):
   | slot | reg call     | installed unpacker VA | command             | evidence |
   |------|--------------|-----------------------|---------------------|----------|
   | **0**  | 0xd8182e50 | 0xd8182ec0 → 0xd8183f30 | **RADIO_CONFIG**    | F3 @0xc3555b70 "[FTM.RFTEST][RADIO_CONFIG][UNPACK]" **FACT** |
   | **1**  | 0xd818552c | 0xd8185654 (→0xd8185b1c) | **RX_MEASURE**      | F3 @0xc3555bc0 "[FTM.RFTEST][RX_MEASURE][REPACK]"; field-tbl 0xc37c0478 @0xd8185b1c **FACT** |
   | 2      | 0xd81883c0 | 0xd8188428              | TX_CONTROL          | F3 @0xc3555c10 **FACT** |
   | 3      | 0xd86c2324 | 0xd86c0fe4              | TRM_RRA             | F3 @0xc3561a10 **FACT** |
   | (4)    | —          | (not registered here)   | — (slot 0x3c empty in this module) | **FACT** (no `add(r2,#0x3c)`) |
   | **5**  | 0xd818933c | 0xd81893a4 (→0xd8189818) | **IQ_CAPTURE**      | F3 @0xc3555c30 "[FTM.RFTEST][IQ_CAPTURE][REPACK]"; field-tbl 0xc37c0828 @0xd8189818 **FACT** |
   | 6      | 0xd818ab08 | 0xd818ab6c              | TX_MEASURE          | F3 @0xc3555c70/c80 "[FTM.RFTEST][TX_MEASURE][REPACK]" **FACT** |
   | 7      | 0xd8187f80 | 0xd8187fe4              | MSIM_CFG            | F3 @0xc3555c00 **FACT** |
   | 8      | 0xd84afe9c | 0xd84aff68              | IRAT_CONFIG         | F3 @0xc3555e20 **FACT** |
   | 9      | 0xd8187098 | 0xd81870fc              | WAIT_TRIGGER        | F3 @0xc3555bd0 **FACT** |
   | 10     | 0xd84ad048 | 0xd84ad0b8              | tx_measure (RFDEBUG)| F3 @0xc3555e00 "[FTM.RFDEBUG][tx_measure][REPACK]" **FACT** |
   | **11** | 0xd8184980 | 0xd81849ec              | **COMMAND_CAPABILITY**| F3 @0xc3555b90 "[FTM.RFTEST][COMMAND_CAPABILITY][UNPACK]" **FACT** |

3. **command_id (wire byte 10, 0..0x31) = module_base + slot_index.**
   - The **module-internal slot index** (= registration order) is **FACT** (table above).
   - The **absolute integer command_id** = the module's base offset in the global 0..0x31 space +
     slot_index. The module→base mapping is a **runtime byte table @`0xca9ef490`** indexed by
     command_id (`memb(0xca9ef490+command_id)`; resolver 0xd84aa9e0). This byte table is populated
     at RUNTIME (no static immediate). Therefore the **absolute command_id integers are UNKNOWN
     statically** — but the *relative registration order within the RF-test module is FACT*. §4.

4. **RELATIVE registration order of the four targets (FACT, static):**
   `RADIO_CONFIG(slot0) < RX_MEASURE(slot1) < IQ_CAPTURE(slot5) < COMMAND_CAPABILITY(slot11)`.
   If the RF-test module base = B, then:
   **RADIO_CONFIG = B+0, RX_MEASURE = B+1, IQ_CAPTURE = B+5, COMMAND_CAPABILITY = B+11.**

---

## 1. THE INIT/REGISTRATION FUNCTION AND THE COUNTER

### 1.1 Master registrar chain (FACT)
```
0xd84aa03c  (master RF registrar):
   call 0xd8182640   ; MODULE 0 = RF-TEST commands (RADIO_CONFIG..COMMAND_CAPABILITY)  ← FIRST
   call 0xd81793b0   ; MODULE 1 (desc+0x10 = 0x13)
   call 0xd86c3ee8   ; MODULE 2 (desc+0x10 = 0x0b)
```
**FACT** (dis.sh 0xd84aa03c 0x14). Module 0 is registered first, so its commands get the lowest
command_ids in registration order (if the counter is global and starts at 0).

### 1.2 The module ctx and the per-command slot array (FACT)
`0xd8182640` allocates a module descriptor (0x18 B) at ctx @`0xca65d640` (word @0xca65d65c holds
the ptr), sets `memb(desc+0x10)=0x12` (the module/group id — **FACT** 0xd8182698), then allocs a
sub-ctx (0xd8..) into `desc+0x14` and registers each command into a **12-byte slot** at
`desc+0x14 → sub+0xc, +0x18, +0x24, …` :
```
d81826ac call 0xd8182e50 ; r0 = sub+0xc   → SLOT 0
d81826bc call 0xd818552c ; r0 = sub+0x18  → SLOT 1
d81826cc call 0xd81883c0 ; r0 = sub+0x24  → SLOT 2
d81826e0 call 0xd86c2324 ; r0 = sub+0x30  → SLOT 3
d81826f0 call 0xd818933c ; r0 = sub+0x48  → SLOT 5   (0x3c/SLOT 4 skipped)
d8182704 call 0xd8187f80 ; r0 = sub+0x60  → SLOT 7
d8182714 call 0xd818ab08 ; r0 = sub+0x54  → SLOT 6
d8182728 call 0xd84afe9c ; r0 = sub+0x6c  → SLOT 8
d8182738 call 0xd8187098 ; r0 = sub+0x78  → SLOT 9
d818274c call 0xd84ad048 ; r0 = sub+0x84  → SLOT 10
d818275c call 0xd8184980 ; r0 = sub+0x90  → SLOT 11
```
A second pass (0xd81827a0..) installs the REPACK handlers into the SAME slots. **FACT.**
slot_index = `(offset - 0xc) / 0xc`. **FACT.**

### 1.3 The COUNTER (command_id assignment)
- **The command_id is the INDEX into the master table @`0xca79a850`** (resolver 0xd8272684:
  `r3=memw(##0xca79a850); r3=addasl(r3,id,#2); r3=memw(r3+#0x34)`; bound `id ≤ 0x31`). **FACT.**
- The master table is alloc/memset by **0xd8272368** (`##0x954c`), defaults filled by **0xd82723c0**
  loop over 0..0x31 (50 slots, 0x28 B each, generic defaults only, no names/handlers). **FACT.**
- **The command_id → module/group binding is a runtime byte table @`0xca9ef490`** :
  `r24 = ##0xca9ef490; r2 = memb(r24 + command_id); if (r2 != group) …` (0xd84aa9e0:0xd84aaa44,
  and the earlier setup at 0xd84aa174 uses 0xca9ef4c0-0x30 = 0xca9ef490). **FACT.**
- **The "counter" is therefore the position at which each command's slot struct is written into
  @0xca79a850 during runtime registration.** The RELATIVE order is fixed by 0xd8182640 (slot index);
  the ABSOLUTE base offset into 0..0x31 is set at runtime via @0xca9ef490. **FACT/UNKNOWN split §4.**

---

## 2. HOW EACH TARGET WAS IDENTIFIED AT ITS SLOT (FACT)

Each per-command sub-init installs `memw(slot+0x0)=unpacker_VA`, `memw(slot+0x4)=repacker_VA`,
`memw(slot+0x8)=bufsize`. The unpacker (or its call-tree) emits an F3 diag string
`[FTM.RFTEST][<NAME>][UNPACK|REPACK]`. Resolving that string is a **byte-exact** command name.

### 2.1 RADIO_CONFIG — slot 0 (FACT)
```
reg fn   0xd8182e50 : memw(r16+0)=##0xd8182ec0 (unpack wrapper), memw(r16+4)=##0xd8182f48 (repack),
                      bufsz 0x174810
wrapper  0xd8182ec0 → 0xd818327c → 0xd8183f30 (real unpacker, field-tbl @0xc37c0290)
F3 name  0xd8183f30 refs F3 @0xc3555b70 → fmt @0xc3555b70[2] =
         "[FTM.RFTEST][RADIO_CONFIG][UNPACK]:[%3d][ %12s ][ %12d ]"  (file ftm_rf_test_radio_config.c)
```

### 2.2 RX_MEASURE — slot 1 (FACT)
```
reg fn   0xd818552c : memw(r16+0)=##0xd8185654 (unpack wrapper), memw(r16+4)=##0xd81865f4 (repack)
unpacker 0xd8185654 fn-extent contains the RX_MEASURE field dispatcher:
         0xd8185b1c: r9 = memw(r3<<2 + ##0xc37c0478); jumpr r9     (field-tbl @0xc37c0478, 1 ref)
F3 name  in-extent F3 @0xc3555bc0 = "[FTM.RFTEST][RX_MEASURE][REPACK]: …" (file ftm_rf_test_rx_measure.c)
```

### 2.3 IQ_CAPTURE — slot 5 (FACT)
```
reg fn   0xd818933c : memw(r16+0)=##0xd81893a4 (unpack wrapper), memw(r16+4)=##0xd8189d38 (repack)
unpacker 0xd81893a4 fn-extent contains the IQ_CAPTURE field dispatcher:
         0xd8189818: r2 = memw(r2<<2 + ##0xc37c0828); jumpr r2     (field-tbl @0xc37c0828, 1 ref)
F3 name  in-extent F3 @0xc3555c30/c40/c50 = "[FTM.RFTEST][IQ_CAPTURE][REPACK]…" (file ftm_rf_test_iq_capture.c)
```

### 2.4 COMMAND_CAPABILITY — slot 11 (FACT)
```
reg fn   0xd8184980 : memw(r16+0)=##0xd81849c0→0xd81849ec (unpack), memw(r16+4)=##0xd81851a4 (repack),
                      bufsz 0x2a0
unpacker 0xd81849ec (field-tbl @0xc37c03b4, name-tbl @0xc906ce18: QUERY_COMMAND=1, CMD_MASK=3)
F3 name  refs F3 @0xc3555b90 = "[FTM.RFTEST][COMMAND_CAPABILITY][UNPACK]:[%3d][ %12s ][ %12d ]"
         (file ftm_rf_test_command_capability.c)
```

---

## 3. FULL REGISTRATION ORDER (module 0 = RF-TEST) — FACT

| reg step | slot index | command name        | unpacker (entry / real)         | field-tbl / F3 |
|---------:|-----------:|---------------------|---------------------------------|----------------|
| 1        | **0**      | **RADIO_CONFIG**    | 0xd8182ec0 → 0xd8183f30          | @0xc37c0290 / F3 @0xc3555b70 |
| 2        | **1**      | **RX_MEASURE**      | 0xd8185654 (disp 0xd8185b1c)     | @0xc37c0478 / F3 @0xc3555bc0 |
| 3        | 2          | TX_CONTROL          | 0xd8188428                       | F3 @0xc3555c10 |
| 4        | 3          | TRM_RRA             | 0xd86c0fe4                       | F3 @0xc3561a10 |
| —        | (4)        | *(empty in mod0)*   | — (slot 0x3c not registered)     | — |
| 5        | **5**      | **IQ_CAPTURE**      | 0xd81893a4 (disp 0xd8189818)     | @0xc37c0828 / F3 @0xc3555c30 |
| 6        | 6          | TX_MEASURE          | 0xd818ab6c                       | F3 @0xc3555c70 |
| 7        | 7          | MSIM_CFG            | 0xd8187fe4                       | F3 @0xc3555c00 |
| 8        | 8          | IRAT_CONFIG         | 0xd84aff68                       | F3 @0xc3555e20 |
| 9        | 9          | WAIT_TRIGGER        | 0xd81870fc                       | F3 @0xc3555bd0 |
| 10       | 10         | tx_measure (RFDEBUG)| 0xd84ad0b8                       | F3 @0xc3555e00 |
| 11       | **11**     | **COMMAND_CAPABILITY**| 0xd81849ec                     | @0xc37c03b4 / F3 @0xc3555b90 |

> NOTE: the *call order* in 0xd8182640 is slot 0,1,2,3,5,7,6,8,9,10,11 (slot 6 and 7 are registered
> out of numeric order in the call list, but each is written to its own fixed slot offset, so the
> command_id = slot index, not call position). **FACT** (each `add(r2,#slot_off)` before the call).

---

## 4. command_id INTEGERS — FACT vs UNKNOWN

**FACT (relative order, static):**
```
RADIO_CONFIG       = B + 0
RX_MEASURE         = B + 1
IQ_CAPTURE         = B + 5
COMMAND_CAPABILITY = B + 11
```
where B = the RF-test module's base offset into the global command_id space (0..0x31).

**Why B is UNKNOWN statically:**
- The command_id → module/group resolution is the RUNTIME byte table @`0xca9ef490`
  (`memb(0xca9ef490 + command_id)`, resolver 0xd84aa9e0). No static immediate assigns B. **FACT.**
- The master table @0xca79a850 is malloc'd (0xd8272368) and only default-filled (0xd82723c0); the
  per-command unpacker/module binding is written at runtime. **FACT** (verified 4 ways in
  command_id_map_B.md §2).
- The unpacker VAs (0xd81849ec / 0xd8189818 / 0xd8183f30 / 0xd8185b1c) appear as **0 static
  immediates** outside their own function bodies. **FACT** (grep §6).

**INFERENCE for B (evidence-backed, not proven):**
- 0xd8182640 is the **first** module init from 0xd84aa03c, and its commands are the primary RFTEST
  set. If the global counter starts at 0 for this module (i.e. B = 0), then:
  `RADIO_CONFIG=0, RX_MEASURE=1, IQ_CAPTURE=5, COMMAND_CAPABILITY=11(0x0b)`.
  **INFERENCE (plausible, unverified).** The Qualcomm-FTM convention (query/capability commands
  early) is consistent with COMMAND_CAPABILITY at the small value 11 and RADIO_CONFIG at 0.
- The prior `desc+0x10` group ids (mod0=0x12, mod1=0x13, mod2=0x0b) are **group/subsys ids, NOT
  command_id bases** — do not use them as B.

**UNKNOWN (live only):**
- The absolute base B (hence the exact integer command_id of each command).
- Close live via COMMAND_CAPABILITY: read CMD_MASK (field 3); bit N set ⇒ command_id N registered;
  then QUERY_COMMAND=N + PROPERTY_MASK to name each (command_id_map_B.md §6). RADIO_CONFIG is the
  bit whose properties carry BAND/CHANNEL/BANDWIDTH; COMMAND_CAPABILITY is the responder itself.

---

## 5. FACT / INFERENCE / UNKNOWN — with VAs

**FACT**
- Master registrar 0xd84aa03c → module inits 0xd8182640 (RF-TEST, first), 0xd81793b0, 0xd86c3ee8.
- RF-TEST module init **0xd8182640** installs 12 commands into a 12-byte slot array (module ctx
  @0xca65d640, sub-ctx @desc+0x14). slot_index = (slot_offset-0xc)/0xc = registration ordinal.
- Per-command sub-inits (unpack pass): 0xd8182e50/552c/883c0/86c2324/818933c/8187f80/818ab08/
  84afe9c/8187098/84ad048/8184980 → slots 0,1,2,3,5,7,6,8,9,10,11.
- Command names by F3 string (byte-exact, §2/§3): slot0 RADIO_CONFIG, slot1 RX_MEASURE,
  slot2 TX_CONTROL, slot3 TRM_RRA, slot5 IQ_CAPTURE, slot6 TX_MEASURE, slot7 MSIM_CFG,
  slot8 IRAT_CONFIG, slot9 WAIT_TRIGGER, slot10 tx_measure(RFDEBUG), slot11 COMMAND_CAPABILITY.
- The four targets: RADIO_CONFIG unpacker 0xd8183f30 (wrap 0xd8182ec0→0xd818327c);
  COMMAND_CAPABILITY 0xd81849ec; IQ_CAPTURE dispatcher 0xd8189818 (tbl @0xc37c0828);
  RX_MEASURE dispatcher 0xd8185b1c (tbl @0xc37c0478). Each field-tbl referenced exactly once.
- command_id resolver 0xd8272684 (`memw(memw(0xca79a850)+id*4+0x34)`, id ≤ 0x31).
- Master table alloc/memset 0xd8272368 (##0x954c); default populate 0xd82723c0 (0..0x31, 0x28 B).
- command_id → group runtime byte table @0xca9ef490 (memb-indexed; resolver 0xd84aa9e0).

**INFERENCE**
- The RF-test module base B in the global command_id space is likely 0 (first-registered module,
  primary RFTEST set) ⇒ RADIO_CONFIG=0, RX_MEASURE=1, IQ_CAPTURE=5, COMMAND_CAPABILITY=0x0b.
  Not proven — B is set at runtime.

**UNKNOWN (live only)**
- The absolute integer command_id of each command (needs @0xca9ef490 / @0xca79a850 from RAM, or a
  live COMMAND_CAPABILITY CMD_MASK query).

---

## 6. REPRODUCE
```
dis.sh 0xd84aa03c 0x14        # master registrar: module inits (0xd8182640 first)          FACT §1.1
dis.sh 0xd8182640 0x2a0       # RF-TEST module init: 12 slot registrations (2 passes)       FACT §1.2
dis.sh 0xd8182e50 0x70        # slot0 sub-init → unpack 0xd8182ec0 (RADIO_CONFIG)           FACT §2.1
dis.sh 0xd818552c 0x70        # slot1 sub-init → unpack 0xd8185654 (RX_MEASURE)             FACT §2.2
dis.sh 0xd818933c 0x70        # slot5 sub-init → unpack 0xd81893a4 (IQ_CAPTURE)             FACT §2.3
dis.sh 0xd8184980 0x70        # slot11 sub-init → unpack 0xd81849ec (COMMAND_CAPABILITY)    FACT §2.4
dis.sh 0xd8185b1c 0x08        # RX_MEASURE field dispatcher (tbl @0xc37c0478)               FACT §2.2
dis.sh 0xd8189818 0x08        # IQ_CAPTURE field dispatcher (tbl @0xc37c0828)               FACT §2.3
dis.sh 0xd8272684 0x30        # command_id → struct resolver (@0xca79a850, id*4+0x34)       FACT §1.3
dis.sh 0xd8272368 0x120       # master table alloc/memset + default populate (no names)     FACT §1.3
dis.sh 0xd84aa9e0 0x80        # command_id → group byte table @0xca9ef490 (RUNTIME)         FACT §1.3
# Resolve each slot's F3 name (16-B msg entries; fmt ptr = word[2]):
python3 - <<'PY'
import struct
SEGS=[(0xc3553000,0xd315f4,'modem.b21'),(0xc8b6a000,0x5cdd97,'modem.b23')]
def rb(va,n):
  for b,s,f in SEGS:
    if b<=va<b+s:
      d=open('/tmp/modemre/'+f,'rb').read();o=va-b;return d[o:o+n]
  return b''
def cs(va):
  x=rb(va,120);i=x.find(b'\x00');return x[:i].decode('latin1','replace')
for a in (0xc3555b70,0xc3555bc0,0xc3555c30,0xc3555b90):
  print(hex(a),'->',cs(struct.unpack('<4I',rb(a,16))[2]))
PY
grep -c "0xc37c0478\|0xc37c0828\|0xc37c0290\|0xc37c03b4" /tmp/_full_dis.txt   # each field-tbl: 1 ref
grep -c "##0xd81849ec\|##0xd8189818\|##0xd8183f30\|##0xd8185b1c" /tmp/_full_dis.txt  # 0 static imms
```
