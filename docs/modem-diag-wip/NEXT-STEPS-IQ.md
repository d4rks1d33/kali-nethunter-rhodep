# Next steps — closing raw-IQ capture on the rhodep modem

State as of 2026-09-26 (session 7). Everything that depends on the AP works; the
one remaining blocker is entering the modem's RF calibration mode, whose trigger
lives in resident modem code that is **not in the MBN** we have. This file is the
plan to get past that, with the two concrete avenues we agreed on plus the
supporting facts, so the next session starts here.

## Where we are (one paragraph)

The FTM/RFTEST protocol is fully reverse-engineered and the transport works end to
end over QRTR (userspace diag client, no kernel diagchar). `TECH_ENTER` executes
and returns its 63-byte reply reliably (restart mode). We now know the IQ samples
come back **inline over the DIAG FETCH response, paginated** (NOT memshare — see
below). The memshare OS plumbing (DT reserve 0x8ab00000, `rhodep_memassign` with
dual-VMID hyp_assign, `/dev/rhodep_memshare`) is built, flashed and correct, but
is **off the IQ critical path**. The blocker: `RADIO_CONFIG`/`RX_MEASURE`/
`IQ_CAPTURE` NULL-deref the active-carrier pointer `0xca79c494` and SSR, because
that pointer is only set once the RF-cal-mode gate `0xcbf4f740 == 2` is active, and
that gate is written by the resident/paged RF-cal driver (outside the MBN) when the
modem is in "FTM/cal service". No DIAG command in the image sets it; factory-test
mode + TECH_ENTER alone do not do it reliably.

## Key facts to carry forward (all FACT unless noted)

- IQ samples: inline over DIAG FETCH, chunked. `pkt[0x02]=0x24`,
  `pkt[0x08]=MAX_DIAG_SIZE` (use 0xffff), body TLVs `FETCH_IQ`, `SAMP_OFFSET=k`,
  `NUM_SAMP_BYTES=M`, `RX_CARRIER`. Samples IQIQ interleaved, signed two's-complement,
  LE, 8/16-bit per `IQ_DATA_FORMAT`. ~80 requests for 5 MiB at 64 KiB chunks.
  (`iq_delivery_channel.md`)
- memshare (QMI 52) client on the modem is connect-only at boot; QUERY/ALLOC/FREE
  senders have zero callers; IQ_CAPTURE uses the modem's internal heap, not memshare.
  (`memshare_request_flow.md`)
- The SSR: getter `0xd827923c` reads `memw(0xca79c494)`; NULL -> `err_fatal` -> SSR.
  Writer `0xd8279264` runs only if `0xcbf4f740==2` AND `0xcbf56000!=0`.
  (`gate_resolution.md`, `rf_cal_mode_gates.md`, `base_and_safe_B.md`)
- The per-tech callback table `0xca733d10` IS populated at boot (LTE callback
  `0xd8247540`); TECH_ENTER's `callr` into it runs. But the gate store itself is in
  resident out-of-MBN code. `gp = 0xcbf4f000`. (`cal_entry_sequence.md`)
- Handshake: reliable in restart mode (`--restart-modem`), or AP-initiated via
  `--kick` on the CNTL channel (port = CMD_port-1) but non-deterministic. The reply
  drains to the COMMAND global peer `0xc8c2d870/74`; with `connected==1` (can't be
  reset without reboot) the endpoint doesn't re-wire, so replies don't always come
  back no-restart. (`handshake_no_restart.md`, `reply_routing_norestart.md`,
  `force_rewire.md`, `rewire_encoding.md`)
- Live contradiction to chase: ONE run (online -> settle -> factory-test, module
  loaded) gave IQ_CAPTURE **0 SSR**; the same recipe later gave 64 SSR. So the
  carrier-ptr / cal state is reachable but we haven't pinned what makes it stick.

## Avenue A — live RAM dump of the resident RF-cal driver

The cal-mode trigger and the `rflte_*`/RFLM bodies live in resident modem code
backed by physical DDR (~`0x2a3xxxxx`) / the dlpager pool, outside every program
header. Static RE cannot reach it. A live RAM read of the modem's code/data would
let us RE the exact cal-mode entry and/or read the gate to confirm state.

Steps:
1. Extend the kernel module (we already have `rhodep_memassign.ko` with SCM +
   char-device patterns, and older `rhodep_dbgmem.c`/`rhodep_kvpeek.c` diag modules)
   to map/read modem physical DDR regions. Anchor on the seg27 string VAs
   (0xce6xxxxx) to locate the RF driver in the dump.
   - CAUTION: modem DDR is behind the XPU (VMID_MSS). Reading it from HLOS may need
     an assign to HLOS first (we can do that with qcom_scm_assign_mem, as the
     memshare module already does) or may fault. Test on a small, known region.
