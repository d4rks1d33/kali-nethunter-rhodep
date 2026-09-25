# base_and_safe_B — RF-test module base B, why sub 0x1004 crashes, and the non-crashing wrapper for COMMAND_CAPABILITY

**Target:** SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Code base:** `/tmp/modemre/clade_dec_full.bin` (VA 0xd8000000), tool `dis.sh <va> <len>`.
**Rodata:** `rd.py <va> <n>` (b21 @0xc3553000, b23 @0xc8b6a000).
**Cross-refs:** `reg_order_B.md`, `command_id_map_B.md`, `wire_offset_A.md`, `wire_offset_B.md`,
`slot_correlation.md`, `sub_command_map.md`, `tech_state_gate.md`.

**Legend:** **FACT** = read directly from disasm/bytes at a cited VA · **INFERENCE** = deduced with
evidence · **UNKNOWN** = only resolvable from a live device (RAM/runtime).

---

## 0. TL;DR — DIRECT ANSWERS

1. **Base B is set at RUNTIME, not statically. There is NO static immediate assigning B.**
   The command_id→module binding lives in the RAM byte table `@0xca9ef490` (resolver `0xd84aa9e0`)
   and the master command table `@0xca79a850` (resolver `0xd8272684`), both populated at runtime.
   The relative order is FACT (reg_order_B.md): `RADIO_CONFIG=B+0, RX_MEASURE=B+1, IQ_CAPTURE=B+5,
   COMMAND_CAPABILITY=B+11`. **The registration "counter" is not a static integer initialized to a
   constant** — it is the position at which each command's 0x28-byte descriptor is written into the
   malloc'd table `@0xca79a850` (alloc `0xd8272368` `##0x954c`; default-fill `0xd82723c0`).
   **INFERENCE (strong): B = 0** because `0xd8182640` is the FIRST module init from the master
   registrar `0xd84aa03c`, so its commands take the lowest command_ids in registration order.
   ⇒ **typical / expected values:** RADIO_CONFIG = **0**, RX_MEASURE = **1**, IQ_CAPTURE = **5**,
   COMMAND_CAPABILITY = **0x0b**. Confirm live via COMMAND_CAPABILITY CMD_MASK (§5).

2. **Why sub 0x1004 crashes even though "query-safe":** it is NOT a query. Its wrapper
   `0xd86fd328` runs the **RF-execution path** after the resolver: `0xd826d7b0` / `0xd826d9b4`,
   which call `0xd827923c` → **`memw(0xca79c494)`** (the *current active carrier context* pointer)
   and dereference the per-carrier accumulator region at **`0xca6e3228`**. `0xca79c494` is set by
   `0xd8279264` only when a carrier is **applied via RADIO_CONFIG**, NOT by TECH_ENTER. After only
   TECH_ENTER it is still **NULL/stale**, and the exec path dereferences it → **modem SSR**.
   The fault is NOT at bytes 15/16/17 and NOT in the resolver. With `tlv_count=0` the envelope
   `0xd8272c10` and the command_id read `memub(req+0xa)` succeed; the crash is the
   **uninitialized per-carrier pointer** in the post-resolver exec path. **FACT (VAs below).**

3. **Which wrapper does NOT crash and still runs the resolver:** **NONE of the 0x10xx / 0x20xx
   wrappers is purely query-safe** — every wrapper that reaches `0xd8272684` also invokes an
   RF-exec path that touches `0xca79c494` / `0xca6e3228` (per-carrier state). See §3 table.
   ⇒ To reach COMMAND_CAPABILITY **without SSR you must first establish a carrier** (TECH_ENTER +
   RADIO_CONFIG so `0xca79c494` is non-NULL), THEN send the wrapper with **command_id = B+11 =
   0x0b**. The **simplest wrapper** that reads command_id at byte 10, calls `0xd8272684`, and does
   the LEAST after it is **sub_command 0x1002** (`0xd86fd0f4`): single resolver call, single
   packed-param read (bytes 15..17), single exec call `0xd8272cd8`. Full wire layout in §3.3.
   (0x1003 `0xd86fd230` is an equally simple alternative; it too needs the carrier context.)

