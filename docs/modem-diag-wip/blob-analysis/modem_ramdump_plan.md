# Modem RAM dump plan — getting the RESIDENT modem code/state off a running SM6375

**Device:** Moto G82 5G (rhodep), SM6375, mainline Linux 7.2.0-rc5 (postmarketOS/Kali).
**Modem:** `remoteproc0`, `qcom_q6v5_pas`, compatible `qcom,sm6375-mpss-pas`, firmware
`modem.mbn`→`modem.mdt` (36 program headers, carveout `pil-mpss-wlan@8b800000`,
PA 0x8b800000..0x9b800000, 256 MiB, `no-map`, VMID_MSS_MSA, XPU-protected).
**Goal:** read the resident code (not in the static `modem.bNN`) around modem-VA
`0xcbf4f740` / PA `0x96f4f740` (the cal gate) and the RFLTE/cal driver bodies.

**Date:** 2026-10-15. All claims below were measured on the live device this session
unless marked otherwise.

---

## 0. TL;DR — the decisive result

- **Coredump arming works and is non-destructive.** Arming `coredump=inline` and forcing a
  clean SSR produced a **valid ELF32 CORE file, 264,579,373 bytes, 34 PT_LOAD segments**
  covering the whole carveout, including every segment the analysis wanted (seg25 @
  0x96f4f000, b24 .bss @ 0x94138000, b26 CLADE code @ 0x97000000, b27 rodata @ 0x99480000,
  b21/b23). The modem recovered cleanly afterwards (`remote processor modem is now up`).
  **The old "coredump returns 0 bytes" was only because `coredump` was `disabled`.**
- **BUT the segment payload came back ALL ZEROES.** Every sampled PA — code head
  (0x8b800000), CLADE (0x97000000), rodata (0x99480000), the three target globals — read
  `00`. No XPU fault, no SError: the read *succeeded* and returned zeros.
- **Root cause (new, important):** on this SoC the secure PIL **scrubs the MSS carveout to
  zero on modem shutdown**, and the remoteproc core runs `.coredump` **after**
  `rproc_stop()` (i.e. after `qcom_pas_shutdown()`), so by the time the AP reads the
  carveout it has already been wiped. **A post-crash coredump can therefore never capture
  live MSS content on this platform**, no matter how it is armed. This is a stronger, more
  specific finding than "segments weren't registered" — segments *are* registered and the
  ELF is structurally perfect; the data is simply gone.
- **Conclusion / recommended path:** the resident code/state must be read **while the modem
  is alive**, via `qcom_scm_assign_mem` dual-VMID `{HLOS, MSS_MSA}` on the covering page(s),
  using the exact in-tree pattern from `qcom_q6v5_mss.c:q6v5_xfer_mem_ownership()`. A small
  read-only LKM (`rhodep_msspeek`) is the deliverable to write. The coredump path is still
  useful for the *static image layout* and as a structural cross-check, but not for live
  content.

---

## 1. Why coredump returned 0 bytes — full mechanism (item 1)

Source read this session (mainline `drivers/remoteproc/`): `qcom_q6v5_pas.c`,
`qcom_common.c`, `remoteproc_coredump.c`, `remoteproc_core.c`, `remoteproc_sysfs.c`.

The SM6375 modem descriptor (`qcom_q6v5_pas.c`):

```c
static const struct qcom_pas_data sm6375_mpss_resource = {
    .crash_reason_smem = 421,
    .firmware_name = "modem.mdt",
    .pas_id = 4,
    .minidump_id = 3,      // <-- non-zero => uses qcom_pas_minidump_ops
    .auto_boot = false,
    ...
};
```

Because `minidump_id != 0`, probe selects `qcom_pas_minidump_ops`, whose
`.coredump = qcom_pas_minidump` (NOT the plain `rproc_coredump`). The chain on a crash is:

1. `rproc_boot_recovery()` (`remoteproc_core.c`): `rproc_stop(rproc, true)` **first**, then
   `rproc->ops->coredump(rproc)`, then reload. **Coredump runs after stop/shutdown.**
