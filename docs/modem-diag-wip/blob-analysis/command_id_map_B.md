# command_id_map_B — RFTEST command table (command_id 0..0x31) → name → handler → query/action

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Base:** `0xd8000000` (`/tmp/modemre/clade_dec_full.bin`, tool `dis.sh <va> <len>`).
**Rodata:** `rd.py <va> <n>` (b21 @0xc3553000, b23 @0xc8b6a000).
**Full disasm:** `/tmp/_full_dis.txt`. Cross-refs: `slot_correlation.md`, `cmd_capability_map.md`,
`radio_config_unpack.md`, `gate_resolution.md`, `wire_offset_B.md`.

**Legend:** **FACT** = read from disasm/bytes at a cited VA · **INFERENCE** = deduced with evidence ·
**UNKNOWN** = only resolvable from a live device (RAM/runtime).

---

## 0. TL;DR — DIRECT ANSWERS

1. **There are TWO distinct index axes on the wire — do not confuse them.** **FACT.**
   - `sub_command` = wire bytes [4..5] (LE), value `0x10xx`. Selects the **handler wrapper**
     via the static jump table @0xc37c649c (24 slots). **FACT** (dispatcher 0xd82714b4).
   - `command_id` = wire **byte 10** (`memub(req+0xa)`, ≤0x31). Selects the **unpacker struct**
     in the runtime table `memw(memw(0xca79a850)+id*4+0x34)` (resolver 0xd8272684). **FACT.**
   - The unpacker that runs (COMMAND_CAPABILITY / RADIO_CONFIG / IQ_CAPTURE / RX_MEASURE) is
     chosen by **command_id (byte 10)**, NOT by sub_command. **FACT** (0xd86fd0f4:0xd86fd130).

2. **The command_id → name → unpacker binding is populated at RUNTIME. There is NO static
   descriptor table in the image.** **FACT — verified 4 independent ways (§2).** Therefore the
   *exact numeric* command_id of each named command is **UNKNOWN from static analysis alone**;
   it is a registration-order counter assigned in RAM and read back live via COMMAND_CAPABILITY's
   CMD_MASK (§6). Best-evidenced inferences are given in §5.

3. **The four target unpackers are IDENTIFIED and DECODED (FACT, with exclusive field-tables):**
   | command | unpacker VA | field-table | name-table | semantics |
   |---|---|---|---|---|
   | COMMAND_CAPABILITY | **0xd81849ec** | @0xc37c03b4 | @0xc906ce18 (grp-22) | query, returns CMD_MASK bitmap |
   | RADIO_CONFIG | **0xd8183f30** (wrap 0xd8182ec0→0xd818327c) | @0xc37c0290 | @0xc906c630 | tune: BAND/CHANNEL/BANDWIDTH |
   | RX_MEASURE | **0xd8185b1c** | @0xc37c0478 | — | measurement query |
   | IQ_CAPTURE | **0xd8189818** (dispatcher) | @0xc37c0828 | — | sample capture |
   Each field-table is referenced **only** inside its own unpacker (grep §7) → confirms per-command
   runtime dispatch, no cross-referencing static table.

4. **COMMAND_CAPABILITY fields (grp-22 name-table @0xc906ce18, verified byte-exact §4.2):**
   **QUERY_COMMAND = field 1**, QUERY_PROPERTY = field 2, **CMD_MASK = field 3**,
   PROPERTY_MASK_0_63..192_255 = fields 4..7. **FACT.**

---

## 1. THE TWO-AXIS WIRE MODEL (FACT)

```
off  size field                                    selects
0x00  1   0x4B  DIAG_SUBSYS_CMD_F
0x01  1   0x0B  DIAG_SUBSYS_FTM
0x02  2   ftm_cmd_id (u16 LE) = 0x0027 (LTE)
0x04  2   sub_command (u16 LE) = 0x10xx  ───────►  handler wrapper (jump-tbl @0xc37c649c)  FACT
0x06  2   tlv_count  (u16 LE)
0x08  ..  body[0]
0x0a  1   command_id (≤0x31)  ───────────────────►  unpacker struct (@0xca79a850, resolver
                                                    0xd8272684)                              FACT
0x0b+ ..  more body / packed params (byte 0x0b,0x0f,0x10,0x11 read by some handlers)
0x12+ ..  TLV records: {u16 field_id, u16 len, u8 value[len]}                     INFERENCE(fmt)
```