4. **`memub(req+0xf/0x10/0x11)` = wire bytes 15/16/17 = a packed 24-bit body parameter**
   (`p = b15 | (b16 | b17<<8)<<16`), the request's first parameter block (channel/carrier/param
   triple). In the 0x1004/0x1005/0x1007 family the low 16 bits (b15|b16<<8) are **range-checked
   `<= 0x513`**; if exceeded, the handler calls error `0xc0988c7c`. **With these bytes = 0 the
   value is 0, which PASSES the `<=0x513` check — so bytes 15/16/17 = 0 do NOT cause the crash.**
   They are a real body parameter (not a tlv-count and not a sub-id). Leaving them 0 is safe for
   the envelope; the crash is the carrier-context pointer, not these bytes. **FACT.**

---

## 1. BASE B — WHY IT IS RUNTIME, AND THE COUNTER (Q1)

### 1.1 Master registrar and the first module (FACT)
```
dis.sh 0xd84aa03c 0x14
   call 0xd8182640   ; MODULE 0 = RF-TEST (RADIO_CONFIG..COMMAND_CAPABILITY)  ← FIRST
   call 0xd81793b0   ; MODULE 1
   call 0xd86c3ee8   ; MODULE 2
```
`0xd8182640` installs the 12 RF-test commands into 12-byte slots (module ctx @0xca65d640).
slot_index = registration ordinal = `(slot_off-0xc)/0xc`. **FACT** (reg_order_B.md §1.2).

### 1.2 The command_id resolver reads a RUNTIME table (FACT)
```
dis.sh 0xd8272684 0x30
   d8272688: p0 = cmph.gtu(r0,#0x31)                 ; bound id <= 0x31
   d8272694: r3 = memw(##0xca79a850)                 ; master table base (RAM ptr)
   d8272698: r3 = addasl(r3,r2,#0x2)                 ; + id*4
   d827269c: r3 = memw(r3+#0x34)                     ; struct = *(base + id*4 + 0x34)
   d82726a0: if (r3 != 0) jump 0xd82726b4            ; else error, return 0
```
The struct at `id*4+0x34` is the per-command descriptor. **`@0xca79a850` is malloc'd
(`0xd8272368`, `##0x954c`) and only default-filled (`0xd82723c0`, 0..0x31, 0x28 B each) — no
names/handlers baked in.** **FACT.** ⇒ the command_id→command binding is **runtime**.

### 1.3 The module→base byte table is also RUNTIME (FACT)
```
dis.sh 0xd84aa9e0 0x80
   r24 = ##0xca9ef490 ; r2 = memb(r24 + command_id) ; if (r2 != group) ...
```
`@0xca9ef490` is populated at runtime. **No static immediate assigns B.** **FACT.**

### 1.4 The "counter" initial value (Q1 direct)
- **There is NO static counter constant.** The command_id is the *table index* into `@0xca79a850`;
  each command's descriptor is written at runtime into `base + id*4 + 0x34`.
- **Typical/expected value: B = 0** (INFERENCE, strong): `0xd8182640` is the first module init, and
  its commands are the primary RFTEST set, so they occupy the lowest ids in registration order.
  ⇒ RADIO_CONFIG=0, RX_MEASURE=1, IQ_CAPTURE=5, **COMMAND_CAPABILITY=0x0b**.
- **Absolute B is UNKNOWN statically** (needs @0xca9ef490 / @0xca79a850 from RAM, or a live
  COMMAND_CAPABILITY CMD_MASK query, §5).

**Absolute command_ids (with B=0, INFERENCE):**
`RADIO_CONFIG=0x00, RX_MEASURE=0x01, IQ_CAPTURE=0x05, COMMAND_CAPABILITY=0x0b`.

---

## 2. WHY sub 0x1004 CRASHES (Q2 / Q4)

