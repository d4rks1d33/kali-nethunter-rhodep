# HALPHY ADC-Capture-In-Memory (DPD/cal) — is it a reachable raw-IQ path?

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Snapdragon 695 / Moto G82 5G)
Base disasm: `clade_dec_36m.bin` (VA base **0xd8000000**, valid **0xd8000000..0xdb400000**, 52 MB).
Tooling (preserved, reused): `dis36.sh <va> <len>` (llvm-objdump-18 hexagon v66), `rd.py <va> <n>`
(rodata by VA, auto-selects `modem.bNN`), `find_const.py`/`fc2.py` (immext-aware constant xref),
full disasm index `/tmp/opencode/full_dis.txt` (9.2M lines). rodata segments: **b21 @0xc3553000**,
**b23 @0xc8b6a000**. Work dir: `/tmp/opencode`, segments in `/tmp/modemre`.

Legend: **OBSERVED** = byte/instruction/pointer read directly from this image · **INFERRED** =
deduction from observed facts · **UNKNOWN** = not isolable statically here (paged / separate image).

Companion reports re-used: `map_iq_capture.md`, `iq_capture_num_captures.md`,
`iq_delivery_channel.md`, `map_ftm_framework.md`, `map_segments.md`, `live-logs/DECODE.md`.

---

## 0. EXECUTIVE SUMMARY — DIRECT ANSWERS TO THE 5 QUESTIONS

| # | Question | Answer | Confidence |
|---|----------|--------|-----------|
| — | **Are the quoted strings / HALPHY ADC-in-mem code in this blob?** | **NO.** `halphyPaprdAdcCaptInMemConfig`, `RxDCO Data Frm HWREG`, `RXDCOSM:::...`, `hanaKernelMask`, `enable_self_eql`, and *all* HALPHY/`paprd`/`rxdco`/`cal_sm_*_device.c` strings are **absent from every segment** (flat code, decompressed CLADE 0xd8xxxxxx, and b27). Exhaustive case-insensitive scan over `modem.b00..b34` + `clade_dec_36m.bin` + `clade_exc_high.bin` + `seg27_dec.bin`: `halphy`=0, `paprd`=0, `rxdco`=0 real hits, `AdcCapt`=0, `CaptInMem`=0, `self_eql`=0, `hanaKernel`=0. | **OBSERVED** |
| 1 | Find `halphyPaprdAdcCaptInMemConfig`, disassemble it, find the buffer | **Cannot — the function and its F3 strings are not present in this image.** The strings you saw in live F3 are QSHRINK2-compressed (hash-only in the blob) resolved against the **on-device QDB**, and the HALPHY code itself is **not** in the loadable `modem.mdt` segments (it is paged / in a separate layer; see §1, §6). | **OBSERVED (absence) / INFERRED (location)** |
| 2 | How is ADC-in-mem triggered; call graph | The **reachable** analogue is the **FTM.RFDEBUG DPD/cal command family** (`DEVICE_CAL`, `VDPD_CAL`, `VDPD_CONVERSION`, `LOAD_DPD`, `LOAD_UNITY_DPD`, `SET_DPD_DEBUG_MODE`, `IDC_CAL`, `THERM_READ`), dispatched from DIAG `4B 0B` → **0xd816d2d4** via runtime `callr memw(0xca65b414 + sub*0xc)` (sub ≤ 0x15). Handlers live at 0xd816c000–0xd8178000 (`ftm_lte_rf_debug.c`). (§2, §3) | **OBSERVED** |
| 3 | `RxDCO Data Frm HWREG` HW registers — can we read them? | **Not locatable in this image** (string + handler absent). RxDCO HW register reads happen inside the HALPHY/`cal_sm_rxdco_device.c` layer that is not present. No HW register base address can be cited from this blob for that function. The live `0x60` telemetry stream (field_id 0x120/0x121) in `DECODE.md` is this RxDCO cal telemetry, but it is a **periodic snapshot of a 52 KiB DDR buffer at VA 0x00d019a1** (see §5), not a readable register map. | **OBSERVED (absence) / OBSERVED (telemetry handle)** |
| 4 | DIAG-reachable FTM cmd that triggers ADC-in-mem and returns the buffer | **The RFDEBUG DPD/cal commands ARE DIAG-reachable** (subsys 0x0B, dispatch 0xd816d2d4) and **do** return data via **REPACK** `[ %12s ][ %4d ][ 0x%8x ]` = **{name, size_bytes, source_VA}**. BUT (a) the `0x%8x` is an **internal modem heap VA, not AP-readable memory** (same contract as mode-105 IQ, see `iq_delivery_channel.md`): samples come back **inline in the DIAG response**, copied from the heap buffer; and (b) the acquire is gated by the **same active-carrier pointer 0xca79c494** that is NULL in your setup → same SSR/empty-result failure mode as mode-105. (§3, §4) | **OBSERVED** |
| 5 | Buffer VA — can we read it after triggering cal? | **No stable AP-readable VA.** The DPD-cal capture buffer is an **internal DSM/heap allocation** (`p_sample_capture_buffer`-class), not a memshare/physical region (OBSERVED in `iq_delivery_channel.md` / `memshare_request_flow.md`). The only buffer address exposed on the wire (the RxDCO telemetry `0x00d019a1`, size `0xd000`) is a **modem-internal VA in the b24 .bss runtime RAM region**, not mapped into AP address space. (§5) | **OBSERVED / INFERRED** |