2. Dump the region(s), feed to the disassembler (mkelf.py + llvm-objdump hexagon),
   RE the cal-mode entry: what sets `0xcbf4f740=2` and what message/command from the
   AP side (if any) triggers it.
3. Also: read `0xcbf4f740`, `0xcbf56000`, `0xca79c494` live at various points
   (before/after TECH_ENTER, in the "0 SSR" state) to see exactly when the gate flips.
   This alone would confirm/deny whether any AP-reachable sequence sets it.
   - NOTE: DIAG PEEK/POKE are compiled out (`map_diag_core.md`), so the read must be
     a kernel module (mmap/ioremap of the modem region), not a DIAG command.

## Avenue B — chase the "online -> FTM" state that gave 0 SSR

Once, after `online -> settle 8s -> factory-test` with the module loaded,
IQ_CAPTURE ran with 0 SSR (no crash) — i.e. the carrier ptr was set. It wasn't
reproducible. If we can pin the condition, we set the gate indirectly without the
resident driver's help.

Steps:
1. Instrument: a kernel-module peek of `0xcbf4f740` / `0xca79c494` (Avenue A step 3)
   run repeatedly while cycling operating modes, to catch the transition that sets
   the gate. Correlate with: time spent online, network attach, RF cal NV load,
   MCPM votes, `rflte_ftm_mc_wakeup`.
2. Hypotheses to test (each: set the state, then peek the gate, then one IQ_CAPTURE,
   count SSR — all via the on-device script so SSH drops don't matter):
   - online long enough to complete RF bring-up / attach, THEN factory-test.
   - a specific QMI (NAS/DMS) or an AT command that puts RF in a test/cal posture.
   - TECH_ENTER with different SCENARIO/SUB values (we tried 0 and 1).
   - RADIO_CONFIG with a full, valid tune (BAND/EARFCN/BW/RFM_DEVICE) — maybe the
     carrier-apply sets the ptr itself when the TLVs are complete AND the modem is
     in the right posture.
3. If a reproducible recipe is found, wire it into `rhodep-iq-cleanboot.sh` and go
   straight to the FETCH loop for the samples.

## Tooling ready for next session

- On-device drivers (log to /tmp/iqrun/): `scripts/modem/rhodep-iq-memshare-run.sh`,
  `rhodep-diag-probe.sh` (MODE=kick|restart), `rhodep-iq-cleanboot.sh`.
- Diag client: `userspace/debug-tools/rhodep-diag-server.py` (--kick, --cmd-peer,
  --restart-modem, --raw-seq, --cntl-port).
- Kernel module: `kernel/diag-modules/rhodep_memassign.c` (SCM assign + char dev),
  plus `rhodep_dbgmem.c` / `rhodep_kvpeek.c` as starting points for a modem-DDR peek.
- Decompressed modem code for RE: `research/modem-blob/clade_dec_36m.bin.gz`
  (0xd8000000, 36 MB) + `clade_exc_high.bin.gz` (0xd0000000). RE toolkit +
  on-device scripts in `research/modem-blob/re-scripts.tgz`. All findings in
  `research/modem-blob/findings-all.tgz` and mirrored in
  `docs/modem-diag-wip/blob-analysis/`.

## Recommendation

Start with **Avenue A step 3** (a kernel-module peek of the three gate globals),
because it is cheap, safe-ish (read-only), and immediately tells us whether any
AP-reachable state (Avenue B) ever sets the gate — which decides whether B is even
possible or whether we must RE the resident driver (rest of A).

## Update (session 8) — both live-dump avenues are blocked; IQ path paused here

Tested both dump avenues from the running OS; both are hard-blocked in mainline:
- **rhodep_mpeek** (SCM reassign of one modem page MSS->{HLOS,MSS}, read, assign
  back): `qcom_scm_assign_mem` returns **-22 EINVAL** on every modem page. TZ
  refuses to reassign the modem's active pages. Clean fail, no crash. So Avenue A
  (live peek / dump of the resident RF-cal driver) is not possible this way.
- **remoteproc coredump**: `inline` hangs SSH (synchronous 256MB); `enabled` (async)
  produces devcoredump nodes with **data-size 0** — the mainline q6v5 MSS driver
  does not vault modem memory. So no usable dump from the running OS.

Also reconfirmed: the byte-perfect config-apply packet (RFTEST 0x10xx,
wire[0x0f]=wire[0x10]=0) still SSRs live, because config-apply derefs internal RF
state the resident cal driver sets - which we can neither read (above) nor trigger
by DIAG.

**Net:** the IQ path is blocked by resident modem code we can't reach from the OS.
The only remaining dump route is QDL/EDL ramdump (special download mode; captures
state at EDL entry, not in cal mode - so it wouldn't show the cal trigger firing).
Pausing IQ here: fully reverse-engineered and documented; the blocker is outside
the AP's reach. Revisit if a factory tool (QRCT/QMSL) trace or an EDL ramdump of a
cal-mode session becomes available.