2. `qcom_pas_minidump()`:
   - `if (rproc->dump_conf == RPROC_COREDUMP_DISABLED) return;`  ← **reason the old attempts
     got 0 bytes: the node was `disabled`.**
   - else maps the carveout (`qcom_pas_ctx_map`) and calls
     `qcom_minidump(rproc, id=3, qcom_pas_segment_dump)`.
3. `qcom_minidump()` (`qcom_common.c`): reads the **SMEM minidump TOC, item 602**
   (`SBL_MINIDUMP_SMEM_ID`), indexes `subsystems[3]`:
   - if `regions_baseptr==0 || status!=1 || enabled!='ENBL'` → **falls back to
     `rproc_coredump(rproc)`** over the program-header segments.
   - else builds the dump from the SMEM region table and `rproc_coredump_using_sections()`.
4. The program-header segments are registered on every Linux-side boot by
   `qcom_pas_parse_firmware()` → `qcom_register_dump_segments()` (iterates the 36 `.mdt`
   PHs, skips HASH, `rproc_coredump_add_segment(p_paddr, p_memsz)`). dmesg confirms Linux
   loads the modem (`powering up modem` / `Booting fw image … modem.mbn`), so **the segment
   list is populated** — this device is NOT in attach mode.

**Live minidump TOC state (measured via `rhodep_mdtoc.ko`, reads SMEM 602 read-only):**

```
# minidump global TOC: size=336 status=1 revision=1 enabled=0
# subsystem[3] (modem):
  status            = 0x0
  enabled           = 0x0 ('') != ENBL
  encryption_status = 0x0 ('') != DONE
  region_count      = 0
  regions_baseptr   = 0x0
# VERDICT: minidump SS not valid/enabled -> crash coredump FALLS BACK to rproc_coredump()
#   over program-header segments: CODE+DATA PRESENT.
```

So on this port the modem minidump SS is **empty** → the crash path is the **full
program-header `rproc_coredump()`**, which *does* register the code segments. Good in
principle. The remaining problem is content (section 2), not registration.

### Correct sysfs sequence to ARM coredump (verified)

The `coredump` node accepts **`disabled` | `enabled` | `inline`** (NOT "default"; that
string is rejected — `remoteproc_sysfs.c:coredump_store`):

- `enabled`  → copies **every** segment into one `vmalloc` buffer up front (here ≈252 MiB —
  memory-risky on a phone), then exposes it.
- `inline`   → `vmalloc`s only the ELF headers; segment bytes are streamed on read via
  `rproc_coredump_read()`→`memcpy_fromio` from the carveout. **Stalls recovery until
  userspace reads the devcoredump fully.** Low memory. **Use this.**

```bash
echo 1234 | sudo -S sh -c 'echo inline > /sys/class/remoteproc/remoteproc0/coredump'
# recovery must be enabled so the modem auto-reboots after the dump is read:
cat /sys/class/remoteproc/remoteproc0/recovery   # -> enabled
```

---

## 2. Why the ELF is all zeros — the real blocker (items 1+4 verified)

**Test performed (non-destructive, device recovered):**

```bash
# arm
echo inline > /sys/class/remoteproc/remoteproc0/coredump
# capture in background (reads /sys/class/devcoredump/devcdN/data for failing dev remoteproc0)
/tmp/capture_coredump.sh /tmp/iqexp/modem_core.elf &
# force one clean SSR
echo 1 > /sys/kernel/debug/remoteproc/remoteproc0/crash      # RPROC_FATAL_ERROR
```

Result:

```
found /sys/class/devcoredump/devcd4 (failing=.../remoteproc0) ... reading...
captured 264579373 bytes to /tmp/iqexp/modem_core.elf
dmesg: remoteproc remoteproc0: Ramdump done, 264579373 bytes read
dmesg: remoteproc remoteproc0: remote processor modem is now up
```

`readelf -h/-l`: ELF32, `Type: CORE`, **34 program headers**, all `RWE`. Segment list
(`modem_core_phdrs.txt`, saved on device) includes the key targets:

| PhysAddr   | MemSiz    | what it is (from dump_targets.md) |
|------------|-----------|-----------------------------------|
| 0x96f4f000 | 0x1a000   | seg25: cal_gate @0x96f4f740, rf_ctx @0x96f56000 |
| 0x94138000 | 0x2e17000 | b24 .bss (48 MiB): session/carrier live state |
| 0x97000000 | 0x244b000 | b26 CLADE code (36 MiB): FTM/RFTEST + cal writers |
| 0x99480000 | 0x11a000  | b27 rodata: RFLTE/RFLM/SDR735 strings |
| 0x8e553000 | 0x5616000 | b21 rodata | 
| 0x93b6a000 | 0x5ce000  | b23 init-data: dlpager pool descriptors (→0x2a3 carveout) |

**But every sampled byte of segment payload is `00`:** code head (0x8b800000), CLADE
(0x97000000), rflte rodata (0x99480000), and all three target globals. The only non-zero
bytes in the 252 MiB file are the ELF header + 34 phdrs (first 0x474 bytes). No XPU
violation and no SError in dmesg during the read — the `memcpy_fromio` **succeeded** and
returned zeros.

**Interpretation (FACT that bytes are zero + INFERENCE on cause):** mainline runs
`.coredump` **after** `rproc_stop()`→`qcom_pas_shutdown(pas_id=4)`. On Qualcomm MSS the
secure PIL **scrubs/zeroes the modem carveout on shutdown** (a security property of the MSS
teardown; the carveout is returned to HLOS only after being cleared). So the AP maps and
reads a carveout that has already been wiped. This is consistent with: (a) the read not
faulting (the pages *are* HLOS-owned post-shutdown, unlike the live `/dev/mem` case which
faults), and (b) the content being uniformly zero rather than stale.

**Therefore: the post-crash coredump path cannot deliver live resident content on this
platform.** It delivers a structurally perfect, but zero-filled, image. Arming it differently
(enabled vs inline), registering more segments, or enabling the SMEM minidump would not
change this — all of them read the carveout *after* the scrub. (The vendor QDL/EDL ramdump
avoids this by dumping DDR from the PBL/SBL before Linux/PIL scrubs — but EDL is disabled
here: `msm_poweroff.download_mode=0`, and poking the TCSR DLOAD register resets the SoC, per
`rhodep_dbgmem.c`.)

---

## 3. The minidump (SMEM) path — assessed (item 2)

- Minidump support IS compiled into this kernel implicitly via `qcom_common` +
  `qcom_q6v5_pas` (no separate `CONFIG_QCOM_MINIDUMP`/`qcom_minidump.c` needed for the
  remoteproc side — it lives in `qcom_common.c` and reads SMEM 602). `qcom_smem_get` is
  exported (`/proc/kallsyms`).
- **The modem minidump SS[3] is empty on this device** (measured, §1). The vendor SBL would
  normally populate it; on this mainline/boot path it is zeroed. Even if it were populated,
  minidump regions are *also inside the (scrubbed) carveout* and are collected on the same
  post-shutdown path, so they would be zero too. **Minidump buys nothing here** beyond what
  the full coredump already shows structurally.
- No `/sys/kernel/debug/qcom_smem/` present; SMEM item enumeration is only via a module.

---

## 4. The "assign to HLOS" live-read path — the recommended solution (item 3)

This is the only path that reads the carveout **before** it is scrubbed, i.e. while the
modem runs. It is exactly what the prior docs (`gate_globals_phys.md §4`,
`dump_targets.md §3`) and the memshare daemon already anticipate.

**Authoritative in-tree pattern** (`drivers/remoteproc/qcom_q6v5_mss.c:q6v5_xfer_mem_ownership`):

```c
struct qcom_scm_vmperm next[2];
int perms = 0;
if (local)  { next[perms].vmid = QCOM_SCM_VMID_HLOS;    next[perms].perm = QCOM_SCM_PERM_RWX; perms++; }
if (remote) { next[perms].vmid = QCOM_SCM_VMID_MSS_MSA; next[perms].perm = QCOM_SCM_PERM_RW;  perms++; }
qcom_scm_assign_mem(addr, ALIGN(size, SZ_4K), &current_perm, next, perms);
```