**Bottom line:** The ADC-capture-in-memory / DPD-cal path is **not a new reachable raw-IQ exfil
route on this target**. Its HALPHY implementation is not in the image we can analyze, its
DIAG-reachable RFDEBUG front-end delivers samples **inline via DIAG** (not via an AP-readable
buffer), and it is blocked by the **same carrier-activation gate (0xca79c494 NULL)** that already
defeats the mode-105 IQ_CAPTURE path. See §7 for the honest next steps.

---

## 1. WHY THE STRINGS ARE NOT HERE — METHODOLOGY & PROOF  [OBSERVED]

The four strings you quoted are **live F3/QSR log output**, not static rodata. Two independent
reasons they are absent from the artifacts:

1. **QSHRINK2 compression.** Qualcomm MPSS F3/QSR messages store only a numeric *message hash* +
   argument list in the binary; the human-readable format string lives in an external **QDB**
   (`.qdb`/`Diag_...` database) on the build server / phone. The workspace QDBs
   (`qdb_full.json`, `qdb_content_id2fmt.json`) are from a *different* build and contain
   `halphy`=0, `paprd`=0, `rxdco`=0. So even the *strings* are not greppable here. **OBSERVED.**

2. **The HALPHY code itself is not in `modem.mdt`.** Exhaustive scan (command below) over all 34
   `modem.bNN` segments, the decompressed CLADE window, the high-exceptions blob, and the
   decompressed b27:

   ```
   for f in modem.b* clade_dec_36m.bin clade_exc_high.bin seg27_dec.bin; do
     for t in halphy hana paprd rxdco self_eql AdcCapt CaptInMem cal_sm; do
       grep -abic "$t" "$f"; done; done
   ```
   Result: **zero real hits.** (The two incidental `hana` hits are "GloGhana"@0xc367aac6 — a
   country name; the `device.c`/`HWREG`/`hana` byte-matches in `seg27_dec.bin` are inside binary
   data, not printable strings — verified by printable-run extraction.) **OBSERVED.**

