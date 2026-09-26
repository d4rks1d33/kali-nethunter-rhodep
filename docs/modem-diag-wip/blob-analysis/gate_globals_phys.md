# Modem VA → physical DDR mapping for three RF-cal globals — SM6375 (MPSS.HI.4.3.4)

Static analysis of `modem.mdt` (ELF32 program headers) + `modem.bNN` + the DDR image
`mssdump.elf`. Read-only. Goal: give the AP the physical DDR address (or "unknown")
for three modem Hexagon virtual addresses so a kernel module can ioremap+read them.

Legend: **FACT** = verified byte/structure in this image · **INFERENCE** = deduced with
basis · **UNKNOWN** = not statically determinable here.

The three globals (modem VAs):

| name       | VA          | width | role                                     |
|------------|-------------|-------|------------------------------------------|
| cal_gate   | 0xcbf4f740  | byte  | "RF cal mode" gate (gp=0xcbf4f000)       |
| rf_ctx     | 0xcbf56000  | word  | RF context pointer (gp+0x7000)           |
| carrier    | 0xca79c494  | word  | active-carrier pointer                   |

---

## 1. Segment(s) covering the three VAs — FACT

Parsed directly from `modem.mdt` program headers (e_type=ET_EXEC, e_machine=EM_QDSP6,
e_entry(paddr)=0x8b800000, e_phnum=36). p_flags 0x8000000 = QC "MBN segment" marker;
low bits 4=R 2=W 1=X.

| VA          | seg | p_vaddr    | p_paddr    | p_filesz  | p_memsz    | p_flags       | perm | off_in_seg | notes |
|-------------|----:|------------|------------|-----------|------------|---------------|------|-----------|-------|
| 0xca79c494  | 24  | 0xc9138000 | 0x94138000 | 0x0       | 0x2e17000  | 0x8000006     | RW-  | 0x1664494 | **.bss** (filesz=0, 48 MiB), zero at boot |
| 0xcbf4f740  | 25  | 0xcbf4f000 | 0x96f4f000 | 0x19900   | 0x1a000    | 0x8000006     | RW-  | 0x740     | file-backed DATA (off 0x740 < filesz) |
| 0xcbf56000  | 25  | 0xcbf4f000 | 0x96f4f000 | 0x19900   | 0x1a000    | 0x8000006     | RW-  | 0x7000    | file-backed DATA (off 0x7000 < filesz) |

- **carrier (0xca79c494)** lives in **seg24 = pure .bss** (p_filesz=0). No static content;
  populated in runtime by the modem's C init / dynamic FTM handler registration. (FACT)
- **cal_gate (0xcbf4f740)** and **rf_ctx (0xcbf56000)** live in **seg25 = file-backed
  DATA RW**. Both offsets (0x740, 0x7000) are < p_filesz (0x19900), so both DO have a
  static load image in `modem.b25`. Static file values: **cal_gate = 0x02**, **rf_ctx =
  0x00000000** (these are just the boot-init values; runtime overwrites them). (FACT)

---

## 2. The VA → PA rule (fixed offset) — FACT

For the **entire main load region** the modem's program headers carry a **constant
`p_vaddr − p_paddr = 0x35000000`**. This holds for every segment whose p_vaddr is in
0xc0xxxxxx–0xce5bxxxx (segs 2, 4–29, including the three that matter: 24 and 25):

```
seg 2  va=0xc0800000 pa=0x8b800000  delta=0x35000000
...
seg24  va=0xc9138000 pa=0x94138000  delta=0x35000000
seg25  va=0xcbf4f000 pa=0x96f4f000  delta=0x35000000
seg26  va=0xcc000000 pa=0x97000000  delta=0x35000000
...
```

(Segments with other bases — seg1 0x9b8 1:1, seg3/22/33/34 rodata, seg30–32 TCM/island
at low VA 0x1e4xxxxx — use different deltas, but NONE of the three globals live there.)