Handler prologue (canonical, 0xd86fd0f4 = sub 0x1002 wrapper) **FACT**:
```
d86fd0fc call 0xd86fd0e8            ; header parser (→0xd8272c10), returns r1 = raw wire ptr
d86fd108 r17 = memub(r18+0x0a)      ; command_id = wire byte 10
d86fd10c p0 = cmp.gtu(r17,#0x31)    ; bound-check ≤0x31
d86fd130 call 0xd8272684 (r0=r17)   ; resolve command_id → unpacker struct
```
Resolver 0xd8272684 **FACT**:
```
p0=cmph.gtu(id,#0x31); r3=memw(##0xca79a850); r3=addasl(r3,id,#2); r3=memw(r3+#0x34); return r3
```
command_id offset varies by handler family (all off the wire ptr): **+0x0a** majority;
**+0x0b** for sub 0x1008 (0xd86fd5c8) & 0x1017 (0xd86fe7e0); **+0x0c** for 0x1006 (0xd8271c94) &
0x1010 (0xd82722c4). **FACT** (slot_correlation.md §1.2).

---

## 2. WHY THERE IS NO STATIC command_id→name TABLE — 4 PROOFS (FACT)

1. **Master table is alloc+memset only.** Sole writer of `memw(0xca79a850)` = `0xd8272368`
   (`r2=##0x954c; memw(0xca79a850)=malloc; memset 0`). Populate loop `0xd82723c0` fills 50 (0..0x31)
   per-command structs (0x28 B each) with **generic defaults** (`+0x1=0x15`, `+0x2=0xffff`,
   `+0x4=0x33`, `+0x25=3`, `+0x26=3`) — no name, no handler. **FACT** (dis.sh 0xd8272368 0x120).
2. **Unpacker VAs appear as NO static immediate.** `grep ##0xd81849ec / ##0xd8189818 / ##0xd8183f30`
   → 0 hits outside each function's own prologue. RADIO_CONFIG's wrapper 0xd8182ec0 is stored only
   as `entry[0]` of its **own module ctx** (0xd8182e90 `immext #0xd8182ec0`, into ctx @0xca65d640),
   never into a command_id slot by literal. **FACT** (§7 grep).
3. **Setters index the master struct by a runtime command_id argument (r0), not a constant.**
   - `0xd827263c(id,r1,r2)`: `struct=memw(memw(0xca79a850)+id*4+0x34)`; `memb(struct+0x1)=r1`;
     `memh(struct+0x2)=r2`. **FACT.**
   - `0xd8272608(id,r1)`: same resolve; `memh(struct+0x4)=r1`. **FACT.**
   - `0xd82725e0(...,r4,r1,r2)`: writes `memb(r4+{0x0|0x6|0x7})` on a *passed* struct (helper,
     not id-indexed). **FACT.**
   All call-sites of 263c/2608 pass `r0` = a value **computed at request time** (e.g. 0xd828ca70
   reads it from `memub(r29+0x91)` of the live request, 0xd86fd434 from the decoded id), never a
   static command name. **FACT.**
4. **Command names exist only inside F3 format-strings**, not as a registration table.
   F3 table @0xc3555b80 lists `[FTM.RFTEST][<NAME>][UNPACK]/[REPACK]` per command but carries
   **no command_id / no sub_command** field (msg_id, ssid, argc, fmt, file only). **FACT** (§3).

> Conclusion: the numeric command_id↔name map is **assigned in registration order at runtime** and
> only observable live via COMMAND_CAPABILITY CMD_MASK. Static analysis pins the *unpackers*,
> *field/name tables*, *wire layout*, and *safety class* fully — but not the integer command_id.

---

## 3. COMMAND NAME SET (F3 table @0xc3555b80, ssid 0x17 = RF) — FACT