**What IS present** (b21 rodata, `/tmp/opencode/b21_strings.txt`) is the layer *above* HALPHY — the
WCDMA firmware (`wfw_*.c`), LTE LL1 (`lte_LL1_iq_samp_capture.c`@0xc356558b,
`lte_LL1_async_samp_capture.c`@0xc3564817), RFLM (`rflm_vswr_capt.c`, `rflm_xpt_capt.c`,
`rflm_xpt_dpd_*.cc`, `rflm_txagc_capt.c`), the FBRX/ADC config (`adc_dac_config`,
`smr526_dtr_rx_adc_cal.cpp`), and the **FTM RF-DEBUG command layer** (`ftm_rf_debug_*.cpp`). The
HALPHY `halphy_mem_paprd.c` / `cal_sm_rxdco_device.c` sit **below** RFLM and are either
dlpager-paged (not materialized in the flat ELF) or in a non-captured RF firmware layer.
**INFERRED (from the module layering + the absence).**

---

## 2. THE REACHABLE DPD/CAL FRONT-END: FTM.RFDEBUG  [OBSERVED]

The DPD/calibration commands that DO exist in this image, with F3 format strings in b21 and
handler code in CLADE:

| RFDEBUG command | UNPACK fmt (b21 VA) | field-handler jump-table (b21) | handler fn start (CLADE) |
|---|---|---|---|
| SET_DPD_DEBUG_MODE | 0xc37be524 | 0xc37be4f8 / 0xc37be510 | 0xd816bff8 |
| DEVICE_CAL         | 0xc37be6cc | **0xc37be6b8** (sub≤4) | 0xd816eff4 |
| TX_MEASURE_ADV     | 0xc37be7ed | — | (region 0xd8170xxx) |
| IDC_CAL            | 0xc37be8c0 | **0xc37be894** | 0xd8170370 |
| THERM_READ         | 0xc37be91f | — | (region 0xd8170xxx) |
| VDPD_CONVERSION    | 0xc37be9b8 | — | (region 0xd8171xxx) |
| VDPD_CAL           | 0xc37bebf5 | **0xc37beb68** | 0xd8172374 |
| LOAD_DPD           | 0xc37bf284 | **0xc37bed4c** | 0xd817633c |
| LOAD_UNITY_DPD     | 0xc37be5dc | 0xc37be588 | 0xd816e5xx |

Verification commands:
`python3 /tmp/opencode/fc2.py 0xc37bebf5` → immext@0xd8172630,0xd817267c (VDPD_CAL REPACK loop).
`python3 /tmp/opencode/scan_rodata_ptrs.py` → the per-command field jump-tables (b21 @0xc37beXXX)
pointing into 0xd816exxx..0xd8174xxx.

### 2.1 REPACK = {name, size, source-VA} — the result-delivery contract  [OBSERVED]
The REPACK format strings are literal evidence of how results come back:
```
0xc37bec45  [FTM.RFDEBUG][VDPD_CAL][REPACK]:Kernel weight I [ %12s ][ %4d ][ 0x%8x ]
0xc37bec8e  [FTM.RFDEBUG][VDPD_CAL][REPACK]:Kernel weight Q [ %12s ][ %4d ][ 0x%8x ]
0xc37bea16  [FTM.RFDEBUG][VDPD_CONVERSION][REPACK]:AMAM [ %12s ][ %4d ][ 0x%8x ]
0xc37be720  [FTM.RFDEBUG][DEVICE_CAL][REPACK]: [%3d][%3d][ %12s ][ %4d ][ 0x%8x ]
```
The REPACK loop (OBSERVED at 0xd817262c..0xd81726cc) iterates N properties, calling the
TLV-pack helper 0xd80d77a8 per property with `{name_ptr, size, value}`. This is the **same
property serializer** used by RX_MEASURE/IQ_CAPTURE (`iq_delivery_channel.md` §2): the `0x%8x`
is the **modem-internal source VA** the serializer reads `size` bytes from — it is **copied
inline into the DIAG response**, it is **not** an address the AP can map. **OBSERVED (contract).**

---

## 3. THE DIAG DISPATCH TO RFDEBUG — HOW TO REACH IT  [OBSERVED, byte-exact]

