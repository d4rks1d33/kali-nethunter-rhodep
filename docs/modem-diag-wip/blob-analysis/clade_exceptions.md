# CLADE exception-word analysis — SM6375 (Moto G82 5G) modem, dispatcher FTM

Build: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
Continuation of `clade_decomp.md`. Goal: apply CLADE "exception words" to fully
decompress the FTM dispatcher at vaddr **0xd8150ed8** and read the exact
TECH enum + tech-enter sub_command.

Legend: **FACT** = verified in this image · **INFERENCE** = deduced · **UNKNOWN** = not determined.

---

## 0. EXECUTIVE RESULT (honest)

**Major breakthrough — the root cause of the "~26% wrong words" is FOUND and FIXED
for the dominant part.** The previous pass' theory (missing exception words) is
confirmed, and the concrete mechanism is now known:

- **FACT — CLADE code `0b11` ("code 3") is an EXCEPTION MARKER that consumes ZERO
  additional stream bits.** It is *not* an inline 32-bit literal.
  `unclade.py` reads `bits.get(32)` on code 3; this desynchronises the bitstream
  at the *first* exception of every page and corrupts *every subsequent word*.
  Treating code 3 as a 0-bit marker keeps the stream **perfectly aligned across
  the entire image (verified past page 336 — NO drift)** and makes every
  non-exception word correct.
- **This overturns the previous pass' "uniform 26% error, contiguous stream"
  claim in its detail**: the 26% was almost entirely *post-exception drift*, not
  a per-word intrinsic error. With the fix, the bitstream is contiguous AND
  aligned; only the exception words themselves (~17% of words) plus a small
  residual (~4–5%, see §4) remain.

- **FACT — proof of the fix (page 0, first exception at output word 31):**
  ```
  word  OLD(unclade)  NEW(marker,0-bit)
  30    a1b15200      a1b15200      (same, pre-exception)
  31    12632681      EXCEPTION     <- first exception
  32    8e4648c5      160bc06a  = "r19=#0; jump 0xd8000154"  (NEW is valid Hexagon)
  33..  drifted...    all correct non-exception words
  ```
  OLD's word 35 `5a015892`(call) reappears in NEW at word 37 — OLD is shifted by
  ~2 words after the exception (it consumed 32 stray bits). NEW never drifts.

- **What is STILL missing for a 100%-correct dispatcher (honest):**
  1. The **exception words** (the ~17% of words flagged by code 3). Their storage
     section + ordering was **not conclusively located** (see §3, candidates).
  2. A **residual ~4–5%** of dict-decoded words still yield invalid packets even
     far from exceptions (see §4) — one more un-modelled encoder feature.

Consequence: the dispatcher structure is now **much cleaner and correctly aligned**
(`disp_improved.txt`), but the *exact numeric* sub_command of tech-enter and the
TECH enum still sit partly on exception words / residual-error words, so they are
reported as best-estimate with confidence, **not fabricated**.

---

## 1. Deliverables produced in this pass

- `/tmp/q6zip/unclade_exc.py` — corrected decoder (code 3 = 0-bit exception marker).
- `/tmp/modemre/clade_dec_full.bin` — improved (aligned) decode of the code region
  (exceptions rendered as visible `0xE0C0DExx` markers so they are obvious in disasm).
- `/tmp/modemre/disp_improved.txt` — dispatcher pages 336–339 disassembled from the
  corrected decode (llvm-objdump-18 --triple=hexagon --mcpu=hexagonv66).
- This report.

Reproduce:
```
PYTHONPATH=/tmp/q6zip python3 /tmp/q6zip/unclade_exc.py /tmp/clade/modem.b26 \
    -n 0x82000 --output /tmp/modemre/clade_dec_full.bin
```

---

## 2. How CLADE exception words work (from evidence)

### 2.1 The bit stream (FACT, verified)
Per 32-bit output word the stream carries a 2-bit `code`:
- `code 0`: 11-bit idx into dict0 (OR=0x3fffffff) + 2 missing bits, interleaved at [1,15].
- `code 1`: 11-bit idx into dict1 (OR=0x007fffff) + 9 missing bits at [1,2,4,5,6,7,8,9,16].
- `code 2`: 11-bit idx into dict2 (OR=0x0001ffff) + 15 missing bits at [0,1,2,4,5,7,8,9,10,15,16,17,18,19,20].
- `code 3`: **EXCEPTION — 0 further stream bits.** The word comes from the
  exception section, in output order.