**VA → PA rule for these globals (FACT):**

```
vbase = 0xc0800000   pbase = 0x8b800000     (image virtual base / physical load base)
PA = VA - 0x35000000                         (equivalently PA = VA - vbase + pbase)
```

This is a **fixed, simple, 1:1-offset load**: p_paddr is contiguous from the PIL load
base 0x8b800000 and the modem's static MMU maps that carveout to the 0xc0.. virtual
window with a constant +0x35000000 delta. There is **no per-page remap** for the data/bss
segments (the only runtime-only remaps — dlpager 0xd4.. and CLADE 0xd8.. at paddr
0x400000000+ — concern *compressed code*, not these data globals). (FACT for the delta /
INFERENCE that no data-side remap applies, corroborated in §3.)

### Computed physical addresses — FACT

| name     | VA          | PA (= VA − 0x35000000) |
|----------|-------------|------------------------|
| carrier  | 0xca79c494  | **0x9579c494**         |
| cal_gate | 0xcbf4f740  | **0x96f4f740**         |
| rf_ctx   | 0xcbf56000  | **0x96f56000**         |

---

## 3. These PAs are inside the pil-mpss DDR carveout — FACT

The physical DDR dump `mssdump.elf` covers **PA 0x8b800000 .. 0x9b800000** (34 segments,
p_paddr==p_vaddr in the loader's own space, base 0x8b800000). This is byte-identical to
the reserved-memory node measured on the device:

```
pil-mpss-wlan@8b800000    0x8b800000 + 0x10000000  (256 MiB)   -> ends 0x9b800000
```
(matches /proc/iomem `8b800000` and the sm6375.dtsi reserved-memory table.) (FACT)

All three computed PAs fall inside this carveout and inside a real dump segment:

| name     | PA          | in mssdump seg (pa/memsz)          | in carveout? |
|----------|-------------|------------------------------------|--------------|
| carrier  | 0x9579c494  | pa=0x94138000 memsz=0x2e17000      | **YES** (the 48 MiB .bss, seg24) |
| cal_gate | 0x96f4f740  | pa=0x96f4f000 memsz=0x1a000        | **YES** (seg25) |
| rf_ctx   | 0x96f56000  | pa=0x96f4f000 memsz=0x1a000        | **YES** (seg25) |

So the modem image is loaded at a **fixed physical base (0x8b800000 = pil-mpss)** and the
VA→PA is a **simple constant offset** for all three globals. (FACT)

Static dump bytes at those PAs read 0x00000000 (the dump was taken with the modem largely
uninitialised for these fields); the live values only exist at runtime. (FACT)

---

## 4. Can HLOS read these PAs? — SMMU/XPU: NO (without re-assign) — FACT/INFERENCE

**FACT:** the `pil-mpss-wlan@8b800000` region is a `no-map` reserved region owned by the
modem via **VMID_MSS_MSA (0xF)** and enforced by the SoC's **XPU** (memory-protection
unit; on this SoC an XPU violation is fatal). After PIL bring-up and `hyp_assign`, the
pages are removed from the HLOS domain:

- The AP's own driver notes (os_iq_path.md §4.1, verified against `qcom_scm_assign_mem`
  semantics) state that once a region is owned by MSS_MSA, **"el AP (VMID_HLOS) no podrá
  leerla"** — an ioremap + read from HLOS takes an **XPU violation**.
- `no-map` also keeps the region out of the Linux linear map precisely so speculative
  reads cannot touch the XPU.

**→ A plain `ioremap(PA) + readl()` from the AP into 0x9579c494 / 0x96f4f740 / 0x96f56000
will FAULT (XPU violation), not return data.** (FACT for the XPU ownership / INFERENCE that
these specific pages are inside the MSS-owned window — they are inside pil-mpss, which is
the MSS carveout.)