From `map_ftm_framework.md` §2.3 (re-verified): DIAG `4B 0B` (subsys 0x0B = FTM) with the FTM
command selector routes to the RFDEBUG dispatcher **0xd816d2d4**. Disassembly (OBSERVED):

```
d816d2d4: r20 = memub(r19+#0x4)                 ; sub_command low byte (pkt off +4)
d816d2d8: r3  = memub(r19+#0x5)                  ; sub_command high byte (pkt off +5)
d816d2e4: r20 |= asl(r3,#0x8)                    ; r20 = 16-bit RFDEBUG command index
d816d2ec: r2  = memw(##0xca65b414)               ; RFDEBUG command-table BASE pointer (.bss)
d816d2f4: p0  = cmp.eq(r2,#0x0) ; if (p0) jump 0xd816d378   ; table NULL -> error path
d816d308: p0  = cmp.gtu(r20,#0x15) ; if(!p0) jump 0xd816d318 ; bound: sub <= 0x15
d816d318: r3  = +mpyi(r20,#0xc)                  ; entry stride = 0x0c (12 bytes)
d816d31c: r22 = add(r2,r3)                       ; &table[sub]
d816d324: r0  = memw(r2+r3<<#0x0)                ; table[sub].field0 (NULL -> "not registered")
d816d34c: r2  = memw(r22+#0x0)                   ; handler pointer
d816d350: callr r2                               ; <-- runtime dispatch to DEVICE_CAL/VDPD_CAL/...
```

**Facts (OBSERVED):**
- The RFDEBUG command index is a **16-bit LE value at packet offsets +4/+5**.
- Valid range **0..0x15** (21 commands). Out of range → F3 error (fmt @ ~0xf8095650).
- The dispatch table **0xca65b414** is in the **b24 .bss runtime RAM** (zero at boot) — it is
  **populated at runtime** by per-command registration (the DPD/cal modules register their
  handlers during FTM init). This is exactly the **runtime-`callr` dispatch** that static BFS
  cannot follow (noted across prior reports). So the **sub-command → DEVICE_CAL/VDPD_CAL index
  map is UNKNOWN statically** (lives in the registration order executed at runtime). **OBSERVED
  (mechanism) / UNKNOWN (index numbers).**

> To recover the index map live: dump `0xca65b414` on-device after FTM init, read the table of
> 12-byte entries, and compare `entry.field0` VAs against the handler starts in §2. (Method;
> requires a memory-peek DIAG cmd — see `mem_read_cmds.md`.)

---

## 4. WHAT DEVICE_CAL / VDPD_CAL ACTUALLY DO (reachable body)  [OBSERVED]

### 4.1 DEVICE_CAL (0xd816eff4) — sub-selector cal dispatcher  [OBSERVED]
```
d816f004: r2 = memub(r16+#0xc)                 ; cal sub-selector from ctx
d816f008: r2 = add(r2,#-0x1)
d816f00c: if (!cmp.gtu(r2,#0x4)) jump 0xd816f014  ; selector 1..5
d816f014: immext(#0xc37be680)
d816f018: r2 = memw(r2<<#0x2 + ##0xc37be6b8)    ; jump-table @0xc37be6b8 (5 cal ops)
d816f01c: jumpr r2
```
DEVICE_CAL is a 5-way cal operation selector. Each op runs a device calibration and REPACKs
results with `[DEVICE_CAL][REPACK]: [%3d][%3d][ %12s ][ %4d ][ 0x%8x ]`. **OBSERVED.**

### 4.2 VDPD_CAL (0xd8172374) — DPD kernel-weight capture + REPACK  [OBSERVED]
Key instructions (OBSERVED, §from §2 disasm):
- `d8172388: r16 += mpyi(r17,#0x90)` — per-handle context (0x90-byte stride).
- `d81723bc: call 0xd84c2650` — DPD cal trigger (RFLM/XPT layer).
- `d8172410: call 0xd845a1a8` with `r5:4=combine(#0x1,##0x800)` — property acquire, 0x800
  (2048)-element buffer setup → this is the **capture/acquire step** that fills the kernel-weight
  buffer. `0xd845a1a8` is the generic FTM TLV property-acquire helper (not HALPHY).