Verified: `reconstructword` reproduces known-good words exactly (e.g. code0
idx=0x56b dict=0x1f202000 missing=0 → 0x7c804000 ✓). The 0-bit interpretation of
code 3 is the single change that removes all post-exception drift.

### 2.2 The three exception sections (from the QuRT XML in modem.b23)
```
.clade.exception_high        clade_register=clade_exception_high      physpool=TCM_POOL      cache_policy=1
.clade.exception_low_large   clade_register=clade_exception_low_large mapping=rx tlb_lock=boot
.clade.exception_low_small   clade_register=clade_exception_low_small mapping=rx tlb_lock=boot
.clade.metadata              (no register)                            mapping=rx tlb_lock=boot
.clade.comp                  clade_register=clade_comp                mapping=rx tlb_lock=boot   <- = modem.b26 off 0
.clade.dict                  physpool=CLADE_DICT                                                  <- = modem.b26 off 0x2444000
<clade_base_paddr value="0x400000000"/>
region_low_clade / region_high_clade_protected_memory
```
HW registers: **CladeCompPDX, CladeExcHiPDX, CladeExcLowPDX, CladeExcLowSmallPDX,
CladeRegion, clade_region_low_pd0, clade_region_high_pd0.**
Kernel error string (modem.b09 @0xc0a3e54e):
`"invalid QURTK_clade_exc_hi_word or QURTK_clade_dict_word!!!"`.

**INFERENCE (mechanism):** the presence of *three* exception sections plus the
0-bit marker strongly suggests exception words are **stored split by bit-range**
across `exc_high` (high bits) and `exc_low_{large,small}` (low bits, two width
classes), reassembled by the CLADE HW when it hits a code-3 marker. This is why
the marker needs **no inline bits** (the HW walks the three sections in output
order and glues the pieces). This was NOT proven byte-for-byte (see §3).

### 2.3 No software decoder to copy from (FACT — important)
`clade.c` in the QuRT kernel (modem.b09, functions @0xc0a27840 / @0xc0a27b80)
only **programs the CLADE HW registers** (via `memw_phys` to register banks at
0xc9122040 / 0xc9123080) and validates the dict/exc-hi words — it does **not**
contain a software decompressor. `using-cladelib/cladetool.cpp` calls the closed
`libclade.so` as a black box. So the exact exception encoding cannot be lifted
from source; it lives in silicon. `libclade.so` is **not present** in this
environment and the Hexagon SDK was not available offline; a public copy of the
CLADE algorithm/`clade_exc_hi_word` source could not be found (grep.app,
sourcegraph, GitHub all negative).

---

## 3. Locating the exception / metadata sections (partial — honest)

The `.clade.exception_*` and `.clade.metadata` sections are **separate linked
segments**, NOT inside modem.b26 (b26 = [comp stream][zero pad @0x2443000][3 dicts
@0x2444000]; no header, no trailing tables). Candidates examined:

| seg | vaddr | size | content found | verdict |
|-----|-------|------|---------------|---------|
| b25 | 0xcbf4f000 | 0x19900 | `{desc:u32, offset:u32}` records; region 0 = 183 pages, **monotonic offsets 0xae..0x37fcb**, then resets per region | **metadata-like, but NOT for the b26 code region** (page-0 comp for b26 is ~2117 B, not 0xae) — likely metadata for a *different* CLADE PD |
| b27 | 0xce480000 | 0x11922f | high-entropy u32 array (~288k words) | **exception-word candidate**, but placing b27[0..] linearly at page-0 exceptions did NOT yield valid code at any tested base offset → not a simple in-order u32 list (consistent with the split hi/low storage theory) |
| b28 | 0xce59a000 | 0x16000 | header `count=33` + 33 self-pointers to 33 bit-packed blocks with per-block headers (`1e000000 e0000000 …`) | **a separate CLADE-compressed pool** (33 blocks), not the b26 metadata |
| b29 | 0xce5b1000 | 0x1064 | ASCII `"diagdiag_comm.mon…"` etc | strings, not exceptions |

**FACT:** decoding the full b26 comp (0x2444000 B) with code3=0-bit yields
17.44M words, 16.6% exceptions (2.89M). That word count (≈69 MB) far exceeds the
0xd8xxxxxx code region (~28 MB), meaning **b26's comp stream almost certainly
contains multiple PDs / trailing non-code data**, and the code PD occupies only
its first portion. The exception count for the code PD alone is therefore much
smaller than 2.89M. Up to page 337 the running exception index is **59,622**
(useful if exceptions are globally ordered).

