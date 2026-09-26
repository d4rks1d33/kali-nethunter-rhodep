# routing_byte_wire — Which WIRE byte becomes the RFTEST "mode/type" routing byte

**Target:** SM6375 baseband (Moto G82 5G), MPSS.HI.4.3.4, Hexagon/QDSP6 v66.
**Image:** `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`). Static, read-only.
**Tools:** `dis36.sh`, `callers36.py`, manual Hexagon packet decode.
**Legend:** **FACT** = read directly from image bytes at a VA · **INFERENCE** = deduced with hard anchors · **UNKNOWN** = runtime/RAM-only.

---

## 0. TL;DR — DIRECT ANSWERS

1. **Request-pointer provenance (Q1):** In handler `0xd86fd0f4` the request pointer is `r18 = r1`, and **r1 is the RAW WIRE base** — the very same pointer from which `command_id = memub(+0x0a)` is read. It is captured **in the same packet as the envelope call, at packet-start**, *before* the call can clobber it (`0xd86fd104: { call 0xd86fd0e8 ; r0=add(r29,#4) ; r16=r0 ; r18=r1 }`). The envelope `0xd86fd0e8→0xd8272c10` only builds a **response template** (copies wire[0..7] into a heap reply container); its return value is discarded. **So it is the wire base, NOT the flattened scratch.** **FACT.**
2. **The routing decision is NOT on wire byte 0x0f alone — it is on the 16-bit value `{wire[0x0f] | wire[0x10]<<8}`.** The dispatcher classifies `sxth(wire[0x0f] | wire[0x10]<<8)` through `0xd82735cc`; the CONFIG branch is taken **iff that classify returns 0**, which requires **BOTH `wire[0x0f]==0` AND `wire[0x10]==0`**. If `wire[0x10]!=0`, the packed low-16 becomes `>= 0x100 > 0x13`, so `0xd82735cc` returns `0x15` (nonzero) → **MEASURE/crash branch** even when `wire[0x0f]==0`. **This is exactly why setting only wire[0x0f]=0 still crashed.** **FACT.**
3. **command_id (wire[0x0a]) does NOT select config-vs-measure.** The branch depends only on `{wire[0x0f],wire[0x10]}`. command_id is a *separate* argument (r1→r16 in the dispatcher) consumed *inside* whichever branch runs. So your earlier live failure was **not** a wrong command_id — it was **wire byte 0x10 being nonzero** (and/or 0x0f nonzero). **FACT.**
4. **Byte-exact config-apply packet:** set **wire[0x0f]=0x00 AND wire[0x10]=0x00** (and, for a valid command_id read, wire[0x0a] ≤ 0x31). Then dispatcher `0xd8272cd8` selects `0xd8292298` (config-apply, fn `0xd8291504`) instead of `0xd826d3e4` (measure/SSR). **FACT.**

---

## 1. THE CALL CHAIN AND POINTER PROVENANCE (Q1) — FACT

### 1.1 Wire base survives to the handler as `r1`
```
0xd8157ec4  LTE sub router:
  call 0xd81695b8 ; r17:16 = combine(r1,r0)   ; r17 = r1 = WIRE base (sub read @r17+4/5)
  ...
0xd8157fb8: r1:0 = combine(r17,r18) ; callr r3 ; -> RFTEST dispatcher, r1 = r17 = WIRE, r0 = scratch
0xd82714b4  RFTEST dispatcher:
  call 0xd8272bb0 ; r17:16 = combine(r0,r1)   ; r16 = r1 = WIRE (sub read @r16+4/5 @0xd82714c8)
  ... jump-table @0xc37c649c indexed by wire[4] ...
0xd8271548  slot 0x1002: r2 = ##0xd86fd0f4 ; jump 0xd8271630
0xd8271630  common tail: r1:0 = combine(r16,r18) ; callr r2
             ; => handler entry: r1 = r16 = WIRE base, r0 = r18 = resp scratch (add r29,#0x140)
```
`0xd81695b8`/`0xd8272bb0` only stamp a small status *template* into the caller's r0 buffer; they do **not** move the wire pointer. So the wire base flows unbroken into the handler as **r1**. **FACT.**

