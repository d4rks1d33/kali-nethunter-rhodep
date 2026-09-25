# wire_offset_B — Byte layout of `memub(r18+0x0a)` in the FTM/RFTEST diag parser

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK · base `0xd8000000`
(`clade_dec_full.bin`, `dis.sh`). Cross-refs: `slot_correlation.md`, `gate_resolution.md`,
`rftest_command_format.md`, `rftest_entry_0x14.md`.

Legend: **FACT** = read directly from disasm at a VA · **INFERENCE** = deduced from
structure/convention · **UNKNOWN** = only resolvable at runtime.

---

## 0. TL;DR — THE ANSWER

- `r18` (in `0xd86fd0f4`) = the parser's returned `r1` = **the raw wire message pointer**,
  pointing at **message START** (byte 0 = `0x4B`). **N = 0.** **FACT.**
- Therefore:

  **`memub(r18 + 0x0a)` == wire byte number 10 (0x0A).**  (0x4B = byte 0.) **FACT.**

  That byte is the **RFTEST `command_id`** (the table index), bound-checked `<= 0x31`
  and used to index the runtime command table at `memw(0xca79a850)+id*4+0x34`
  (resolver `0xd8272684`). **FACT.**

---

## 1. Wire message layout (DIAG-FTM transport)

Header (8 bytes) + body. **FACT** (dispatcher reads [4],[5],[6..7]; format string
line in `rftest_entry_0x14.md:307`):

```
off  size  field
0x00  1    0x4B   DIAG_SUBSYS_CMD_F                         (byte 0)
0x01  1    0x0B   DIAG_SUBSYS_FTM                           (byte 1)
0x02  2    ftm_cmd_id   (u16 LE)                            (bytes 2..3)
0x04  2    sub_command  (u16 LE)  -> 0x10xx/0x20xx/0x30xx   (bytes 4..5)
0x06  2    num_tlv      (u16 LE)  count                     (bytes 6..7)
------------------------------- end of 8-byte header -------------------------------
0x08  ..   BODY / RFTEST payload begins here
```

The dispatcher `0xd82714b4` proves the header offsets **FACT**:
```
d82714c0: r17:16 = combine(r0,r1)        ; r16 = r1 = wire pointer (message start)
d82714c8: r3 = memub(r16+0x5)            ; sub_command HIGH  = wire[5]
          r2 = memub(r16+0x4)            ; sub_command LOW   = wire[4]
d82714cc: p0 = cmp.eq(r3,#0x30)          ; 0x30xx family
d82714dc: r1 = r2 | asl(r3,#0x8)         ; sub_command = wire[4] | (wire[5]<<8)
d82714fc: p0 = cmp.eq(r3,#0x10)          ; 0x10xx family
d8271500: cmp.gtu(r2,#0x17)              ; index (wire[4]) bound-check <=0x17
d8271510: r3 = memw(0xc37c649c + idx*4)  ; 0x10xx jump table
d8271514: jumpr r3                        ; -> slot handler (e.g. 0xd86fd0f4)
```

---

## 2. `r18` points at wire+0 (N = 0) — proof chain

### 2.1 Handler → parser register handoff  [FACT]
`0xd86fd0f4` (a 0x10xx slot handler):
```
d86fd0fc: call 0xd86fd0e8               ; header parser wrapper
d86fd100: r0 = add(r29,#0x4)            ; local RESPONSE descriptor
d86fd104: r16 = r0 ; r18 = r1           ; r16=response desc, r18 = parser's returned r1
d86fd108: r17 = memub(r18+0x0a)         ; <<< command_id  (the table index)
d86fd10c: p0 = cmp.gtu(r17,#0x31)       ; bound-check id <= 0x31
d86fd120: r20 = memub(r18+0x11)         ; body byte 0x11
d86fd12c: r19 = memub(r18+0x0f)         ; body byte 0x0f
d86fd134: r18 = memub(r18+0x10)         ; body byte 0x10
```
`0xd86fd0e8`  [FACT]:
```
d86fd0e8: call 0xd8272c10 { r2 = #0xe ; allocframe(#0x0) }   ; parser, size arg r2 = 0x0e
d86fd0f0: dealloc_return                                     ; returns r1 unchanged
```