**UNKNOWN (blocking):** the exact `.clade.metadata` for the 0xd8000000 PD
(per-page bit offset) and the exact exception section base/format. Because there
is **no drift**, a per-page metadata offset is not strictly required to keep
alignment — but the exception *words* still must be sourced. Reversing the boot
code that writes `CladeExcHiPDX/CladeExcLowPDX` (immext to the 64-bit
clade_base_paddr 0x400000000 + section offset) is the concrete next step; the
CLADE-HW register programming lives around modem.b09 @0xc0a1fc54
(`immext(#0x40000000)`) and the `memw_phys` register writes in the clade init
functions, but full 64-bit pointer reconstruction was not completed here.

---

## 4. Residual ~4–5% dict error (honest, unresolved)

Even in **exception-free runs ≥5 words from any marker**, ~4.2–4.8% of packets
decode to >4 instructions (structurally impossible in Hexagon) → a genuine
second decoder gap, independent of exceptions:
- Sweeping code-3 inline width 0..13 bits does **not** remove it (flat ~4.5%),
  confirming code 3 = 0 bits and this is a *different* feature.
- The base reconstruction math is verified correct on known-good words, and the
  missing-bit positions are correct (dict OR masks match).
- **INFERENCE:** likely one more sub-code (e.g. a "repeat previous word" / short
  back-reference, hinted by the README's mention of a lookback field in the sister
  q6zip codec) or a second-tier code split. Not resolved without ground truth.

There is **no decompressed ground-truth image** to calibrate against: the
0xd8xxxxxx pages map to PA 0x400000000+ (>4 GB), which is NOT in the physical
DDR dump `mssdump.elf` (covers PA 0x8b800000–0x9b800000); a byte-search for the
page-0 prologue in the dump was negative.

---

## 5. FTM dispatcher — what is now 100% reliable (rodata) + best-estimate (code)

### 5.1 FACT (uncompressed rodata — fully reliable)
- **FTM RF-command dispatch table @0xc37bd1e8**: 78 entries `{cmd_id:u16(dup32),
  handler:u32}`, **every handler = 0xd8150ed8**. So 0xd8150ed8 is the single
  `ftm_rf_dispatch`. cmd_ids present include:
  `0x00,0x03,0x20,0x28,`**`0x27`**`,0x69,0x7a,0x7b,0x7e,0x0b,0x07,0x08,0x02,0x3a,
  0x31,0x3d,0x65,0x66,0x11,0x3b,0x32,0x3e,0x67,0x68,0x01,0x15,0x38,0x39,0x44,0x45,
  0x46,0x47,0x09,0x0a,0x0d,0x3c,0x33,0x3f,0x10,0x1b,0x8000,0x12,0x14,0x1d,0x1e,
  0x23,0x2f,0x30,0x4a..0x57,0x80,0x43,0x22,0x25,0x1f,0x21,0x24,0x79,0x7c,0x8001,…`
  → confirms **ftm_cmd_id 0x27 → dispatcher 0xd8150ed8** (FACT).
- Source-file string `tech_enter_exit.c` @0xc37bef3a (the tech-enter handler's
  translation unit — code is in the CLADE region).
- RFA tech name cluster @0xc391b270: `RFA_NR5G, RFA_COMMON, RFA_DEVICE, RFA_LTE`
  (storage order; not a clean numeric enum table).

### 5.2 Best-estimate from the improved (aligned) decode — with confidence
From `disp_improved.txt` (corrected decode, exceptions = `0xE0C0DExx`):
- **The 0x14 (DIAG_BAD_PARM_F) gate lives in the dispatcher body** — CONFIRMED,
  multiple coherent sites, e.g.:
  - `d8151674: r6 = #0x14 ; jump 0xd81516bc`  (clean bad-parm return right after a
    sub_command check)
  - `d8150270: r7 = #0x14`, `d8150978: if(!p0) r16 = #0x14`, `d8151aac: memb(r0+#6)=#0x14`
  Confidence: **HIGH** (rodata-independent, repeated, aligned).