### 2.1 The envelope with tlv_count=0 is BENIGN (FACT)
`0xd86fd0e8` → `0xd8272c10` copies only the 8-byte wire header into a scratch response template
and returns `r1 = wire pointer unchanged`. It does NOT read `num_tlv` (bytes 6..7) and does NOT
deref any per-carrier state. `tlv_count=0` is fine for the envelope. **FACT** (wire_offset_B.md §2).
```
dis.sh 0xd8272c10 0xd0   ; header echo only: dst[0..7]=wire[0..7], dst[8..9]={0x0e,0x00}; returns r1=wire
```

### 2.2 The command_id read and resolver both SUCCEED with tlv_count=0 (FACT)
`0xd86fd328` (0x1004 wrapper):
```
d86fd330: call 0xd86fd0e8            ; envelope; r20 = wire ptr
d86fd33c: r16 = memub(r20+#0xa)      ; command_id = wire byte 10           <-- read OK
d86fd340: p0 = cmp.gtu(r16,#0x31)    ; bound-check id <= 0x31
d86fd360: r18 = memub(r20+#0xf)      ; byte 15 (param lo)
d86fd358: r21 = memub(r20+#0x11)     ; byte 17
d86fd368: r19 = memub(r20+#0x10)     ; byte 16
d86fd364: call 0xd827dfc8            ; decode(r0=&stack, r1=id) — BENIGN (returns; writes to stack)
d86fd370: r1 = memub(r20+#0x12)      ; byte 18
d86fd374: r2 = memub(r20+#0x13)      ; byte 19
d86fd37c: r2 = sxth(byte18|byte19<<8)
d86fd384: p0 = cmp.gtu(r2,##0x513)   ; bound-check
d86fd394: if (p0) call 0xc0988c7c    ; error only if > 0x513  (with zeros: p0=false, NOT taken)
d86fd3a4: call 0xd8272684            ; command_id resolver  <-- reached, returns struct or 0 (guarded)
d86fd3ac: call 0xd827e064            ; repack-template alloc (guarded r1!=0)
d86fd3f8: call 0xd826d7b0            ; <<< RF-EXECUTION path A
d86fd40c: call 0xd826d9b4            ; <<< RF-EXECUTION path B
```
`0xd827dfc8` with a non-NULL r0 (stack addr) just returns (writes to the stack local). The resolver
`0xd8272684` is guarded (`id<=0x31`, `struct!=0`). **Neither faults with zeros.** **FACT.**

### 2.3 The ACTUAL crash: uninitialized per-carrier pointer in the exec path (FACT)
`0xd826d7b0` (and the RF-exec core `0xd826d3e4` on the 0x1002 path) both do:
```
dis.sh 0xd826d7b0 0x60
   d826d7cc: call 0xd827dfac
   d826d7e8: call 0xd82735cc              ; carrier-id clamp (<=0x13) — benign
   d826d800: r2 = memw(r3=##0xca6e3228)   ; <<< deref per-carrier accumulator region

dis.sh 0xd826d3e4 0x50   (0x1002 exec core)
   d826d40c: call 0xd827923c              ; returns *current carrier ctx* = memw(0xca79c494)
   d826d42c: r2 = memw(r3=##0xca6e3228)   ; <<< same per-carrier region
```
`0xd827923c` (FACT):
```
dis.sh 0xd827923c 0x70
   d8279240: r0 = memw(##0xca79c494)      ; the ACTIVE carrier context pointer
   d8279244: if (r0 != 0) return r0
   d8279248: else -> log error, return the (NULL) value
```
`0xca79c494` is written only by `0xd8279264` (`memw(##0xca79c494) = r0`), which is reached when a
**carrier is applied (RADIO_CONFIG)** — NOT by TECH_ENTER. After only TECH_ENTER, `0xca79c494` is
**NULL/stale**; the exec path uses the returned pointer (and the 0xca6e3228-indexed per-carrier
arrays) as an active RF-driver context and **faults → modem SSR**. **FACT.**