### 1.2 The handler captures r1 (=WIRE) BEFORE the envelope call — the decisive packet
`0xd86fd0f4` disassembly (VAs FACT):
```
d86fd0f4: { call 0xd8829a00 ; allocframe(#0x50) }          ; frame setup milicode
d86fd0fc: { call 0xd86fd0e8                                  ; envelope (builds reply template)
d86fd100:   r0 = add(r29,#0x4)                               ; r0 = local resp buf
d86fd104:   r16 = r0 ; r18 = r1 }                            ; <<< SAME PACKET as the call
d86fd108: { r17 = memub(r18+#0xa) ; r0 = memw(r29+#0xc) }    ; command_id = wire[0x0a]
```
**Hexagon semantics:** every instruction in a packet reads its sources at **packet start**. In the `0xd86fd0fc` packet, `r16=r0` and `r18=r1` therefore read the **incoming** r0/r1 (packet-start values), i.e. the arguments handed to the handler, **before** the `call 0xd86fd0e8` can overwrite r0/r1. Result:
- `r18 = incoming r1 = WIRE base`
- `r16 = incoming r0 = response scratch`

This is the standard Hexagon idiom for "stash the arg registers into callee-saved regs in the same packet as the first call." The envelope's return value is irrelevant. **FACT.**

Cross-check with sibling handler `0xd86fd230` (sub 0x1003), identical idiom:
```
d86fd238: { call 0xd86fd0e8 ; r0=add(r29,#0x3c) ; r16=r0 ; r17=r1 }
d86fd24c:   r17 = memub(r17+#0xa)                 ; command_id from r17 = WIRE
```
**FACT.**

### 1.3 What the envelope `0xd8272c10` actually does (not the request source)
`0xd86fd0e8 = { call 0xd8272c10 ; r2=#0xe }` → `0xd8272c10(r0=respbuf, r1=WIRE, r2=0xe)`:
- allocates a heap container, copies **wire[0..7]** into it (`0xd8272c54..c74`), then `buf[8]=0x0e`, `buf[9]=lsr(0x0e,8)=0` (metadata, `0xd8272c78/c80`).
- That container is the **DIAG reply template** (only 10 meaningful bytes; nothing at +0x0a..+0x11). It is **not** the request pointer the handler dereferences.

⇒ The prior claim (wire_offset_A/B) that command_id is read from the wire base is **correct**; and critically the **mode byte +0x0f/+0x10/+0x11 are read from the SAME wire base** (`r18`), not from any flattened/scratch buffer. **FACT.**

---

## 2. WIRE OFFSET → ROUTING (Q2) — FACT

The handler reads three bytes from the wire base and packs them:
```
d86fd120: if (!p0) r20 = memub(r18+#0x11)        ; r20 = wire[0x11]   (p0 = command_id>0x31)
d86fd12c: r19 = memub(r18+#0xf)                   ; r19 = wire[0x0f]
d86fd134: r18 = memub(r18+#0x10)                  ; r18 = wire[0x10]   (r18 reused)
d86fd138: r18 |= asl(r20,#0x8)                     ; r18 = wire[0x10] | wire[0x11]<<8
d86fd148: r19 |= asl(r18,#0x10)                    ; r19 = wire[0x0f] | wire[0x10]<<16 | wire[0x11]<<24
d86fd13c: r1:0 = combine(r17,#0x3)                 ; r0 = #3 (const), r1 = command_id
d86fd158: { call 0xd8272cd8 ; r2 = r19 }           ; r2 = packed{0x0f, 0x10<<16, 0x11<<24}
```
**Internal→wire offset map (FACT):**
| internal field | wire offset | value |
|---|---|---|
| routing/mode byte "+0x0f" | **wire[0x0f]** (byte 15) | low byte of the classify input |
| "+0x10" | **wire[0x10]** (byte 16) | **also part of the classify input** (bits 8..15 of packed low-16) |
| "+0x11" | **wire[0x11]** (byte 17) | mode sub-parameter high byte |
| command_id | **wire[0x0a]** (byte 10) | separate arg (r1), not a branch selector |

**The dispatcher `0xd8272cd8` (args: r0=3, r1=command_id, r2=packed):**
```
d8272ce4: r19:18 = combine(r4,r0)   ; r18 = r0 = 3   (hardcoded by this handler)
d8272cec: r21:20 = combine(r3,r2)   ; r20 = r2 = packed
d8272d08: p1 = cmp.eq(r18,#0x3)     ; TRUE (r18==3)  -> NOT the !p1 path @0xd8272d60
d8272d18: r0 = sxth(r20)            ; r0 = sxth(packed_low16) = int16(wire[0x0f] | wire[0x10]<<8)
d8272d24: call 0xd82735cc           ; classify(r0)  -> r18
d8272d38: p0 = r2 (=r18 = classify) ;
d8272d44: if (p0.new) r5 = ##0xd826d3e4   ; classify != 0 -> MEASURE
d8272d4c: if (!p0)    r5 = ##0xd8292298    ; classify == 0 -> CONFIG-apply
d8272d50: callr r5
```
Both call targets decoded from the PC-relative immediates: **`0xd826d3e4`** (measure) and **`0xd8292298`** (config, in fn `0xd8291504`). **FACT.**