- `d817262c..d81726cc` — the **REPACK loop**: for each kernel tap, pack
  `{"Kernel weight I/Q", size, source-VA}` via 0xd80d77a8.

**So the full reachable chain is:**
```
DIAG 4B 0B <ftm-sel> | sub(+4/+5)=VDPD_CAL
  -> 0xd816d2d4 dispatch -> callr table[VDPD_CAL] -> 0xd8172374
       -> 0xd84c2650  (DPD cal trigger, RFLM/XPT; pages into HALPHY paprd below us)
       -> 0xd845a1a8  (acquire -> fills internal kernel-weight/sample buffer)
       -> REPACK loop  (copies {name,size,value} INLINE into DIAG response)
```
The HALPHY `halphyPaprdAdcCaptInMemConfig` you saw in F3 is called **under** `0xd84c2650`
(the RFLM/XPT→HALPHY paprd descent) — i.e., it is the *real ADC-in-mem config the cal SM runs* —
but that code is **not in this image** (§1), so its buffer VA and register config are **UNKNOWN**
from static RE here. **OBSERVED (upper chain) / UNKNOWN (HALPHY body).**

---

## 5. THE RxDCO TELEMETRY BUFFER YOU ALREADY OBSERVED  [OBSERVED]

`live-logs/DECODE.md` already decoded the DIAG `0x60` background stream that appears during the
cal SM run (your `RXDCOSM`/`RxDCO Data Frm HWREG` strings are the F3 companions of this stream):

```
record 0x60:  {field_id, A2, A3, A4, tag=0x27c28a, ptr1=0x00d019a1, size=0xd000, ptr2=0x00d019a1}
field_id observed: 0x0120 (288), 0x0121 (289)  ; ptr/size CONSTANT across captures
```

- `0x00d019a1` is a **modem-internal VA** (low DDR window), size `0xd000` = **52 KiB**. It is a
  periodic **snapshot handle** to a cal/telemetry buffer — this is the closest thing to an
  "ADC-capture-in-memory buffer address" that the modem *emits on the wire*. **OBSERVED.**
- **But it is not in AP address space.** `0x00d0xxxx` is not any AP-mapped memshare region
  (the memshare reserved region in your bring-up is `0x8ab00000`, `memshare_request_flow.md`).
  It is the modem's own buffer; the only way the content leaves the modem is the DIAG stream
  itself (the `0x60` records), which carry the *handle*, not a copy you can mmap. **OBSERVED /
  INFERRED.**
- field_id 288/289 are **not** in any RF-test TLV namespace (IQ_CAPTURE/RX_MEASURE field tables
  top out ~222) — confirming this is a **separate log/telemetry namespace** (RxDCO cal), not a
  command you can parameterize. **OBSERVED (from the field tables).**

---

## 6. WHERE THE HALPHY CODE/BUFFER ACTUALLY LIVES  [INFERRED]

Given §1 (absent) + §4 (RFLM descends into it) + `map_segments.md`:

- The flat ELF covers DIAG core, FTM framework, RFLM/XPT, and the CLADE-decompressed window.
- `halphy_mem_paprd.c` / `cal_sm_rxdco_device.c` are **below RFLM**. On SM6375 these are part of
  the **RF HAL-PHY / transceiver-control layer**, which on this build is **dlpager-demand-paged**
  from compressed backing (the body is not materialized in the flat ELF; only the layer above it,
  the RFLM glue, is). The F3 strings are QSHRINK, so even paging in the code would not surface the
  format strings in the flat image. **INFERRED (strong: consistent with absence + the paging
  notes in `iq_capture_num_captures.md`/`iq_delivery_channel.md`).**