**⇒ 0x1004 is an EXECUTE-class command (like 0x1002), not a query. The "query-safe" label was
wrong. The crash is the missing carrier context, not bytes 15/16/17 and not the resolver.**

### 2.4 What bytes 15/16/17 are (Q4, FACT)
```
0x1002 path (dis.sh 0xd86fd0f4):
   r19 = memub(r18+#0xf)                     ; byte 15
   r18 = memub(r18+#0x10)                    ; byte 16
   r20 = memub(r18+#0x11)                    ; byte 17
   r18 = byte16 | (byte17<<8)                ; 16-bit LE @ [16..17]
   r19 = byte15 | (r18 << 16)                ; packed 24-bit param -> r2 into 0xd8272cd8
```
So bytes 15/16/17 = a **packed 24-bit body parameter** (the request's first parameter block —
INFERENCE: channel/carrier/param triple). In 0x1004/0x1005/0x1007 the low 16 bits are range-checked
`<= 0x513`. **They are NOT a tlv-count and NOT a sub-id.** **Value 0 PASSES the check** (0 <= 0x513),
so `bytes 15/16/17 = 0` do not crash. Correct values are command-specific (a valid channel/param);
0 is acceptable for reaching the resolver. **FACT.**

---

## 3. THE WRAPPERS THAT RUN THE RESOLVER (Q3)

### 3.1 Every resolver-caller also runs an RF-exec (per-carrier) path — FACT
0x10xx dispatch: `dis.sh 0xd82714b4 0x60`; stub table `rd.py 0xc37c649c 24`.

| sub_command | wrapper VA  | reads cmd_id @ | calls resolver 0xd8272684 | post-resolver EXEC (per-carrier deref) |
|-------------|-------------|----------------|---------------------------|-----------------------------------------|
| 0x1002      | 0xd86fd0f4  | byte 0x0a      | yes (d86fd130)            | 0xd8272cd8 → 0xd826d3e4 (0xca79c494/0xca6e3228) |
| 0x1003      | 0xd86fd230  | byte 0x0a      | yes (d86fd290)            | 0xd826d67c → 0xd827923c (0xca79c494)     |
| 0x1004      | 0xd86fd328  | byte 0x0a      | yes (d86fd3a4)            | 0xd826d7b0 / 0xd826d9b4 (0xca6e3228)     |
| 0x1005      | 0xd86fd474  | byte 0x0a      | yes (d86fd4e4)            | 0xd826db54                              |
| 0x1007      | 0xd86fdb88  | byte 0x0a      | yes (d86fdbb8)            | 0xd826e090                              |
| 0x1008      | 0xd86fd5c8  | byte 0x0b (!)  | yes x2 (d86fd62c/634)     | 0xd81578f0 + carrier calls              |
| 0x1009      | 0xd86fd80c  | byte 0x0a      | (deep)                    | many body bytes 0x18..0x1f + exec       |
| 0x100a      | 0xd86fda68  | byte 0x0a      | (deep)                    | exec + 0xc0988c7c bound                 |
| 0x100d      | 0xd86fde80  | byte 0x0a      | yes (d86fdec4)            | 0xd828067c → 0xd82798f8 (per-tech ctx)  |

Non-resolver 0x10xx slots (0x100b/0x100c/0x100e/0x100f/0x1006/0x1010..0x1012) never reach
`0xd8272684`, so they cannot address COMMAND_CAPABILITY by command_id. **FACT.**
0x20xx (e.g. 0x2002 = 0xd8288a68) is byte-identical to 0x1002 (same 0xd8272cd8 exec). **FACT.**

### 3.2 Conclusion: NO purely-query wrapper exists among these
There is **no** 0x10xx/0x20xx wrapper that runs the resolver and skips the per-carrier exec.
**INFERENCE (evidence: all 9 resolver-callers disassembled).** COMMAND_CAPABILITY's own unpacker
(0xd81849ec, reg_order_B.md §2.4) is invoked by the resolver-selected descriptor, but the *wrapper*
that carries it always performs an exec that needs a live carrier context.

