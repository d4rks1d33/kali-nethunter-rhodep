# dump_targets — Prioritized modem RAM regions to dump from the AP (SM6375 / Moto G82 5G)

**Build:** MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 v66
**Purpose:** page-by-page AP-side DDR dump (via `qcom_scm_assign_mem` one page at a time →
`ioremap`+read → assign back) for legitimate driver bring-up: raw-IQ capture + RF understanding.
Goal of the dump: recover the **resident RF-cal / RFLM / RFLTE driver code** and read the
**live RF state** the factory tool sets (cal-mode trigger).

**Method / evidence base:** ELF program headers of `modem.mdt` (parsed here), the DDR image
`mssdump.elf` (34 PT_LOAD, PA 0x8b800000..0x9b800000, parsed here), the decompressed CLADE
code (`clade_dec_36m.bin`, VA base 0xd8000000), the dlpager control-block in `modem.b23`
(read here), and the prior reports `gate_globals_phys.md`, `cal_trigger_chain.md`,
`ftm_phase_byte.md`, `locate_rflte.md`, `map_segments.md`, `MASTER_MODEM_MAP.md`.

**Legend:** **FACT** = verified byte/structure/PH in this image · **INFERENCE** = deduced with
hard anchors · **UNKNOWN** = only observable on a live device.

---

## 0. TL;DR — read this first

1. **VA→PA is a single fixed offset for the whole loaded image: `PA = VA − 0x35000000`**
   (vbase 0xc0800000 → pbase 0x8b800000). Verified per-segment for every PH with p_vaddr in
   0xc0..0xce5b. **FACT.**
2. **The pil-mpss carveout is PA 0x8b800000 .. 0x9b800000 (256 MiB, no-map, VMID_MSS_MSA,
   XPU-protected).** Everything inside it needs `qcom_scm_assign_mem` to HLOS before an
   ioremap+read; a plain read faults (XPU). **FACT.** `mssdump.elf` covers exactly this range
   and is byte-for-byte the *static/PIL-load layout* (its 34 PT_LOADs equal the modem.mdt PHs)
   — it is **not** a live RF dump. **FACT.**
3. **The cal-mode trigger code you want is NOT resident-in-DDR-elsewhere for the FTM side — it
   is CLADE-compressed inside pil-mpss (b26 backing @ PA 0x97000000).** The whole FTM/RFTEST
   dispatch + the two `session+0x89a8=7` writers + the FTM phase-byte writer all live in the
   0xd8xxxxxx decompressed window whose *backing* is b26. **You already have this
   decompressed** (`clade_dec_36m.bin`). Dumping b26 gives you the compressed source of it.
   **FACT.**
4. **The RFLTE/RFLM/SDR735/RFDEVICE driver bodies are NOT in the MBN at all** (0 immext xrefs
   to their string VAs across 36 MB CLADE + 7.3 MB exc_high + all flat code). They are
   **resident RF/PHY code backed by DDR physical ~0x2a000000..0x2b000000**, which is **outside
   pil-mpss and outside the ELF/mssdump**. That region is the single highest-value NEW dump
   target — but it is a *separate carveout* (different owner/XPU domain, may fault differently).
   **FACT (absence in MBN) + INFERENCE (0x2a3 = its backing, from the dlpager control-block).**
5. **The live RF state (session, gates, per-carrier array) is all in b24 (.bss, 46 MiB) and
   seg25** — inside pil-mpss. Those are the pages to read *live* to answer the cal-mode
   question; they are 0 in the static mssdump. **FACT.**

---

## 1. PRIORITIZED PA RANGES TO DUMP

Ordering = value-for-the-cal-mode-question first, then completeness. "inside pil-mpss" ⇒ dump
via the assign/read/assign dance (XPU-safe once assigned). "outside" ⇒ separate carveout,
handle independently, may fault.