- Consequence: the **ADC-in-mem buffer VA is allocated at runtime inside that paged layer**
  (heap/DSM), and is **not a fixed address** recoverable statically. **INFERRED.**

---

## 7. IS THIS A USABLE RAW-IQ PATH? — HONEST VERDICT

**No — it does not give you a new readable raw-IQ buffer on this target.** Three independent
blockers, all OBSERVED:

1. **Same gate as mode-105.** The DPD/cal acquire runs through the RF path that requires the
   **active-carrier pointer 0xca79c494 to be non-NULL** (written only by the gated carrier-
   activate path 0xd81bd018→0xd8273b2c→setter 0xd8279264, gated by `gp+0x740==2` and
   `gp+0x7000!=0`). In your setup that pointer is NULL (the documented SSR / num_captures=0 root
   cause). The cal SM "running" in F3 does **not** imply the acquire filled a buffer — the RxDCO
   telemetry (§5) is emitted regardless. **OBSERVED (`iq_capture_num_captures.md` §0.b).**

2. **Delivery is inline-DIAG, not shared memory.** Even on success, the REPACK `0x%8x` is a
   **modem-internal source VA**; the bytes come back **copied inside the DIAG response packet**
   (the FTM property serializer, `iq_delivery_channel.md`), capped at `MAX_DIAG_CMD_PAYLOAD`.
   There is no AP-mappable buffer to peek. **OBSERVED (contract).**

3. **The index map is runtime-only.** The DEVICE_CAL/VDPD_CAL sub-command numbers live in the
   runtime table 0xca65b414 (zero at boot), so you cannot derive the exact DIAG bytes statically.
   **OBSERVED.**

### What would actually move this forward (method, not done here)
- **Fix the carrier gate first** (same prerequisite as mode-105): activate the RF carrier so
  0xca79c494 is non-NULL. Until then *no* FTM capture path (IQ_CAPTURE or DPD-cal) yields data.
- **Dump 0xca65b414 live** (12-byte entries) to map RFDEBUG sub → handler (§3) and confirm the
  DEVICE_CAL/VDPD_CAL indices, then send `4B 0B <ftm-sel> | sub` and read the **REPACK from the
  CMD reply** (not the background DATA socket).
- **If you want the RxDCO buffer** (§5, `0x00d019a1`/52 KiB): it is modem-internal; the only
  static-feasible exfil is parsing the `0x60` DIAG telemetry records you already capture, OR a
  modem memory-peek DIAG command targeting that VA (see `mem_read_cmds.md`) — but that VA is in
  the modem's own b24 .bss RAM, so it requires a modem-side peek primitive, not an AP read.

---

## 8. ARTIFACTS / REPRO

- String dump (b21, all printable): `/tmp/opencode/b21_strings.txt` (107,427 lines).
- Full code disasm index: `/tmp/opencode/full_dis.txt` (base 0xd8000000).
- immext xref: `python3 /tmp/opencode/fc2.py <rodata_va>` (reused `find_const.py` logic).
- rodata→code pointer-table scan: `python3 /tmp/opencode/scan_rodata_ptrs.py`.
- Key VAs cited: RFDEBUG dispatch **0xd816d2d4**, table base **0xca65b414** (.bss),
  DEVICE_CAL **0xd816eff4** (jt @0xc37be6b8), VDPD_CAL **0xd8172374** (REPACK @0xd817262c),
  LOAD_DPD **0xd817633c**, SET_DPD_DEBUG_MODE **0xd816bff8**.
- Carrier gate (shared blocker): setter **0xd8279264** → `memw(0xca79c494)=0xca6e3b88`,
  gated by `gp+0x740==2` / `gp+0x7000!=0` (see `map_ftm_framework.md` §2.3, §3).
- RxDCO telemetry handle: VA **0x00d019a1**, size **0xd000** (52 KiB), field_id 0x120/0x121
  (see `live-logs/DECODE.md`).
</content>
</invoke>