Declared RFTEST commands (order of declaration, the guide for §5 inference):
```
RADIO_CONFIG(=RADIO_CFG)   fmt@0xc37c0374/0320  file ftm_rf_test_radio_config.c
COMMAND_CAPABILITY         fmt@0xc37c03cc/042c  file ftm_rf_test_command_capability.c
RX_MEASURE                 fmt@0xc37c0590/05e0  file ftm_rf_test_rx_measure.c
WAIT_TRIGGER               fmt@0xc37c0664       file ftm_rf_test_wait_trigger.c
MSIM_CFG                   fmt@0xc37c073c       file ftm_rf_test_msim_config.c
TX_CONTROL                 fmt@0xc37c07d8       file ftm_rf_test_tx_control.c
IQ_CAPTURE                 fmt@0xc37c08f4/0949  file ftm_rf_test_iq_capture.c
TX_MEASURE                 fmt@0xc37c0dc4/0e14  file ftm_rf_test_tx_measure.c
IRAT_CONFIG                fmt@0xc37c/…         file ftm_rf_test_irat_config.c
```
**FACT** (decoded from the 16-B/entry F3 table; see §7 to reproduce).

---

## 4. THE FOUR TARGET UNPACKERS — DECODE (FACT)

### 4.1 COMMAND_CAPABILITY = 0xd81849ec (FACT)
```
d81849ec call 0xd814e6ac                 ; get request context → r0
d8184a00 r25 = memw(r18+0x0)             ; r25 = parsed command DESCRIPTOR
d8184a30 r0  = memub(r25+0x16)           ; query selector
d8184a3c r22 = memub(r25+0x12)           ; sub/idx byte
d8184a40 if (r19 > 0x14) error           ; gate ≤0x14
d8184ab0 r22 = memub(r25+0x1a) (|1b<<8|…); NUM_TLV (packed 0x1a..0x1d)
d8184ad4 if (num_tlv==0) jump 0xd8184bc4 ; EMPTY REQUEST allowed → REPACK defaults (incl CMD_MASK)
d8184afc if (field_id > 0x7) error       ; CC field range ≤7
```
Field-dispatch tbl @0xc37c03b4 (6 slots): idx0→0xd8185010, idx1(QUERY_COMMAND)→0xd8184ff0,
idx2(QUERY_PROPERTY)→0xd8184ff8, idx3(CMD_MASK)→0xd8185000, idx4→0xd8185004, idx5→0xd818500c. **FACT.**

### 4.2 grp-22 field name-table @0xc906ce18 (byte-exact) — FACT
```
0 UNASSIGNED   1 QUERY_COMMAND   2 QUERY_PROPERTY   3 CMD_MASK
4 PROPERTY_MASK_0_63   5 PROPERTY_MASK_64_127   6 PROPERTY_MASK_128_191   7 PROPERTY_MASK_192_255
9 SUB_IDX 10 TECH 11 RXTX 12 CHAIN 13 CARRIER_IDX 14 RFM_DEVICE 15 SIG_PATH 16 ANTENNA_PATH
17 ANTENNA_NUM 18 CURR_ASDIV_CFG 19 CURR_ASDIV_CAL 20 PEND_ASDIV_CFG 21 PEND_ASDIV_CAL
22 PLL_ID 23 BAND 24 CHANNEL 25 BANDWIDTH 27 BAND_NUMBER 28 SUBBAND_NUMBER 29 RAW_RESULT
30 IF_TRX_INFO 31 RFTRX_ANTMOD_INTF_INFO 32 ANT_MODULE_INFO 33 SUBBAND_FREQ_INFO
34 ANT_MODULE_TYPE 35 TX_RX_CAL_FEED_INFO 36 PLATFORM_TYPE 38 gps_dc_cancellation
```
→ **QUERY_COMMAND = field 1, CMD_MASK = field 3.** **FACT.**

### 4.3 RADIO_CONFIG = 0xd8183f30 (wrapper 0xd8182ec0 → 0xd818327c) — FACT
- Installed as `entry[0]` of module ctx @0xca65d640 (0xd8182e90 `immext #0xd8182ec0`; repack
  0xd8182f40 @entry[4]; bufsz 0x174810 @entry[8]). Module init 0xd8182640 (called first from
  master registrar 0xd84aa03c). **FACT.**
- Field-handler tbl @0xc37c0290 (index = field_id-1, valid 1..36). **FACT.**
- Field name-table @0xc906c630 (byte-exact): **5=BAND, 6=CHANNEL, 7=BANDWIDTH, 25=TECH_MODE**,
  1=RX_CARRIER 2=TX_CARRIER 3=RFM_DEVICE 9=SIG_PATH 10=ANT_PATH … **FACT.**
  → RADIO_CONFIG is the **tuning** command (band/channel/bandwidth). **FACT.**