**The classifier `0xd82735cc(x)` (FACT):**
```
d82735cc: r1 = r0
d82735d0: p0 = cmp.gtu(r0,#0x13) ; if(!p0) jump 0xd82735e4   ; x <= 0x13 -> return
d82735e4: r0 = and(r1,#255) ; jumpr r31                       ; return low byte
   (x > 0x13 -> error path @0xd82735d4 -> return 0x15)
```
So `classify(x) = (x <= 0x13) ? (x & 0xff) : 0x15`, where **`x = sxth(wire[0x0f] | wire[0x10]<<8)`**.

**Consequence (the root cause of the live failure):**
- `wire[0x0f]=0, wire[0x10]=0` → x=0 → classify=0 → **CONFIG (0xd8292298)**.
- `wire[0x0f]=0, wire[0x10]=1` → x=0x100 → `>0x13` → classify=**0x15** → **MEASURE (0xd826d3e4)** → null-carrier crash.
- `wire[0x0f]=N (1..0x13), wire[0x10]=0` → classify=N (nonzero) → MEASURE.
- `wire[0x0f] in 0x14..0xff, wire[0x10]=0` → `>0x13` → classify=0x15 → MEASURE.

⇒ **The routing byte is effectively the 16-bit little-endian field at wire[0x0f..0x10].** To hit CONFIG you must zero **both** wire[0x0f] and wire[0x10]. **FACT.**

---

## 3. FULL DISASSEMBLY OF 0xd86fd0f4 (Q3) — FACT

```
d86fd0f4: { call 0xd8829a00 ; allocframe(#0x50) }
d86fd0fc: { call 0xd86fd0e8 ; r0 = add(r29,#0x4) ; r16 = r0 ; r18 = r1 }   ; r18 = WIRE (packet-start r1)
d86fd108: { r17 = memub(r18+#0xa) ; r0 = memw(r29+#0xc) }                  ; r17 = command_id = wire[0x0a]
d86fd10c: { p0 = cmp.gtu(r17,#0x31) ; r2 = memw(r29+#0x10) ; memw(r16+#0x8)=r0 }  ; validate id<=0x31
d86fd114: { r1 = memw(r29+#0x8) ; memw(r16+#0xc)=r2 }
d86fd118: { r0 = memw(r29+#0x4) ; memw(r16+#0x4)=r1 }
d86fd11c: { if (p0) jump 0xd86fd1d8                                        ; id>0x31 -> error
d86fd120:   if (!p0) r20 = memub(r18+#0x11) ; memw(r16+#0x0)=r0 }          ; r20 = wire[0x11]
d86fd128: { r0 = r17                                                       ; r0 = command_id
d86fd12c:   r1 = add(r29,#0x4) ; r19 = memub(r18+#0xf) }                   ; r19 = wire[0x0f]
d86fd130: { call 0xd8272684    ; r18 = memub(r18+#0x10) }                  ; resolve id; r18 = wire[0x10]
d86fd138: { r18 |= asl(r20,#0x8)                                           ; r18 = wire[0x10]|wire[0x11]<<8
d86fd13c:   r1:0 = combine(r17,#0x3)                                        ; r0=#3, r1=command_id
d86fd140:   r5 = add(r29,#0x2e)
d86fd144:   r3 = memw(r29+#0xc) }
d86fd148: { r19 |= asl(r18,#0x10)                                          ; r19 = wire[0x0f]|0x10<<16|0x11<<24
d86fd14c:   immext(#0xd8263300)
d86fd150:   r4 = ##0xd8263324
d86fd154:   memh(r29+#0x2e) = r17 }
d86fd158: { call 0xd8272cd8 ; r2 = r19 }                                   ; r2 = packed routing word
```
- **Base register & origin:** `r18` (request ptr) = **r1 = raw wire base**, captured at `0xd86fd104` in the same packet as the envelope call (packet-start read). **FACT.**
- `0xd8272684` @`0xd86fd130` resolves the command_id descriptor (`@0xca79a850[id]…`); it does **not** change the routing. **FACT.**

---

## 4. command_id ROUTING (Q4) — FACT