To actually read them from the AP you must first **hyp_assign / `qcom_scm_assign_mem` the
covering page(s) to BOTH VMIDs** (dest = {HLOS RW, MSS_MSA RW}); assigning to MSS only
*removes* HLOS access. That is the documented fix in os_iq_path.md §4.2. Even then you are
reading a page the live modem is concurrently writing (no coherency guarantee).

Note: these are also **arbitrary 4-byte/1-byte scalars inside the modem's private DATA/BSS**,
not a shared/memshare buffer. Re-assigning modem code/data pages to HLOS while the modem
runs is not the sanctioned path (memshare exists for exactly this reason); doing it on
live private state risks XPU faults and modem crash. (INFERENCE.)

---

## 5. Concrete answer per global

| global   | modem VA    | **physical DDR PA** | backing            | AP can ioremap+read directly? |
|----------|-------------|---------------------|--------------------|-------------------------------|
| cal_gate | 0xcbf4f740  | **0x96f4f740**      | seg25 file DATA    | **No** — inside pil-mpss (MSS/XPU); faults unless re-assigned to HLOS |
| rf_ctx   | 0xcbf56000  | **0x96f56000**      | seg25 file DATA    | **No** — same |
| carrier  | 0xca79c494  | **0x9579c494**      | seg24 .bss (runtime) | **No** — same; value only meaningful at runtime |

**VA→PA:** fixed offset `PA = VA − 0x35000000` (vbase 0xc0800000 → pbase 0x8b800000).
**Carveout:** all three ∈ `pil-mpss-wlan@8b800000` (0x8b800000..0x9b800000, 256 MiB,
no-map, MSS_MSA-owned, XPU-protected). Confirmed present in `mssdump.elf`.

---

## 6. FACT / INFERENCE / UNKNOWN — evidence

**FACT**
- Program headers parsed from `modem.mdt`: seg24 (0xc9138000/0x94138000, memsz 0x2e17000,
  filesz 0, RW-) covers 0xca79c494; seg25 (0xcbf4f000/0x96f4f000, memsz 0x1a000, filesz
  0x19900, RW-) covers both 0xcbf4f740 and 0xcbf56000.
- p_vaddr − p_paddr = 0x35000000 constant across the whole 0xc0..0xce5b load region
  (verified per-segment, incl. seg24 and seg25).
- PA(cal_gate)=0x96f4f740, PA(rf_ctx)=0x96f56000, PA(carrier)=0x9579c494.
- All three PAs lie inside `mssdump.elf` (DDR PA 0x8b800000..0x9b800000) → inside real
  dump segments (seg pa=0x94138000 for carrier; pa=0x96f4f000 for the other two).
- That DDR range == `pil-mpss-wlan@8b800000` + 0x10000000 (256 MiB), no-map reserved,
  matching /proc/iomem and sm6375.dtsi.
- cal_gate file-init value = 0x02; rf_ctx file-init value = 0; carrier has no file backing.
- pil-mpss is owned by VMID_MSS_MSA and XPU-protected; HLOS reads fault unless the page is
  re-assigned to HLOS (qcom_scm_assign_mem to BOTH VMIDs).

**INFERENCE**
- No per-page MMU/SMMU remap applies to these DATA/BSS globals: the only runtime remaps in
  this image (dlpager 0xd4.., CLADE 0xd8.. at paddr 0x4_0000_0000+) are for compressed
  *code*, not data; the data segments load 1:1 at +0x35000000. So the fixed-offset rule is
  the whole story for these three.
- The three specific pages are within the MSS-owned window (they are inside pil-mpss),
  therefore behind the XPU for HLOS.

**UNKNOWN**
- Live runtime values of the globals (carrier is .bss = 0 at boot; the other two get
  overwritten by init) — only observable on a running device / live dump, not statically.
- Exact XPU sub-region granularity / whether a partial re-assign of just seg25's page is
  safe while the modem runs (needs runtime test; risks modem crash).