| # | PA start | size | VA | contents | code/data | inside pil-mpss? | safety |
|--:|----------|------|----|----------|-----------|------------------|--------|
| **1** | **0x2a000000** | **~0x1000000 (16 MiB)** | (no PH; runtime code VA out-of-image) | **RESIDENT RF/PHY code+data: RFLTE ML1, RFLM, SDR735, RFDEVICE, PHY threads (SYMPROC_IUSS / DEMOD_LITE_IUSS0-2). The cal-mode HW trigger (writer of `gp+0x740=2`), carrier_activate/wakeup bodies.** | **CODE + data (mixed)** | **NO — separate carveout, NOT in ELF/mssdump** | live-written by PHY; **racy**. Code pages are static-ish once loaded. |
| **2** | **0x94138000** | **0x2e17000 (46 MiB)** | 0xc9138000 | **b24 = pure .bss RAM: ALL live FTM/RF state** — session object (~0x9579a820), carrier ptr (0x9579c494), tech callback table (0x95733d10), FTM dispatch block (0x968dbeb0) + phase byte (0x968dbec8), per-carrier RF-config array (~0x969eb074), all gate/state globals. | **DATA (live)** | **YES** | **actively written by modem — racy.** Read specific pages (§4) not the whole 46 MiB while live. |
| **3** | **0x96f4f000** | **0x1a000 (104 KiB)** | 0xcbf4f000 | **seg25 = .sdata/.sbss (gp base 0xcbf4f000):** the two HW gates — `gp+0x740`=cal-mode enum (PA 0x96f4f740), `gp+0x7000`=RF-instance ctx ptr (PA 0x96f56000). | **DATA (live)** | **YES** | gate bytes are written at cal-mode transitions — read before/after (§4). Small, cheap. |
| **4** | **0x97000000** | **0x244b000 (36 MiB)** | 0xcc000000 | **b26 = `.clade.comp` + `.clade.dict`:** the CLADE-compressed source of the entire 0xd8xxxxxx code window (FTM/RFTEST dispatch, both `0x89a8=7` writers, phase-byte writer, IQ/measure pipeline). | **CODE (compressed)** | **YES** | **static** (compressed image, not written at runtime after boot) → **safest large dump.** You already have it decompressed; dump only to re-verify/complete. |
| **5** | **0x99480000** | **0x11a000 (1.1 MiB)** | 0xce480000 | **b27 = RF ML1 LTE (ZLIB):** decompresses to the RF **rodata** (all `rflte_*`/`rflm_*`/`sdr735_*` strings + tables). Anchors for locating #1's code bodies. | **DATA (rodata)** | **YES** | **static** → safe. Already extracted (`seg27_dec.bin`); dump to re-verify. |
| **6** | **0x93b6a000** | **0x5ce000 (5.9 MiB)** | 0xc8b6a000 | **b23 = DATA-init:** master DIAG table, g-tables, **the dlpager/PHY-pool control-block @0xc8cdf130** and region tables — the descriptors that point at #1's 0x2a3 backing. | **DATA (init)** | **YES** | mostly static-init; a few runtime fields. Safe-ish. Key to *deriving* #1's live extent. |
| 7 | 0x8e553000 | 0x5616000 (86 MiB sparse) | 0xc3553000 | b21 = RODATA: FTM/DIAG/TLV tables, jump-tables (`@0xc37c649c`), UMID/enum names. | DATA (rodata) | YES | static → safe (large but you already have `modem.b21`). |

**Notes on sizing #1 (the resident RF code):** the dlpager control-block records (`modem.b23`
@0xc8cde568/5a8/5e8, read here) carry physical DDR addresses **0x2a300000, 0x2a33f40c,
0x2a33f20c** with region size **0x20000**, and the pool base/cur is `0xd4400000/0xd4400560`,
page 0x10000, count 0x0f. A whole-b23 scan of structured `4955xxxx`-tagged records shows
page-aligned pools repeating on a 0x40000 stride with `…f20c/f400/f600` markers from
~0x2a300000 up through ~0x2ad00000. **Dump 0x2a000000..0x2b000000 (16 MiB)** to bracket the
PHY working+code pools; if that faults or is empty at the low end, the confirmed live records
sit in **0x2a300000..0x2a540000**. (INFERENCE on exact extent; FACT that 0x2a3 addresses are
the pool backing.)