- num_tlv=0 → clean bail (status 0x14, no SSR) at 0xd818486c. **FACT.**

### 4.4 RX_MEASURE = 0xd8185b1c (field-tbl @0xc37c0478) — FACT
`d8185b1c: r9 = memw(r3<<2 + ##0xc37c0478); jumpr r9` — indexed jump, tbl @0xc37c0478 referenced
only here. **FACT.**

### 4.5 IQ_CAPTURE = 0xd8189818 (dispatcher, field-tbl @0xc37c0828) — FACT
`d8189818: r2 = memw(r2<<2 + ##0xc37c0828); jumpr r2` — indexed jump, tbl @0xc37c0828 referenced
only here (grep §7). 0 static refs → pure runtime dispatch by command_id. **FACT.**

---

## 5. command_id (0..0x31) → NAME → HANDLER → CLASS

### 5.1 What IS static (FACT): sub_command 0x10xx → handler wrapper (jump-tbl @0xc37c649c)
Plus per-slot decode-function class → query/action safety (base FACT of decode fns; class INFERENCE).

| sub    | slot | handler wrapper | id-offset | decode fn(s)                    | class (INFERENCE)          | tlv_count=0 |
|--------|------|-----------------|-----------|--------------------------------|----------------------------|-------------|
| 0x1000 | 0    | 0xd8271630      | —         | dispatch tail / no-op          | reserved                   | (bail)      |
| 0x1001 | 1    | 0xd827176c      | —         | error stub                     | unsupported                | (err)       |
| 0x1002 | 2    | 0xd86fd0f4      | +0x0a     | 0xd8272684 + 0xd8272cd8(callr) | **measure/capture (IQ?)**  | ❌ FAULTS   |
| 0x1003 | 3    | 0xd86fd230      | +0x0a     | 0xd8272684 + 0xd827e064        | tune/config (RADIO_CFG?)   | ❌ FAULTS   |
| 0x1004 | 4    | 0xd86fd328      | +0x0a     | 0xd827dfc8                     | **query/read**             | ✅ SAFE     |
| 0x1005 | 5    | 0xd86fd474      | +0x0a     | 0xd827dfc8                     | **query/read**             | ✅ SAFE     |
| 0x1006 | 6    | 0xd8271c94      | +0x0c     | 0xd829c008 (writes +0xe0)      | init RF state (ACTION)     | ⚠️ mutates  |
| 0x1007 | 7    | 0xd86fdb88      | +0x0a     | 0xd8272684 + 0xd827dfc8        | query w/ resolve           | ✅ prob.SAFE|
| 0x1008 | 8    | 0xd86fd5c8      | +0x0b     | 0xd827dfc8 ×2 (cb 0xd8263324)  | **query/read (RX_MEAS?)**  | ✅ SAFE     |
| 0x1009 | 9    | 0xd86fd80c      | +0x0a/+0x19| 0xd86fd0e8 (parse+repack)     | query                      | ✅ SAFE     |
| 0x100a | 10   | 0xd86fda68      | +0x0a     | 0xd827dfc8                     | **query/read**             | ✅ SAFE     |
| 0x100b | 11   | 0xd86fe120      | +0x0a     | 0xd8272c10+0xd8264e4c+0xd82805f0| config (writes)           | ⚠️ avoid    |
| 0x100c | 12   | 0xd86fe1e4      | +0x0a     | 0xd8272c10 + 0xd8264f08        | config (writes)            | ⚠️ avoid    |
| 0x100d | 13   | 0xd86fde80      | +0x0a     | 0xd8272684+0xd82735e8+0xd828067c| config/list (writes)      | ⚠️ avoid    |
| 0x100e | 14   | 0xd86fe0a4      | +0x0a     | 0xd8263f98 (0xd81bf0e8 getter) | config-read                | ⚠️ semi     |
| 0x100f | 15   | 0xd86fdf94      | +0x0a     | early bail                     | reserved                   | ✅ (bail)   |
| 0x1010 | 16   | 0xd82722c4      | +0x0c     | 0xd829c404 (writes +0xdc)      | init RF state (ACTION)     | ⚠️ mutates  |
| 0x1011 | 17   | 0xd8271b64      | —         | 0xd828054c (0xd8279154 getter) | **query state**            | ✅ SAFE     |
| 0x1012 | 18   | 0xd8271bcc      | —         | 0xd82805f0                     | **query/dispatch**         | ✅ SAFE     |
| 0x1013 | 19   | 0xd82715d4      | —         | error stub                     | invalid                    | (err)       |
| 0x1014 | 20   | 0xd86fe300      | +0x0a     | 0xd8272bb0 + 0xd84b5184        | **capability read**        | ✅ prob.SAFE|
| 0x1015 | 21   | 0xd86fe558      | +0x0a     | 0xd8272bb0 + 0xd81bf0e8        | **capability/state read**  | ✅ prob.SAFE|
| 0x1016 | 22   | 0xd86fe60c      | +0x0a     | 0xd827dfc8 + 0xd827e064        | decode+copy (cb 0xd8263324)| ⚠️ semi     |
| 0x1017 | 23   | 0xd86fe7e0      | +0x0b     | 0xd8263f98 (0xd81bf0e8 getter) | config-read                | ✅ prob.SAFE|