⇒ **To query COMMAND_CAPABILITY without SSR: FIRST establish a carrier so `0xca79c494` is non-NULL
(TECH_ENTER LTE, THEN RADIO_CONFIG with a valid band/channel), THEN send the wrapper with
command_id = B+11 = 0x0b.** With the carrier context populated, the exec path no longer NULL-derefs.
This matches the live symptom (crash after TECH_ENTER-only; the missing piece is RADIO_CONFIG).

### 3.3 Simplest wrapper + full wire layout — sub_command 0x1002 (COMMAND_CAPABILITY = cmd_id 0x0b)
`0x1002` (`0xd86fd0f4`) is the minimal resolver path: one resolver call, one packed-param read,
one exec call. Wire (0x4B = byte 0; command_id at byte 10; num_tlv at 6..7):

```
byte  8  9 10 11        15 16 17
 4B 0B 27 00 02 10 00 00 XX XX 0B XX YY YY YY
 |  |  |     |     |        |  |
 |  |  |     |     |        |  +-- byte 10 = command_id = 0x0B (COMMAND_CAPABILITY, B=0)  <<<
 |  |  |     |     |        +----- byte  9 = body[1]  (0x00)
 |  |  |     |     +-------------- byte  6..7 = num_tlv = 0x0000
 |  |  |     +-------------------- byte  4..5 = sub_command = 0x1002 (LE: 02 10)
 |  |  +-------------------------- byte  2..3 = ftm_cmd  = 0x0027 (LE: 27 00) = LTE
 |  +----------------------------- byte  1    = 0x0B DIAG_SUBSYS_FTM
 +-------------------------------- byte  0    = 0x4B DIAG_SUBSYS_CMD_F
```
Byte assignments (fill the 8..17 window explicitly):
```
byte  8 = 0x00   body[0]              (don't-care for envelope)
byte  9 = 0x00   body[1]
byte 10 = 0x0B   command_id           = COMMAND_CAPABILITY (B+11, B=0)   <-- REQUIRED
byte 11 = 0x00   body[3]              (0x1002 reads cmd_id at 0x0a, not 0x0b)
byte 15 = 0x00   packed param lo      (<=0x513 → OK)
byte 16 = 0x00   packed param mid
byte 17 = 0x00   packed param hi
```
Concatenated (18 bytes, num_tlv=0):
```
4B 0B 27 00 02 10 00 00 00 00 0B 00 00 00 00 00 00 00
```
**Precondition (MANDATORY to avoid SSR):** run TECH_ENTER (LTE) THEN RADIO_CONFIG (valid
band/channel) first, so `memw(0xca79c494) != 0`. Then this frame resolves command_id 0x0B and
runs COMMAND_CAPABILITY without the NULL-carrier fault. **INFERENCE (from §2.3 mechanism).**

If B != 0 on the live device, set byte 10 = B+0x0b (read CMD_MASK to confirm, §5).

---

## 4. FACT / INFERENCE / UNKNOWN — with VAs

**FACT**
- Resolver `0xd8272684`: `memw(memw(0xca79a850)+id*4+0x34)`, `id<=0x31`, `struct!=0` guarded.
- Master table `@0xca79a850` malloc'd `0xd8272368` (`##0x954c`), default-filled `0xd82723c0`.
- command_id→module byte table `@0xca9ef490` (resolver `0xd84aa9e0`) — RAM-populated.
- Envelope `0xd8272c10` (via `0xd86fd0e8`) echoes 8-byte header only; returns r1=wire; ignores
  num_tlv; no per-carrier deref. tlv_count=0 is benign for the envelope.
- 0x1004 wrapper `0xd86fd328`: cmd_id @ byte 0x0a; decode `0xd827dfc8` (benign); resolver
  `0xd8272684` @ d86fd3a4; EXEC `0xd826d7b0`/`0xd826d9b4` after.
- EXEC per-carrier deref: `0xd827923c` returns `memw(0xca79c494)`; `0xca6e3228` per-carrier region;
  `0xca79c494` written only by `0xd8279264` (RADIO_CONFIG/carrier-apply), NOT by TECH_ENTER.