### 2.2 The parser `0xd8272c10` treats its `r1` as the raw wire  [FACT]
```
d8272c18: r17:16 = combine(r1,r2)       ; r17 = r1 = WIRE ptr, r16 = r2 = size(=0x0e)
d8272c1c: r0 = add(r29,#0x0)            ; local descriptor
d8272c20: r18 = r0 ; r1 = r2            ; r1 = size, for the allocator
d8272c24: call 0xd8151060               ; zero-alloc a scratch buffer of 'size' bytes
d8272c28: r2 = memw(r29+0x8)            ; r2 = scratch buffer ptr (dst)
          memw(r18+0x8) = r2
d8272c30: r2 = memw(r18+0x8)            ; dst
; --- copy the 8-byte header from the wire (r17) into the scratch dst ---
d8272c54: r3 = memub(r17+0x0) ; memb(r2+0x0)=r3    ; dst[0] = wire[0]
d8272c5c: r4 = memub(r17+0x1) ; memb(r2+0x1)=r4    ; dst[1] = wire[1]
d8272c64: r4=memub(r17+0x2); r5=memub(r17+0x3)     ; dst[2],dst[3] = wire[2],wire[3]
d8272c6c: r4=memub(r17+0x4); r5=memub(r17+0x5)     ; dst[4],dst[5] = wire[4],wire[5]
d8272c74: r4=memub(r17+0x6); r5=memub(r17+0x7)     ; dst[6],dst[7] = wire[6],wire[7]
d8272c78: memb(r2+0x8) = r16                        ; dst[8] = size_lo  (0x0e)
d8272c80: memb(r2+0x9) = r0 (=r16>>8)              ; dst[9] = size_hi  (0x00)
```
`r16` in the copy = the parser's **r2 arg = 0x0e** (from `combine(r1,r2)` → r16=r2).
So `dst[8..9] = {0x0e, 0x00}` (NOT wire data). The scratch dst is only the 10-byte
**response-template echo** (8-byte header + a 16-bit length field). `0xd8151060` is a
zero-init vector allocator (`0xd814d760` alloc; writes tag=2 @+0, size @+4, ptr @+8,
flag=1 @+c) — it copies **no** wire body. **FACT.**

### 2.3 Why `r18` MUST be the wire (not the 14-byte scratch)  [FACT]
The handler reads `memub(r18+0x0a)`, `+0x0f`, `+0x10`, `+0x11` (offsets 10, 15, 16, 17).
The scratch dst allocated in the parser is **size 0x0e = 14 bytes** and only 10 bytes are
populated. Offsets 0x0f/0x10/0x11 (15/16/17) are **past the end** of the scratch buffer
and dst[0x0a] would be zero. Since these reads return meaningful body data (command_id,
params), `r18`/returned-`r1` is the **original wire pointer**, and the reads are wire body
bytes. The parser therefore **returns the input wire pointer in r1**; the scratch dst is
consumed separately as the response template. **FACT (by size contradiction).**

### 2.4 Independent confirmation from sibling handlers  [FACT]
- `0xd86f8624`: `call 0xd8272c10` then `r17 = memub(r1+0x0a)` — command_id at wire+0x0a
  read straight off the parser's returned r1 (no reassignment).
- `0xd86fd5c8` (0x1008 family): `r21 = r1`; `r16 = memub(r21+0x0b)` (command_id +0xb here);
  `r20 = memub(r21+0x0a)`; same `cmp.gtu(#0x31)` bound-check.
- Explicit (0x30xx) dispatch tail `0xd8271630`: `r1:0 = combine(r16,r18)` → **`r1 = r16 =
  wire`**, `r0 = r18 = response buf`, `callr r2` (handler). Directly proves the handler ABI
  **r1 = wire pointer @ message start**. **FACT.**

→ Same ABI on both dispatch paths: **handler receives r1 = wire (byte 0 = 0x4B). N = 0.**

---

## 3. Final mapping (0x4B = byte 0)

| access in `0xd86fd0f4` | wire byte # | hex off | meaning |
|---|---|---|---|
| `memub(r18+0x0a)` | **10** | 0x0A | **command_id** (table index, `<=0x31`) **FACT** |
| `memub(r18+0x0f)` | **15** | 0x0F | body param byte (low of packed value) **FACT** |
| `memub(r18+0x10)` | **16** | 0x10 | body param byte **FACT** |
| `memub(r18+0x11)` | **17** | 0x11 | body param byte **FACT** |

### Packed-parameter math at `0xd86fd134`–`0xd86fd148`  [FACT]
```
r19 = memub(r18+0x0f)                         ; byte 15
r18 = memub(r18+0x10)                         ; byte 16
r20 = memub(r18+0x11)                         ; byte 17
r18 = r18 | asl(r20,#0x8)   -> r18 = byte16 | (byte17<<8)          ; 16-bit LE @ wire[16..17]
r19 = r19 | asl(r18,#0x10)  -> r19 = byte15 | (u16 above << 16)    ; packed 24-bit param
```
`r19` (the packed value) is then passed as `r2` into the measure path `0xd8272cd8`
(`d86fd158: call 0xd8272cd8 { r2 = r19 }`). **FACT.** Its semantic (channel/carrier/param
triple) is **INFERENCE** — it is the request's first body parameter block.

---

## 4. Full RFTEST body/header offset map (0x4B = byte 0)