**FACT** = handler VA, id-offset, decode-fn VA, and (for 0x1002/0x1003) the fault path.
**INFERENCE** = the class/name label and safety verdict.

### 5.2 command_id (byte 10, 0..0x31) → name (best INFERENCE)
The command_id integer is **runtime-assigned (registration counter)** so exact values are UNKNOWN
statically (§2). The evidence-backed best inference (from decode-fn signatures + F3 declaration
order + field-table semantics) is:

| command            | unpacker VA | best-inference command_id | evidence |
|--------------------|-------------|---------------------------|----------|
| RADIO_CONFIG       | 0xd8183f30  | **INFERENCE ≈ small, ≠ 0x1002 slot-fault** ; via handler 0x1003 path | uses 0xd8272684 (field-tbl resolve) + faults w/ tlv=0 = needs TLVs (tune) |
| COMMAND_CAPABILITY | 0xd81849ec  | **INFERENCE (query, empty-request-safe)** ; via handlers 0x1004/0x1005/0x100a class | only decode 0xd827dfc8, REPACK with tlv=0, allows empty request (0xd8184bc4) |
| RX_MEASURE         | 0xd8185b1c  | **INFERENCE (query)** ; via handler 0x1008 (cb 0xd8263324, safe w/ tlv=0) | query/read pair 0xd827dfc8×2 |
| IQ_CAPTURE         | 0xd8189818  | **INFERENCE (measure/capture)** ; via handler 0x1002 (path 0xd8272cd8 → callr 0xd826d3e4) | faults w/ tlv=0 (uninit capture buffer) |

**UNKNOWN:** the exact byte-10 integer of each. Close live with §6 (read CMD_MASK, then probe each
set bit N with QUERY_COMMAND=N and read the PROPERTY_MASK bits + name).

### 5.3 Safe-to-probe with tlv_count=0 (won't fault) vs action (needs TLVs / mutates)
```
QUERY-SAFE (tlv_count=0 → clean REPACK/bail, no SSR):
   sub 0x1004, 0x1005, 0x1007, 0x1008, 0x1009, 0x100a, 0x100f, 0x1011, 0x1012, 0x1014, 0x1015, 0x1017
ACTION / DANGEROUS with tlv_count=0:
   0x1002, 0x1003  → FAULT (FACT: measure/tune deref uninit state)
   0x1006, 0x1010  → mutate RF state (init, ACTION)
   0x100b,0x100c,0x100d,0x100e,0x1016 → config write / semi
```
**Precondition (FACT):** ALL 0x10xx require **TECH_ENTER first** — gate 0xd81e5d60
(`state==0x7 → err 0x10 → status 0x14`). A 0x14 during a sweep = "tech not entered", NOT a crash.

---

## 6. WIRE PACKET — COMMAND_CAPABILITY with QUERY_COMMAND (to read CMD_MASK)

TLV record layout: `<field_id:u16 LE> <length:u16 LE> <value[length]>`.
ftm_cmd_id = `27 00` (LTE). **sub_command must be COMMAND_CAPABILITY's 0x10xx slot** — UNKNOWN
statically, so this is a directed sweep over the query-safe slots (§5.3). **command_id (byte 10)
must be COMMAND_CAPABILITY's runtime id** — for the empty-request/QUERY path the unpacker keys off
the descriptor's own field, and CMD_MASK is emitted regardless of a specific body id; use the same
byte-10 value the slot handler expects (start 0, sweep 0..0x31 if needed).