- Bytes 15/16/17 = packed 24-bit body param; low16 range-checked `<=0x513` (0 passes).
- 0x1002 `0xd86fd0f4` and 0x2002 `0xd8288a68` share exec `0xd8272cd8` → `0xd826d3e4`.
- All 9 resolver-calling 0x10xx wrappers also run a per-carrier exec path (§3.1 table).
- Registration order (reg_order_B.md): RADIO_CONFIG=B+0, RX_MEASURE=B+1, IQ_CAPTURE=B+5,
  COMMAND_CAPABILITY=B+11.

**INFERENCE**
- B = 0 (first-registered module) ⇒ RADIO_CONFIG=0x00, RX_MEASURE=0x01, IQ_CAPTURE=0x05,
  COMMAND_CAPABILITY=0x0b. Not proven — B is a runtime binding.
- The packed 24-bit param (bytes 15..17) is a channel/carrier/param triple.
- To reach COMMAND_CAPABILITY safely, establish a carrier (TECH_ENTER + RADIO_CONFIG) first.

**UNKNOWN (live only)**
- Absolute B (thus exact integer command_ids). Close via COMMAND_CAPABILITY CMD_MASK (§5).
- Whether any build variant NULL-guards `0xca79c494` in the exec path (this build does not; it
  logs then derefs).

---

## 5. CLOSE IT LIVE (COMMAND_CAPABILITY CMD_MASK)

After TECH_ENTER(LTE)+RADIO_CONFIG(valid carrier), send 0x1002 with command_id=0x0b and one TLV
`{field 1 QUERY_COMMAND = 0xFFFFFFFF}` (field-tbl @0xc37c03b4, unpacker 0xd81849ec):
```
4B 0B 27 00 02 10 01 00  00 00 0B 00 00 00 00 00 00 00  01 00 04 00 FF FF FF FF
```
Response CMD_MASK (field 3): bit N set ⇒ command_id N registered. Bit 0 ⇒ RADIO_CONFIG present,
bit 5 ⇒ IQ_CAPTURE, bit 0x0b ⇒ COMMAND_CAPABILITY itself ⇒ confirms **B=0**. Then QUERY_COMMAND=N
+ PROPERTY_MASK to name each bit. **FACT of the format** (cmd_capability_map.md / sub_command_map.md §5).

---

## 6. REPRODUCE

```
dis.sh 0xd84aa03c 0x14     # master registrar: 0xd8182640 first (module 0 = RF-TEST)     FACT §1.1
dis.sh 0xd8272684 0x30     # command_id resolver (@0xca79a850, id*4+0x34, guarded)        FACT §1.2
dis.sh 0xd82723c0 0x40     # default-fill 0..0x31 (no names) — table is runtime           FACT §1.2
dis.sh 0xd84aa9e0 0x80     # command_id->module byte table @0xca9ef490 (RAM)              FACT §1.3
dis.sh 0xd86fd328 0x120    # 0x1004 wrapper: cmd_id@0xa, decode, resolver, EXEC           FACT §2.2
dis.sh 0xd827dfc8 0xa0     # decode (benign with non-NULL r0)                             FACT §2.2
dis.sh 0xd8272c10 0xd0     # envelope: header echo only, returns wire ptr                 FACT §2.1
dis.sh 0xd826d7b0 0x60     # 0x1004 EXEC path A: derefs 0xca6e3228                        FACT §2.3
dis.sh 0xd826d3e4 0x50     # 0x1002 EXEC core: 0xd827923c(0xca79c494) + 0xca6e3228        FACT §2.3
dis.sh 0xd827923c 0x70     # returns memw(0xca79c494) (active carrier ctx)                FACT §2.3
dis.sh 0xd86fd0f4 0x120    # 0x1002 wrapper (simplest resolver path)                      FACT §3.3
rd.py  0xc37c649c 24       # 0x10xx stub table (24)                                       FACT §3.1
```