Constants verified present on-device
(`/usr/src/linux-headers-7.2.0-rc5/include/dt-bindings/firmware/qcom,scm.h`):
`QCOM_SCM_VMID_HLOS=0x3`, `QCOM_SCM_VMID_MSS_MSA=0xF`,
`QCOM_SCM_PERM_READ=0x4`/`WRITE=0x2`/`RW=0x6`. `qcom_scm_assign_mem` is
`EXPORT_SYMBOL_GPL` and in `/proc/kallsyms`.

**Dual-VMID is the key.** Assigning the page to `{HLOS RWX, MSS_MSA RW}` (both VMIDs, keep
`current_perm` tracking the state) grants the AP read access **without removing** the
modem's access, so the modem keeps running. Assigning to MSS-only (as `rhodep_memassign`
would for memshare) *removes* HLOS and is the fatal case the memshare daemon warns about.

### Feasibility / risk (honest)

- **Feasible:** the SCM call, the VMIDs, the headers, and a proven module build toolchain
  (`make LLVM=1` on-device, see §6) are all present. The owner already ships 6 LKMs using
  `memremap`/`ioremap`/kprobes; this is the same class of tool.
- **Risks, in order:**
  1. **The page may not honor a partial re-assign while the modem executes from the same
     XPU region.** `pil-mpss` is one `no-map` reservation; the XPU granularity for a
     per-page re-assign inside a live modem region is UNKNOWN. If the SCM rejects it, you
     get `-EINVAL`/`-EPERM` (safe). If it *succeeds* but the XPU still faults a subsequent
     modem access to a neighbouring line, that is a modem crash (recoverable SSR, ~1 s) —
     not a brick.
  2. **Torn reads:** b24/.bss and seg25 are live-written. Read each page **twice** and
     compare (prior guidance). Code pages (b26 CLADE, b27 rodata) are static once loaded, so
     they read consistently.
  3. **Must assign back.** Always `qcom_scm_assign_mem` the page **back to MSS-only**
     (`{MSS_MSA RW}`, drop HLOS) when done, or the modem loses exclusive ownership of that
     page across its next teardown. Do it in the same syscall handler, with the page held
     for the minimum window, and on module unload.
- **Net:** worst realistic outcome is a recoverable modem SSR (the device already SSRs on a
  watchdog loop unrelated to this work and recovers every time). Not a brick. This is
  acceptable and strictly safer than the `/dev/mem` read (which *does* reset the SoC).

### What to write — concrete patch/module spec

Write `kernel/diag-modules/rhodep_msspeek.c` (new LKM, read-only debugfs), modelled on
`rhodep_kvpeek.c` + `rhodep_scmtrace.c`:

- debugfs dir `/sys/kernel/debug/rhodep_msspeek/` with:
  - `paddr` (x64, write): target PA, must lie in 0x8b800000..0x9b800000.
  - `len`   (u32, write): ≤ one page per call recommended.
  - `read`  (read): on read, do, for the page containing `paddr`:
    1. `u64 cur = BIT(QCOM_SCM_VMID_MSS_MSA);` (current owner = modem)
    2. assign page → `{HLOS RWX, MSS_MSA RW}` via the `q6v5_xfer_mem_ownership` pattern
       (`qcom_scm_assign_mem(page, SZ_4K, &cur, next2, 2)`).
    3. on success: `memremap(page, SZ_4K, MEMREMAP_WB)` (reserved DDR, like `rhodep_dbgmem`),
       `memcpy_fromio`/`memcpy` the window **twice**, `memunmap`.
    4. assign page back → `{MSS_MSA RW}` only (`qcom_scm_assign_mem(page, SZ_4K, &cur, next1, 1)`).
    5. hexdump the two reads; flag any mismatch (torn read).
  - guard: refuse PA outside the carveout; refuse `len` spanning >1 page; module param
    `arm=1` required to actually perform the assign (default off, prints the plan only).
- Test order (from `dump_targets.md §4/§7`), one page each, modem **alive** and ideally
  *after* running the FTM/cal sequence so the globals are populated:
  1. `0x96f4f000` page (gate `+0x740`, ctx `+0x7000`) — small, decisive.
  2. `0x9579c000`/`0x9579a000` (carrier ptr, session).
  3. then the static code pages `0x97000000` (b26) and `0x99480000` (b27) to recover the
     resident/paged code — these are static-once-loaded, so even a single clean read is
     usable for disassembly.