```
byte  0   0x4B                         header  DIAG_SUBSYS_CMD_F           FACT
byte  1   0x0B                         header  DIAG_SUBSYS_FTM             FACT
byte  2-3 ftm_cmd_id  (u16 LE)         header                             FACT
byte  4-5 sub_command (u16 LE)         header  (0x10xx/0x20xx/0x30xx)      FACT (dispatcher)
byte  6-7 num_tlv     (u16 LE)         header  TLV count                  FACT (fmt+dispatch)
--------------------------------------------------------------------------------------
byte  8   body[0]                      (first RFTEST body byte)           FACT (offset)
byte  9   body[1]
byte 10   command_id  <<< memub(r18+0x0a)   RFTEST sub-op / table index   FACT
byte 11   body[3]      (some handlers read command_id here: +0x0b, 0x1008/0x1017)  FACT
byte 12   body[4]
byte 13   body[5]
byte 14   body[6]
byte 15   param byte   <<< memub(r18+0x0f)                                FACT
byte 16   param byte   <<< memub(r18+0x10)                                FACT
byte 17   param byte   <<< memub(r18+0x11)                                FACT
byte 18+  TLV records: { u16 field_id, u16 length, u8 value[length] } ... (INFERENCE, fmt)
```

Notes:
- **command_id offset varies by handler family** (all read off the wire pointer):
  `+0x0a` for the majority (0x1002,0x1003,0x1004,0x1005,0x1007,0x100a,0x100b…,0x1016);
  `+0x0b` for 0x1008 (`0xd86fd5c8`) and 0x1017 (`0xd86fe7e0`);
  `+0x0c` for 0x1006 (`0xd8271c94`) and 0x1010 (`0xd82722c4`). **FACT (slot_correlation.md §1.2).**
- The command_id (wire byte 10 for this handler) is resolved by `0xd8272684`:
  `struct = memw( memw(0xca79a850) + command_id*4 + 0x34 )`; that struct (0x28 bytes) holds
  the runtime unpack/repack pointers. **FACT.**

---

## 5. Answers to the three sub-questions

1. **Who calls / which reg holds the wire, start vs +8?**
   The 0x10xx dispatcher `0xd82714b4` receives the wire in **r1** (→ `r16`), reading
   `sub_command` at wire[4],[5] and dispatching via table `0xc37c649c` (`jumpr`). The
   explicit dispatch tail `0xd8271630` hands the handler **`r1 = r16 = wire`**
   (`combine(r16,r18)`, `callr r2`). The handlers pass that same r1 through
   `0xd86fd0e8` → `0xd8272c10` unchanged. **The pointer is the MESSAGE START (byte 0 =
   0x4B), NOT message+8. N = 0.** **FACT.** (No static caller of `0xd82714b4`/`0xd86fd0f4`
   exists — the dispatcher itself is installed in the runtime DIAG-FTM table and reached by
   `callr`; consistent with `slot_correlation.md`. **FACT.**)

2. **`0xd8151060` and what fills dst beyond offset 9.**
   `0xd8151060` is a **zero-initializing vector allocator** (calls `0xd814d760` to alloc,
   sets descriptor tag/size/ptr/flag; no source pointer). It copies **no** wire body.
   In `0xd8272c10` the scratch dst gets only: `dst[0..7]=wire[0..7]` (header echo) and
   `dst[8..9]={0x0e,0x00}` (the size literal `r2=0x0e`). **dst+0x0a is NOT wire data** —
   it is the response template, left zero. The field at `r18+0x0a` therefore does **not**
   come from this scratch buffer; it comes from the **wire body directly**, because
   `r18` = the returned wire pointer. **FACT.**

3. **Final mapping.**
   **`memub(r18 + 0x0a) == wire byte number 10` (0x0A), with 0x4B = byte 0.** **FACT.**
   It is the RFTEST `command_id` / sub-op table index (`<= 0x31`).

---

## 6. Key VAs

```
0xd82714b4   0x10xx/0x20xx/0x30xx dispatcher   (reads wire[4],[5]; jumpr table 0xc37c649c)   FACT
0xd8271630   explicit dispatch tail            (r1=r16=wire ; r0=resp ; callr r2)            FACT
0xd8272bb0   response-descriptor initializer   (copies 16B template into r0)                 FACT
0xd86fd0f4   0x10xx slot handler (0x1002 fam)  (r18=wire ; memub(r18+0xa)=command_id)        FACT
0xd86fd0e8   header-parser wrapper             (call parser ; r2=0x0e ; return r1=wire)      FACT
0xd8272c10   header parser / response template (r17=wire ; copies wire[0..7] to scratch)     FACT
0xd8151060   zero-init vector allocator        (no wire-body copy)                           FACT
0xd814d760   heap allocator                                                                  FACT
0xd8272684   command_id resolver               (memw(0xca79a850)+id*4+0x34)                  FACT
0xd8272cd8   measure/capture path              (r2 = packed param from wire[15..17])         FACT
0xca79a850   runtime command-table base pointer                                              FACT
0xc37c649c   0x10xx jump table (RW data seg, not in code bin)                                FACT
```