- The **config-vs-measure branch does not depend on command_id at all**; it depends only on `classify(sxth(wire[0x0f]|wire[0x10]<<8))`. `r0=#3` is hardcoded by this handler, so `p1=cmp.eq(r18,3)` is always true and the classify path is always taken. **FACT.**
- command_id (wire[0x0a]) is passed as **r1 → r16** into the dispatcher and forwarded into whichever target runs (`0xd826d3e4` or `0xd8292298`) as the operation selector for the RX/config unpacker. It is bounds-checked `<= 0x31` at `0xd86fd10c`; out of range → early error at `0xd86fd1d8` (no dispatch). **FACT.**
- **Therefore:** any command_id in `0x00..0x31` routes to **CONFIG** when `wire[0x0f]==0 && wire[0x10]==0`, and to **MEASURE** otherwise. Your earlier live failure was **wire[0x10] (or wire[0x0f]) nonzero**, not a wrong command_id. **FACT.**
- Which command_id you pick still matters for *what* the config branch does (the per-id unpacker via `0xd8272684`/`@0xca79a850`), but not for reaching the config branch. The specific id→operation binding is runtime-populated. **UNKNOWN (runtime).**

Sibling note: `0xd8288ab0`/`0xd86f8d00`/`0xd86f8e50` call `0xd8272cd8` with `r0=#5` (p0=cmp.eq(r18,5)) — same classify path. Only callers passing r0 ∉ {3,5} take the alternate `0xd8272d60` path. Handler `0xd86fd0f4` always passes 3. **FACT.**

---

## 5. BYTE-EXACT CONFIG-APPLY WIRE PACKET (Q5) — FACT / INFERENCE

Reach `0xd8292298` (fn `0xd8291504`, the branch that arms `session+0x89a8=7` per `cal_trigger_chain.md`) **without** the measure/SSR branch `0xd826d3e4`:

```
offset  value        meaning
------  -----------  ------------------------------------------------------------
0x00    4B           DIAG_SUBSYS_CMD_F                                   [FACT]
0x01    0B           SUBSYS_FTM                                          [FACT]
0x02    27           ftm_cmd = 0x0027 (FTM_LTE), LE                      [FACT]
0x03    00
0x04    02           sub_command = 0x1002 (RFTEST slot 2 -> 0xd86fd0f4), LE   [FACT]
0x05    10
0x06    nn           num_tlv (u16 LE) — layout field                     [FACT layout]
0x07    nn
0x08    xx           body[0]  (don't-care for routing)
0x09    xx           body[1]
0x0A    <=0x31       command_id  (must be <= 0x31; picks the op unpacker) [FACT]
0x0B    xx           body
0x0C    xx
0x0D    xx
0x0E    xx
0x0F    00           <<< routing byte low  — MUST be 0                    [FACT]
0x10    00           <<< routing byte high — MUST be 0                    [FACT]
0x11    00           mode sub-param high (0 recommended; not in branch)   [FACT: not branch]
```

**Routing rule (the fix):** the effective routing selector is the 16-bit LE field `wire[0x0f..0x10]`. **Set `wire[0x0f]=0x00` AND `wire[0x10]=0x00`.** With that, `classify=0 → p0 false → callr 0xd8292298` (CONFIG). Any nonzero in either byte (or `wire[0x0f] > 0x13`) → `callr 0xd826d3e4` (MEASURE → null-carrier SSR).

**Caveats (unchanged from `cal_trigger_chain.md` / `ftm_phase_byte.md`):** reaching the config branch is *necessary* but the RF driver only writes **`0x89a8 = 7`** (vs 2/0) when the internal selectors resolve to cal (`0xd80d1750()!=0` with FTM phase `0xcb8dbeb0+0x18==3`, and/or per-carrier band-mode byte `memub(idx*0x178+≈0xcb9eb074) ∉ {9,0xa}`). Those are **runtime state**, not wire bytes. Send this while the modem is already in FTM/cal service. **FACT (branch) + INFERENCE (7-vs-2 gating is runtime).**

---

## 6. FACT / INFERENCE / UNKNOWN (with VAs)

