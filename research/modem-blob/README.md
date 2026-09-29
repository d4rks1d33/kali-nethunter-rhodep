# rhodep modem firmware + CLADE decompression + FTM RE

This folder holds the Moto G82 5G (rhodep, SM6375) modem firmware and everything
produced while decompressing it and reverse-engineering the FTM RF-test protocol
(the path toward using the modem transceiver as an SDR / raw-IQ capture).

**Why this README grew:** it started (2026-09-23) as just the pulled firmware
blob, with a plan to "load modem.b26 in Ghidra and read the FTM tables". That
plan was wrong in an instructive way — see below — so as the work progressed the
folder gained the decompressed code, the tools that produced it, the runtime
dump, and the analysis reports. This file was updated to describe the whole set
and, importantly, to record the dead ends so they are not retried.

## Contents

| File | What it is |
|------|------------|
| `modem-blob-rhodep.tgz` (48 MB) | the modem firmware itself (see below) |
| `clade-re-preserve.tgz` (6.8 MB) | the decompressed code + the built extractor + all RE findings |
| `clade-tools-repos.tgz` (264 KB) | the two GitHub repos that made decompression possible — **with our local changes; use this copy, do NOT re-clone** (see below) |
| `re-scripts.tgz` (165 KB) | the static-analysis toolkit (dis.sh, call-graph BFS, xref, table extractors) that produced the RFTEST findings, PLUS the on-device bring-up drivers (`on-device-scripts/`: rhodep-iq-memshare-run.sh, rhodep-diag-probe.sh, rhodep-iq-cleanboot.sh) — see its own README inside |
| `findings-all.tgz` (580 KB) | every RE report (61 `.md` + 11 `.txt`), all sessions incl. the full-firmware + IQ-delivery + cal-mode passes — the complete set; the `.md` are also mirrored in the git repo. **Start with `findings/MASTER_MODEM_MAP.md`**, then the IQ chain: memshare_request_flow / iq_delivery_channel / cal_entry_sequence.md |
| `clade_dec_36m.bin.gz` (25 MB) | the CORRECT re-extracted CLADE code, VA base 0xd8000000, valid code 0xd8000000..~0xda440000 (~36 MB). Supersedes the 10 MB `clade_dec_full.bin` in clade-re-preserve.tgz and the CORRUPT 54 MB clade_dec.bin. gunzip to use with dis.sh |
| `clade_exc_high.bin.gz` (5 MB) | the `.clade.exception_high` code window, VA base 0xd0000000 (~6.3 MB valid) — a window earlier passes never had |
| `mssdump-runtime.elf.gz` (257 KB) | a runtime RAM dump of the MSS (data only, no code — a dead end, kept as evidence) |
| `live-logs/` | the on-device DIAG/FTM probe logs |

### `re-scripts.tgz`
The Python/shell toolkit from the RE sessions (was in `/tmp/modemre/`). The
load-bearing tool is `dis.sh <va> <len>` (disassembles `clade_dec_full.bin` via a
minimal Hexagon ELF wrapper + `llvm-objdump-18`); the rest are xref finders,
sound call-graph reachability (`reach.py`/`bfs5.py`/`check_resolver.py`), a
`callr` resolver, the active-carrier pointer scanner (`scan_ptr_access.py`), and
the FTM/RFTEST table/TLV extractors. Extract `clade_dec_full.bin` (from
`clade-re-preserve.tgz`) alongside them to use. Its `README.md` documents each
script. The narrative `.md` reports are in the git repo under
`docs/modem-diag-wip/blob-analysis/`; the raw `.txt` dumps they cite are in the
tarball's `findings-txt/`.

### `modem-blob-rhodep.tgz`
The complete MSS firmware of THIS device, pulled 2026-09-23 from
`/readonly/firmware/image/modem.*`.
- sha256: `e82f6452b94097c21f9aaf35404d6365832fff857ba499250a9d321cc7368b0e`
- build: `MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133`
- the split MBN: `modem.mdt` (ELF header) + `modem.b00 .. modem.b31`.

## Why the first plan was wrong (the key correction)

The original note here said "modem.b26 is the code, load it in Ghidra." **The
code in b26 is not directly loadable** — the FTM dispatch code lives in a virtual
window (0xd8xxxxxx) that is empty in the static image and is filled at boot. It
took several passes to work out why:
- it is NOT q6zip software paging (the page table for that is boot-built, not in
  the ELF);
- DIAG memory-peek (0x02/0x03/0x04) is compiled out of this firmware;
- the remoteproc RAM coredump is data-only (no code/rodata) — that is what
  `mssdump-runtime.elf.gz` is, kept to prove that avenue is closed;
- the real answer: the code is **CLADE**-compressed (a hardware decompression
  engine). The dictionaries are in `modem.b26` at offsets 0x2444000/0x2446000/
  0x2448000, `.clade.comp` is b26 @ 0, and it decompresses with the real
  `libclade.so`.

So the reliable way to read the FTM tables is NOT static Ghidra on b26 — it is:
decompress b26 with libclade.so → get clean Hexagon → disassemble that.

## `clade-tools-repos.tgz` — the two repos, and what we changed