### STEP 0 (MANDATORY) — TECH_ENTER LTE (clears the 0x14 tech-state gate)
```
4B 0B 27 00 0D 00 03 00 \
   01 00 04 00 00 00 00 00   ; SUB      = 0
   02 00 04 00 01 00 00 00   ; TECH     = 1 (LTE)
   03 00 04 00 00 00 00 00   ; SCENARIO = 0
```
Concatenated:
```
4B 0B 27 00 0D 00 03 00 01 00 04 00 00 00 00 00 02 00 04 00 01 00 00 00 03 00 04 00 00 00 00 00
```
Expect status = 0.

### STEP 1 — COMMAND_CAPABILITY, QUERY_COMMAND = 0xFFFFFFFF (mask of all supported commands)
**sub_command** = `<SUB_CAP:u16 LE>` = the COMMAND_CAPABILITY slot (sweep the query-safe list).
**tlv_count** = `01 00` (one TLV: QUERY_COMMAND).
**command_id** = wire byte 10 = `00` (byte 8=body0, 9=body1, 10=command_id).
```
4B 0B 27 00 <SUB_CAP_LE> 01 00 <b8> <b9> <cmd_id> <b11..> 01 00 04 00 FF FF FF FF
```
Minimal concrete form (body bytes 8,9 = 0, command_id byte10 = 0, then the TLV starts at byte 12
after b8,b9,b10,b11 — pad body to the TLV region; the parser copies the 8-B header and reads TLVs
from the body per the fmt). Practical minimal packet (body = command_id + immediate TLV):
```
4B 0B 27 00 <SUB_CAP_LE> 01 00  00 00 00 00  01 00 04 00 FF FF FF FF
                                 ^b8 b9 b10 b11  ^field=1(QUERY_COMMAND) ^len=4 ^value=0xFFFFFFFF
```
Sweep SUB_CAP over the query-safe slots (LE bytes):
```
04 10, 05 10, 07 10, 08 10, 09 10, 0A 10, 0F 10, 11 10, 12 10, 14 10, 15 10, 17 10
```
The slot whose reply is a REPACK containing **field_id 3 (CMD_MASK) with a non-zero 32-bit value**
is COMMAND_CAPABILITY; its SUB_CAP and command_id are thereby pinned live.

- **sub_command to use:** COMMAND_CAPABILITY's 0x10xx slot (unknown statically → sweep above).
- **tlv_count to use:** `01 00` (=1) for the explicit QUERY_COMMAND TLV.
  *Alternative:* `00 00` (=0, empty request) — the unpacker allows num_tlv=0 (0xd8184ad4→0xd8184bc4)
  and REPACKs all fields incl CMD_MASK with defaults. Both read CMD_MASK.

### STEP 2 — interpret CMD_MASK (field_id 3)
REPACK TLV `03 00 <len> <bitmap>`: **bit N = 1 ⇒ command_id N is registered/supported.** Iterate
bits; re-query each with `QUERY_COMMAND = N` and read PROPERTY_MASK_0_63..192_255 (fields 4..7) to
get that command's properties (and cross-ref F3 names §3). **FACT of mechanism** (grp-22 §4.2 +
REPACK loop 0xd8185060, fmt @0xc3555ba0).

---

## 7. FACT / INFERENCE / UNKNOWN — with VAs

**FACT**
- Two-axis wire: sub_command @[4..5]→jump-tbl @0xc37c649c (dispatcher 0xd82714b4);
  command_id @byte10 (≤0x31)→resolver 0xd8272684→`memw(memw(0xca79a850)+id*4+0x34)`.
- Master table: alloc/memset 0xd8272368 (0x954c); populate defaults 0xd82723c0 (50 slots, no names).
- Setters: 0xd827263c `struct+0x1=r1, struct+0x2(h)=r2` (id in r0); 0xd8272608 `struct+0x4(h)=r1`
  (id in r0); 0xd82725e0 helper `memb(r4+{0|6|7})` (struct passed, not id-indexed).
- Unpackers (each field-tbl referenced ONLY in its own body, grep below):
  COMMAND_CAPABILITY 0xd81849ec (tbl @0xc37c03b4, names @0xc906ce18);
  RADIO_CONFIG 0xd8183f30 (wrap 0xd8182ec0→0xd818327c, tbl @0xc37c0290, names @0xc906c630);
  RX_MEASURE 0xd8185b1c (tbl @0xc37c0478); IQ_CAPTURE 0xd8189818 (tbl @0xc37c0828).