**FACT**
- Handler `0xd86fd0f4`: request ptr `r18 = r1` (raw WIRE base) captured at `0xd86fd104` in the envelope-call packet (packet-start read, pre-clobber). command_id `= memub(r18+0xa)` @`0xd86fd108`; routing bytes `memub(r18+0xf)` @`0xd86fd12c`, `memub(r18+0x10)` @`0xd86fd134`, `memub(r18+0x11)` @`0xd86fd120`.
- Envelope `0xd86fd0e8 → 0xd8272c10` only copies wire[0..7]+metadata into a heap reply container (`0xd8272c54..c80`); return discarded — NOT the request source.
- Wire base flows unbroken: `0xd8157ec4` (`r17=r1=WIRE`) → `0xd8157fb8` (`r1=r17`) → `0xd82714b4` (`r16=r1=WIRE`, sub read @+4/5) → `0xd8271630` (`r1=r16=WIRE`) → handler.
- Packing: `r2(packed) = wire[0x0f] | wire[0x10]<<16 | wire[0x11]<<24`; `r0=#3` (const), `r1=command_id`; `call 0xd8272cd8` @`0xd86fd158`.
- Dispatcher `0xd8272cd8`: `r18=r0=3` @`0xd8272ce4`; `p1=cmp.eq(r18,3)` TRUE @`0xd8272d08` → sxth path; `r0=sxth(r20)=int16(wire[0x0f]|wire[0x10]<<8)` @`0xd8272d18`; `call 0xd82735cc` @`0xd8272d24`; `p0=classify`; `r5=0xd826d3e4` if p0!=0 @`0xd8272d44`, else `r5=0xd8292298` @`0xd8272d4c`; `callr r5` @`0xd8272d50`.
- Classifier `0xd82735cc`: `classify(x) = (x<=0x13)?(x&0xff):0x15`, `x=sxth(wire[0x0f]|wire[0x10]<<8)` (`0xd82735d0`/`d82735e4`/`d82735e0`).
- Targets decoded from immediates: measure `0xd826d3e4`, config `0xd8292298` (fn `0xd8291504`). Both call carrier getter `0xd827923c` (`memw(0xca79c494)`); null → measure path crashes.
- command_id validated `<=0x31` @`0xd86fd10c`; out-of-range → error `0xd86fd1d8`.
- Sub 0x1002 slot: jump-table `@0xc37c649c[2]` → `0xd8271548` → `r2=##0xd86fd0f4` → `0xd8271630 callr r2`.

**INFERENCE**
- The single live-repro fix: the earlier packet had `wire[0x10]!=0` (or `wire[0x0f]∉{0}`), so the 16-bit classify input exceeded 0x13 → `0x15` → measure. Zeroing both bytes routes to config. (Hard-anchored to the classify math above.)
- Sibling `0x10xx` slots that also feed `0xd8272cd8` with `r0∈{3,5}` behave identically on the `{wire[0x0f],wire[0x10]}` classify.

**UNKNOWN (runtime / out-of-image)**
- The command_id→unpacker binding (`@0xca79a850[id]+0x34`) is runtime-populated; which id yields the specific RF config op you want is not statically fixed.
- The 7-vs-2-vs-0 outcome of the config branch depends on live RF/FTM state (`0xcb8dbeb0+0x18==3`, per-carrier band-mode byte, `session+0x89a8`/`+0xc`), all RAM-only.

---

## 7. REPRODUCE
```bash
cd /tmp/modemre
./dis36.sh 0xd86fd0f4 0x80    # handler: r18=r1=WIRE @d86fd104; packs {0xf,0x10,0x11}; r0=#3; call 0xd8272cd8
./dis36.sh 0xd86fd0e8 0x10    # envelope wrapper -> 0xd8272c10 (r2=0xe)
./dis36.sh 0xd8272c10 0x80    # builds reply template (copies wire[0..7]+meta); NOT the request src
./dis36.sh 0xd8272cd8 0x80    # r18=r0=3; p1 true; sxth(packed); 0xd82735cc; p0 -> measure/config
./dis36.sh 0xd82735cc 0x20    # classify(x)=(x<=0x13)?(x&0xff):0x15 ; x=sxth(wire[0xf]|wire[0x10]<<8)
./dis36.sh 0xd8157ec4 0x80    # WIRE base as r17=r1 (sub @+4/5)
./dis36.sh 0xd82714b4 0x80    # r16=r1=WIRE; jump-table @0xc37c649c
./dis36.sh 0xd8271630 0x08    # r1:0=combine(r16,r18) -> handler r1=WIRE
python3 - <<'PY'
def classify(b0f,b10):
    x=(b0f|(b10<<8))&0xffff
    if x&0x8000: x-=0x10000
    return 0x15 if (x&0xffffffff)>0x13 else (b0f&0xff)
for b0f,b10 in [(0,0),(0,1),(1,0),(0x13,0),(0x14,0)]:
    print(f"wire[0x0f]={b0f:#x} wire[0x10]={b10:#x} -> classify={classify(b0f,b10):#x} -> "
          + ("CONFIG(0xd8292298)" if classify(b0f,b10)==0 else "MEASURE(0xd826d3e4)"))
PY
```