---

## 2. CODE vs DATA (what to disassemble vs read-live)

**CODE — disassemble to find the cal-mode trigger / RF driver bodies:**
- **#1 (0x2a000000, resident RF/PHY)** — the ONLY place the `rflte_mc_carrier_activate`,
  `rflte_ftm_mc_wakeup`, `iq_capture_prop_action_{8,16}bit`, `rflm_dtr_rx_activate_chain`,
  `sdr735_common_class` bodies, and the **writer of `gp+0x740=2`** exist. Anchor by finding
  functions that `immext` to 0xce6e72c0 / 0xce6e08a8 / 0xce6ded20 (their string VAs). **This is
  the cal-mode HW trigger the factory tool uses.**
- **#4 (0x97000000, b26 CLADE)** — the FTM-side trigger *chain* (already decompressed): the
  RFTEST dispatcher `0xd82714b4`, canonical handler `0xd86fd0f4`, branch dispatcher
  `0xd8272cd8`, config-apply `0xd8291504`, both `session+0x89a8=7` writers (`0xd8d3ee28`,
  `0xd84b2124`), and the sole FTM-phase writer `0xd80d1534`. Dump only to complete/verify.

**DATA — read live to see what the factory flow sets:**
- **#2 (0x94138000, b24 .bss)** — the session object, gates, carrier ptr, per-carrier RF class
  array, phase struct. All 0 in the static dump; meaningful only live.
- **#3 (0x96f4f000, seg25)** — the two HW gate scalars.
- **#5/#7 (b27/b21 rodata)** — read once, static; used as anchors, not for live state.

---

## 3. SAFETY: which pages are live-written (racy) vs static

| region | write activity | verdict |
|--------|----------------|---------|
| #4 b26 CLADE code (0x97000000) | none after boot (compressed image; runtime maps decompress *elsewhere*) | **safest large dump** |
| #5 b27 rodata (0x99480000) | none (R-- rodata) | safe |
| #7 b21 rodata (0x8e553000) | none (R-- rodata) | safe |
| #6 b23 init-data (0x93b6a000) | mostly static; a few runtime control fields | safe-ish |
| #3 seg25 gates (0x96f4f000) | **written at cal-mode transitions** (the very thing you're watching) | racy but tiny — read as a snapshot before/after a sequence |
| #2 b24 .bss (0x94138000) | **heavily live-written** (session, AGC, PHY state) | **racy — read only the specific pages in §4, twice, and compare** |
| #1 0x2a3 PHY pool | **continuously live-written by PHY DSP threads** for data pages; code pages static once loaded | **most racy for data; code pages OK** — separate carveout, may fault |