- **sub_command switch** is a chain of `cmp.eq(r2,#N)` (r2 = sub_command) with
  bad-parm fallthrough. Readable comparisons near the switch (aligned decode):
  `cmp.eq(r2,#0x0)`, `cmp.eq(r2,#0x1)`, `cmp.eq(r2,#0x2)`, `cmp.eq(r2,#0x5)`,
  `cmp.eq(r2,#0x6)`, and register-compare `cmp.eq(r1,#0x2)`.
  **The two words immediately inside the tech-enter branch of the switch
  (d815166c, d8151670) are EXCEPTION words** (`e0c0de68/69`) — i.e. the *exact*
  sub_command constant for tech-enter falls on an exception word and cannot be
  read verbatim. Confidence on the surrounding structure: MEDIUM-HIGH; on the
  single tech-enter constant: **LOW (blocked by exception)**.
- **TECH enum**: the comparisons that select LTE vs NR5G sit on
  exception/residual-error words in this page; no reliable numeric value could be
  read. **UNKNOWN.** (Do NOT trust any single number here.)

### 5.3 Values requested — status
| Item | Value | Confidence | Basis |
|------|-------|-----------|-------|
| ftm_cmd_id (LTE) | **0x27** | FACT | rodata dispatch table @0xc37bd1e8 |
| Dispatcher entry | **0xd8150ed8** | FACT | rodata table (all 78 handlers) |
| 0x14 gate location | **inside 0xd8150ed8 body** | FACT | aligned decode, repeated sites |
| Sub_command register | **r2** | HIGH | `cmp.eq(r2,#N)` chain |
| tech-enter sub_command # | **UNKNOWN** | LOW | falls on exception word @d815166c/70 |
| TECH enum (LTE / NR5G) | **UNKNOWN** | — | comparisons on exception/residual words |
| tech_entered flag → 0x14 | gate present, value UNKNOWN | MED | `memw(gp+#...)` compare before 0x14, but operands partly on exceptions |

---

## 6. What the next pass must do (concrete, prioritised)

1. **Get the exception words.** Two routes:
   a. **libclade.so (definitive):** fetch it from the Hexagon SDK / a QC RFE
      package, compile `using-cladelib/cladetool.cpp`, run
      `cladetool --dictofs 0x2444000 -o 0 -l <codePDsize> modem.b26`. This uses
      the REAL codec (exceptions included) and yields the exact page 336.
   b. **Static (hard):** reverse the boot code that writes `CladeExcHiPDX /
      CladeExcLowPDX / CladeExcLowSmallPDX` (immext to 0x400000000 + offset) in
      modem.b09/b13 to get the section VAs, then confirm the split hi/low
      reassembly order (theory §2.2) and the residual sub-code (§4).
2. **Resolve the residual ~4% sub-code** (likely a repeat/back-reference).
3. With exceptions applied, page 336 should drop to <2% unknown (b10-like); then
   read `cmp.eq(r2,#N)` for tech-enter and the TECH enum directly.

---

## 7. FACT / INFERENCE / UNKNOWN — summary

**FACT**
- code 3 = exception marker, **0 stream bits**; fixes post-exception drift; stream
  stays aligned to page 336+ (proven; word-level OLD-vs-NEW divergence at first
  exception shown in §0).
- 3 exception sections + metadata exist as separate linked segments (XML in b23);
  registers CladeExc{Hi,Low,LowSmall}PDX; clade_base_paddr=0x400000000.
- clade.c only programs HW registers — no SW decoder exists to copy.
- ftm_cmd_id 0x27 → 0xd8150ed8; the 0x14 bad-parm gate is inside that dispatcher;
  sub_command is in r2.
- Improved decode + tooling produced (`unclade_exc.py`, `clade_dec_full.bin`,
  `disp_improved.txt`).

**INFERENCE**
- Exception words are stored split across exc_high / exc_low_large / exc_low_small
  by bit-range and reassembled by HW in output order (explains the 0-bit marker
  and the 3 sections). b27 = strongest raw exception-data candidate.
- Residual ~4% is one more encoder sub-code (repeat/back-reference).

**UNKNOWN (blocked)**
- Exact exception-section base/format for the 0xd8000000 PD (needed to fill ~17%).
- The residual sub-code.
- Therefore: the exact tech-enter sub_command number and the TECH enum values —
  these land on exception/residual words. **Not fabricated.**

The project moved from "impossible / 26% intrinsic error" to "**exception marker
mechanism solved, stream aligned; only the external exception-word bytes (and one
minor sub-code) remain — best obtained via libclade.so.**"