**Do NOT re-clone these from GitHub — extract this tarball instead.** Both repos
carry local changes of ours that upstream does not have (a decoder fix in one, a
chip-specific harness in the other), and the decompression depends on them. A
fresh clone would be incomplete/different and would not decode this modem. Each
repo inside the tarball has a `LOCAL-CHANGES.md` spelling out what we added and
why; the tarball's own `README.md` repeats the warning.

Both cloned 2026-09-24, kept with their `.git` so the exact versions are frozen
and `git status` shows our additions as untracked.

1. **nlitsme/qualcomm-q6zip** (commit `ecf58b7`) — the q6zip/delta/CLADE format
   knowledge and a pure-Python CLADE decoder (`unclade.py`).
   **We modified it:** the copy in the archive has `unclade_exc.py`, our patched
   decoder. Reason: `unclade.py` mis-decodes CLADE 2-bit code `0b11` as an inline
   32-bit literal, but it is actually a **zero-bit EXCEPTION marker** (the real
   word lives in the `.clade.exception_*` sections). That bug desynchronised the
   bitstream at the first exception of every page and corrupted ~26% of words.
   Our fix treats code `0b11` as a 0-bit marker, which removes all post-exception
   drift and makes every non-exception word correct (~83% overall; the remaining
   exception words themselves need the sections we couldn't source in Python).

2. **mzakocs/qualcomm_baseband_scripts** (commit `1bd9198`) — this is the one that
   finished the job: it ships **`libclade.so`**, the real Qualcomm CLADE codec
   (x86-64) with the exception words implemented. **We used it as-is** plus wrote
   a small harness for our chip: `clade_extractor_sm6375.c` (derived from the
   repo's Pixel-5 extractor) feeds b26 + the three dicts to libclade.so and dumps
   the decompressed code. Since the host is aarch64 and libclade.so is x86-64, it
   runs under `qemu-x86_64-static`. Result: HW-accurate decompression (0.14%
   invalid over 1M instructions).

## `clade-re-preserve.tgz` — the reusable output

- `clade_dec_full.bin` (10 MB) — the decompressed modem code, base VA 0xd8000000.
  This is the expensive artifact; keep it so libclade.so + qemu don't have to be
  re-run. Disassemble a range: `off = vaddr - 0xd8000000`, then
  `llvm-objdump-18 --triple=hexagon -D <slice>`.
- `clade-toolchain/` — libclade.so, the extractor (.c/.h/binary), cladetool.cpp,
  unclade_exc.py.
- `findings/` — the RE reports from the FIRST passes only (this tarball predates
  the 2026-09-25 work). For the COMPLETE, current set of reports use
  `findings-all.tgz` (36 `.md` + 11 `.txt`) or the git mirror under
  `docs/modem-diag-wip/blob-analysis/`.

## Where the work stands (updated 2026-09-26, session 7)

**Read `../../nethunter-rhodep-repo/docs/modem-diag-wip/NEXT-STEPS-IQ.md` first** —
it has the current blocker and the two avenues to close IQ (live RAM dump of the
resident RF-cal driver; and chasing the "online->FTM" state that once gave 0 SSR).

Session-7 summary (all FACT, live on the flashed memshare kernel):
- IQ samples do NOT come via memshare — they return inline over the DIAG FETCH
  response, paginated (findings/iq_delivery_channel.md). The memshare OS work
  (patch 0120 flashed, rhodep_memassign dual-VMID, /dev/rhodep_memshare) is correct
  and boots clean, but is OFF the IQ critical path.
- TECH_ENTER works reliably (63-byte reply) in restart mode. But RADIO_CONFIG /
  IQ_CAPTURE SSR (null carrier ptr 0xca79c494) unless the modem is in RF cal mode
  (gate 0xcbf4f740==2), whose trigger is in resident modem code OUTSIDE the MBN
  (findings/cal_entry_sequence.md). That is the deepest, current blocker.
- New on-device scripts (in re-scripts.tgz `on-device-scripts/` and the repo
  `scripts/modem/`) log to /tmp/iqrun/ to avoid SSH-glitch pain. Session-7 live
  logs in `live-logs/iqrun-session7/`.

### Earlier dead ends (kept so they are not retried)
- The `14 00 5a 03` wrapper was the *cause* of the silence, not the fix: it put the
  constant 0x14 at packet offset 0x02, which the diag core routes to a reserved
  handler that returns 0 (silent drop). The correct packet is the short header
  `4b 0b | 27 00 | sub_command(2) | tlv_count(2) | body`.
- The `allow_flow`/drain theory was also wrong: responses come back fine once we
  become the modem's COMMAND peer (`--cmd-peer` in the diag tool). TECH_ENTER now
  returns a reproducible 63-byte reply + big REPACK blocks.

**What is now fully mapped (8 RE passes, all in `docs/modem-diag-wip/blob-analysis/`):**
- Transport + COMMAND-peer routing works end to end; TECH_ENTER (sub 0x0d, TECH=1)
  executes live and answers.
- RFTEST wire layout: `4b 0b | 27 00 | sub_command(2) | tlv_count(2) | body@8 |
  command_id@0x0a | params@0x0f..0x11 | TLVs@0x12`. The real command selector is
  **command_id (wire byte 10)** indexing the runtime table @0xca79a850; the
  sub_command only picks a wrapper. (`wire_offset_{A,B}.md`, `command_id_map_B.md`)
- Registration order (module init 0xd8182640) gives the command_ids:
  **RADIO_CONFIG=0x00, RX_MEASURE=0x01, IQ_CAPTURE=0x05, COMMAND_CAPABILITY=0x0b**
  (base B=0 inference). (`reg_order_B.md`)
- The RFTEST tech-state gate (0xd8202224) does NOT cover RADIO_CONFIG — no
  circularity. (`gate_resolution.md`)
- 0x100D is the only wrapper whose call-graph never derefs the active-carrier
  pointer. (`canonical_wrapper.md`)

### Full-firmware RE pass (2026-09-25) — the SSR root cause is now exact

A whole-firmware RE (many parallel agents; see `findings/MASTER_MODEM_MAP.md` and
the `map_*.md`) pinned the crash precisely. It is NOT "static RE exhausted" — it is
a missing HW-mode step:

- The SSR is a NULL deref of the active-carrier pointer `@0xca79c494` in the getter
  `0xd827923c` (NULL -> `err_fatal` -> SSR; it asserts, does not return).
- Its only writer `0xd8279264` runs solely when **two hardware gates** are both set:
  `gp+0x740` = **0xcbf4f740** (byte) must == 2 ("RF cal mode"), and `gp+0x7000` =
  **0xcbf56000** (word, RF context) must != 0. `TECH_ENTER` alone sets NEITHER.
- The missing step is an **FTM_SET_MODE (cal) command** under ftm_cmd 0x00
  (FTM_COMMON, handler 0xd8271290): `4b 0b 00 00 <SET_MODE:u16> 02 00`. The exact
  SET_MODE sub-cmd number is UNKNOWN statically (runtime dispatch); candidate 0x10F
  was disproved. (`rf_cal_mode_gates.md`, `ftm_set_mode_verified.md`)

So the real bring-up order is: DMS factory-test -> **FTM_SET_MODE cal** -> TECH_ENTER
LTE -> RADIO_CONFIG (BAND/EARFCN/BW) -> IQ_CAPTURE. The earlier live attempts SSR'd
because FTM_SET_MODE was never sent.

**Proven hard limit:** the RFLTE/RFLM/SDR735 driver *code* is not in the MBN at all
— it lives in resident RF/PHY code backed by physical DDR `0x2a3xxxxx` / a dlpager
pool outside every program header (`locate_rflte.md`, `rflte_new_windows.md`). The
re-extracted `clade_dec_36m.bin` (36 MB, correct) confirms this over the full code.

Next levers (roadmap `docs/modem-diag-wip/FTM-IQ-SDR-ROADMAP.md`):
(a) live-find the FTM_SET_MODE sub-cmd (sweep ftm_cmd 0x00 sub-cmds with mode=2,
watch gate 0xcbf4f740 flip — needs a RAM read path or the F3 confirming cal mode);
(b) a live RAM dump anchored on the seg27 string VAs to recover the RFLTE bodies;
(c) diff against a known-good FTM tool trace. DIAG PEEK/POKE/NV are compiled out
(`map_diag_core.md`), so reading modem RAM in vivo needs another path.

This is the device's own vendor firmware and the third-party RE tools; kept only
for interop/analysis of the port, not redistributed.

## Update (session 9, 2026-09-28) — raw IQ ruled unreachable; QMI-NAS power survey queued

Three parallel RE passes checked every AP-reachable avenue for raw IQ without the
blocked cal-mode; all negative (reports here: `OUT_diag_log_iq.md`,
`OUT_rx_measure.md`, `OUT_qmi_qdss.md`, mirrored in the port repo under
`docs/modem-diag-wip/blob-analysis/`):
- DIAG log packets carry no raw IQ in normal operation (LOG_RX_IQ_SAMPLES is
  CDMA1x-only; the LTE/NR "IQ logs" are the gated capture engine).
- Every FTM RX command's real data passes the abort-on-null getter behind the
  cal-mode gate (single writer, unique dominated chain, store out-of-MBN).
- No QMI/QDSS/NV route surfaces raw IQ (QDSS is CoreSight SW trace; the ML1 "STM"
  is a software state machine, not the trace bus).

Verdict: raw-IQ capture is structurally unreachable from the AP on mainline, and the
modem cannot be an SDR front-end for a BTS (no full-duplex IQ streaming, RF-chain
code not in the MBN). A real SDR (LimeSDR/bladeRF/USRP) is needed for yateBTS/srsRAN.

Usable today (no cal-mode): a per-frequency signal-power survey via QMI-NAS
(RSSI/RSRP/RSRQ vs EARFCN/ARFCN in dBm) — network scan + cell-location-info, a
cell-search-granularity spectrum survey. Planned next step: a `rhodep-rf-survey`
qmicli userspace tool. Details + command sketch in
`../docs/05-modem-rf-gnss.md` §11.