**Assign/read/assign guidance:** for the racy data pages (#2/#3), read each page **twice** and
flag mismatches (torn reads). Keep the page assigned to HLOS for the minimum window. Never
assign a page the modem is executing from as MSS-only (that removes modem access → crash);
assign to **BOTH** {HLOS RW, MSS_MSA RW} if you must read while the modem runs (per
`gate_globals_phys.md §4`). For #1 (separate carveout) test one page first — it is a different
XPU sub-region and may not honor the same assign path.

---

## 4. THE ~8 SMALL PEEKS THAT ANSWER THE CAL-MODE QUESTION FASTEST

Read these exact PAs (1 page each = 0x1000) **before and after** a factory-mode-like FTM
sequence; the deltas are decisive. All are inside pil-mpss (assign to HLOS first). Widths are
what the code uses.

| # | PA | width | VA | what it tells you | expected in cal |
|--:|----|-------|----|-------------------|-----------------|
| 1 | **0x957a31c8** | byte | 0xca7a31c8 (session+0x89a8) | **THE cal RF-mode byte.** The whole `cal_trigger_chain` is about who writes this. | **== 7** (cal); 2 = normal; 0 = unset |
| 2 | **0x96f4f740** | byte | 0xcbf4f740 (gp+0x740) | HW gate (a): RF-cal-mode enum. Writer lives in #1 resident code. | **== 2** |
| 3 | **0x96f56000** | word | 0xcbf56000 (gp+0x7000) | HW gate (b): RF-instance ctx pointer (created lazily). | **!= 0** |
| 4 | **0x9579c494** | word | 0xca79c494 | active-carrier ptr; NULL here ⇒ IQ/measure will SSR. | **!= 0** (e.g. ~0x95ae3b88 region) |
| 5 | **0x968dbec8** | byte | 0xcb8dbeb0+0x18 | FTM dispatch-service phase/"ready" flag (sole writer 0xd80d1534). | **== 3** once any FTM cmd ran |
| 6 | **0x968dbec0** | word | 0xcb8dbeb0+0x10 | handler-table count (bounds-check field) — confirms the dispatch block is populated. | non-zero (e.g. 0x1000) |
| 7 | **0x9579a82c** | word | 0xca79a820+0xc (session+0xc) | session operating-mode enum; enter-writer gate `!=2 skip`. | **== 2** (active) |
| 8 | **0x969eb074** | byte(s) | ~0xcb9eb074 | per-carrier band-mode class array (stride 0x178). `0xd8deabf0` reads it; value ∉ {9,0xa} selects the `0x89a8=7` branch. | carrier[idx] ∉ {9,0xa} |

**Optional 9th/10th (locate #1's code without a full dump):**
- 9. **0x93bce568** (= PA of 0xc8cde568, dlpager rec0 in b23) — re-read the PHY-pool records
  live to get the *current* 0x2a3 backing addresses (they may move at runtime). word[9] =
  physical base, word[7] = alt base.
- 10. **0x93bcf130** (= PA of 0xc8cdf130, pool control-block) — live cur pointer + count to
  bound how much of 0x2a3 is populated before you dump it.

**Interpretation:** if peeks 1–4 flip to {7, 2, !=0, !=0} after your sequence, the factory
flow reached cal mode and armed the carrier. If 5/6 are {3, nonzero}, the FTM service is up.
If 8 shows the carrier's class ∉ {9,0xa}, that's *why* the driver chose 7 over 2. This is the
minimal live evidence set that closes `cal_trigger_chain.md`'s remaining UNKNOWNs.

---

## 5. WHICH RANGES HOLD THE RESIDENT RF-CAL TRIGGER AND RFLTE CODE

- **The FTM-side cal-arming chain** (RFTEST 0x10xx, mode byte +0x0f==0 → `0x89a8=7`) is
  **CLADE code, backing = b26 @ PA 0x97000000 (#4)**. Already decompressed. **FACT.**
- **The HW-side cal-mode trigger** — the code that actually writes `gp+0x740=2` and runs
  `rflte_mc_carrier_activate`/`wakeup` — is **NOT in the MBN**. It is **resident RF/PHY code in
  DDR ~0x2a000000..0x2b000000 (#1)**, a **separate carveout outside pil-mpss and outside
  mssdump**. This is the single region you must dump *live* to RE the factory cal trigger and
  the RFLTE/RFLM/SDR735 bodies. **FACT (not in MBN) + INFERENCE (0x2a3 backing).**
- **RFLTE rodata/strings** (the anchors) are **b27 @ PA 0x99480000 (#5)**, inside pil-mpss,
  static. **FACT.**
- **The descriptors that point at #1** are in **b23 @ PA 0x93b6a000 (#6)**. **FACT.**

**So the two "code" dumps that matter:** #4 (have it; verify) and **#1 (the real prize —
resident RF code, must dump live).** The two "state" dumps that matter: **#2** and **#3**.

---

## 6. FACT / INFERENCE / UNKNOWN

**FACT**
- 36 PHs of modem.mdt parsed here; `PA = VA − 0x35000000` holds for all 0xc0..0xce5b segments.
- mssdump.elf = 34 PT_LOAD, PA 0x8b800000..0x9b800000, equal to the modem.mdt static layout
  (b24 .bss present as PA 0x94138000/memsz 0x2e17000; b26 as 0x97000000; b27 as 0x99480000).
  It is the PIL-load image, **not** a live RF dump (no 0xd8/0xd4/0x2a3 runtime regions).
- b24 = pure .bss (filesz 0, 0x2e17000) → all listed globals (session, carrier, tech-cb, FTM
  dispatch block+phase, per-carrier array) are inside PA 0x94138000..0x96f4f000, zero in the
  static dump.
- seg25 (PA 0x96f4f000, 0x1a000) holds gate (a) 0x96f4f740 and gate (b) 0x96f56000.
- b26 (PA 0x97000000, 0x244b000) is the CLADE backing of the 0xd8xxxxxx code window that
  contains the full FTM/RFTEST chain and all three cal writers (from cal_trigger_chain.md /
  ftm_phase_byte.md).
- b27 (PA 0x99480000) = ZLIB RF ML1 rodata (RFLTE/RFLM/SDR735 strings).
- b23 control-block @0xc8cdf130 + records @0xc8cde568/5a8/5e8 (read here) carry physical DDR
  addresses 0x2a300000 / 0x2a33f40c / 0x2a33f20c, size 0x20000, tied to PHY thread names
  (SYMPROC_IUSS/DEMOD_LITE_IUSS0-2, name_ptr 0xc366f24a in b21).
- RFLTE/RFLM/SDR735 code bodies: 0 immext xrefs anywhere in the MBN (locate_rflte.md).

**INFERENCE**
- The resident RF/PHY code (incl. the `gp+0x740=2` writer and carrier_activate/wakeup) lives
  in the DDR pool backed by ~0x2a000000..0x2b000000; dump 16 MiB to bracket it, core at
  0x2a300000..0x2a540000. (Anchor: the b23 records; the toolchain author's note.)
- That pool is a separate carveout with its own XPU domain; assign/read may behave differently
  than pil-mpss — test one page first.
- The 8 live peeks in §4 are sufficient to confirm cal-mode entry; the 7-vs-2 outcome is
  decided by peek #8 (carrier class) + #5/#1 (phase/gate).

**UNKNOWN (live-only)**
- Exact runtime extent and current base of the 0x2a3 pool (records may relocate — read peek
  9/10 live first).
- Live values of every §4 peek (all .bss/.sbss = 0 statically).
- Whether the 0x2a3 carveout is HLOS-assignable at all (it may be owned by a different VMID /
  not `qcom_scm_assign_mem`-able); the resident RF code may only be reachable via ramdump/QDL.

---

## 7. RECOMMENDED DUMP ORDER (one-go plan)

1. **Live-peek the 8 (–10) small pages in §4** first — cheap, decisive, answers the cal-mode
   question and tells you if the modem is actually in cal (peeks 1–4) before you spend time on
   big dumps.
2. **Dump #1 (0x2a000000, 16 MiB)** — the resident RF code (the real gap). Test one page's
   assign/read first; if it faults, fall back to ramdump/QDL for this carveout.
3. **Dump #2 targeted pages of b24** around 0x9579a000 (session), 0x95733000 (tech-cb),
   0x968db000 (FTM block), 0x969eb000 (carrier array) — read each twice (torn-read guard).
4. **Dump #3 (seg25, 0x96f4f000, 104 KiB)** — gates snapshot.
5. **Dump #4 (b26, 36 MiB)** only if you want to re-verify the CLADE source (already have it
   decompressed) — safest large region.
6. #5/#6/#7 are static and already on disk (b27/b23/b21); dump only to cross-check.

Everything in steps 3–6 is **inside pil-mpss** (assign/read/assign). Step 2 is the **separate
0x2a3 carveout**. Step 1 is the fastest path to the cal-mode answer.