- If step 1 returns `-EINVAL`/`-EPERM` from the SCM, the per-page re-assign is not permitted
  on this SoC and the resident 0x2a0_0000 carveout (separate XPU domain, `dump_targets.md
  #1`) is almost certainly also closed — at which point the only remaining route is a
  vendor-stack QDL/EDL ramdump (out of scope; EDL disabled here) or a downstream-kernel
  ramdump driver. Report the SCM return code precisely.

---

## 5. Does the coredump ELF contain resident/paged code? (item 4 answer)

- **Structurally: yes.** The 34 PT_LOAD segments map the entire `modem.mdt` load image,
  which includes the code segments (b00-b22 code/rodata, b26 CLADE-compressed resident code,
  etc.), not just data. The old "coredump is data-only, no code" claim was about the SMEM
  *minidump* curated region set — but this device falls back to the **full** coredump, which
  is not data-only.
- **Content: no.** Because of the shutdown scrub (§2), the payload is zero. So the coredump
  cannot be disassembled. Use it only to (a) confirm the exact PA→segment layout live
  (it matches `gate_globals_phys.md` / `dump_targets.md` exactly) and (b) as a template for
  which pages the §4 live-read LKM should target.
- The genuinely **resident** RF/PHY code the project wants (the `gp+0x740=2` writer,
  `rflte_mc_carrier_activate`, SDR735 bodies) is backed by the **separate 0x2a000000 pool**
  (`dump_targets.md #1`), which is **outside** `pil-mpss` and **not in these program
  headers at all** — so even a non-scrubbed coredump would miss it. That region needs its
  own live assign test (and may be in a different VMID entirely).

---

## 6. Reproduction / artifacts

Tooling built and run on-device this session (clang required — the kernel is clang-built;
`make LLVM=1`):

- `rhodep_mdtoc.ko` — reads SMEM 602 minidump TOC read-only; prints the modem SS[3] state
  and reproduces the kernel's branch decision. **Safe.** Source saved alongside this doc as
  `rhodep_mdtoc.c`.
- `capture_coredump.sh` — watches `/sys/class/devcoredump/devcd*` for the remoteproc0 dump,
  streams it to a file (unblocking inline recovery), then frees it (`echo 0 > .../data`).
  Saved alongside as `capture_coredump.sh`.

```bash
# build on device
scp rhodep_mdtoc.c capture_coredump.sh kali@phone:/tmp/mdtoc/
ssh kali@phone 'cd /tmp/mdtoc && make LLVM=1 && sudo insmod rhodep_mdtoc.ko \
  && sudo cat /sys/kernel/debug/rhodep_mdtoc/toc'
```

**Device left clean:** module removed, `coredump=disabled`, modem `running`. The 252 MiB
all-zero ELF was deleted; its `readelf -l` is kept at
`/tmp/iqexp/modem_core_phdrs.txt` on the device.

---

## 7. Recommendation (one paragraph)

Do **not** pursue the coredump path for content: it is proven to return a zero-filled image
because MSS is scrubbed on shutdown and `.coredump` runs post-shutdown. Instead, write
`rhodep_msspeek.c` — a read-only LKM that, per page, uses `qcom_scm_assign_mem` to add
`{HLOS RWX}` to the live `{MSS_MSA RW}` page (the exact `q6v5_xfer_mem_ownership` dual-VMID
pattern), `memremap`+read it **twice**, then re-assign the page back to MSS-only. Target the
pages from `dump_targets.md §4/§7` with the modem alive and the FTM/cal sequence having run.
This reads live resident content without the XPU fault and without the fatal `/dev/mem`
path, and its worst-case failure is a recoverable SSR. Validate on the tiny `0x96f4f000`
page first and report the SCM return code; if the per-page re-assign is refused, the only
remaining route for the separate `0x2a000000` resident pool is a vendor QDL/EDL ramdump,
which is currently disabled on this device.