- CC fields: QUERY_COMMAND=1, QUERY_PROPERTY=2, CMD_MASK=3, PROPERTY_MASK 4..7 (@0xc906ce18).
- RADIO_CONFIG fields: BAND=5, CHANNEL=6, BANDWIDTH=7, TECH_MODE=25 (@0xc906c630).
- Fault path (0x1002/0x1003 with tlv=0): 0xd86fd158→0xd8272cd8→callr 0xd826d3e4/0xd8292298
  (magic 0x51eb851f; gate 0xd8273c84; callr repack 0xd826d514).
- Tech-state gate 0xd81e5d60 (==0x7→0x10→status 0x14) precedes every 0x10xx.
- F3 name table @0xc3555b80 (RADIO_CONFIG/COMMAND_CAPABILITY/RX_MEASURE/IQ_CAPTURE/… §3),
  carries no command_id.

**INFERENCE**
- command_id (byte10) ↔ name: RADIO_CONFIG via the tune/fault slot 0x1003 family; COMMAND_CAPABILITY
  via the query-safe slots (0x1004/0x1005/0x100a class, empty-request-safe); RX_MEASURE via 0x1008
  (safe query pair); IQ_CAPTURE via 0x1002 (measure/capture path, faults w/ tlv=0). §5.2.
- Safety classes per slot (§5.1/5.3) — decode-fn VAs are FACT, class labels INFERENCE.

**UNKNOWN (live only)**
- The exact integer command_id (0..0x31) for each named command — assigned at runtime in
  registration order; the master table @0xca79a850 is filled dynamically; unpacker VAs never appear
  as static immediates. Close with §6 (CMD_MASK bit N = command_id N).
- The exact mapping command_id ↔ sub_command 0x10xx (resolved by reading the runtime descriptor).
- The concrete CMD_MASK value.

---

## 8. REPRODUCE
```
dis.sh 0xd8272368 0x120      # master table alloc/memset + populate (no names)      FACT §2.1
dis.sh 0xd8272684 0x20       # resolver command_id→struct                            FACT §1
dis.sh 0xd827263c 0x40       # setter A (struct+0x1,+0x2)                            FACT §2.3
dis.sh 0xd8272608 0x30       # setter B (struct+0x4)                                 FACT §2.3
dis.sh 0xd82725e0 0x30       # helper setter (memb r4+{0,6,7})                       FACT §2.3
dis.sh 0xd86fd0f4 0x40       # handler 0x1002: command_id=memub(req+0xa)→0xd8272684  FACT §1
dis.sh 0xd81849ec 0x140      # COMMAND_CAPABILITY unpacker                           FACT §4.1
dis.sh 0xd8183f30 0x20 ; dis.sh 0xd8182ec0 0x80   # RADIO_CONFIG wrap/unpacker       FACT §4.3
dis.sh 0xd8185b1c 0x10       # RX_MEASURE indexed jump (@0xc37c0478)                 FACT §4.4
dis.sh 0xd8189818 0x10       # IQ_CAPTURE dispatcher (@0xc37c0828)                   FACT §4.5
dis.sh 0xd81e5d40 0x30       # tech-state gate (==0x7→0x14)                          FACT §5
rd.py  0xc906ce18 160        # grp-22 CC field names (QUERY_COMMAND=1, CMD_MASK=3)   FACT §4.2
rd.py  0xc906c630 120        # RADIO_CONFIG field names (BAND=5,CHANNEL=6,BW=7)      FACT §4.3
rd.py  0xc37c649c 96         # 0x10xx handler jump-table (24 slots)                  FACT §5.1
rd.py  0xc37c03b4 24         # CC field-dispatch table (6 slots)                     FACT §4.1
grep -n "0xc37c0828\|0xc37c0478\|0xc37c0290\|0xc37c03b4" /tmp/_full_dis.txt  # each 1 ref (own unpacker)
grep -n "##0xd81849ec\|##0xd8189818\|##0xd8183f30" /tmp/_full_dis.txt        # 0 static immediates
grep -n "0xca79a850" /tmp/_full_dis.txt   # 1 store (0xd8272368) + loads (resolver/setters)
```
